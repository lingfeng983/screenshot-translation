#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云 OCR 提供者（在线识别接口，可扩展）

当前支持：百度云 OCR（通用文字识别标准版）
- 每月有免费额度（标准版约1000次/月），需注册获取 API Key
- 对中文识别效果显著优于 Tesseract，适合截图翻译场景
- 扩展其他云服务（腾讯/阿里/OCR.space）时参照本模块实现 recognize 即可
"""

import base64
import json
import logging
import time

import requests

from .base import OCRProvider

logger = logging.getLogger('screenshot-translation')


class BaiduOCRProvider(OCRProvider):
    """百度云 OCR（在线，需免费注册的API Key，中文效果佳）"""

    name = 'baidu_cloud'
    display_name = '百度云OCR(在线)'
    config_fields = (
        ('baidu_ocr_api_key', 'API Key', True),
        ('baidu_ocr_secret_key', 'Secret Key', True),
    )

    TOKEN_URL = 'https://aip.baidubce.com/oauth/2.0/token'
    OCR_URL = 'https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic'

    def __init__(self, config_manager=None):
        """
        初始化，复用连接会话与access_token缓存

        Args:
            config_manager: 配置管理器实例
        """
        super().__init__(config_manager)
        self.session = requests.Session()
        self.timeout = 30
        # access_token 缓存：避免每次识别都请求一次令牌
        self._access_token = None
        self._token_expire_at = 0

    def _get_access_token(self):
        """
        获取（带缓存的）access_token

        Returns:
            str: 有效的access_token

        Raises:
            Exception: 密钥未设置或令牌获取失败
        """
        api_key = self._get_option('baidu_ocr_api_key')
        secret_key = self._get_option('baidu_ocr_secret_key')
        if not api_key or not secret_key:
            raise Exception("百度云OCR密钥未设置（需在配置中填入API Key与Secret Key）")

        # 令牌有效期一般为30天，提前10分钟刷新
        if self._access_token and time.time() < self._token_expire_at:
            return self._access_token

        try:
            response = self.session.post(
                self.TOKEN_URL,
                params={
                    'grant_type': 'client_credentials',
                    'client_id': api_key,
                    'client_secret': secret_key,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            token = data.get('access_token')
            if not token:
                raise Exception(f"获取access_token失败: {str(data)[:200]}")
            self._access_token = token
            self._token_expire_at = time.time() + float(data.get('expires_in', 2592000)) - 600
            return token
        except requests.RequestException as e:
            raise Exception(f"获取access_token网络错误: {e}") from e

    def recognize(self, image):
        """
        对图像执行在线OCR识别

        Args:
            image (PIL.Image.Image): 待识别的图像

        Returns:
            str: 按行拼接的识别文本

        Raises:
            Exception: 密钥未设置或识别失败
        """
        token = self._get_access_token()

        # 图像编码为base64（PNG无损，避免JPEG压缩损失小字清晰度）
        from io import BytesIO
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        image_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

        try:
            response = self.session.post(
                self.OCR_URL,
                params={'access_token': token},
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                data=f'image={image_b64}',
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = response.json()

            # 百度对业务错误返回HTTP 200 + error_code
            if 'error_code' in result:
                raise Exception(
                    f"百度云OCR错误: {result.get('error_code')} {result.get('error_msg', '')}")

            words = result.get('words_result') or []
            text = '\n'.join(w.get('words', '') for w in words)
            logger.info(f"百度云OCR识别完成，{len(words)} 行文本")
            if not text.strip():
                raise Exception("百度云OCR未识别到任何文本")
            return self.clean_text(text)

        except (requests.RequestException, json.JSONDecodeError) as e:
            raise Exception(f"百度云OCR请求失败: {e}") from e


def register(registry):
    """注册到全局OCR provider注册表"""
    registry.register(BaiduOCRProvider)
