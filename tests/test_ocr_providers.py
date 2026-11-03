#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR 提供者架构测试
验证：注册表、自动发现、分发、降级链、单引擎配置隔离
不依赖网络与真实模型（识别调用均被mock/fake替换）
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

    expected = {'rapidocr', 'mock'}
    missing = expected - set(names)
    assert not missing, f"缺少内置OCR提供者: {missing}"

    assert ocr_provider_registry.display_name_of('rapidocr') == 'RapidOCR(本地)'
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
    try:
        manager.set_ocr_type('fake_ocr_fail')
        manager.fallback_checked = False

        result = manager.perform_ocr(image=None)
        print(f"主OCR故障后的降级结果: {result}")
        # 降级链：fake_ocr_fail -> rapidocr(未装模型则也失败) -> mock（mock总成功）
        assert result and not result.startswith('[OCR识别失败'), \
            f"最终应降级到mock成功: {result}"
        assert calls['fail'] == ocr_retry_handler.max_attempts, (
            f"主引擎应按 OCR 策略重试 {ocr_retry_handler.max_attempts} 次，"
            f"实际 {calls['fail']} 次")

        # 主引擎正常时不走降级
        manager.set_ocr_type('fake_ocr_ok')
        result2 = manager.perform_ocr(image=None)
        assert result2 == "备用OCR识别文本"

        # 未知引擎回退到rapidocr->mock链
        manager.set_ocr_type('no_such_ocr')
        result3 = manager.perform_ocr(image=None)
        assert result3 and not result3.startswith('[OCR识别失败')
    finally:
        # 注册表是进程级单例：断言一失败就把 fake 留在上面，之后任何用例的降级链
        # 都可能命中"备用OCR识别文本"，报出来的原因和真正的缺陷对不上
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
    try:
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
    finally:
        ocr_provider_registry.unregister('fake_ocr_retryable')
    print("✅ 主引擎重试策略测试通过\n")


def test_per_engine_config_isolation():
    """测试 [OCR] 段按引擎前缀命名隔离的设计（当前只剩本地引擎）"""
    print("=== 测试引擎配置隔离 ===")
    from managers.ocr_providers.rapidocr import RapidOCRProvider

    class FakeConfig:
        def __init__(self):
            from configparser import ConfigParser
            self.config = ConfigParser()
            self.config.add_section('OCR')
            self.config.set('OCR', 'rapidocr_score_threshold', '0.7')
            self.config.set('OCR', 'legacy_option', 'ignored')

    rapid = RapidOCRProvider(FakeConfig())

    # 引擎只消费自己前缀的选项
    assert rapid._get_option('rapidocr_score_threshold') == '0.7'
    # 未设置的选项返回fallback，不误读其他前缀的值
    assert rapid._get_option('nonexistent_option', fallback='def') == 'def'

    # config_fields 定义的选项名与 [OCR] 段选项名一致（写入能被读回）
    for field_name, _, _ in RapidOCRProvider.config_fields:
        assert field_name.startswith('rapidocr_'), field_name

    print("✅ 引擎配置隔离测试通过\n")


class _FakeOutput:
    """rapidocr 3.x 输出对象的最小替身（boxes/txts/scores）"""

    def __init__(self, boxes, txts, scores):
        self.boxes = boxes
        self.txts = txts
        self.scores = scores


class _FakeEngine:
    """记录调用入参的假 RapidOCR 引擎"""

    def __init__(self, output):
        self.output = output
        self.calls = []

    def __call__(self, image):
        self.calls.append(image)
        return self.output


def _fake_engine_provider(output, threshold=None):
    """
    把模块级引擎换成假引擎，返回 (provider, engine, restore)

    引擎构造会加载真实模型，适配层用例只验返回格式与坐标换算，不应依赖模型。
    """
    import managers.ocr_providers.rapidocr as rapid_mod
    from managers.ocr_providers.rapidocr import RapidOCRProvider

    class FakeConfig:
        def __init__(self):
            from configparser import ConfigParser
            self.config = ConfigParser()
            self.config.add_section('OCR')
            if threshold is not None:
                self.config.set('OCR', 'rapidocr_score_threshold', str(threshold))

    provider = RapidOCRProvider(FakeConfig())
    engine = _FakeEngine(output)
    saved = rapid_mod._engine
    rapid_mod._engine = engine
    return provider, engine, lambda: setattr(rapid_mod, '_engine', saved)


