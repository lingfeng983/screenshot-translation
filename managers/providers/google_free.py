#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google 免费翻译（Copyfish 同款接口）

即网页版翻译使用的公开接口 translate.googleapis.com/translate_a/single，
无需API密钥，与 Copyfish 浏览器扩展内置翻译使用的是同一免费端点。
"""

import logging

import requests

from ..provider_common import (
    ProviderUnavailableError, raise_for_status, wrap_request_error)
from .base import TranslationProvider

logger = logging.getLogger('screenshot-translation')

DEFAULT_ENDPOINT = 'https://translate.googleapis.com/translate_a/single'


class GoogleFreeProvider(TranslationProvider):
    """Google免费翻译（无需密钥）"""

    name = 'google_free'
    display_name = '谷歌翻译(免费)'
    config_fields = ()

    def translate(self, text, source='auto', target='zh'):
        """
        使用Google免费接口翻译

        Args:
            text (str): 要翻译的文本
            source (str): 源语言代码，'auto' 表示自动检测
            target (str): 目标语言代码

        Returns:
            str: 翻译后的文本
        """
        def translate_chunk(chunk):
            """翻译单个文本块"""
            params = {
                'client': 'gtx',       # gtx 即网页版翻译使用的公开客户端
                'sl': source or 'auto',
                'tl': target,
                'dt': 't',             # 返回逐段翻译结果
                'dj': '1',             # 结构化JSON响应，便于拼接
                'q': chunk,
            }
            try:
                response = self.session.get(
                    DEFAULT_ENDPOINT,
                    params=params,
                    timeout=self._http_timeout(),
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                )
            except requests.RequestException as e:
                raise wrap_request_error(e, 'Google免费翻译') from e

            raise_for_status(response, 'Google免费翻译')
            data = response.json()

            sentences = data.get('sentences') or []
            translated = ''.join(s.get('trans', '') for s in sentences)
            if not translated:
                raise ProviderUnavailableError(
                    f"Google免费翻译返回空结果: {str(data)[:200]}")

            detected = (data.get('src') or '').strip()
            if detected:
                logger.debug(f"Google检测到源语言: {detected}")
            return translated

        return self._translate_chunks(text, translate_chunk)


def register(registry):
    """注册到全局provider注册表"""
    registry.register(GoogleFreeProvider)
