#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR 提供者基类与公共工具

基类与文本工具由 managers/provider_common.py 统一提供，
此处只保留向后兼容的导入别名。
"""

from ..provider_common import BaseProvider, ProviderUnsupportedError


class OCRProvider(BaseProvider):
    """
    OCR 提供者基类

    子类实现 recognize(image)，接收 PIL.Image.Image，返回 str；
    识别失败抛异常，由上层重试/降级机制处理。
    需要支持"就地覆盖原文"的子类再实现 recognize_blocks(image)，
    返回带像素坐标的 TextBlock 列表。
    配置从 [OCR] 段读取。
    """

    _config_section = 'OCR'

    # 同一张图重试必然得到相同结果（本地离线引擎）。为 True 时，上层会把
    # "引擎跑通了但没识别到文字"当作最终答案，不再白跑一遍兜底识别；
    # 云端引擎偶发空结果可能只是限流/抖动，保持降级链路。
    deterministic_results = False

    def recognize_blocks(self, image):
        """
        行级块识别（就地覆盖原文所需的坐标来源）

        只有能返回文本框的引擎实现本方法；未实现时抛
        ProviderUnsupportedError，由 OCRManager 降级为纯文本链路。

        Args:
            image (PIL.Image.Image): 待识别图像

        Raises:
            ProviderUnsupportedError: 当前引擎不提供行级坐标
        """
        raise ProviderUnsupportedError(f"{self.display_name} 不支持行级坐标识别")

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
