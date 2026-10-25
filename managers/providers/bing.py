#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
必应翻译提供者（Azure Translator，需要API密钥）

端点用官方 Azure Translator v3（api.cognitive.microsofttranslator.com）。
早先写的 api.bing.microsofttranslator.com/v3 是 2018 年的旧版必应接口，
早已下线；而 Azure 的资源分「区域」与「全球」两种，区域资源必须带
Ocp-Apim-Subscription-Region 头，否则一律 401。区域因此做成可选项。
"""

import requests

from ..provider_common import (
    ProviderRejectedError, ProviderUnavailableError, describe_error,
    raise_for_status, wrap_request_error)
from .base import MICROSOFT_LANG_MAP, TranslationProvider

# Azure Translator v3 的公开端点
TRANSLATE_URL = 'https://api.cognitive.microsofttranslator.com/translate'


class BingProvider(TranslationProvider):
    """必应/Azure 翻译"""

    name = 'bing'
    display_name = '必应翻译'
    config_fields = (
        ('bing_api_key', 'API密钥', True),
        ('bing_region', '区域(可选，如 eastasia)', False),
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
        # 非密钥项：环境变量 BING_REGION 也认，方便自建部署
        region = (self._get_secret('bing_region') or '').strip()

        headers = {
            'Ocp-Apim-Subscription-Key': api_key,
            'Content-Type': 'application/json',
        }
        if region:
            # 区域/多服务资源必须带；全球资源带上也无害
            headers['Ocp-Apim-Subscription-Region'] = region
        # Azure 只认它自己的语种表：繁体中文送 zh-TW 会 400 Bad Request
        params = {'api-version': '3.0',
                  'to': MICROSOFT_LANG_MAP.get(target, target)}

        def translate_chunk(chunk):
            """翻译单个文本块"""
            data = [{'text': chunk}]
            try:
                response = self.session.post(
                    TRANSLATE_URL, headers=headers, params=params, json=data,
                    timeout=self._http_timeout())
            except requests.RequestException as e:
                raise wrap_request_error(e, '必应翻译') from e

            if response.status_code == 401 and not region:
                # 401 在没填区域时几乎都是区域资源：直接给可行动的原因，
                # 而不是把 Azure 的英文响应体原样抛给用户
                raise ProviderRejectedError(
                    "必应翻译返回 401：密钥无效，或该资源需要填写“区域”"
                    "（Azure 门户里资源的 Location，如 eastasia）")
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
