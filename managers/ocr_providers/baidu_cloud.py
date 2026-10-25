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
import io
import json
import logging
import threading
import time

import requests

from .base import OCRProvider
from ..provider_common import ProviderConfigError, ProviderDataError
from ..text_block import TextBlock

logger = logging.getLogger('screenshot-translation')

# 令牌类错误：清掉缓存重新取一次令牌即可恢复
# 必须按字符串写：分类处用的是 str(error_code)，int 集合永远匹配不上，
# 于是"换令牌再来一次"从来没发生过，偶发抖动也被当成确定性失败
_TOKEN_ERROR_CODES = {'216002', '216003', '216004', '216005', '216006'}
# 服务侧偶发（繁忙/内部错误）：交给重试层退避后再来
_TRANSIENT_ERROR_CODES = {'17', '18', '216300', '216301', '216302', '216303'}
# 百度接口对图片的硬限制：超了重试也不会变，只能改图或不重试
_MAX_EDGE = 4096
_MIN_EDGE = 15
_MAX_BYTES = 4 * 1024 * 1024


def _safe_error_text(value):
    """
    把接口错误体压成一行可日志化的文本（不含请求URL）

    Args:
        value: 响应 JSON 或异常对象

    Returns:
        str: 截断后的描述
    """
    return str(value).replace('\n', ' ')[:200]


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
    # general 接口比 general_basic 多返回每行 location 坐标，就地覆盖原文时用它
    GENERAL_URL = 'https://aip.baidubce.com/rest/2.0/ocr/v1/general'

    def __init__(self, config_manager=None):
        """
        初始化，复用连接会话与access_token缓存

        Args:
            config_manager: 配置管理器实例
        """
        # 连接会话由基类建并记账（_owns_session）：这里再造一个会把基类那份
        # 直接丢掉，而 close() 只关得到眼前这份——每次重载配置都漏一个 Session
        super().__init__(config_manager)
        # access_token 缓存：避免每次识别都请求一次令牌
        self._access_token = None
        self._token_expire_at = 0
        # 并发首识别时只让一个线程去取令牌，否则同一份密钥会打两次令牌接口
        self._token_lock = threading.Lock()
        # 请求串行化：provider 实例按类缓存在 OCR 管理器里、被多个线程共用，
        # 而 requests.Session 不是文档保证线程安全的。识别本身是串行的
        # （一次截图一个请求），这把锁只在两次截图并发时生效，代价可忽略。
        self._request_lock = threading.Lock()

    def _get_access_token(self):
        """
        获取（带缓存的）access_token

        凭证走 form body 而不是 URL query：异常文本里带着完整 URL，
        一次网络抖动就能把 client_secret 写进本地日志与代理访问日志。

        Returns:
            str: 有效的access_token

        Raises:
            ProviderConfigError: 密钥未设置
            Exception: 令牌获取失败（可重试）
        """
        api_key = self._require_option(
            'baidu_ocr_api_key', "百度云OCR密钥未设置（需在配置中填入API Key与Secret Key）")
        secret_key = self._require_option(
            'baidu_ocr_secret_key', "百度云OCR密钥未设置（需在配置中填入API Key与Secret Key）")

        # 令牌有效期一般为30天，提前10分钟刷新
        with self._token_lock:
            if self._access_token and time.time() < self._token_expire_at:
                return self._access_token

            try:
                response = self.session.post(
                    self.TOKEN_URL,
                    data={
                        'grant_type': 'client_credentials',
                        'client_id': api_key,
                        'client_secret': secret_key,
                    },
                    # 用 _http_timeout 而不是裸 timeout：整条识别链只有一个
                    # deadline，预算快用完时不该再按 30 秒去等令牌
                    timeout=self._http_timeout(),
                )
                response.raise_for_status()
                data = response.json()
                token = data.get('access_token')
                if not token:
                    # error_description 里可能重复带上密钥，只留错误码/描述前 200 字
                    raise ProviderConfigError(
                        f"获取access_token失败: {_safe_error_text(data)}")
                self._access_token = token
                self._token_expire_at = (time.time()
                                         + float(data.get('expires_in', 2592000)) - 600)
                return token
            except ProviderConfigError:
                raise
            except (requests.RequestException, json.JSONDecodeError) as e:
                raise Exception(f"获取access_token网络错误: {type(e).__name__}") from e

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
        result, _restore = self._post_image(self.OCR_URL, image)
        words = result.get('words_result') or []
        text = '\n'.join(w.get('words', '') for w in words)
        logger.info(f"百度云OCR识别完成，{len(words)} 行文本")
        if not text.strip():
            raise Exception("百度云OCR未识别到任何文本")
        return self.clean_text(text)

    def recognize_blocks(self, image):
        """
        行级块识别：走带坐标的 general 接口

        Args:
            image (PIL.Image.Image): 待识别的图像

        Returns:
            list[TextBlock]: 按接口返回顺序（阅读顺序）的行块

        Raises:
            Exception: 密钥未设置或未识别到文本
        """
        result, (restore_x, restore_y) = self._post_image(self.GENERAL_URL, image)
        items = result.get('words_result') or []
        blocks = []
        for item in items:
            text = (item.get('words') or '').strip()
            loc = item.get('location') or {}
            if not text or not loc:
                continue
            # restore_x/y：图被压过就送出去了，坐标要按各自轴乘回原图尺度
            # （细长条选区被顶到最小边时两轴缩放比不一样）
            blocks.append(TextBlock(
                text, loc.get('left', 0) * restore_x, loc.get('top', 0) * restore_y,
                loc.get('width', 0) * restore_x, loc.get('height', 0) * restore_y))
        logger.info(f"百度云OCR块级识别完成，{len(blocks)} 行")
        if not blocks:
            raise Exception("百度云OCR未识别到任何文本")
        return blocks

    def _encode_image(self, image):
        """
        PNG 编码并压进百度接口的图片硬限制

        超限的选区送上去只会得到一句业务错误（以前还被当成可重试错误再打
        两次），所以在本地先缩到能过。

        Args:
            image (PIL.Image.Image): 待送图

        Returns:
            tuple: (base64 字符串, (x 回乘系数, y 回乘系数))。接口给的坐标是
                按压缩后的图算的，调用方必须各自乘回原图尺度，否则就地覆盖
                会整体错位

        Raises:
            ProviderDataError: 压满 5 轮仍超字节上限（换引擎可能能吃下）
        """
        restore_x = restore_y = 1.0
        for _ in range(5):
            width, height = image.size
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            raw = buffer.getvalue()
            if (len(raw) <= _MAX_BYTES and max(image.size) <= _MAX_EDGE
                    and min(image.size) >= _MIN_EDGE):
                return base64.b64encode(raw).decode('utf-8'), (restore_x, restore_y)

            scale = 0.7 if max(image.size) > _MAX_EDGE else 0.6
            # 短边本来就低于下限（细长条选区，例如 10x1000 的行距截图）时，
            # max() 会把它顶回去甚至撑大，两条轴的实际缩放比于是各不相同：
            # 按同一个系数回乘会把行宽算错，覆盖框贴歪
            new_size = (max(_MIN_EDGE, round(width * scale)),
                        max(_MIN_EDGE, round(height * scale)))
            logger.info(f"图片超出百度OCR限制（{len(raw)} 字节, {image.size}），"
                        f"缩到 {new_size}")
            image = image.resize(new_size)
            restore_x *= width / max(1, new_size[0])
            restore_y *= height / max(1, new_size[1])
        raise ProviderDataError("选区过大，压缩后仍不满足百度OCR的图片限制，请缩小选区")

    def _post_image(self, url, image):
        """
        把图像送进指定百度OCR接口并返回解析后的响应

        Args:
            url (str): 接口地址（general / general_basic）
            image (PIL.Image.Image): 待识别的图像

        Returns:
            tuple: (响应 dict, (x 回乘系数, y 回乘系数))

        Raises:
            ProviderConfigError: 密钥类确定性错误（重试无意义）
            ProviderDataError: 图片压不进接口限制
            Exception: 网络或服务侧偶发错误（交给重试层）
        """
        image_b64, restore = self._encode_image(image)

        for attempt in (0, 1):
            if attempt:
                # 令牌类错误：清缓存重新取一次再打。置空必须和取令牌用同一把锁，
                # 否则并发的另一次识别可能刚好把新令牌写进来又被这里抹掉
                with self._token_lock:
                    self._access_token = None
                    self._token_expire_at = 0
            token = self._get_access_token()
            try:
                # 串行化同一 provider 实例上的 HTTP 调用（Session 非线程安全）
                with self._request_lock:
                    response = self.session.post(
                        url,
                        # access_token 放 body：放 query 时异常文本里的完整 URL
                        # 会把令牌带进本地日志与代理日志
                        data={'image': image_b64, 'access_token': token},
                        headers={'Content-Type': 'application/x-www-form-urlencoded'},
                        timeout=self._http_timeout(),
                    )
                response.raise_for_status()
                result = response.json()
            except (requests.RequestException, json.JSONDecodeError) as e:
                # 只留异常类型：URL 与响应体可能含令牌或整页 HTML
                raise Exception(f"百度云OCR请求失败: {type(e).__name__}") from e

            if 'error_code' not in result:
                return result, restore

            code = str(result.get('error_code'))
            message = (f"百度云OCR错误 {code}: "
                       f"{_safe_error_text(result.get('error_msg', ''))}")
            if code in _TOKEN_ERROR_CODES and not attempt:
                continue
            if code in _TRANSIENT_ERROR_CODES:
                raise Exception(message)
            # 认证、额度、参数、图片规格类：同一张图再打一次也是同样结果
            raise ProviderConfigError(message)

        raise ProviderConfigError("百度云OCR令牌刷新后仍失败")


def register(registry):
    """注册到全局OCR provider注册表"""
    registry.register(BaiduOCRProvider)
