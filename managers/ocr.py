#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR管理器
负责图像文字识别，基于 provider 注册表分发到具体OCR引擎

内置提供者：Tesseract(本地离线)、百度云OCR(在线)、模拟OCR(兜底)
扩展方式：在 managers/ocr_providers/ 下新建符合约定的模块即可自动注册
"""

import logging

from .error_handler import error_handler
from .ocr_providers import ocr_provider_registry
from .provider_common import ProviderChainManager, ProviderEmptyResultError
from .retry_handler import RetryHandler

logger = logging.getLogger('screenshot-translation')

# OCR失败重试：识别是本地/在线调用，重试2次间隔短即可
ocr_retry_handler = RetryHandler(max_attempts=2, base_delay=0.5)

# 识别结果错误标记，与翻译的 [翻译失败] 风格一致
_OCR_FAIL_PREFIX = '[OCR识别失败'

# OCR 降级提示文本（mock 引擎的输出与“图里没字”的可行动提示）。
# 这类文本是给用户看的提示，不是原文，不应再送翻译 API。
OCR_NOTICE_TEXTS = (
    "[OCR识别不可用：请检查OCR引擎配置（Tesseract未安装或密钥未填写）]",
    "未识别到文字，请重新框选包含文字的区域",
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
        # 最近一次识别失败的异常：用于区分“引擎坏了”与“图里没字”
        self._last_error = None
        self._load_config()

        logger.info(f"OCR管理器初始化完成，当前引擎: {self.ocr_type}，"
                    f"可用提供者: {ocr_provider_registry.names()}")

    def _get_provider(self, provider_cls):
        """
        取得 provider 实例（按类缓存）

        Tesseract 构造时要探测引擎路径、跑一次 `tesseract --version` 子进程并
        扫盘定位 tessdata，若每次识别都新建实例，一次翻译就白白多起一个进程。

        Args:
            provider_cls: OCR provider 类

        Returns:
            provider 实例（首次调用时构造）
        """
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

    def reload_config(self):
        """重新加载配置（配置窗口保存后调用）"""
        self._providers.clear()
        self._load_config()

    def set_ocr_type(self, ocr_type):
        """
        设置OCR引擎类型

        Args:
            ocr_type (str): OCR引擎类型
        """
        self.ocr_type = ocr_type
        logger.info(f"OCR引擎已设置为: {ocr_type}")

    def perform_ocr(self, image):
        """
        对图像执行OCR识别（带重试与降级）

        降级链：当前引擎(带重试) -> Tesseract -> 模拟OCR
        保证任何情况下都返回文本，不中断截图翻译流程。

        Args:
            image (PIL.Image.Image): 待识别的图像

        Returns:
            str: 识别出的文本；全部失败时返回错误提示文本
        """
        provider_cls = self._resolve_provider(self.ocr_type)
        if provider_cls is None:
            return f"{_OCR_FAIL_PREFIX}: 无可用OCR引擎]"

        result = self._recognize_via(provider_cls, image)

        # 1) 当前引擎失败时，尝试本地Tesseract兜底
        if self._is_failure(result):
            fallback_cls = ocr_provider_registry.get('tesseract')
            if fallback_cls is not None and fallback_cls is not provider_cls:
                logger.warning("当前OCR引擎失败，尝试本地Tesseract")
                fallback_result = self._recognize_via(fallback_cls, image)
                if not self._is_failure(fallback_result):
                    return fallback_result

        # 2) 最后兜底：模拟OCR，保证流程不中断
        if self._is_failure(result):
            if isinstance(self._last_error, ProviderEmptyResultError):
                # 引擎都跑通了、只是这张图里没文字：给可行动的提示，
                # 否则用户看到"OCR识别不可用，请检查配置"会以为配置坏了
                logger.info("所有OCR引擎都未识别到文字")
                return "未识别到文字，请重新框选包含文字的区域"
            mock_cls = ocr_provider_registry.get('mock')
            if mock_cls is not None:
                logger.warning("所有OCR引擎均失败，降级为模拟OCR")
                mock_result = self._recognize_via(mock_cls, image)
                if not self._is_failure(mock_result):
                    return mock_result

        return result

    def perform_ocr_with_blocks(self, image):
        """
        一次识别同时拿到整段文本与带坐标的行块

        就地覆盖原文需要坐标，所以先走块级链路；引擎不支持坐标或识别失败时
        退回 perform_ocr 的文本降级链路，此时行块列表为空，
        调用方据此降级为整体气泡展示。

        Args:
            image (PIL.Image.Image): 待识别的图像

        Returns:
            tuple[str, list[TextBlock]]: (整段文本, 行块列表)
        """
        blocks = self._perform_ocr_blocks(image)
        if blocks:
            text = '\n'.join(block.text for block in blocks)
            if text.strip():
                return text, blocks
        return self.perform_ocr(image), []

    def _perform_ocr_blocks(self, image):
        """
        块级识别降级链：当前引擎 -> 本地Tesseract

        Args:
            image (PIL.Image.Image): 待识别的图像

        Returns:
            list[TextBlock]: 行块列表；无可用坐标时为空列表
        """
        provider_cls = self._resolve_provider(self.ocr_type)
        if provider_cls is None:
            return []

        blocks = self._recognize_blocks_via(provider_cls, image)
        if not blocks:
            fallback_cls = ocr_provider_registry.get('tesseract')
            if fallback_cls is not None and fallback_cls is not provider_cls:
                logger.warning("当前OCR引擎拿不到块级坐标，尝试本地Tesseract")
                blocks = self._recognize_blocks_via(fallback_cls, image)
        return blocks

    def _recognize_blocks_via(self, provider_cls, image):
        """
        通过指定provider做块级识别；任何失败返回空列表而不是抛异常

        Args:
            provider_cls: OCR provider类
            image (PIL.Image.Image): 待识别的图像

        Returns:
            list[TextBlock]: 行块列表或空列表
        """
        def factory():
            return self._get_provider(provider_cls)

        def call(provider):
            return provider.recognize_blocks(image)

        result = self._run_provider(factory, call, lambda err: None)
        return result if isinstance(result, list) else []

    def _recognize_via(self, provider_cls, image, retry_handler=None):
        """
        通过指定provider识别，自带重试；失败返回错误文本而非抛异常，
        保证降级链能继续往下走（由公共基类的 _run_provider 实现）

        Args:
            provider_cls: OCR provider类
            image (PIL.Image.Image): 待识别的图像
            retry_handler: 重试处理器，缺省用OCR重试器（见 _default_retry_handler）

        Returns:
            str: 识别出的文本，彻底失败时返回 [OCR识别失败...] 文本
        """
        def factory():
            return self._get_provider(provider_cls)

        def call(provider):
            try:
                return provider.recognize(image)
            except Exception as e:
                # 在这里记真实异常：重试器降级时只把 None 交给 failure_text，
                # 异常类型拿不到，而上层要靠它区分“引擎坏了”与“图里没字”
                self._last_error = e
                raise

        def failure_text(err):
            if err is not None:
                return f"{_OCR_FAIL_PREFIX}: {err}]"
            return f"{_OCR_FAIL_PREFIX}: {provider_cls.display_name}]"

        self._last_error = None
        return self._run_provider(factory, call, failure_text, retry_handler)

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
