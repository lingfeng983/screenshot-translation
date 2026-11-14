#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google 免费翻译（Copyfish 同款接口）

即网页版翻译使用的公开接口 translate.googleapis.com/translate_a/single，
无需API密钥，与 Copyfish 浏览器扩展内置翻译使用的是同一免费端点。
"""

import logging

from ..provider_common import (
    ProviderUnavailableError, describe_error, raise_for_status,
    wrap_request_error)
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
        # 延迟导入：启动期加载 urllib3 要 87ms，真正发请求时再付
        import requests

        def translate_chunk(chunk):
            """翻译单个文本块（原文只放请求体，不进 URL 查询串）"""
            params = {
                'client': 'gtx',       # gtx 即网页版翻译使用的公开客户端
                'sl': source or 'auto',
                'tl': target,
                'dt': 't',             # 返回逐段翻译结果
                'dj': '1',             # 结构化JSON响应，便于拼接
            }
            # 原先用 GET 把 q 拼进 URL：查询串会被浏览器历史、正向代理、
            # CDN 与服务端访问日志原样记录，截图里的原文等于被抄走一份。
            # 该端点接受表单编码的 POST，原文放 body 就不会出现在这些日志里。
            try:
                response = self.session.post(
                    DEFAULT_ENDPOINT,
                    params=params,
                    data={'q': chunk},
                    timeout=self._http_timeout(),
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                        'Content-Type': 'application/x-www-form-urlencoded',
                    }
                )
            except requests.RequestException as e:
                raise wrap_request_error(e, 'Google免费翻译') from e

            raise_for_status(response, 'Google免费翻译')
            # dj=1 时响应必定是对象；结构对不上就按空结果降级
            data = self._parse_json(response)

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

    @staticmethod
    def _parse_json(response):
        """解析响应JSON：非JSON（如网关返回HTML）按可重试处理，而不是抛裸 ValueError"""
        try:
            data = response.json()
        except ValueError as e:
            raise ProviderUnavailableError(
                f"Google免费翻译响应不是合法JSON: "
                f"{describe_error(response.text, limit=120)}"
            ) from e
        if not isinstance(data, dict):
            raise ProviderUnavailableError(
                f"Google免费翻译返回结构异常: {str(data)[:200]}")
        return data


def register(registry):
    """注册到全局provider注册表"""
    registry.register(GoogleFreeProvider)
