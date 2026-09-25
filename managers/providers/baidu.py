#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度翻译提供者（需要 app_id + api_key）
"""

import time

from ..provider_common import make_md5_sign
from .base import TranslationProvider


class BaiduProvider(TranslationProvider):
    """百度翻译"""

    name = 'baidu'
    display_name = '百度翻译'
    config_fields = (
        ('baidu_app_id', '应用ID', False),
        ('baidu_api_key', 'API密钥', True),
    )

    # 百度目标语言代码映射
    _target_map = {'zh-TW': 'cht'}

    def translate(self, text, source='auto', target='zh'):
        """
        使用百度翻译

        Args:
            text (str): 要翻译的文本
            source (str): 源语言代码，'auto' 表示自动检测
            target (str): 目标语言代码

        Returns:
            str: 翻译后的文本
        """
        if self.config_manager:
            app_id, api_key = self.config_manager.get_baidu_api_info()
        else:
            app_id, api_key = '', ''
        self._require_key(app_id, "百度翻译应用ID未设置")
        self._require_key(api_key, "百度翻译API密钥未设置")

        url = "https://api.fanyi.baidu.com/api/trans/vip/translate"
        target = self._target_map.get(target, target)

        def translate_chunk(chunk):
            """翻译单个文本块"""
            salt = str(int(time.time()))
            params = {
                'q': chunk,
                'from': source or 'auto',
                'to': target,
                'appid': app_id,
                'salt': salt,
                'sign': make_md5_sign(app_id, chunk, salt, api_key),
            }
            # 重试由上层统一处理，这里只发起单次请求
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()

            result = response.json()
            # 百度对业务错误同样返回HTTP 200，需显式校验error_code
            if result.get('error_code'):
                raise Exception(
                    f"百度翻译返回错误: {result.get('error_code')} {result.get('error_msg', '')}")

            chunk_result = ''.join(
                item.get('dst', '') for item in result.get('trans_result', []))
            if not chunk_result:
                raise Exception(f"百度翻译返回空结果: {result}")
            return chunk_result

        return self._translate_chunks(text, translate_chunk, max_length=3000)


def register(registry):
    """注册到全局provider注册表"""
    registry.register(BaiduProvider)
