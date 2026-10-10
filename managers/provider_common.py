#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Provider 公共基础设施

被 providers（翻译API）与 ocr_providers（OCR引擎）两个插件体系共用：
- ProviderRegistry：通用注册表（注册/注销/按名查找）
- discover_providers：包内模块自动发现
- BaseProvider：provider 公共基类（配置读取、文本清理、长文本切分）
- ProviderChainManager：管理器公共基类（分发/重试/失败文本转换）

约定（两个体系一致）：
- provider 模块放在对应包目录下，定义 register(registry) 即被自动发现
- provider 类需有 name / display_name / config_fields 类属性与工作方法
- 识别/翻译失败抛异常，由管理器统一重试并按降级链兜底
"""

import configparser
import hashlib
import importlib
import logging
import pkgutil
import re
import time

from .retry_handler import (
    ChainExpiredError, NonRetryableError, default_retry_handler)

logger = logging.getLogger('screenshot-translation')

# 带查询串的 URL：签名参数与 access_token 都在这里
_URL_WITH_QUERY = re.compile(r'(https?://[^\s?]+)\?[^\s"\']*')

# 值得再试一次的 HTTP 状态码：瞬时故障（限流、超时、网关抖动）
# 其余 4xx 是“请求本身有问题”（密钥无效、额度用尽、参数非法），重试必然重犯
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 507, 509})


def redact_url_query(text):
    """
    抹掉文本里 URL 的查询部分

    requests 的异常字符串带着完整请求 URL，而百度/有道这类接口的凭证就放在
    query 里：一次网络抖动就足以把 client_secret、access_token 或 sign 写进
    本地日志（还会留在代理的访问日志里）。

    Args:
        text (str): 任意文本

    Returns:
        str: 查询串替换后的文本
    """
    if not text:
        return text
    return _URL_WITH_QUERY.sub(r'\1?<查询参数已隐藏>', text)


def describe_error(error, limit=300):
    """
    把异常压成一行可展示/可日志的安全文本

    Args:
        error: 异常对象或 None
        limit (int): 最大长度

    Returns:
        str: 脱敏并截断后的描述
    """
    if error is None:
        return '未知错误'
    text = str(error).replace('\n', ' ').strip()
    return redact_url_query(text)[:limit]


class ProviderConfigError(NonRetryableError):
    """配置缺失类错误（未填密钥/未设置地址）：重试无用，直接降级"""


class ProviderUnsupportedError(NonRetryableError):
    """能力不支持类错误（如引擎不提供行级坐标）：重试无用，直接降级"""


class ProviderTimeoutError(NonRetryableError):
    """
    超时类错误：再跑一次只会再等一个超时

    重试的代价是“用户再等满一次时限”，比降级换引擎更糟，所以按非重试处理。
    """


class ProviderEmptyResultError(NonRetryableError):
    """
    确定性“没有结果”类错误（如本地引擎在同一张图上识别不到文字）

    仅用于结果可复现的引擎：同一张图重试必然得到同样结果，等退避只是
    拖长反馈。云端引擎偶发返回空结果可能是限流/抖动，仍保留重试。
    """


class ProviderRejectedError(NonRetryableError):
    """
    接口明确拒绝本次请求（4xx，除 429/408 这类瞬时码）

    密钥无效、签名过期、额度用尽、参数非法：重试只会再被拒一次，还会把
    整条降级链的时限吃光，因此直接降级到下一个引擎。
    """


class ProviderUnavailableError(Exception):
    """
    接口暂时不可用（5xx / 429 / 网络层失败）：同一引擎再试一次可能就好了

    与 ProviderRejectedError 的区分就是为了保住重试的价值：只有值得重试
    的错误才占用退避时间。
    """


def raise_for_status(response, prefix='接口'):
    """
    按状态码抛可重试/不可重试异常（替代 requests 的 raise_for_status）

    Args:
        response: requests.Response
        prefix (str): 日志与错误文案里的接口名

    Raises:
        ProviderRejectedError: 4xx 明确拒绝
        ProviderUnavailableError: 5xx / 429 等瞬时故障
    """
    status = getattr(response, 'status_code', 0)
    if 200 <= status < 300:
        return
    body = ''
    try:
        body = (response.text or '')[:200]
    except Exception:
        pass
    message = f"{prefix}返回 HTTP {status}: {describe_error(body, limit=200)}"
    if status in _RETRYABLE_STATUS:
        raise ProviderUnavailableError(message)
    if 400 <= status < 500:
        raise ProviderRejectedError(message)
    raise ProviderUnavailableError(message)


def wrap_request_error(error, prefix='接口'):
    """
    把 requests 的传输层异常归一为可重试的 ProviderUnavailableError

    连接重置、DNS 抖动、读超时都属于“再试一次有可能成”，同时错误文本
    要脱敏：requests 的异常串里带着完整请求 URL（含签名与令牌）。

    Args:
        error: 原始异常
        prefix (str): 接口名

    Returns:
        ProviderUnavailableError: 归一后的异常（cause 已挂好，调用方 raise 即可）
    """
    wrapped = ProviderUnavailableError(
        f"{prefix}请求失败: {describe_error(error)}")
    wrapped.__cause__ = error
    return wrapped


def raise_business_error(prefix, code, message, retryable_codes=()):
    """
    接口用业务错误码（HTTP 200 + error_code）表达失败时按码分类抛出

    各家翻译接口的额度用尽、签名错误、语种不支持都混在这一类返回里。
    只有明确属于“瞬时”的码（频率超限、服务升级中）才值得重试；其余一律
    立即降级，否则一次密钥填错就要等完整条链的退避。

    Args:
        prefix (str): 接口名，用于错误文案
        code: 接口返回的错误码（按字符串比较）
        message (str): 接口返回的错误描述
        retryable_codes (iterable): 允许重试的码集合

    Raises:
        ProviderUnavailableError: code 在 retryable_codes 里
        ProviderRejectedError: 其余所有码
    """
    text = f"{prefix}返回错误 {code}: {message}"
    if str(code) in {str(c) for c in retryable_codes}:
        raise ProviderUnavailableError(text)
    raise ProviderRejectedError(text)


class ProviderRegistry:
    """
    通用 provider 注册表

    维护 name -> provider类 的映射与注册顺序，供管理器与配置界面使用。
    """

    def __init__(self, label='provider'):
        """
        初始化注册表

        Args:
            label (str): 日志中显示的体系名称（如 翻译提供者 / OCR提供者）
        """
        self.label = label
        self._providers = {}  # name -> provider类
        self._order = []      # 保持注册顺序，用于UI展示

    def register(self, provider_cls, override=False):
        """
        注册一个provider类

        Args:
            provider_cls: provider类（需有 name 属性与可调用的该方法）
            override (bool): 是否允许覆盖同名provider

        Returns:
            bool: 是否注册成功
        """
        name = getattr(provider_cls, 'name', None)
        if not name:
            logger.error(f"注册{self.label}失败: {provider_cls.__name__} 缺少 name 属性")
            return False

        if name in self._providers and not override:
            logger.warning(f"{self.label} {name} 已存在，跳过重复注册")
            return False

        self._providers[name] = provider_cls
        if name not in self._order:
            self._order.append(name)
        logger.debug(f"{self.label}已注册: {name} ({provider_cls.display_name})")
        return True

    def unregister(self, name):
        """注销一个provider（主要用于测试）"""
        if name in self._providers:
            del self._providers[name]
            self._order.remove(name)

    def get(self, name):
        """按名称获取provider类，未注册时返回None"""
        return self._providers.get(name)

    def names(self):
        """按注册顺序返回所有provider名称"""
        return list(self._order)

    def display_names(self):
        """按注册顺序返回所有provider的显示名"""
        return [self._providers[n].display_name for n in self._order]

    def display_name_of(self, name):
        """获取provider的显示名，未知名称原样返回"""
        cls = self._providers.get(name)
        return cls.display_name if cls else name

    def find_by_display_name(self, display_name):
        """按显示名反查provider名称（配置界面用），未找到返回None"""
        for n in self._order:
            if self._providers[n].display_name == display_name:
                return n
        return None

    def all(self):
        """按注册顺序返回所有provider类"""
        return [self._providers[n] for n in self._order]


def discover_providers(registry, package):
    """
    自动发现并注册包内所有 provider 模块

    约定：模块需定义 register(registry) 函数；以 _ 开头的模块跳过；
    单个模块加载失败不影响其他模块与主程序。

    Args:
        registry: ProviderRegistry 实例
        package: 要扫描的包模块对象
    """
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name.startswith('_'):
            continue
        try:
            module = importlib.import_module(
                f'.{module_info.name}', package.__name__)
            register_fn = getattr(module, 'register', None)
            if callable(register_fn):
                register_fn(registry)
            else:
                logger.debug(f"模块 {module_info.name} 未定义 register 函数，跳过")
        except Exception as e:
            logger.error(f"加载{registry.label}模块 {module_info.name} 失败: {e}")


class BaseProvider:
    """
    provider 公共基类

    子类需有 name / display_name / config_fields 类属性并实现各自的工作方法
    （翻译为 translate，OCR 为 recognize）。
    """

    name = ''
    display_name = ''
    # (字段名, UI显示名, 是否密码框)；字段名同时是对应配置段中的选项名
    config_fields = ()

    # 单次请求的读超时上限（秒）：接口真挂了要的是尽快降级，而不是干等
    _READ_TIMEOUT = 10
    # 单次请求的连接超时上限（秒）：连不上基本就是网络/域名问题，等更久无意义
    _CONNECT_TIMEOUT = 5

    def __init__(self, config_manager=None, session=None, timeout=30):
        """
        初始化provider

        Args:
            config_manager: 配置管理器实例
            session: requests.Session，复用连接；不传则自建
            timeout: 请求超时秒数，或 (连接, 读取) 二元组
        """
        self.config_manager = config_manager
        self.timeout = timeout
        # 自己造的 session 才归自己关：共享 session 属于管理器
        self._owns_session = session is None
        self.session = session
        if self.session is None:
            import requests
            self.session = requests.Session()
        # 本次调用的截止时间戳（time.monotonic 口径），由管理器设置；
        # None 表示不限。分块翻译逐块检查它，避免长文本把降级链拖成几分钟
        self.deadline = None

    def close(self):
        """释放自有连接（共享 session 由管理器统一关闭，不在这里动）"""
        if self._owns_session and self.session is not None:
            try:
                self.session.close()
            except Exception as e:
                logger.debug(f"关闭provider连接失败: {e}")
            self._owns_session = False

    def request_timeout(self, deadline=None):
        """
        本次请求实际使用的 (连接, 读取) 超时

        requests 的 timeout 是“每次 socket 操作”的上限而不是整个请求的
        上限，所以慢接口可以靠不断续命地读下去。这里同时用降级链的剩余
        预算夹住读取时间：预算只剩 2 秒时，不会再给 10 秒的读超时。

        Args:
            deadline (float 或 None): time.monotonic() 口径的截止时间

        Returns:
            tuple(float, float): (连接超时, 读取超时)
        """
        connect, read = self._CONNECT_TIMEOUT, self._READ_TIMEOUT
        if isinstance(self.timeout, (tuple, list)) and len(self.timeout) == 2:
            connect, read = float(self.timeout[0]), float(self.timeout[1])
        elif self.timeout:
            read = float(self.timeout)
        if deadline is not None:
            remaining = max(deadline - time.monotonic(), 0.0)
            # requests 会把这两个数各自用满：连接等满 remaining 再读满
            # remaining，一次请求就能把整条降级链拖到时限的两倍。连接失败
            # 基本是网络/域名问题，先把它限到预算的一半，剩下的留给读取。
            connect = min(connect, max(0.1, remaining * 0.5))
            read = min(read, max(0.1, remaining))
        return connect, read

    def check_deadline(self):
        """
        长文本分块请求之间的时限检查

        一次识别可能截出几十行，逐块请求时每块都有各自的超时；不检查的话
        光分块循环就能跑几分钟，后面的降级引擎和取消都失效。

        Raises:
            ChainExpiredError: 管理器设定的截止时间已过
        """
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ChainExpiredError("降级链整体时限已用尽")

    def _http_timeout(self):
        """按当前 deadline 夹取后的 (连接, 读取) 超时，供各 provider 直接传入 requests"""
        return self.request_timeout(self.deadline)

    # ---- 配置读取 ----

    def _read_config(self, section, option, fallback=''):
        """
        读取指定配置段的选项

        Args:
            section (str): 配置段名（Translation / OCR）
            option (str): 配置项名
            fallback (str): 未设置时的默认值

        Returns:
            str: 配置值
        """
        if self.config_manager is None:
            return fallback
        try:
            return self.config_manager.config.get(
                section, option, fallback=fallback)
        except (configparser.Error, ValueError) as e:
            # 只兜“缺段/缺项/类型不对”这类可预期错误，并且留下痕迹：
            # 静默返回 fallback 会让用户填好的密钥悄悄变成空串
            logger.warning(f"读取配置 [{section}] {option} 失败，按默认值处理: {e}")
            return fallback

    def _require_value(self, value, error_hint):
        """配置值为空时抛出带提示的异常（不抛 Exception：重试器要区分可重试性）"""
        if not value:
            raise ProviderConfigError(error_hint)
        return value

    # ---- 文本工具 ----

    @staticmethod
    def clean_text(text):
        """
        清理识别/译文文本：去除行首尾空白与空行

        Args:
            text (str): 原始文本

        Returns:
            str: 清理后的文本
        """
        if not text:
            return ''
        lines = [line.strip() for line in text.splitlines()]
        cleaned = [line for line in lines if line]
        return '\n'.join(cleaned)

    @staticmethod
    def _chunk_text(text, max_length=4500, max_bytes=None):
        """
        按段落切分长文本，避免超出接口的单次请求上限

        有些接口按字节限长而不是按字符限长（百度单条请求上限 6000 字节），
        中文一个字在 UTF-8 里占 3 字节：只按 3000 字符切会整段请求被拒。
        传 max_bytes 时字符数与字节数两条线都要满足。

        Args:
            text (str): 原始文本
            max_length (int): 单块最大字符数
            max_bytes (int 或 None): 单块最大 UTF-8 字节数；None 表示不限字节

        Returns:
            list[str]: 文本块列表
        """
        text = text.strip()
        if BaseProvider._fits(text, max_length, max_bytes):
            return [text]

        chunks, current = [], ''
        # 优先按段落切，段落太长再按句子切
        for para in text.split('\n'):
            candidate = f"{current}\n{para}" if current else para
            if BaseProvider._fits(candidate, max_length, max_bytes):
                current = candidate
                continue
            if current:
                chunks.append(current)
            while para and not BaseProvider._fits(para, max_length, max_bytes):
                head, para = BaseProvider._split_prefix(para, max_length, max_bytes)
                chunks.append(head)
            current = para
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _fits(text, max_length, max_bytes):
        """单块是否同时满足字符数与字节数上限"""
        if len(text) > max_length:
            return False
        return max_bytes is None or len(text.encode('utf-8')) <= max_bytes

    @staticmethod
    def _split_prefix(text, max_length, max_bytes):
        """
        切出不超过字符/字节上限的最长前缀

        逐字符回退而不是按字节位置硬切：多字节字符被从中间切断之后，
        那一段在下一个请求里就成乱码了。

        Args:
            text (str): 待切文本
            max_length (int): 字符上限
            max_bytes (int 或 None): UTF-8 字节上限

        Returns:
            tuple[str, str]: (前缀, 剩余部分)
        """
        head = text[:max_length]
        while head and max_bytes is not None and len(head.encode('utf-8')) > max_bytes:
            head = head[:-1]
        if not head:
            # 上限小到一个字符都装不下时仍要保证有进展，否则分块循环不会结束
            head = text[:1]
        return head, text[len(head):]


class ProviderChainManager:
    """
    管理器公共基类：provider 分发、重试与失败文本转换

    子类设置类属性：
    - _registry: 对应体系的 ProviderRegistry 实例
    - _default_provider_name: 未知类型时回退的默认provider名
    - _failure_prefix: 失败文本前缀（如 '[翻译失败' / '[OCR识别失败'）
    并可覆盖 _default_retry_handler() 指定默认重试器。
    """

    _registry = None
    _default_provider_name = ''
    _failure_prefix = '[失败'

    def _resolve_provider(self, name):
        """
        按名称解析provider类，未知时回退到默认provider

        Args:
            name (str): provider名称

        Returns:
            provider类或None（连默认provider都未注册时）
        """
        cls = self._registry.get(name) if self._registry else None
        if cls is None and self._default_provider_name:
            logger.warning(
                f"不支持的provider: {name}，回退到 {self._default_provider_name}")
            cls = self._registry.get(self._default_provider_name)
        return cls

    def _default_retry_handler(self):
        """子类可覆盖以指定默认重试器"""
        return default_retry_handler

    def _run_provider(self, provider_factory, call, failure_text, retry_handler=None,
                      deadline=None, cancel_event=None):
        """
        构造并执行单个provider，自带重试；任何异常都转为失败文本而非抛出，
        保证降级链能继续往下走

        Args:
            provider_factory: 零参工厂，返回provider实例
            call: 接收 (provider实例, deadline) 的调用，返回结果
            failure_text: 接收异常(或None)的失败文本生成器
            retry_handler: 重试处理器，缺省用 _default_retry_handler()
            deadline (float 或 None): 本次调用的截止时间戳；None表示不限
            cancel_event (threading.Event 或 None): 置位后立刻放弃等待

        Returns:
            执行结果或失败文本
        """
        handler = retry_handler or self._default_retry_handler()

        try:
            provider = provider_factory()
        except Exception as e:
            # provider构造失败（如引擎缺失）也要走降级而不是崩溃
            logger.error(f"{self._failure_prefix[1:]}provider初始化失败: "
                         f"{describe_error(e)}")
            return failure_text(e)

        def attempt():
            return call(provider, deadline)

        try:
            return handler.execute_with_retry(
                attempt, fallback_result=lambda: failure_text(None),
                deadline=deadline, cancel_event=cancel_event)
        except Exception as e:
            logger.error(f"provider {getattr(provider, 'name', '?')} 执行异常: "
                         f"{describe_error(e)}")
            return failure_text(e)

    @classmethod
    def _is_failure(cls, result):
        """判断结果是否为失败降级文本"""
        return isinstance(result, str) and result.startswith(cls._failure_prefix)


# 常用签名算法：百度/有道等接口的MD5签名
def make_md5_sign(app_id, text, salt, api_key):
    """
    计算 app_id + text + salt + api_key 形式的MD5签名

    Returns:
        str: 32位小写MD5签名
    """
    return hashlib.md5((app_id + text + salt + api_key).encode('utf-8')).hexdigest()
