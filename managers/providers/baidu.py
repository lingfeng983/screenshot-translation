#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度翻译提供者（需要 app_id + api_key）
"""

import time

import requests

from ..provider_common import (
    ProviderUnavailableError, describe_error, make_md5_sign,
    raise_business_error, raise_for_status, wrap_request_error)
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
    # 只有这几个码是瞬时的（并发超限、服务升级/不可用），重试有意义；
    # 其余（签名错、appid 无效、额度用尽、语种不支持）重试必然重犯
    _transient_codes = ('54005', '54008', '58000', '58001', '58002')

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
            # 凭证走表单体而不是 URL query：签名与 appid 一旦进了 query,
            # 就会跟着 requests 的异常文本落到本地日志和各级代理日志里
            try:
                response = self.session.post(
                    url, data=params, timeout=self._http_timeout())
            except requests.RequestException as e:
                raise wrap_request_error(e, '百度翻译') from e

            raise_for_status(response, '百度翻译')
            result = self._parse_json(response)
            # 百度对业务错误同样返回HTTP 200，需显式校验error_code
            if result.get('error_code'):
                raise_business_error('百度翻译', result.get('error_code'),
                                     result.get('error_msg', ''),
                                     self._transient_codes)

            chunk_result = ''.join(
                item.get('dst', '') for item in result.get('trans_result', []))
            if not chunk_result:
                raise ProviderUnavailableError(f"百度翻译返回空结果: {str(result)[:200]}")
            return chunk_result

        return self._translate_chunks(text, translate_chunk, max_length=3000)

    @staticmethod
    def _parse_json(response):
        """解析响应JSON：非JSON（如网关返回HTML）按可重试处理"""
        try:
            return response.json()
        except ValueError as e:
            raise ProviderUnavailableError(
                f"百度翻译响应不是合法JSON: {describe_error(response.text, limit=120)}"
            ) from e


def register(registry):
    """注册到全局provider注册表"""
    registry.register(BaiduProvider)
