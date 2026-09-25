#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR 提供者（Provider）插件体系

与翻译的 providers 包同构：每个 OCR 引擎是一个独立模块，放在本目录
并定义 ``register(registry)`` 函数即可被自动发现与注册，无需改动
其他代码。

模块内典型写法::

    from .base import OCRProvider

    class MyOCRProvider(OCRProvider):
        name = "my_ocr"
        display_name = "我的OCR"
        config_fields = (("api_key", "API密钥", True),)

        def recognize(self, image):
            return "识别出的文本"

    def register(registry):
        registry.register(MyOCRProvider)

注册表与自动发现逻辑由 managers/provider_common.py 统一提供。
"""

from ..provider_common import ProviderRegistry, discover_providers


class OCRProviderRegistry(ProviderRegistry):
    """OCR 提供者注册表"""

    def __init__(self):
        super().__init__(label='OCR提供者')


# 全局注册表实例
ocr_provider_registry = OCRProviderRegistry()


def _discover_ocr_providers():
    """自动发现并注册本包内所有 OCR provider 模块"""
    discover_providers(ocr_provider_registry, __import__(__name__, fromlist=['']))


_discover_ocr_providers()
