#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LibreTranslate 提供者（开源免费，可自建服务）
"""

import requests

from ..provider_common import (
    ProviderUnavailableError, describe_error, raise_for_status,
    wrap_request_error)
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

        接口没有批量端点，长文本靠分块逐次请求；每次请求都受降级链
        剩余时限约束，块数再多也不会把整条链拖死。

        Args:
            text (str): 要翻译的文本
            source (str): 源语言代码，'auto' 表示自动检测
            target (str): 目标语言代码

        Returns:
            str: 翻译后的文本
        """
        url = self._get_secret('libretranslate_url') or DEFAULT_URL
        target = self._target_map.get(target, target)

        def translate_chunk(chunk):
            """翻译单个文本块"""
            data = {
                'q': chunk,
                'source': source or 'auto',
                'target': target,
                'format': 'text',
            }
            try:
                response = self.session.post(
                    url,
                    json=data,
                    timeout=self._http_timeout(),
                    headers={'Content-Type': 'application/json'}
                )
            except requests.RequestException as e:
                raise wrap_request_error(e, 'LibreTranslate') from e

            raise_for_status(response, 'LibreTranslate')
            try:
                translated = response.json().get('translatedText', '')
            except ValueError as e:
                raise ProviderUnavailableError(
                    "LibreTranslate响应不是合法JSON: "
                    f"{describe_error(response.text, limit=120)}") from e
            if not translated:
                raise ProviderUnavailableError(
                    f"LibreTranslate返回空结果: {describe_error(response.text, limit=120)}")
            return translated

        return self._translate_chunks(text, translate_chunk)


def register(registry):
    """注册到全局provider注册表"""
    registry.register(LibreTranslateProvider)
