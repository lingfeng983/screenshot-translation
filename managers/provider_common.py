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

import hashlib
import importlib
import logging
import pkgutil

from .retry_handler import NonRetryableError, default_retry_handler

logger = logging.getLogger('screenshot-translation')


class ProviderConfigError(NonRetryableError):
    """配置缺失类错误（未填密钥/未设置地址）：重试无用，直接降级"""


class ProviderUnsupportedError(NonRetryableError):
    """能力不支持类错误（如引擎不提供行级坐标）：重试无用，直接降级"""


class ProviderEmptyResultError(NonRetryableError):
    """
    确定性“没有结果”类错误（如本地引擎在同一张图上识别不到文字）

    仅用于结果可复现的引擎：同一张图重试必然得到同样结果，等退避只是
    拖长反馈。云端引擎偶发返回空结果可能是限流/抖动，仍保留重试。
    """


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

    def __init__(self, config_manager=None, session=None, timeout=30):
        """
        初始化provider

        Args:
            config_manager: 配置管理器实例
            session: requests.Session，复用连接；不传则自建
            timeout (int): 请求超时秒数
        """
        self.config_manager = config_manager
        self.session = session
        self.timeout = timeout
        if self.session is None:
            import requests
            self.session = requests.Session()

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
        except Exception:
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
    def _chunk_text(text, max_length=4500):
        """
        按段落切分长文本，避免超出接口的单次请求上限

        Args:
            text (str): 原始文本
            max_length (int): 单块最大字符数

        Returns:
            list[str]: 文本块列表
        """
        text = text.strip()
        if len(text) <= max_length:
            return [text]

        chunks, current = [], ''
        # 优先按段落切，段落太长再按句子切
        for para in text.split('\n'):
            candidate = f"{current}\n{para}" if current else para
            if len(candidate) <= max_length:
                current = candidate
                continue
            if current:
                chunks.append(current)
            while len(para) > max_length:
                chunks.append(para[:max_length])
                para = para[max_length:]
            current = para
        if current:
            chunks.append(current)
        return chunks


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

    def _run_provider(self, provider_factory, call, failure_text, retry_handler=None):
        """
        构造并执行单个provider，自带重试；任何异常都转为失败文本而非抛出，
        保证降级链能继续往下走

        Args:
            provider_factory: 零参工厂，返回provider实例
            call: 接收provider实例的调用，返回结果
            failure_text: 接收异常(或None)的失败文本生成器
            retry_handler: 重试处理器，缺省用 _default_retry_handler()

        Returns:
            执行结果或失败文本
        """
        handler = retry_handler or self._default_retry_handler()

        try:
            provider = provider_factory()
        except Exception as e:
            # provider构造失败（如引擎缺失）也要走降级而不是崩溃
            logger.error(f"{self._failure_prefix[1:]}provider初始化失败: {e}")
            return failure_text(e)

        def attempt():
            return call(provider)

        try:
            return handler.execute_with_retry(
                attempt, fallback_result=lambda: failure_text(None))
        except Exception as e:
            logger.error(f"provider {getattr(provider, 'name', '?')} 执行异常: {e}")
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
