#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR 提供者架构测试
验证：注册表、自动发现、分发、降级链、单引擎配置隔离
不依赖网络与本地Tesseract（识别调用均被mock/fake替换）
"""

import sys
import os
# 项目根目录加入 sys.path（tests/ 的上一级）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_ocr_provider_registry():
    """测试OCR provider注册表与自动发现"""
    print("=== 测试OCR provider注册表 ===")
    from managers.ocr_providers import ocr_provider_registry

    names = ocr_provider_registry.names()
    print(f"已注册的OCR提供者: {names}")

    expected = {'tesseract', 'baidu_cloud', 'mock'}
    missing = expected - set(names)
    assert not missing, f"缺少内置OCR提供者: {missing}"

    assert ocr_provider_registry.display_name_of('tesseract') == 'Tesseract(本地)'
    assert ocr_provider_registry.display_name_of('unknown') == 'unknown'
    assert ocr_provider_registry.find_by_display_name('模拟OCR(降级)') == 'mock'

    # 重复注册被拒绝
    from managers.ocr_providers.mock import MockOCRProvider
    assert not ocr_provider_registry.register(MockOCRProvider), "重复注册应被拒绝"

    print("✅ OCR provider注册表测试通过\n")


def test_dispatch_and_fallback():
    """测试OCR分发与降级链（fake provider，无需真实引擎）"""
    print("=== 测试OCR分发与降级 ===")
    from managers.ocr import OCRManager, ocr_retry_handler
    from managers.ocr_providers import ocr_provider_registry
    from managers.ocr_providers.base import OCRProvider

    calls = {'fail': 0}

    class FakeFailOCR(OCRProvider):
        name = 'fake_ocr_fail'
        display_name = '故障OCR'

        def recognize(self, image):
            calls['fail'] += 1
            raise Exception("模拟OCR故障")

    class FakeOKOCR(OCRProvider):
        name = 'fake_ocr_ok'
        display_name = '备用OCR'

        def recognize(self, image):
            return "备用OCR识别文本"

    ocr_provider_registry.register(FakeFailOCR, override=True)
    ocr_provider_registry.register(FakeOKOCR, override=True)

    manager = OCRManager(config_manager=None)
    manager.set_ocr_type('fake_ocr_fail')
    manager.fallback_checked = False

    result = manager.perform_ocr(image=None)
    print(f"主OCR故障后的降级结果: {result}")
    # 降级链：fake_ocr_fail -> tesseract(本机未装则也失败) -> mock（mock总成功）
    assert result and not result.startswith('[OCR识别失败'), \
        f"最终应降级到mock成功: {result}"
    assert calls['fail'] == ocr_retry_handler.max_attempts, (
        f"主引擎应按 OCR 策略重试 {ocr_retry_handler.max_attempts} 次，"
        f"实际 {calls['fail']} 次")

    # 主引擎正常时不走降级
    manager.set_ocr_type('fake_ocr_ok')
    result2 = manager.perform_ocr(image=None)
    assert result2 == "备用OCR识别文本"

    # 未知引擎回退到tesseract->mock链
    manager.set_ocr_type('no_such_ocr')
    result3 = manager.perform_ocr(image=None)
    assert result3 and not result3.startswith('[OCR识别失败')

    ocr_provider_registry.unregister('fake_ocr_fail')
    ocr_provider_registry.unregister('fake_ocr_ok')

    print("✅ OCR分发与降级测试通过\n")


def test_primary_retry_policy():
    """
    测试文本链路的主引擎也走 OCR 自己的重试策略

    漏覆盖 _default_retry_handler 时主引擎会落到基类通用的 3 次/1s 策略：
    一次瞬时故障要先退避 1s+2s 才降级，还多打一次识别请求（云端引擎算配额），
    且与块级链路/降级链路的 2 次/0.5s 不一致。
    """
    print("=== 测试主引擎重试策略 ===")
    import time
    from managers.ocr import OCRManager, ocr_retry_handler
    from managers.ocr_providers import ocr_provider_registry
    from managers.ocr_providers.base import OCRProvider
    from managers.retry_handler import default_retry_handler

    calls = {'n': 0}

    class FakeRetryableOCR(OCRProvider):
        name = 'fake_ocr_retryable'
        display_name = '瞬时故障OCR'

        def recognize(self, image):
            calls['n'] += 1
            # 普通异常（非 NonRetryableError）才会走退避重试
            raise Exception("模拟瞬时故障")

    ocr_provider_registry.register(FakeRetryableOCR, override=True)
    manager = OCRManager(config_manager=None)

    # 默认重试器就是 OCR 重试器：块级、文本、降级三条链路共用同一套参数
    assert manager._default_retry_handler() is ocr_retry_handler, \
        "主引擎应使用 OCR 重试器而非基类通用重试器"

    started = time.monotonic()
    manager._recognize_via(FakeRetryableOCR, None)
    elapsed = time.monotonic() - started

    assert calls['n'] == ocr_retry_handler.max_attempts, (
        f"主引擎应重试 {ocr_retry_handler.max_attempts} 次，实际 {calls['n']} 次"
        f"（通用策略是 {default_retry_handler.max_attempts} 次）")
    # 通用策略退避 1s+2s≈3s；OCR 策略两次之间只等 0.5s±25% 抖动
    assert elapsed < 1.5, f"主引擎退避过久: {elapsed:.2f}s，应使用短间隔重试"

    ocr_provider_registry.unregister('fake_ocr_retryable')
    print("✅ 主引擎重试策略测试通过\n")


def test_per_engine_config_isolation():
    """测试 [OCR] 段共享 + 引擎前缀命名隔离的设计"""
    print("=== 测试引擎配置隔离 ===")
    from managers.ocr_providers.baidu_cloud import BaiduOCRProvider
    from managers.ocr_providers.tesseract import TesseractProvider

    class FakeConfig:
        def __init__(self):
            from configparser import ConfigParser
            self.config = ConfigParser()
            self.config.add_section('OCR')
            self.config.set('OCR', 'baidu_ocr_api_key', 'ak_test')
            self.config.set('OCR', 'tesseract_langs', 'eng')

    cfg = FakeConfig()
    baidu = BaiduOCRProvider(cfg)
    tess = TesseractProvider(cfg)

    # 各引擎只消费自己前缀的选项
    assert baidu._get_option('baidu_ocr_api_key') == 'ak_test'
    assert tess._get_option('tesseract_langs') == 'eng'

    # 未设置的选项返回fallback，不误读其他引擎的值
    assert baidu._get_option('baidu_ocr_secret_key') == ''
    assert baidu._get_option('nonexistent_option', fallback='def') == 'def'
    assert tess._get_option('nonexistent_option', fallback='def') == 'def'

    # config_fields 定义的选项名与 [OCR] 段选项名一致（写入能被读回）
    for field_name, _, _ in BaiduOCRProvider.config_fields:
        assert field_name.startswith('baidu_ocr_'), field_name
    for field_name, _, _ in TesseractProvider.config_fields:
        assert field_name.startswith('tesseract_'), field_name

    # 百度密钥缺失时应抛出明确异常
    empty_baidu = BaiduOCRProvider(None)
    try:
        empty_baidu._get_access_token()
        assert False, "密钥为空应抛异常"
    except Exception as e:
        assert '密钥未设置' in str(e)

    print("✅ 引擎配置隔离测试通过\n")


def test_preprocess_image():
    """测试图像预处理（无需Tesseract）"""
    print("=== 测试图像预处理 ===")
    from PIL import Image
    from managers.ocr_providers.tesseract import TesseractProvider

    # 常规图：放大2倍，四周留白（PAD*2），保持 'L' 模式
    img = Image.new('RGB', (100, 50), color=(200, 200, 200))
    processed = TesseractProvider._preprocess_image(img)
    assert processed.size == (200 + 24, 100 + 24), "常规图应放大2倍并加四周留白"
    assert processed.mode == 'L', "应转为灰度"

    # 小图：自适应放大到约64px高（倍数限制在2~4），不再固定2倍
    tiny = Image.new('RGB', (60, 20), color='white')
    tiny_out = TesseractProvider._preprocess_image(tiny)
    # 20px 高 -> ceil(64/20)=4倍 -> 80px，再加留白
    assert tiny_out.size == (60 * 4 + 24, 20 * 4 + 24), "小图应自适应放大4倍"

    # 30px 高：ceil(64/30)=3倍，验证中间档
    mid = Image.new('RGB', (90, 30), color='white')
    mid_out = TesseractProvider._preprocess_image(mid)
    assert mid_out.size == (90 * 3 + 24, 30 * 3 + 24), "30px高应放大3倍"

    # 非法输入回退原图
    weird = Image.new('RGBA', (10, 10))
    out = TesseractProvider._preprocess_image(weird)
    assert out is not None

    # 深底浅字（暗色主题）二值化后字迹仍在：暗背景像素应占多数（黑色）
    dark = Image.new('L', (80, 40), color=20)
    for x in range(20, 60):
        for y in range(15, 25):
            dark.putpixel((x, y), 235)
    dark_out = TesseractProvider._preprocess_image(dark.convert('RGB'))
    hist = dark_out.histogram()
    total = sum(hist)
    white_ratio = sum(hist[129:]) / total if total else 0.0
    assert 0.01 < white_ratio < 0.6, (
        f"深底浅字应保留亮色字迹（白像素占比 {white_ratio:.2f}），固定阈值会抹掉它们")

    print("✅ 图像预处理测试通过\n")


def test_block_coords_round_trip():
    """
    块级坐标必须精确回到原图坐标系

    识别图 = 原图放大 factor 倍 + 四周各 pad 像素留白，所以回映射要
    “先减 pad 再除以 factor”。只按 image.width/processed.width 缩放会漏算
    留白，块位置会随横向位置线性偏移（越靠左越偏右、越靠右越偏左，
    中心附近恰好抵消），就地覆盖的译文就会与原文错开。

    用假 pytesseract 把地面真值词框按“放大+留白”变换成留白图上的坐标，
    再要求 recognize_blocks 原样返还——不需安装引擎，也不靠目测。
    """
    print("=== 测试块级坐标回映射 ===")
    from PIL import Image
    import managers.ocr_providers.tesseract as tess_mod
    from managers.ocr_providers.tesseract import TesseractProvider, _PAD

    width, height = 1000, 120
    factor = TesseractProvider._scale_factor(height)
    assert factor == 2
    image = Image.new('RGB', (width, height), 'white')

    # 地面真值：词框在原图坐标系下的位置（左/中/右三段，放大后可分辨）
    truth = [(40, 20, 120, 20), (500, 24, 90, 18), (900, 22, 80, 22)]

    class FakeOutput:
        """pytesseract.Output 的占位（provider 只把它透传给 image_to_data）"""
        DICT = 'dict'

    class FakePytesseract:
        """把地面真值变换成留白图上的词框，模拟 Tesseract 的输出"""

        Output = FakeOutput

        @staticmethod
        def image_to_data(processed, config=None, output_type=None, timeout=None):
            assert processed.size == (width * factor + 2 * _PAD,
                                      height * factor + 2 * _PAD), \
                f"预处理尺寸异常: {processed.size}"
            data = {k: [] for k in (
                'text', 'conf', 'block_num', 'par_num', 'line_num',
                'word_num', 'left', 'top', 'width', 'height')}
            for i, (left, top, w, h) in enumerate(truth):
                data['text'].append(f'word{i}')
                data['conf'].append('95')      # 引擎返回的是字符串
                data['block_num'].append(1)
                data['par_num'].append(1)
                data['line_num'].append(i + 1)
                data['word_num'].append(1)
                data['left'].append(left * factor + _PAD)
                data['top'].append(top * factor + _PAD)
                data['width'].append(w * factor)
                data['height'].append(h * factor)
            return data

    # 跳过 __init__（它要探测引擎路径、跑子进程并扫盘找 tessdata）
    provider = TesseractProvider.__new__(TesseractProvider)
    provider.config_manager = None
    # 引擎探测也是 __init__ 做的：识别入口会先校验 _engine_ok，测试里直接声明可用
    provider._engine_ok = True

    saved = (tess_mod.pytesseract, tess_mod.pytesseract_available)
    tess_mod.pytesseract = FakePytesseract
    tess_mod.pytesseract_available = True
    try:
        blocks = provider.recognize_blocks(image)
    finally:
        tess_mod.pytesseract, tess_mod.pytesseract_available = saved

    assert len(blocks) == len(truth), f"块数不符: {len(blocks)} != {len(truth)}"
    for block, (left, top, w, h) in zip(blocks, truth):
        # 缩放系数是 1/factor，int() 取整最多差 1px
        assert abs(block.left - left) <= 1, \
            f"块横向位置错位: 应 {left}，实际 {block.left}（漏算留白的线性偏移）"
        assert abs(block.top - top) <= 1, \
            f"块纵向位置错位: 应 {top}，实际 {block.top}"
        assert abs(block.width - w) <= 1, f"块宽错位: {block.width} != {w}"

    print("✅ 块级坐标回映射测试通过\n")


def test_scale_factor():
    """放大倍数随选区高度自适应：≥40px 用 2 倍，越小倍数越大，封顶 4 倍"""
    from managers.ocr_providers.tesseract import TesseractProvider

    assert TesseractProvider._scale_factor(400) == 2
    assert TesseractProvider._scale_factor(40) == 2
    assert TesseractProvider._scale_factor(32) == 2   # ceil(64/32)=2
    assert TesseractProvider._scale_factor(30) == 3   # ceil(64/30)=3
    assert TesseractProvider._scale_factor(20) == 4   # ceil(64/20)=4
    assert TesseractProvider._scale_factor(10) == 4   # 封顶
    assert TesseractProvider._scale_factor(1) == 4
    print("✅ 放大倍数自适应测试通过\n")


def test_otsu_threshold():
    """Otsu 阈值应落在两峰之间，深浅背景都能正确分离"""
    from PIL import Image
    from managers.ocr_providers.tesseract import TesseractProvider

    # 浅底黑字：阈值应在背景(240)与前景(30)之间
    light = Image.new('L', (100, 40), 240)
    for x in range(30, 70):
        for y in range(15, 25):
            light.putpixel((x, y), 30)
    t_light = TesseractProvider._otsu_threshold(light)
    assert 30 < t_light < 240, f"浅底阈值异常: {t_light}"

    # 深底浅字：阈值同样应落在中间（固定128会把暗背景全判成字/背景）
    dark = Image.new('L', (100, 40), 35)
    for x in range(30, 70):
        for y in range(15, 25):
            dark.putpixel((x, y), 220)
    t_dark = TesseractProvider._otsu_threshold(dark)
    assert 35 < t_dark < 220, f"深底阈值异常: {t_dark}"
    print("✅ Otsu阈值测试通过\n")


def test_text_cleaning():
    """测试识别文本清理"""
    print("=== 测试文本清理 ===")
    from managers.ocr_providers.base import OCRProvider

    dirty = "  hello  \n\n\n  world \n \n foo  bar "
    cleaned = OCRProvider.clean_text(dirty)
    assert cleaned == "hello\nworld\nfoo  bar", f"清理结果不符: {cleaned!r}"
    assert OCRProvider.clean_text("") == ""
    assert OCRProvider.clean_text(None) == ""

    print("✅ 文本清理测试通过\n")


class FakeOCRConfig:
    """带百度OCR密钥的假配置管理器（provider 只读它的 .config）"""

    def __init__(self, api_key='ak_test', secret_key='sk_test'):
        from configparser import ConfigParser
        self.config = ConfigParser()
        self.config.add_section('OCR')
        self.config.set('OCR', 'baidu_ocr_api_key', api_key)
        self.config.set('OCR', 'baidu_ocr_secret_key', secret_key)


class FakeResponse:
    """requests.Response 的最小替身"""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_reload_config_closes_stale_providers():
    """
    重载配置要关掉被淘汰的 provider

    云端 provider 持有 requests.Session（连接池）：只从缓存字典里摘掉就得等
    GC 才回收套接字，用户每点一次"保存"就漏一份连接。
    """
    print("=== 测试重载配置释放旧provider ===")
    from managers.ocr import OCRManager
    from managers.ocr_providers import ocr_provider_registry
    from managers.ocr_providers.base import OCRProvider

    closed = []

    class ClosableOCR(OCRProvider):
        name = 'fake_ocr_closable'
        display_name = '可关闭OCR'

        def recognize(self, image):
            return "可用"

        def close(self):
            closed.append(self)

    ocr_provider_registry.register(ClosableOCR, override=True)
    try:
        manager = OCRManager(config_manager=None)
        manager.set_ocr_type('fake_ocr_closable')
        provider = manager._get_provider(ClosableOCR)
        assert manager._get_provider(ClosableOCR) is provider, "同一类应复用实例"

        manager.reload_config()
        assert closed == [provider], f"重载配置应关闭被淘汰的实例: {closed}"
        assert manager._providers == {}, "旧实例应已从缓存摘除"

        # 没有 close() 的引擎（本地/模拟）不能因为缺方法而炸掉整次保存
        manager.reload_config()
    finally:
        ocr_provider_registry.unregister('fake_ocr_closable')

    print("✅ 重载配置释放旧provider测试通过\n")


def test_baidu_encode_image_per_axis_restore():
    """
    细长条选区压缩后，坐标要按横竖各自回乘

    短边低于接口下限（例如 10x1000 的行距截图）时，max(_MIN_EDGE, ...) 会把
    短边顶回去甚至撑大，两条轴的实际缩放比于是各不相同。原来只按宽算一个
    系数并同时用于 x/y，行高与 top 会整体按错误比例缩放，就地覆盖的框子贴歪。
    """
    print("=== 测试百度OCR压缩后各轴回乘 ===")
    from PIL import Image
    from managers.ocr_providers.baidu_cloud import BaiduOCRProvider, _MIN_EDGE

    provider = BaiduOCRProvider(None)
    b64, (restore_x, restore_y) = provider._encode_image(
        Image.new('RGB', (10, 1000), 'white'))

    assert b64, "应返回可送出的图像数据"
    # 0.6 倍缩放后短边被顶到下限 15，长边正常缩到 600
    assert abs(restore_x - 10 / _MIN_EDGE) < 1e-6, f"x 回乘系数错: {restore_x}"
    assert abs(restore_y - 1000 / 600) < 1e-6, f"y 回乘系数错: {restore_y}"
    assert abs(restore_x - restore_y) > 0.5, "细长条的两轴系数本就不该相同"

    # 常规大图：等比缩放，两轴系数一致
    _, (rx2, ry2) = provider._encode_image(Image.new('RGB', (5000, 400), 'white'))
    assert abs(rx2 - ry2) < 1e-6, f"等比压缩不该分出两轴: {rx2}, {ry2}"

    print("✅ 百度OCR各轴回乘测试通过\n")


def test_baidu_blocks_restore_uses_per_axis_factors():
    """
    行块坐标按各自轴回乘

    接口给的 left/top/width/height 是压缩图上的像素，x 系数与 y 系数在细长条
    选区上不一样，统一乘一个数会让行高/行距整体失真。
    """
    print("=== 测试百度OCR行块按轴回乘 ===")
    from PIL import Image
    from managers.ocr_providers.baidu_cloud import BaiduOCRProvider

    provider = BaiduOCRProvider(None)
    provider._post_image = lambda url, image: (
        {'words_result': [{'words': 'hello',
                           'location': {'left': 30, 'top': 60,
                                        'width': 90, 'height': 20}}]},
        (2.0, 3.0))

    blocks = provider.recognize_blocks(Image.new('RGB', (60, 180), 'white'))
    assert len(blocks) == 1, f"块数不符: {len(blocks)}"
    block = blocks[0]
    assert (block.left, block.top, block.width, block.height) == \
        (60.0, 180.0, 180.0, 60.0), \
        f"坐标未按各轴回乘: {block.left}, {block.top}, {block.width}, {block.height}"

    print("✅ 百度OCR行块按轴回乘测试通过\n")


def test_baidu_oversize_image_is_data_error():
    """
    压不进接口限制的图片是数据问题，不是配置问题

    报成"密钥未设置"那一类会把用户引去检查配置，而真正要做的是缩小选区；
    两类都必须是"重试无用"，否则同一张图会把整条降级链的时限烧光。
    """
    print("=== 测试超限图片抛数据类错误 ===")
    from PIL import Image
    import managers.ocr_providers.baidu_cloud as baidu_mod
    from managers.ocr_providers.baidu_cloud import BaiduOCRProvider
    from managers.provider_common import (
        NonRetryableError, ProviderConfigError, ProviderDataError)

    provider = BaiduOCRProvider(None)
    saved = baidu_mod._MAX_BYTES
    baidu_mod._MAX_BYTES = 1        # 任何图片都算超字节上限
    try:
        try:
            provider._encode_image(Image.new('RGB', (20, 20), 'white'))
            raise AssertionError('超限应抛数据类错误')
        except ProviderDataError as e:
            assert isinstance(e, NonRetryableError), "超限不该重试"
            assert not isinstance(e, ProviderConfigError), \
                "不该把图片超限报成配置问题"
            assert '缩小选区' in str(e)
    finally:
        baidu_mod._MAX_BYTES = saved

    print("✅ 超限图片数据类错误测试通过\n")


def test_baidu_token_refresh_clears_under_lock():
    """
    令牌缓存的清理要和取令牌共用同一把锁，连过期时间一起清

    不设防的 self._access_token = None 可能刚好抹掉并发线程刚取回的新令牌，
    于是同一份密钥被反复打令牌接口（该接口自身也限频）。
    """
    print("=== 测试令牌清理持锁 ===")
    import threading
    from PIL import Image
    from managers.ocr_providers.baidu_cloud import BaiduOCRProvider

    class CountingLock:
        """记录进入次数的锁替身"""

        def __init__(self):
            self._real = threading.Lock()
            self.entered = 0

        def __enter__(self):
            self._real.acquire()
            self.entered += 1
            return self

        def __exit__(self, *exc):
            self._real.release()
            return False

    class FakeSession:
        def __init__(self):
            self.calls = []
            self._tokens = iter(['tok-1', 'tok-2'])

        def post(self, url, data=None, headers=None, timeout=None):
            data = dict(data or {})
            self.calls.append((url, data))
            if url == BaiduOCRProvider.TOKEN_URL:
                return FakeResponse({'access_token': next(self._tokens),
                                     'expires_in': 3600})
            if len([c for c in self.calls if c[0] != BaiduOCRProvider.TOKEN_URL]) == 1:
                # 第一次识别请求：令牌已失效
                return FakeResponse({'error_code': 216003,
                                     'error_msg': 'Invalid access_token'})
            return FakeResponse({'words_result': [{'words': 'hello'}]})

    provider = BaiduOCRProvider(FakeOCRConfig())
    session = FakeSession()
    provider.session = session
    lock = CountingLock()
    provider._token_lock = lock

    result, _restore = provider._post_image(provider.GENERAL_URL,
                                            Image.new('RGB', (60, 20), 'white'))
    assert result.get('words_result'), f"应最终识别成功: {result}"

    ocr_calls = [c for c in session.calls if c[0] != BaiduOCRProvider.TOKEN_URL]
    assert len(ocr_calls) == 2, f"识别请求次数不符: {len(ocr_calls)}"
    assert ocr_calls[0][1]['access_token'] == 'tok-1'
    assert ocr_calls[1][1]['access_token'] == 'tok-2', "换令牌后没带上新令牌"
    # 两次取令牌 + 一次清缓存 = 3 次进锁；漏掉锁时只有 2
    assert lock.entered == 3, f"清理令牌缓存应持锁进行: {lock.entered}"

    print("✅ 令牌清理持锁测试通过\n")


def test_baidu_error_code_sets_are_strings():
    """
    业务码集合必须按字符串写

    分类处比的是 `str(result['error_code'])`，而集合里放的是 int：两个分支
    永远匹配不上，"换令牌再来一次"从来没发生过，服务侧偶发抖动（百度明确
    要求重试的那几个码）也被当成确定性失败直接降级。
    """
    print("=== 测试百度OCR业务码类型 ===")
    import managers.ocr_providers.baidu_cloud as baidu_mod

    for code_set in (baidu_mod._TOKEN_ERROR_CODES, baidu_mod._TRANSIENT_ERROR_CODES):
        assert all(isinstance(code, str) for code in code_set), \
            f"业务码集合应按字符串存: {code_set}"
    # 接口返回的是 JSON 数字，落到 str() 之后必须还能命中
    assert str(216003) in baidu_mod._TOKEN_ERROR_CODES
    assert str(18) in baidu_mod._TRANSIENT_ERROR_CODES
    print("✅ 百度OCR业务码类型测试通过\n")


def test_baidu_provider_does_not_leak_second_session():
    """
    构造 provider 不能顶掉基类那份连接会话

    基类建 Session 并记账（_owns_session），子类再造一份直接覆盖 self.session：
    前一份立刻无人认领，而 close() 只关得到后一份——每次重载配置漏一个连接池。
    """
    print("=== 测试百度OCR连接会话归属 ===")
    import requests
    from managers.ocr_providers.baidu_cloud import BaiduOCRProvider

    created = []
    real_session = requests.Session

    class CountingSession(real_session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    requests.Session = CountingSession
    try:
        provider = BaiduOCRProvider(FakeOCRConfig())
    finally:
        requests.Session = real_session

    assert len(created) == 1, f"应只建一份连接会话: {len(created)}"
    assert provider.session is created[0]
    assert getattr(provider, '_owns_session', False) is True, \
        "自建的会话必须标记为自有，否则 close() 关不到"

    closed = []
    created[0].close = lambda: closed.append(1)
    provider.close()
    assert closed == [1], "close() 必须释放那份自有会话"

    print("✅ 百度OCR连接会话归属测试通过\n")


def main():
    print("开始OCR架构测试...\n")

    try:
        test_ocr_provider_registry()
        test_dispatch_and_fallback()
        test_primary_retry_policy()
        test_per_engine_config_isolation()
        test_reload_config_closes_stale_providers()
        test_preprocess_image()
        test_block_coords_round_trip()
        test_scale_factor()
        test_otsu_threshold()
        test_baidu_encode_image_per_axis_restore()
        test_baidu_blocks_restore_uses_per_axis_factors()
        test_baidu_oversize_image_is_data_error()
        test_baidu_token_refresh_clears_under_lock()
        test_baidu_error_code_sets_are_strings()
        test_baidu_provider_does_not_leak_second_session()
        test_text_cleaning()

        print("🎉 所有OCR架构测试通过！")
        return True
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
