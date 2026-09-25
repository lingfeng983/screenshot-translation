#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LibreTranslate 提供者（开源免费，可自建服务）
"""

from .base import TranslationProvider

DEFAULT_URL = 'https://libretranslate.com/translate'


class LibreTranslateProvider(TranslationProvider):
    """LibreTranslate 翻译"""

    name = 'libretranslate'
    display_name = 'LibreTranslate'
    config_fields = (
        ('libretranslate_url', 'API地址', False),
    )

    # LibreTranslate 的中文代码就是 zh，暂不支持繁体
    _target_map = {'zh-TW': 'zh'}

    def translate(self, text, source='auto', target='zh'):
        """
        使用LibreTranslate翻译

        Args:
            text (str): 要翻译的文本
            source (str): 源语言代码，'auto' 表示自动检测
            target (str): 目标语言代码

        Returns:
            str: 翻译后的文本
        """
        url = self._get_secret('libretranslate_url') or DEFAULT_URL
        target = self._target_map.get(target, target)

        data = {
            'q': text,
            'source': source or 'auto',
            'target': target,
            'format': 'text',
        }

        response = self.session.post(
            url,
            json=data,
            timeout=self.timeout,
            headers={'Content-Type': 'application/json'}
        )
        response.raise_for_status()

        translated = response.json().get('translatedText', '')
        if not translated:
            raise Exception(f"LibreTranslate返回空结果: {response.text[:200]}")
        return translated


def register(registry):
    """注册到全局provider注册表"""
    registry.register(LibreTranslateProvider)
