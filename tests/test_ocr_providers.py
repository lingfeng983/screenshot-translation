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
    from managers.ocr import OCRManager
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
    assert calls['fail'] >= 2, "主引擎应至少重试过"

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

    img = Image.new('RGB', (100, 50), color=(200, 200, 200))
    processed = TesseractProvider._preprocess_image(img)
    assert processed.size == (200, 100), "应放大2倍"
    assert processed.mode == 'L', "应转为灰度"

    # 非法输入回退原图
    weird = Image.new('RGBA', (10, 10))
    out = TesseractProvider._preprocess_image(weird)
    assert out is not None

    print("✅ 图像预处理测试通过\n")


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


def main():
    print("开始OCR架构测试...\n")

    try:
        test_ocr_provider_registry()
        test_dispatch_and_fallback()
        test_per_engine_config_isolation()
        test_preprocess_image()
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
