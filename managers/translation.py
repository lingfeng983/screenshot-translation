#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
翻译管理器
负责处理翻译相关功能，基于 provider 注册表分发到具体翻译API

内置提供者：Edge翻译(免费,国内可直连)、谷歌翻译(免费)、LibreTranslate、必应、百度、有道、大模型(LLM)
扩展方式：在 managers/providers/ 下新建符合约定的模块即可自动注册
"""

import logging

import requests

from .cache_manager import cache_manager
from .error_handler import error_handler
from .provider_common import ProviderChainManager
from .providers import provider_registry
from .providers.base import TARGET_LANGUAGES
from .retry_handler import RetryHandler

logger = logging.getLogger('screenshot-translation')

# 统一重试策略：次数少、间隔短，避免降级链整体耗时过长。
# 主API与降级API都用它：主API若落到基类通用的 3 次/1s 策略，一次瞬时故障会
# 先白等约 3 秒（1s+2s 退避）才降级，还多打一次请求（云端API是算额度/计费的）。
translation_retry_handler = RetryHandler(max_attempts=2, base_delay=0.5)


class TranslationManager(ProviderChainManager):
    """
    翻译管理器类
    负责处理翻译相关功能；分发/重试/失败文本转换由 ProviderChainManager 提供
    """

    # 链式调度基类配置
    _registry = provider_registry
    _default_provider_name = 'edge_free'
    _failure_prefix = '[翻译失败'

    def _default_retry_handler(self):
        """
        默认重试器：翻译链统一的轻量策略

        主API、配置的降级API与免费兜底接口共用同一套重试参数，
        避免只有主API落回基类的 3 次/1s 通用策略。

        Returns:
            RetryHandler: 翻译重试处理器
        """
        return translation_retry_handler

    def __init__(self, config_manager=None):
        """
        初始化翻译管理器

        Args:
            config_manager: 配置管理器实例
        """
        self.config_manager = config_manager
        self.api_type = 'edge_free'     # 默认使用Edge免费翻译（微软翻译，国内可直连）
        self.fallback_api_type = ''     # 主API失败后的降级API（空为不降级）
        self.target_lang = 'zh'         # 目标语言
        self.timeout = 30  # 30秒超时
        # 复用连接，避免每次请求重建 TCP+TLS
        self.session = requests.Session()

        # 加载配置
        if config_manager:
            self._load_config()

        logger.info(f"翻译管理器初始化完成，当前API: {self.api_type}，"
                    f"可用提供者: {provider_registry.names()}")

    # 启动预热用的短文本与可预热的接口
    _WARMUP_TEXT = 'Hello'
    # 只有免密钥的免费接口才预热：付费接口（LLM/百度/有道）每次调用都算额度，
    # 不能在用户还没截图时就替用户花钱
    _WARMUP_PROVIDERS = ('edge_free', 'google_free')

    def warm_up(self):
        """
        预热翻译链路（供启动后在后台线程调用）

        首个请求要花 0.2~0.3 秒在 DNS 解析与 TLS 握手上，而本应用的使用
        节奏是“按下快捷键就要出结果”：启动后先用一次免费接口的短请求把
        连接池与握手提前付掉，第一次截图就少等这一截。
        任何失败都只记日志，不影响后续使用。

        Returns:
            bool: 预热请求是否成功；未预热（当前接口非免费接口）也返回 False
        """
        try:
            if self.api_type not in self._WARMUP_PROVIDERS:
                logger.debug(f"当前接口 {self.api_type} 不做启动预热")
                return False
            if self._resolve_provider(self.api_type) is None:
                return False
            result = self.perform_translation(self._WARMUP_TEXT, force_refresh=True)
            warmed = not self._is_failure(result)
            logger.info(f"翻译链路预热{'成功' if warmed else '失败'}: {str(result)[:40]}")
            return warmed
        except Exception as e:
            logger.warning(f"翻译链路预热异常: {e}")
            return False

    def _load_config(self):
        """加载配置"""
        try:
            self.api_type = self.config_manager.get_api_type()
            self.fallback_api_type = self.config_manager.get_fallback_api_type()
            self.target_lang = self.config_manager.get_target_lang()
            logger.info(f"从配置加载: api_type={self.api_type}, "
                        f"fallback={self.fallback_api_type}, target={self.target_lang}")
        except Exception as e:
            logger.error(f"加载配置失败: {e}")

    def reload_config(self):
        """重新加载配置（配置窗口保存后调用）"""
        self._load_config()

    def set_api_type(self, api_type):
        """
        设置翻译API类型

        Args:
            api_type (str): API类型
        """
        self.api_type = api_type
        logger.info(f"翻译API类型已设置为: {api_type}")

    def set_target_lang(self, target_lang):
        """
        设置目标语言

        Args:
            target_lang (str): 目标语言代码
        """
        self.target_lang = target_lang
        logger.info(f"目标语言已设置为: {target_lang}")

    def get_target_languages(self):
        """获取支持的目标语言列表，供UI使用"""
        return TARGET_LANGUAGES

    def perform_translation(self, text, force_refresh=False, target_lang=None):
        """
        执行翻译（带缓存支持）

        Args:
            text (str): 要翻译的文本
            force_refresh (bool): 为True时跳过缓存重新翻译（结果仍会写回缓存）
            target_lang (str): 本次翻译的目标语言；None时使用全局配置

        Returns:
            str: 翻译后的文本
        """
        effective_target = target_lang or self.target_lang

        if not text or not text.strip():
            return ""

        logger.info(f"开始翻译，API类型: {self.api_type}，目标语言: {effective_target}，"
                    f"强制刷新: {force_refresh}")

        # 检查缓存（强制刷新时跳过）
        if not force_refresh:
            cached_result = self._get_cached(text, effective_target)
            if cached_result:
                logger.info("使用缓存的翻译结果")
                return cached_result

        try:
            result = self._dispatch_translation(text, effective_target)

            # 缓存结果
            if result and not result.startswith('[翻译失败]'):
                cache_manager.cache_translation(
                    text, self._cache_key_api(effective_target), result)

            return result

        except Exception as e:
            logger.error(f"翻译失败: {e}")
            import traceback
            traceback.print_exc()
            return error_handler.handle_translation_error(e)

    def _cache_key_api(self, target_lang=None):
        """缓存键使用的API标识，包含目标语言避免不同语言结果混淆"""
        return f"{self.api_type}:{target_lang or self.target_lang}"

    def _get_cached(self, text, target_lang=None):
        """按当前API与目标语言查询缓存"""
        try:
            return cache_manager.get_cached_translation(
                text, self._cache_key_api(target_lang))
        except Exception as e:
            logger.warning(f"读取缓存失败: {e}")
            return None

    def _dispatch_translation(self, text, target_lang=None):
        """
        按当前API类型分发翻译，失败时按降级链依次尝试：
        主API(带重试) -> 配置的降级API(轻量重试) -> Edge/谷歌免费翻译(兜底)

        Args:
            text (str): 要翻译的文本
            target_lang (str): 目标语言；None时使用全局配置

        Returns:
            str: 翻译后的文本，全部失败时返回 [翻译失败] 前缀文本
        """
        effective_target = target_lang or self.target_lang

        provider_cls = self._resolve_provider(self.api_type)
        if provider_cls is None:
            return f"{self._failure_prefix}] {text}"

        result = self._translate_via(
            provider_cls, text, target_lang=effective_target)

        # 1) 主API彻底失败时，先尝试配置的降级API
        if self._is_failure(result) and self.fallback_api_type:
            fallback_cls = provider_registry.get(self.fallback_api_type)
            if fallback_cls is not None and fallback_cls is not provider_cls:
                logger.warning(f"主API失败，尝试降级API: {self.fallback_api_type}")
                fallback_result = self._translate_via(
                    fallback_cls, text, target_lang=effective_target,
                    retry_handler=translation_retry_handler)
                if not self._is_failure(fallback_result):
                    return fallback_result

        # 2) 最后兜底：依次尝试免密钥的免费接口（Edge国内可直连，Google质量更好）
        if self._is_failure(result):
            for free_name in ('edge_free', 'google_free'):
                free_cls = provider_registry.get(free_name)
                if free_cls is None or free_cls is provider_cls:
                    continue
                logger.warning(f"所有配置API均失败，兜底尝试免费接口: {free_name}")
                free_result = self._translate_via(
                    free_cls, text, target_lang=effective_target,
                    retry_handler=translation_retry_handler)
                if not self._is_failure(free_result):
                    return free_result

        return result

    def _translate_via(self, provider_cls, text, target_lang=None, retry_handler=None):
        """
        通过指定provider翻译，自带重试；失败返回错误文本而非抛异常，
        保证降级链能继续往下走（由公共基类的 _run_provider 实现）

        Args:
            provider_cls: provider类
            text (str): 要翻译的文本
            target_lang (str): 目标语言；None时使用全局配置
            retry_handler: 重试处理器，缺省用主重试器（见 _default_retry_handler）

        Returns:
            str: 翻译后的文本，彻底失败时返回 [翻译失败] 前缀文本
        """
        effective_target = target_lang or self.target_lang

        def factory():
            return provider_cls(
                config_manager=self.config_manager,
                session=self.session,
                timeout=self.timeout,
            )

        def call(provider):
            return provider.translate(text, source='auto', target=effective_target)

        def failure_text(err):
            return f"{self._failure_prefix}] {text}"

        return self._run_provider(factory, call, failure_text, retry_handler)

    def test_translation(self, text="Hello, world!"):
        """
        测试当前翻译API是否可用（配置窗口使用）

        Args:
            text (str): 测试文本

        Returns:
            tuple[bool, str]: (是否成功, 结果或错误信息)
        """
        try:
            result = self._dispatch_translation(text)
            if self._is_failure(result):
                return False, result
            return True, result
        except Exception as e:
            return False, str(e)
