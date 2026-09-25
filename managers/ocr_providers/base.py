#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR 提供者基类与公共工具

基类与文本工具由 managers/provider_common.py 统一提供，
此处只保留向后兼容的导入别名。
"""

from ..provider_common import BaseProvider


class OCRProvider(BaseProvider):
    """
    OCR 提供者基类

    子类实现 recognize(image)，接收 PIL.Image.Image，返回 str；
    识别失败抛异常，由上层重试/降级机制处理。
    配置从 [OCR] 段读取。
    """

    _config_section = 'OCR'

    def _get_option(self, option, fallback=''):
        """
        读取 [OCR] 段的配置项

        Args:
            option (str): 配置项名
            fallback (str): 未设置时的默认值

        Returns:
            str: 配置值
        """
        return self._read_config(self._config_section, option, fallback)

    def _require_option(self, option, error_hint):
        """配置项为空时抛出带提示的异常"""
        return self._require_value(self._get_option(option), error_hint)

    def get_available_languages(self):
        """
        获取引擎支持的识别语言列表，子类可覆盖

        Returns:
            list[str]: 语言代码列表
        """
        return ['eng', 'chi_sim']
