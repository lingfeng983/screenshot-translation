#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
有道翻译提供者（需要 app_id + api_key）
"""

import time

from ..provider_common import make_md5_sign
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
            # 重试由上层统一处理，这里只发起单次请求
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()

            result = response.json()
            # 有道用errorCode标识业务错误，'0'表示成功
            error_code = str(result.get('errorCode', '0'))
            if error_code != '0':
                raise Exception(f"有道翻译返回错误: {error_code}")

            chunk_result = (result.get('translation') or [''])[0]
            if not chunk_result:
                raise Exception(f"有道翻译返回空结果: {result}")
            return chunk_result

        return self._translate_chunks(text, translate_chunk, max_length=3000)


def register(registry):
    """注册到全局provider注册表"""
    registry.register(YoudaoProvider)
