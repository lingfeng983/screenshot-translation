#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟 OCR 提供者（兜底降级用）

当配置的OCR引擎不可用（如本地组件缺失、密钥未填）时，
返回明确的提示文本而不是抛异常，保证截图流程不中断。
"""

import logging

from .base import OCRProvider

logger = logging.getLogger('screenshot-translation')


class MockOCRProvider(OCRProvider):
    """模拟OCR（不执行真实识别，返回提示文本）"""

    name = 'mock'
    display_name = '模拟OCR(降级)'
    config_fields = ()

    def recognize(self, image):
        """
        返回模拟识别结果

        Args:
            image (PIL.Image.Image): 待识别的图像（忽略）

        Returns:
            str: 提示文本
        """
        logger.warning("使用模拟OCR识别")
        # 与 ocr.OCR_NOTICE_TEXTS[0] 保持同一句：OCR 已固定为本地 RapidOCR，
        # 不再有密钥可填，旧文案里的「密钥未填写」是误导
        text = "[OCR识别不可用：请检查本地OCR组件是否完整]"
        logger.info(f"模拟OCR识别结果: {text}")
        return text


def register(registry):
    """注册到全局OCR provider注册表"""
    registry.register(MockOCRProvider)
