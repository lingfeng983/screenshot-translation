#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR管理器
负责图像文字识别，基于 provider 注册表分发到具体OCR引擎

内置提供者：Tesseract(本地离线)、百度云OCR(在线)、模拟OCR(兜底)
扩展方式：在 managers/ocr_providers/ 下新建符合约定的模块即可自动注册
"""

import logging
import threading
import time

from .error_handler import error_handler
from .ocr_providers import ocr_provider_registry
from .provider_common import (
    ProviderChainManager, ProviderEmptyResultError, describe_error)
from .retry_handler import RetryHandler

logger = logging.getLogger('screenshot-translation')

# OCR失败重试：识别是本地/在线调用，重试2次间隔短即可
ocr_retry_handler = RetryHandler(max_attempts=2, base_delay=0.5)

# 识别结果错误标记，与翻译的 [翻译失败] 风格一致
_OCR_FAIL_PREFIX = '[OCR识别失败'

# 引擎都跑通、但这张图里确实没文字时的可行动提示
NO_TEXT_NOTICE = "未识别到文字，请重新框选包含文字的区域"

# OCR 降级提示文本（mock 引擎的输出与“图里没字”的可行动提示）。
# 这类文本是给用户看的提示，不是原文，不应再送翻译 API。
OCR_NOTICE_TEXTS = (
    "[OCR识别不可用：请检查OCR引擎配置（Tesseract未安装或密钥未填写）]",
    NO_TEXT_NOTICE,
)


def is_ocr_notice(text):
    """
    判断 OCR 结果是否为降级提示文本（而非真实识别内容）

    Args:
        text (str): OCR 返回的文本

    Returns:
        bool: True 表示这是提示文本，调用方应跳过翻译
    """
    return (text or '').strip() in OCR_NOTICE_TEXTS


class OCRManager(ProviderChainManager):
    """
    OCR管理器类
    基于provider注册表分发OCR请求，自带重试与降级；
    分发/重试/失败文本转换由 ProviderChainManager 提供
    """

    # 链式调度基类配置
    _registry = ocr_provider_registry
    _default_provider_name = 'tesseract'
    _failure_prefix = _OCR_FAIL_PREFIX

    def _default_retry_handler(self):
        """
        默认重试器：OCR 的轻量策略（2 次、0.5s 起）

        不覆盖这里，主引擎会落到基类通用的 3 次/1s 策略上：一次失败先白等
        约 3 秒才降级，还多打一次识别请求（云端引擎是算配额的）；
        而块级链路与降级链路用的都是 ocr_retry_handler，同一张图两条链路
        的重试策略不一致。

        Returns:
            RetryHandler: OCR 重试处理器
        """
        return ocr_retry_handler

    def __init__(self, config_manager=None):
        """
        初始化OCR管理器

        Args:
            config_manager: 配置管理器实例（读取 [OCR] 段）
        """
        self.config_manager = config_manager
        self.ocr_type = 'tesseract'  # 默认本地Tesseract
        self._providers = {}
        self._provider_lock = threading.Lock()
        # 最近一次识别失败的异常：用于区分“引擎坏了”与“图里没字”
        self._last_error = None
        # 抛出该异常的 provider 类：降级链里最后动手的可能不是配置的引擎
        self._last_error_provider = None
        self._load_config()

        logger.info(f"OCR管理器初始化完成，当前引擎: {self.ocr_type}，"
                    f"可用提供者: {ocr_provider_registry.names()}")

    def _get_provider(self, provider_cls):
        """
        取得 provider 实例（按类缓存）

        Tesseract 构造时要探测引擎路径、跑一次 `tesseract --version` 子进程并
        扫盘定位 tessdata，若每次识别都新建实例，一次翻译就白白多起一个进程。
        构造过程还会改写进程级全局态（pytesseract 的引擎路径、
        TESSDATA_PREFIX），所以同一次构造必须在锁内完成。

        Args:
            provider_cls: OCR provider 类

        Returns:
            provider 实例（首次调用时构造）
        """
        with self._provider_lock:
            provider = self._providers.get(provider_cls)
            if provider is None:
                provider = provider_cls(config_manager=self.config_manager)
                self._providers[provider_cls] = provider
            return provider

    def _load_config(self):
        """加载OCR引擎配置"""
        try:
            if self.config_manager:
                self.ocr_type = self.config_manager.get_ocr_type()
                logger.info(f"从配置加载OCR引擎: {self.ocr_type}")
        except Exception as e:
            logger.error(f"加载OCR配置失败: {e}")

    def close_providers(self):
        """
        关闭并清空已缓存的 provider 实例

        云端 provider 持有 requests.Session（连接池）：只从字典里摘掉就得等 GC
        才回收套接字，反复保存会攒出一串半开连接，退出时也一样留尾巴。
        摘除在锁内、关闭在锁外——关闭连接不该把下一次 _get_provider 一起挡住。
        正在用旧实例识别的线程最多失败这一次，降级链会带着新 provider 重试。
        """
        with self._provider_lock:
            stale = list(self._providers.values())
            self._providers.clear()
        for provider in stale:
            close = getattr(provider, 'close', None)
            if close is not None:
                try:
                    close()
                except Exception as e:
                    logger.debug(f"关闭OCR提供者失败: {type(e).__name__}")

    def reload_config(self):
        """重新加载配置（配置窗口保存后调用）：被淘汰实例的连接池一起释放"""
        self.close_providers()
        self._load_config()

    def set_ocr_type(self, ocr_type):
        """
        设置OCR引擎类型

        Args:
            ocr_type (str): OCR引擎类型
        """
        self.ocr_type = ocr_type
        logger.info(f"OCR引擎已设置为: {ocr_type}")

    def perform_ocr(self, image, deadline=None, cancel_event=None):
        """
        对图像执行OCR识别（带重试与降级）

        降级链：当前引擎(带重试) -> Tesseract -> 模拟OCR
        保证任何情况下都返回文本，不中断截图翻译流程。

        Args:
            image (PIL.Image.Image): 待识别的图像
            deadline (float 或 None): 本次识别的整体截止时间戳
            cancel_event (threading.Event 或 None): 置位后不再发起后续尝试

        Returns:
            str: 识别出的文本；全部失败时返回错误提示文本
        """
        provider_cls = self._resolve_provider(self.ocr_type)
        if provider_cls is None:
            return f"{_OCR_FAIL_PREFIX}: 无可用OCR引擎]"

        result, last_error, last_provider = self._recognize_via(
            provider_cls, image, deadline=deadline, cancel_event=cancel_event)

        # 1) 当前引擎失败时，尝试本地Tesseract兜底
        if self._is_failure(result) and self._chain_open(deadline, cancel_event):
            fallback_cls = ocr_provider_registry.get('tesseract')
            if fallback_cls is not None and fallback_cls is not provider_cls:
                logger.warning("当前OCR引擎失败，尝试本地Tesseract")
                fallback_result, fb_error, fb_provider = self._recognize_via(
                    fallback_cls, image, deadline=deadline,
                    cancel_event=cancel_event)
                if not self._is_failure(fallback_result):
                    return fallback_result
                # 降级链里最后动手的是兜底引擎：空结果是否定案要看它
                last_error, last_provider = fb_error, fb_provider

        # 2) 最后兜底：模拟OCR，保证流程不中断
        if self._is_failure(result) and self._chain_open(deadline, cancel_event):
            if self._empty_result_is_final(last_error, last_provider):
                # 引擎都跑通了、只是这张图里没文字：给可行动的提示，
                # 否则用户看到"OCR识别不可用，请检查配置"会以为配置坏了
                logger.info("所有OCR引擎都未识别到文字")
                return NO_TEXT_NOTICE
            mock_cls = ocr_provider_registry.get('mock')
            if mock_cls is not None:
                logger.warning("所有OCR引擎均失败，降级为模拟OCR")
                mock_result, _err, _prov = self._recognize_via(
                    mock_cls, image, deadline=deadline,
                    cancel_event=cancel_event)
                if not self._is_failure(mock_result):
                    return mock_result

        return result

    @staticmethod
    def _chain_open(deadline, cancel_event):
        """降级链是否还值得继续往下走（时间还在、用户也没取消）"""
        if cancel_event is not None and cancel_event.is_set():
            return False
        return deadline is None or time.monotonic() < deadline

    def perform_ocr_with_blocks(self, image, deadline=None, cancel_event=None):
        """
        一次识别同时拿到整段文本与带坐标的行块

        就地覆盖原文需要坐标，所以先走块级链路；引擎不支持坐标或识别失败时
        退回 perform_ocr 的文本降级链路，此时行块列表为空，
        调用方据此降级为整体气泡展示。

        Args:
            image (PIL.Image.Image): 待识别的图像
            deadline (float 或 None): 本次识别的整体截止时间戳
            cancel_event (threading.Event 或 None): 置位后不再发起后续尝试

        Returns:
            tuple[str, list[TextBlock]]: (整段文本, 行块列表)
        """
        self._last_error = None
        blocks, last_error, last_provider = self._perform_ocr_blocks(
            image, deadline, cancel_event)
        if blocks:
            text = '\n'.join(block.text for block in blocks)
            if text.strip():
                return text, blocks
        # 本地引擎已经判定“这张图没字”时别再跑一遍文本链路：同一张图重试
        # 结果必然一样，而 Tesseract 一次识别实测约 0.6 秒（模型加载占大头），
        # 白等这一秒只会让“框了个空区域”的反馈慢一倍
        if self._empty_result_is_final(last_error, last_provider):
            logger.info("当前引擎未识别到文字且结果确定，跳过重复识别")
            return NO_TEXT_NOTICE, []
        return self.perform_ocr(image, deadline, cancel_event), []

    def _empty_result_is_final(self, error=None, provider_cls=None):
        """
        本次空结果是否已经是最终答案

        判据必须是“实际动手的那个引擎”，不是配置里的那个：配了云端引擎、
        块级链路里退到本地 Tesseract 后才报“没字”，此时答案同样确定；
        反过来只看配置引擎会把云端偶发空结果当定案。

        错误与引擎由调用方按本次请求局部传递（不再从实例属性上读）：两次
        截图并发时实例属性会被另一次请求覆盖，空结果判定就会串台。

        Args:
            error (Exception 或 None): 本次请求实际抛出的异常；
                None 时回退读实例属性（兼容旧调用方/测试）
            provider_cls: 实际抛出该异常的 provider 类

        Returns:
            bool: 最后尝试的引擎是本地确定性引擎，且刚刚报的就是“没字”
        """
        if error is None and provider_cls is None:
            error = self._last_error
            provider_cls = self._last_error_provider
        if not isinstance(error, ProviderEmptyResultError):
            return False
        provider_cls = provider_cls or self._resolve_provider(self.ocr_type)
        return bool(getattr(provider_cls, 'deterministic_results', False))

    def _perform_ocr_blocks(self, image, deadline=None, cancel_event=None):
        """
        块级识别降级链：当前引擎 -> 本地Tesseract

        Args:
            image (PIL.Image.Image): 待识别的图像
            deadline (float 或 None): 整条识别链共用的截止时间戳
            cancel_event (threading.Event 或 None): 置位后不再发起后续尝试

        Returns:
            tuple[list[TextBlock], Exception 或 None, provider 类 或 None]:
                (行块列表, 本次实际异常, 实际抛异常的 provider 类)
        """
        provider_cls = self._resolve_provider(self.ocr_type)
        if provider_cls is None:
            return [], None, None

        blocks, error, used_cls = self._recognize_blocks_via(
            provider_cls, image, deadline, cancel_event)
        if not blocks:
            fallback_cls = ocr_provider_registry.get('tesseract')
            if fallback_cls is not None and fallback_cls is not provider_cls:
                if not self._chain_open(deadline, cancel_event):
                    logger.warning("识别链时限已用尽，跳过本地Tesseract兜底")
                    return [], error, used_cls
                logger.warning("当前OCR引擎拿不到块级坐标，尝试本地Tesseract")
                blocks, fb_error, fb_cls = self._recognize_blocks_via(
                    fallback_cls, image, deadline, cancel_event)
                if blocks:
                    return blocks, None, fb_cls
                error, used_cls = fb_error, fb_cls
        return blocks, error, used_cls

    def _recognize_blocks_via(self, provider_cls, image, deadline=None,
                              cancel_event=None):
        """
        通过指定provider做块级识别；任何失败返回空列表而不是抛异常

        Args:
            provider_cls: OCR provider类
            image (PIL.Image.Image): 待识别的图像
            deadline (float 或 None): 整条识别链共用的截止时间戳
            cancel_event (threading.Event 或 None): 置位后不再重试

        Returns:
            tuple[list[TextBlock], Exception 或 None, provider 类 或 None]:
                (行块列表, 本次异常, 实际抛异常的 provider 类)
        """
        def factory():
            return self._get_provider(provider_cls)

        def call(provider, deadline=None):
            # 时限要下放到 provider 才能夹住单次 HTTP 超时：只在管理器层
            # 判断 deadline，云端请求仍会按自己的 30 秒超时去等
            provider.deadline = deadline
            try:
                return provider.recognize_blocks(image)
            except Exception as e:
                # 与 _recognize_via 同样记下真实异常：上层要靠它区分
                # “引擎跑通了但图里没字”与“引擎本身不可用”
                self._last_error = e
                self._last_error_provider = provider_cls
                raise

        self._last_error = None
        self._last_error_provider = None
        result = self._run_provider(factory, call, lambda err: None,
                                    deadline=deadline,
                                    cancel_event=cancel_event)
        if isinstance(result, list):
            return result, None, provider_cls
        return [], self._last_error, self._last_error_provider

    def _recognize_via(self, provider_cls, image, retry_handler=None,
                       deadline=None, cancel_event=None):
        """
        通过指定provider识别，自带重试；失败返回错误文本而非抛异常，
        保证降级链能继续往下走（由公共基类的 _run_provider 实现）

        Args:
            provider_cls: OCR provider类
            image (PIL.Image.Image): 待识别的图像
            retry_handler: 重试处理器，缺省用OCR重试器（见 _default_retry_handler）
            deadline (float 或 None): 本次识别的整体截止时间戳
            cancel_event (threading.Event 或 None): 置位后不再重试

        Returns:
            tuple[str, Exception 或 None, provider 类 或 None]:
                (识别文本或失败文本, 本次实际异常, 实际抛异常的 provider 类)
        """
        def factory():
            return self._get_provider(provider_cls)

        def call(provider, deadline=None):
            # 同 _recognize_blocks_via：把整链时限传进 provider，
            # 让它的请求超时按剩余预算夹取
            provider.deadline = deadline
            try:
                return provider.recognize(image)
            except Exception as e:
                # 在这里记真实异常：重试器降级时只把 None 交给 failure_text，
                # 异常类型拿不到，而上层要靠它区分“引擎坏了”与“图里没字”
                self._last_error = e
                self._last_error_provider = provider_cls
                raise

        def failure_text(err):
            if err is not None:
                return f"{_OCR_FAIL_PREFIX}: {describe_error(err)}]"
            return f"{_OCR_FAIL_PREFIX}: {provider_cls.display_name}]"

        self._last_error = None
        result = self._run_provider(factory, call, failure_text, retry_handler,
                                    deadline=deadline, cancel_event=cancel_event)
        if self._is_failure(result):
            return result, self._last_error, self._last_error_provider
        return result, None, provider_cls

    def get_available_languages(self):
        """
        获取当前引擎可用的识别语言列表

        Returns:
            list[str]: 语言代码列表；查询失败时返回默认列表
        """
        provider_cls = ocr_provider_registry.get(self.ocr_type)
        if provider_cls is None:
            return ['eng', 'chi_sim']
        try:
            provider = self._get_provider(provider_cls)
        except Exception as e:
            logger.error(f"OCR提供者初始化失败: {e}")
            return ['eng', 'chi_sim']
        return provider.get_available_languages()

    def handle_ocr_error(self, error):
        """兼容旧接口：统一走错误处理器"""
        return error_handler.handle_ocr_error(error)