def test_rapidocr_blocks_and_score_filter():
    """
    rapidocr 3.x 的 boxes/txts/scores 要正确转成 TextBlock

    - 四点多边形取 min/max 得包围盒
    - 低于置信度阈值的行必须丢弃（图标/分隔线的误检不该送去翻译）
    - 输出按阅读顺序（上→下、左→右）排列
    """
    print("=== 测试 RapidOCR 块级适配 ===")
    from PIL import Image
    import numpy as np
    from managers.ocr_providers.rapidocr import RapidOCRProvider

    image = Image.new('RGB', (200, 100), 'white')
    image.putpixel((0, 0), (255, 0, 0))     # 用来验证 BGR 通道顺序

    # 故意把下一行排在前面：适配层要按坐标重新排序
    lower = [[10, 60], [110, 60], [110, 80], [10, 80]]
    upper = [[10, 10], [110, 10], [110, 30], [10, 30]]
    noisy = [[150, 10], [190, 10], [190, 30], [150, 30]]
    output = _FakeOutput(
        boxes=[lower, upper, noisy],
        txts=('second line', 'first line', 'noise'),
        scores=np.array([0.95, 0.90, 0.20]),
    )

    provider, engine, restore = _fake_engine_provider(output, threshold=0.5)
    try:
        assert RapidOCRProvider.deterministic_results is True
        blocks = provider.recognize_blocks(image)
    finally:
        restore()

    assert [b.text for b in blocks] == ['first line', 'second line'], \
        f"低置信度行未过滤或顺序不对: {[b.text for b in blocks]}"
    first = blocks[0]
    assert (first.left, first.top, first.width, first.height) == (10, 10, 100, 20), \
        f"四点坐标未转成包围盒: {first!r}"

    # 引擎收到的是 BGR：PIL 的纯红 (255,0,0) 应变成 [0,0,255]
    sent = engine.calls[0]
    assert sent.shape == (100, 200, 3), f"图像尺寸/通道不对: {sent.shape}"
    assert tuple(sent[0, 0]) == (0, 0, 255), \
        f"通道未从 RGB 转 BGR: {tuple(sent[0, 0])}"

    print("✅ RapidOCR 块级适配测试通过\n")


def test_rapidocr_text_and_empty_result():
    """文本链路按行拼接；空输出是确定性结果，抛不可重试错误"""
    print("=== 测试 RapidOCR 文本与空结果 ===")
    from PIL import Image
    import numpy as np
    from managers.provider_common import (
        NonRetryableError, ProviderEmptyResultError)

    image = Image.new('RGB', (120, 60), 'white')
    boxes = [[[0, 0], [50, 0], [50, 20], [0, 20]],
             [[0, 30], [50, 30], [50, 50], [0, 50]]]
    provider, _engine, restore = _fake_engine_provider(
        _FakeOutput(boxes, ('  你好  ', 'world'), np.array([0.9, 0.8])))
    try:
        assert provider.recognize(image) == '你好\nworld'
    finally:
        restore()

    # 引擎跑通但没有任何检测框
    provider, _engine, restore = _fake_engine_provider(
        _FakeOutput(None, None, None))
    try:
        try:
            provider.recognize(image)
            raise AssertionError("空结果应抛 ProviderEmptyResultError")
        except ProviderEmptyResultError as e:
            assert isinstance(e, NonRetryableError), "确定性空结果不该重试"
    finally:
        restore()

    print("✅ RapidOCR 文本与空结果测试通过\n")


def test_rapidocr_threshold_parsing():
    """阈值非法时回退默认，且夹在 0~1 之间"""
    print("=== 测试 RapidOCR 阈值解析 ===")
    from managers.ocr_providers.rapidocr import (
        DEFAULT_SCORE_THRESHOLD, RapidOCRProvider)

    class FakeConfig:
        def __init__(self, raw):
            from configparser import ConfigParser
            self.config = ConfigParser()
            self.config.add_section('OCR')
            self.config.set('OCR', 'rapidocr_score_threshold', raw)

    assert RapidOCRProvider(FakeConfig('0.7'))._score_threshold() == 0.7
    assert RapidOCRProvider(FakeConfig('abc'))._score_threshold() == \
        DEFAULT_SCORE_THRESHOLD
    assert RapidOCRProvider(FakeConfig('-1'))._score_threshold() == 0.0
    assert RapidOCRProvider(FakeConfig('9'))._score_threshold() == 1.0
    print("✅ RapidOCR 阈值解析测试通过\n")


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


def test_reload_config_closes_stale_providers():
    """
    重载配置要关掉被淘汰的 provider

    provider 可能持有模型会话/连接池等资源：只从缓存字典里摘掉就得等 GC
    才回收，用户每点一次"保存"就漏一份。
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


def main():
    """脚本运行器：用例名单由 _runner 按本模块命名空间发现，不再手抄"""
    print("开始OCR架构测试...\n")
    from _runner import run_all
    return run_all(globals(), "🎉 所有OCR架构测试通过！")


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
