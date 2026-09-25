#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
翻译提供者基类与公共工具

基类与文本工具由 managers/provider_common.py 统一提供，
此处只保留语言表与向后兼容的导入别名。
"""

from ..provider_common import BaseProvider

# 常见目标语言：UI下拉框与各provider共用的映射表
# 格式: (语言代码, 显示名)
TARGET_LANGUAGES = [
    ('zh', '中文'),
    ('zh-TW', '繁体中文'),
    ('en', '英语'),
    ('ja', '日语'),
    ('ko', '韩语'),
    ('fr', '法语'),
    ('de', '德语'),
    ('es', '西班牙语'),
    ('ru', '俄语'),
]


class TranslationProvider(BaseProvider):
    """
    翻译提供者基类

    子类实现 translate(text, source='auto', target='zh')，
    失败抛异常由上层重试/降级机制处理。
    配置从 [Translation] 段读取。
    """

    _config_section = 'Translation'

    def _get_secret(self, option, env_name=None):
        """
        读取provider的密钥配置，优先环境变量

        Args:
            option (str): Translation段中的配置项名
            env_name (str): 对应环境变量名，缺省用选项名大写

        Returns:
            str: 密钥值，未设置时返回空字符串
        """
        if self.config_manager is None:
            return ''
        if hasattr(self.config_manager, '_get_secret'):
            return self.config_manager._get_secret(env_name or option.upper(), option)
        return self._read_config(self._config_section, option)

    @staticmethod
    def _clean_text(text):
        """清理OCR常见噪声（兼容旧调用名，内部实现同 clean_text）"""
        return BaseProvider.clean_text(text)

    def _translate_chunks(self, text, translate_chunk, max_length=4500):
        """
        通用分块翻译模板：切分长文本、逐块调用、按行拼接

        Args:
            text (str): 原始文本
            translate_chunk: 接收单块文本的翻译函数
            max_length (int): 单块最大字符数

        Returns:
            str: 各块译文拼接结果
        """
        results = []
        for chunk in self._chunk_text(text, max_length=max_length):
            results.append(translate_chunk(chunk))
        return '\n'.join(results)

    def _require_key(self, value, error_hint):
        """密钥为空时抛出带提示的异常（兼容旧调用名）"""
        return self._require_value(value, error_hint)
