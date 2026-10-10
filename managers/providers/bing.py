#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
必应翻译提供者（Azure Translator，需要API密钥）
"""

import requests

from ..provider_common import (
    ProviderUnavailableError, describe_error, raise_for_status,
    wrap_request_error)
from .base import TranslationProvider


class BingProvider(TranslationProvider):
    """必应/Azure 翻译"""

    name = 'bing'
    display_name = '必应翻译'
    config_fields = (
        ('bing_api_key', 'API密钥', True),
    )

    def translate(self, text, source='auto', target='zh'):
        """
        使用必应翻译

        Args:
            text (str): 要翻译的文本
            source (str): 源语言代码，省略 from 参数让必应自动检测
            target (str): 目标语言代码

        Returns:
            str: 翻译后的文本
        """
        api_key = self._require_key(
            self._get_secret('bing_api_key'), "必应翻译API密钥未设置")

        url = "https://api.bing.microsofttranslator.com/v3/translate"
        headers = {
            'Ocp-Apim-Subscription-Key': api_key,
            'Content-Type': 'application/json'
        }
        params = {'api-version': '3.0', 'to': target}

        def translate_chunk(chunk):
            """翻译单个文本块"""
            data = [{'text': chunk}]
            try:
                response = self.session.post(
                    url, headers=headers, params=params, json=data,
                    timeout=self._http_timeout())
            except requests.RequestException as e:
                raise wrap_request_error(e, '必应翻译') from e

            raise_for_status(response, '必应翻译')
            result = self._parse_json(response)
            # 必应业务错误可能返回 200 + dict（如 {"error": {...}}），需先校验结构
            if not result or not isinstance(result, list):
                raise ProviderUnavailableError(f"必应翻译返回结构异常: {str(result)[:200]}")
            translations = result[0].get('translations') if isinstance(result[0], dict) else None
            if not translations:
                raise ProviderUnavailableError(
                    f"必应翻译返回空结果: {str(result)[:200]}")
            return translations[0].get('text', '')

        return self._translate_chunks(text, translate_chunk)

    @staticmethod
    def _parse_json(response):
        """解析响应JSON：非JSON（如网关返回HTML）按可重试处理"""
        try:
            return response.json()
        except ValueError as e:
            raise ProviderUnavailableError(
                f"必应翻译响应不是合法JSON: {describe_error(response.text, limit=120)}"
            ) from e


def register(registry):
    """注册到全局provider注册表"""
    registry.register(BingProvider)
