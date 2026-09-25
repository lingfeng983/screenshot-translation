#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
翻译提供者架构测试
验证：注册表、自动发现、缓存键、长文本切分、降级逻辑
不依赖网络（网络请求均被mock或本地fake替换）
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def test_provider_registry():
    """测试provider注册表与自动发现"""
    print("=== 测试provider注册表 ===")
    from managers.providers import provider_registry

    names = provider_registry.names()
    print(f"已注册的提供者: {names}")

    expected = {'edge_free', 'google_free', 'libretranslate', 'bing', 'baidu', 'youdao', 'llm'}
    missing = expected - set(names)
    assert not missing, f"缺少内置提供者: {missing}"

    # 显示名映射
    assert provider_registry.display_name_of('edge_free') == 'Edge翻译(免费)'
    assert provider_registry.display_name_of('google_free') == '谷歌翻译(免费)'
    assert provider_registry.display_name_of('nonexistent') == 'nonexistent'

    # 显示名反查
    assert provider_registry.find_by_display_name('谷歌翻译(免费)') == 'google_free'

    # 重复注册被拒绝
    from managers.providers.google_free import GoogleFreeProvider
    assert not provider_registry.register(GoogleFreeProvider), "重复注册应被拒绝"

    print("✅ provider注册表测试通过\n")


def test_edge_free_provider():
    """测试Edge免费翻译provider（mock网络）"""
    print("=== 测试Edge免费翻译provider ===")
    from managers.providers.edge_free import EdgeFreeProvider
    from unittest import mock

    provider = EdgeFreeProvider()

    fake_response = mock.Mock()
    fake_response.json.return_value = [
        {'translations': [{'text': '你好世界', 'to': 'zh-Hans'}]}
    ]
    fake_response.raise_for_status = mock.Mock()

    session = mock.Mock()
    session.post.return_value = fake_response
    provider.session = session

    result = provider.translate("Hello world", source='auto', target='zh')
    assert result == '你好世界', f"译文不符: {result}"

    # 校验请求格式：body 为字符串数组、to 参数映射为 zh-Hans
    args, kwargs = session.post.call_args
    assert kwargs['json'] == ['Hello world'], "body必须是字符串数组"
    assert 'to=zh-Hans' in args[0], "目标语言应映射为zh-Hans"
    assert 'from=&' in args[0], "auto源语言应留空from参数"

    # 繁体映射
    provider.translate("hi", 'auto', 'zh-TW')
    assert 'to=zh-Hant' in session.post.call_args[0][0]

    print("✅ Edge免费翻译provider测试通过\n")


def test_chunk_text():
    """测试长文本切分"""
    print("=== 测试长文本切分 ===")
    from managers.providers.base import TranslationProvider

    # 短文本不切分
    assert TranslationProvider._chunk_text("hello") == ["hello"]

    # 长文本按段落切分
    long_text = "\n".join([f"段落{i} " + "x" * 100 for i in range(60)])
    chunks = TranslationProvider._chunk_text(long_text, max_length=500)
    assert len(chunks) > 1, "长文本应被切分"
    assert all(len(c) <= 500 for c in chunks), "每个块不应超过上限"
    # 内容完整性
    assert ''.join(chunks).replace('\n', '') == long_text.replace('\n', '')

    # 无段落超长文本按硬切分
    one_line = "y" * 1200
    chunks2 = TranslationProvider._chunk_text(one_line, max_length=500)
    assert len(chunks2) == 3
    assert ''.join(chunks2) == one_line

    print("✅ 长文本切分测试通过\n")


