#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
翻译提供者（Provider）插件体系

每个 provider 是一个独立的 Python 模块，放在本目录下并定义
``register(registry)`` 函数即可被自动发现与注册，无需改动任何
现有代码——这就是"支持翻译API扩展"的机制。

模块内典型写法::

    from .base import TranslationProvider

    class MyProvider(TranslationProvider):
        name = "my_api"
        display_name = "我的翻译"
        config_fields = (("api_key", "API密钥", True),)  # (字段名, 显示名, 是否密码框)

        def translate(self, text, source="auto", target="zh"):
            ...

    def register(registry):
        registry.register(MyProvider)

注册表与自动发现逻辑由 managers/provider_common.py 统一提供，
此处只保留向后兼容的导入别名。
"""

import logging

from ..provider_common import ProviderRegistry, discover_providers

logger = logging.getLogger('screenshot-translation')


# 翻译体系专用注册表（label用于日志区分）
class TranslationProviderRegistry(ProviderRegistry):
    """翻译提供者注册表"""

    def __init__(self):
        super().__init__(label='翻译提供者')


# 全局注册表实例
provider_registry = TranslationProviderRegistry()


def _discover_providers():
    """自动发现并注册本包内所有翻译 provider 模块"""
    discover_providers(provider_registry, __import__(__name__, fromlist=['']))


_discover_providers()
