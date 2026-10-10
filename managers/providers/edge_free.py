#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Edge 免费翻译提供者（微软翻译，国内可直连，无需API密钥）

即 Microsoft Edge 浏览器内置翻译使用的免费端点
（POST https://edge.microsoft.com/translate/translatetext），
无需鉴权，微软翻译服务在中国大陆可正常访问。

注意：此为 Edge 前端使用的非公开端点，上层已内置重试与降级机制。
"""

import requests

from ..provider_common import (
    ProviderUnavailableError, describe_error, raise_for_status,
    wrap_request_error)
from .base import MICROSOFT_LANG_MAP, TranslationProvider

ENDPOINT = 'https://edge.microsoft.com/translate/translatetext'

# 通用语言代码 -> 微软语言代码（微软用 zh-Hans/zh-Hant 表示简繁体中文）
_TARGET_MAP = MICROSOFT_LANG_MAP


class EdgeFreeProvider(TranslationProvider):
    """Edge/微软免费翻译（无需密钥，国内可直连）"""

    name = 'edge_free'
    display_name = 'Edge翻译(免费)'
    config_fields = ()

    def translate(self, text, source='auto', target='zh'):
        """
        使用Edge免费接口翻译

        Args:
            text (str): 要翻译的文本
            source (str): 源语言代码，'auto'/空 表示自动检测
            target (str): 目标语言代码

        Returns:
            str: 翻译后的文本
        """
        return self._translate_chunks(
            text, lambda chunk: self._translate_chunk(chunk, source, target),
            max_length=9000)

    def _translate_chunk(self, text, source, target):
        """翻译单个文本块"""
        to_lang = _TARGET_MAP.get(target, target)
        # from 留空即自动检测源语言
        from_lang = '' if not source or source == 'auto' else source

        url = (
            f'{ENDPOINT}?from={from_lang}&to={to_lang}&isEnterpriseClient=false'
        )

        try:
            response = self.session.post(
                url,
                json=[text],  # body 必须是字符串数组
                timeout=self._http_timeout(),
                headers={'Content-Type': 'application/json'},
            )
        except requests.RequestException as e:
            # 传输层失败一律可重试：换 IP、重连都可能好
            raise wrap_request_error(e, 'Edge免费翻译') from e

        raise_for_status(response, 'Edge免费翻译')

        data = self._parse_json(response)
        translated = self._extract_text(data)
        if not translated:
            raise ProviderUnavailableError(f"Edge翻译返回空结果: {str(data)[:200]}")
        return translated

    @staticmethod
    def _parse_json(response):
        """解析响应JSON：非JSON（如网关返回HTML）按可重试处理，而不是抛裸 ValueError"""
        try:
            return response.json()
        except ValueError as e:
            raise ProviderUnavailableError(
                f"Edge翻译响应不是合法JSON: {describe_error(response.text, limit=120)}"
            ) from e

    @staticmethod
    def _extract_text(data):
        """从响应中提取译文：[{"translations": [{"text": "..."}]}]"""
        if not isinstance(data, list) or not data:
            return ''
        first = data[0]
        if not isinstance(first, dict):
            return ''
        translations = first.get('translations') or []
        if not translations or not isinstance(translations[0], dict):
            return ''
        return translations[0].get('text', '')


def register(registry):
    """注册到全局provider注册表"""
    registry.register(EdgeFreeProvider)