def test_dispatch_and_fallback():
    """测试分发与降级链（mock网络）"""
    print("=== 测试翻译分发与降级 ===")
    from managers.translation import TranslationManager
    from managers.providers import provider_registry
    from managers.providers.base import TranslationProvider

    class FakeFailProvider(TranslationProvider):
        name = 'fake_fail'
        display_name = '故障API'

        def translate(self, text, source='auto', target='zh'):
            raise Exception("模拟故障")

    class FakeOKProvider(TranslationProvider):
        name = 'fake_ok'
        display_name = '备用API'

        def translate(self, text, source='auto', target='zh'):
            return f"[{target}] {text}"

    provider_registry.register(FakeFailProvider, override=True)
    provider_registry.register(FakeOKProvider, override=True)

    manager = TranslationManager(config_manager=None)
    manager.set_api_type('fake_fail')
    manager.fallback_api_type = 'fake_ok'

    result = manager.perform_translation("hello world")
    print(f"主API故障后的降级结果: {result}")
    assert '[zh] hello world' in result, "应降级到备用API并成功翻译"

    # 主API正常时不走降级
    manager.set_api_type('fake_ok')
    result2 = manager.perform_translation("another")
    assert '[zh] another' in result2

    # 未知API类型回退到谷歌免费
    manager.set_api_type('no_such_api')
    result3 = manager._dispatch_translation("test")
    assert result3, "未知API应回退到谷歌免费并返回结果"

    # 清理fake provider
    provider_registry.unregister('fake_fail')
    provider_registry.unregister('fake_ok')

    print("✅ 分发与降级测试通过\n")


def test_per_translation_target_lang():
    """测试单次翻译指定目标语言（结果对话框切换语言场景）"""
    print("=== 测试单次翻译目标语言 ===")
    from managers.translation import TranslationManager
    from managers.providers import provider_registry
    from managers.providers.base import TranslationProvider

    captured = {}

    class FakeCaptureProvider(TranslationProvider):
        name = 'fake_capture'
        display_name = '捕获API'

        def translate(self, text, source='auto', target='zh'):
            captured['target'] = target
            return f"[{target}] {text}"

    provider_registry.register(FakeCaptureProvider, override=True)
    manager = TranslationManager(config_manager=None)
    manager.set_api_type('fake_capture')

    # 用唯一文本避免持久化缓存跨运行命中，确保真的调用到provider
    import time
    unique = f"hello {time.time()}"

    # 默认使用全局配置的目标语言
    manager.perform_translation(unique)
    assert captured['target'] == 'zh'

    # 单次指定目标语言，不修改全局配置
    result = manager.perform_translation(unique, target_lang='ja')
    assert captured['target'] == 'ja'
    assert result == '[ja] ' + unique
    assert manager.target_lang == 'zh', "全局配置不应被单次翻译修改"

    # 强制刷新 + 指定语言（结果对话框重新翻译路径）
    result2 = manager.perform_translation(
        unique, force_refresh=True, target_lang='en')
    assert captured['target'] == 'en'
    assert result2 == '[en] ' + unique

    provider_registry.unregister('fake_capture')
    print("✅ 单次翻译目标语言测试通过\n")


def test_llm_messages():
    """测试LLM提示词构造与think标签清理"""
    print("=== 测试LLM provider ===")
    from managers.providers.llm import LLMProvider

    provider = LLMProvider()
    messages = provider._build_messages("Hello", 'auto', 'zh')
    assert messages[0]['role'] == 'system'
    assert '中文' in messages[1]['content']
    assert 'Hello' in messages[1]['content']

    # 指定源语言
    messages2 = provider._build_messages("Hello", 'en', 'ja')
    assert '英语' in messages2[1]['content'] and '日语' in messages2[1]['content']

    # think标签清理
    dirty = "<think>推理过程</think>你好世界"
    assert provider._strip_reasoning(dirty) == "你好世界"
    assert provider._strip_reasoning("普通文本") == "普通文本"

    print("✅ LLM provider测试通过\n")


def test_target_languages():
    """测试目标语言表"""
    print("=== 测试目标语言 ===")
    from managers.providers.base import TARGET_LANGUAGES

    codes = [c for c, _ in TARGET_LANGUAGES]
    assert 'zh' in codes and 'en' in codes and 'ja' in codes
    assert len(codes) == len(set(codes)), "语言代码不应重复"

    print("✅ 目标语言测试通过\n")


def main():
    print("开始翻译架构测试...\n")

    try:
        test_provider_registry()
        test_edge_free_provider()
        test_chunk_text()
        test_dispatch_and_fallback()
        test_per_translation_target_lang()
        test_llm_messages()
        test_target_languages()

        print("🎉 所有翻译架构测试通过！")
        return True
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
