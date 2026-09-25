#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Edge 免费翻译提供者（微软翻译，国内可直连，无需API密钥）

即 Microsoft Edge 浏览器内置翻译使用的免费端点
（POST https://edge.microsoft.com/translate/translatetext），
无需鉴权，微软翻译服务在中国大陆可正常访问。

注意：此为 Edge 前端使用的非公开端点，上层已内置重试与降级机制。
"""

import logging

from .base import TranslationProvider

logger = logging.getLogger('screenshot-translation')

ENDPOINT = 'https://edge.microsoft.com/translate/translatetext'

# 通用语言代码 -> 微软语言代码（微软用 zh-Hans/zh-Hant 表示简繁体中文）
_TARGET_MAP = {
    'zh': 'zh-Hans',
    'zh-TW': 'zh-Hant',
}


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
        results = []
        for chunk in self._chunk_text(text):
            results.append(self._translate_chunk(chunk, source, target))
        return '\n'.join(results)

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
                timeout=self.timeout,
                headers={'Content-Type': 'application/json'},
            )
            response.raise_for_status()

            data = response.json()
            translated = self._extract_text(data)
            if not translated:
                raise Exception(f"Edge翻译返回空结果: {str(data)[:200]}")
            return translated

        except Exception as e:
            if isinstance(e, Exception) and 'Edge翻译返回空结果' in str(e):
                raise
            logger.error(f"Edge免费翻译请求失败: {e}")
            raise Exception(f"Edge免费翻译请求失败: {e}") from e

    @staticmethod
    def _extract_text(data):
        """从响应中提取译文：[{"translations": [{"text": "..."}]}]"""
        if not isinstance(data, list) or not data:
            return ''
        translations = data[0].get('translations') or []
        if not translations:
            return ''
        return translations[0].get('text', '')


def register(registry):
    """注册到全局provider注册表"""
    registry.register(EdgeFreeProvider)
