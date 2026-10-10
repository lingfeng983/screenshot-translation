#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
有道翻译提供者（需要 app_id + api_key）
"""

import time

import requests

from ..provider_common import (
    ProviderUnavailableError, describe_error, make_md5_sign,
    raise_business_error, raise_for_status, wrap_request_error)
from .base import TranslationProvider


class YoudaoProvider(TranslationProvider):
    """有道翻译"""

    name = 'youdao'
    display_name = '有道翻译'
    config_fields = (
        ('youdao_app_id', '应用ID', False),
        ('youdao_api_key', 'API密钥', True),
    )

    # 有道目标语言代码映射
    _target_map = {'zh': 'zh-CHS', 'zh-TW': 'zh-CHT'}
    # 105=频率超限、200=译文为空：这两个重试有意义；其余（密钥无效、
    # 未付费、账户欠费、文本过长）重试必然重犯
    _transient_codes = ('105', '200')

    def translate(self, text, source='auto', target='zh'):
        """
        使用有道翻译

        Args:
            text (str): 要翻译的文本
            source (str): 源语言代码，'auto' 表示自动检测
            target (str): 目标语言代码

        Returns:
            str: 翻译后的文本
        """
        if self.config_manager:
            app_id, api_key = self.config_manager.get_youdao_api_info()
        else:
            app_id, api_key = '', ''
        self._require_key(app_id, "有道翻译应用ID未设置")
        self._require_key(api_key, "有道翻译API密钥未设置")

        url = "https://openapi.youdao.com/api"
        target = self._target_map.get(target, target)

        def translate_chunk(chunk):
            """翻译单个文本块"""
            salt = str(int(time.time()))
            params = {
                'q': chunk,
                'from': source or 'auto',
                'to': target,
                'appKey': app_id,
                'salt': salt,
                'sign': make_md5_sign(app_id, chunk, salt, api_key),
            }
            # 凭证走表单体：GET + query 会把 appKey 与 sign 暴露在
            # 异常文本、日志和各级代理的访问日志里
            try:
                response = self.session.post(
                    url, data=params, timeout=self._http_timeout())
            except requests.RequestException as e:
                raise wrap_request_error(e, '有道翻译') from e

            raise_for_status(response, '有道翻译')
            result = self._parse_json(response)
            # 有道用errorCode标识业务错误，'0'表示成功
            error_code = str(result.get('errorCode', '0'))
            if error_code != '0':
                raise_business_error('有道翻译', error_code,
                                     result.get('errorMsg', ''),
                                     self._transient_codes)

            chunk_result = (result.get('translation') or [''])[0]
            if not chunk_result:
                raise ProviderUnavailableError(
                    f"有道翻译返回空结果: {str(result)[:200]}")
            return chunk_result

        return self._translate_chunks(text, translate_chunk, max_length=3000)

    @staticmethod
    def _parse_json(response):
        """解析响应JSON：非JSON（如网关返回HTML）按可重试处理"""
        try:
            return response.json()
        except ValueError as e:
            raise ProviderUnavailableError(
                f"有道翻译响应不是合法JSON: {describe_error(response.text, limit=120)}"
            ) from e


def register(registry):
    """注册到全局provider注册表"""
    registry.register(YoudaoProvider)
