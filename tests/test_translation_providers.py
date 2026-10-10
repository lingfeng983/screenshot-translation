#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
翻译提供者架构测试
验证：注册表、自动发现、缓存键、长文本切分、降级逻辑
不依赖网络（网络请求均被mock或本地fake替换）
"""

import sys
import os
import tempfile
# 项目根目录加入 sys.path（tests/ 的上一级）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _isolate_translation_cache():
    """
    把全局翻译缓存改到临时目录

    用例里的 fake provider 会真的走 perform_translation，而它写的是全局
    cache_manager；不隔离就会把项目里的 translation_cache.json 灌满
    "hello <时间戳>" 这类测试文本，每次跑测试都多一堆遗留内容。
    """
    from managers.cache_manager import cache_manager
    tmp_dir = tempfile.TemporaryDirectory()
    cache_manager.cache_file = os.path.join(tmp_dir.name, 'cache.json')
    cache_manager._cache.clear()
    cache_manager._pending_writes = 0
    return tmp_dir


# 模块导入即隔离（pytest 与脚本模式都生效），引用要到进程结束才释放
_CACHE_TMP_DIR = _isolate_translation_cache()


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
    # 状态码必须是真整数：分发层按状态码判断可重试性，Mock 比较会 TypeError
    fake_response.status_code = 200
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
    try:
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
    finally:
        # 注册表是进程级单例：断言一失败 fake 就一直挂着，后面的用例会在不相干的
        # 那个断言上失败（结果里冒出 "[故障API] …"），真正的原因反而看不出来
        provider_registry.unregister('fake_fail')
        provider_registry.unregister('fake_ok')

    print("✅ 分发与降级测试通过\n")


def test_primary_retry_policy():
    """
    测试主API也走翻译链统一的轻量重试（2 次/0.5s）

    漏覆盖 _default_retry_handler 时主API会落到基类通用的 3 次/1s 策略：
    一次瞬时故障先白等 1s+2s 才降级，还多打一次请求（付费/计次 API 是烧额度的），
    而配置的降级API与免费兜底接口用的却是 2 次/0.5s。
    """
    print("=== 测试主API重试策略 ===")
    import time
    from managers.translation import TranslationManager, translation_retry_handler
    from managers.providers import provider_registry
    from managers.providers.base import TranslationProvider
    from managers.retry_handler import default_retry_handler

    calls = {'n': 0}

    class FakeRetryableProvider(TranslationProvider):
        name = 'fake_tr_retryable'
        display_name = '瞬时故障API'

        def translate(self, text, source='auto', target='zh'):
            calls['n'] += 1
            # 普通异常（非 NonRetryableError）才会走退避重试
            raise Exception("模拟瞬时故障")

    provider_registry.register(FakeRetryableProvider, override=True)
    manager = TranslationManager(config_manager=None)
    try:
        assert manager._default_retry_handler() is translation_retry_handler, \
            "主API应使用翻译链统一重试器而非基类通用重试器"

        # 直接走单provider链路，避开免费接口兜底的真实网络请求
        started = time.monotonic()
        result, serving = manager._translate_via(FakeRetryableProvider, "hello")
        elapsed = time.monotonic() - started

        assert result.startswith('[翻译失败'), f"故障应转为失败文本: {result}"
        assert serving is None, "失败的provider不应被记为实际服务方"
        assert calls['n'] == translation_retry_handler.max_attempts, (
            f"主API应重试 {translation_retry_handler.max_attempts} 次，实际 {calls['n']} 次"
            f"（通用策略是 {default_retry_handler.max_attempts} 次）")
        # 通用策略退避 1s+2s≈3s；统一策略两次之间只等 0.5s±25% 抖动
        assert elapsed < 1.5, f"主API退避过久: {elapsed:.2f}s，应使用短间隔重试"
    finally:
        provider_registry.unregister('fake_tr_retryable')
    print("✅ 主API重试策略测试通过\n")


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
    try:
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
    finally:
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


def _assert_raises(expected, func, label):
    """脚本模式也能跑的断言（本文件同时用 python 直接跑，不依赖 pytest fixture）"""
    try:
        func()
    except expected:
        return
    except Exception as e:
        raise AssertionError(f"{label}: 应抛 {expected.__name__}，实际 {type(e).__name__}: {e}")
    raise AssertionError(f"{label}: 应抛 {expected.__name__}，实际没有抛")


def test_baidu_language_codes_and_byte_limit():
    """
    百度用自己的语种表，也按 UTF-8 字节而不是字符限长

    界面语言表是通用 ISO 码，百度认的是 cht/jp/kor/fra：缺映射时选日语会吃
    58001「译文语言方向不支持」，整条降级链白跑。长度同理——百度单次请求上限是
    6000 字节，中文一个字 3 字节，按 3000 字符切一段中文就有 9000 字节，
    会被判超长直接拒掉。
    """
    print("=== 测试百度语种与字节分块 ===")
    from unittest import mock

    from managers.providers.baidu import BaiduProvider

    class _Config:
        def get_baidu_api_info(self):
            return 'appid', 'secret'

    def _response(text):
        resp = mock.Mock()
        resp.status_code = 200
        resp.text = ''
        resp.json.return_value = {'trans_result': [{'dst': text}]}
        return resp

    provider = BaiduProvider(config_manager=_Config())
    provider.session = mock.Mock()
    provider.session.post.return_value = _response('你好')

    def posted_params():
        return [call.kwargs['data'] for call in provider.session.post.call_args_list]

    for code, expected in (('ja', 'jp'), ('ko', 'kor'), ('fr', 'fra'),
                           ('zh-TW', 'cht'), ('de', 'de'), ('en', 'en')):
        provider.session.post.reset_mock()
        provider.translate('Hello', 'auto', code)
        data = posted_params()[0]
        assert data['to'] == expected, f"{code} 应映射成 {expected}，实际 {data['to']}"

    # 4000 个汉字 = 12000 字节：必须切到每块都不超字节上限
    provider.session.post.reset_mock()
    long_cjk = '中' * 4000
    provider.translate(long_cjk, 'auto', 'en')
    chunks = [d['q'] for d in posted_params()]
    assert len(chunks) > 1, f"超长中文没有被切分: {len(chunks)} 块"
    for chunk in chunks:
        size = len(chunk.encode('utf-8'))
        assert size <= BaiduProvider._MAX_BYTES, f"块超字节上限: {size}"
    assert ''.join(chunks) == long_cjk, "切分后内容不完整"

    print("✅ 百度语种与字节分块测试通过\n")


def test_business_error_code_classification():
    """
    业务错误码只有官方说"重试"的那些才允许重试

    百度的确定性错误占多数——52003 未授权用户、54001 签名错误、54004 余额不足、
    58000 客户端IP非法、58001 语种不支持、58002 服务已关闭；把它们记成瞬时错误
    等于每次都要等满退避再重打一次。有道的官方表里只有 411/412 明说"请稍后访问"，
    外加 302/303 两个服务端故障；原先记的 105（签名类型不支持）重试必然重犯。
    """
    print("=== 测试业务错误码分类 ===")
    from managers.provider_common import (
        ProviderRejectedError, ProviderUnavailableError, raise_business_error)
    from managers.providers.baidu import BaiduProvider
    from managers.providers.youdao import YoudaoProvider

    def _check(codes, transient, label):
        for code in codes:
            should_retry = code in transient
            expected = ProviderUnavailableError if should_retry else ProviderRejectedError
            _assert_raises(
                expected,
                lambda c=code, t=transient: raise_business_error(label, c, 'x', t),
                f"{label} {code}")

    baidu_transient = set(BaiduProvider._transient_codes)
    assert baidu_transient == {'52001', '52002', '54003', '54005'}, \
        f"百度瞬时码集合不对: {baidu_transient}"
    _check(('52001', '52002', '54003', '54005', '52003', '54001', '54004',
            '58000', '58001', '58002'), baidu_transient, '百度翻译')

    youdao_transient = set(YoudaoProvider._transient_codes)
    assert youdao_transient == {'302', '303', '411', '412'}, \
        f"有道瞬时码集合不对: {youdao_transient}"
    _check(('302', '303', '411', '412', '103', '105', '201', '202', '206', '401'),
           youdao_transient, '有道翻译')

    print("✅ 业务错误码分类测试通过\n")


def test_blank_translation_is_not_success():
    """
    空译文不算成功：降级链要接着往下走

    接口偶发返回空结果时，以前 `served` 照样按"这个引擎成功了"记账：链就此
    止步，缓存不写，遮罩显示一片空白而不是可重试的失败卡片。
    """
    print("=== 测试空译文继续降级 ===")
    from managers.providers.base import TranslationProvider
    from managers.providers import provider_registry
    from managers.translation import TranslationManager

    class BlankProvider(TranslationProvider):
        name = 'fake_blank'
        display_name = '空译文API'

        def translate(self, text, source='auto', target='zh'):
            return ''

    class OkProvider(TranslationProvider):
        name = 'fake_ok_after_blank'
        display_name = '空译文之后的API'

        def translate(self, text, source='auto', target='zh'):
            return f'[{target}] {text}'

    provider_registry.register(BlankProvider, override=True)
    provider_registry.register(OkProvider, override=True)
    manager = TranslationManager(config_manager=None)
    manager._FREE_FALLBACKS = ()          # 不打真实网络
    try:
        manager.set_api_type('fake_blank')
        manager.fallback_api_type = 'fake_ok_after_blank'
        result, serving = manager._dispatch_with_serving('空链测试一', 'zh')
        assert serving == 'fake_ok_after_blank', f"没降级到出译文的引擎: {serving}"
        assert result == '[zh] 空链测试一', f"结果不对: {result}"

        # 全链只有空串：必须落成可重试的失败标记
        manager.fallback_api_type = ''
        result, serving = manager._dispatch_with_serving('空链测试二', 'zh')
        assert result.startswith('[翻译失败'), f"空串被当成成功: {result}"
        assert serving is None, f"空串被记成服务方: {serving}"
    finally:
        provider_registry.unregister('fake_blank')
        provider_registry.unregister('fake_ok_after_blank')

    print("✅ 空译文继续降级测试通过\n")


def test_free_fallback_skips_already_tried_engine():
    """
    配置的降级API就是免费接口时不再跑第二遍

    免费兜底那段只跟"主API"去重，用户把降级API填成 edge_free 时同一个引擎
    会在一条链上被请求两次（每次还带自己的重试），时限白烧一半。
    """
    print("=== 测试免费兜底去重 ===")
    from managers.providers.base import TranslationProvider
    from managers.providers import provider_registry
    from managers.translation import TranslationManager

    calls = {'n': 0}

    class FailProvider(TranslationProvider):
        name = 'fake_main_fail_once'
        display_name = '故障主API'

        def translate(self, text, source='auto', target='zh'):
            raise Exception("主API故障")

    class CountingProvider(TranslationProvider):
        name = 'fake_edge_like'
        display_name = '被配置的免费API'

        def translate(self, text, source='auto', target='zh'):
            calls['n'] += 1
            raise Exception("依旧故障")

    provider_registry.register(FailProvider, override=True)
    provider_registry.register(CountingProvider, override=True)
    manager = TranslationManager(config_manager=None)
    manager._FREE_FALLBACKS = ('fake_edge_like',)
    try:
        manager.set_api_type('fake_main_fail_once')
        manager.fallback_api_type = 'fake_edge_like'
        result = manager._dispatch_translation('去重测试', 'zh')
        assert result.startswith('[翻译失败'), result
        # 降级阶段按 max_attempts 跑完，兜底阶段必须整个跳过
        from managers.translation import translation_retry_handler
        expected = translation_retry_handler.max_attempts
        assert calls['n'] == expected, \
            f"同一引擎被跑了 {calls['n']} 次，应为 {expected} 次（去重失效）"
    finally:
        provider_registry.unregister('fake_main_fail_once')
        provider_registry.unregister('fake_edge_like')

    print("✅ 免费兜底去重测试通过\n")


def test_bing_uses_microsoft_language_codes():
    """
    必应/Azure 的语言代码与 Edge 免费端点同源

    zh-TW 直接送过去会 400；两处各自维护映射表的话迟早一边改一边没改。
    """
    print("=== 测试必应语种映射 ===")
    from unittest import mock

    from managers.providers import bing, edge_free
    from managers.providers.bing import BingProvider

    assert bing.MICROSOFT_LANG_MAP is edge_free._TARGET_MAP, \
        "微软系接口应共用同一张语种表"

    provider = BingProvider()
    provider._get_secret = lambda option, env_name=None: 'test-key'
    resp = mock.Mock()
    resp.status_code = 200
    resp.text = ''
    resp.json.return_value = [{'translations': [{'text': '你好'}]}]
    provider.session = mock.Mock()
    provider.session.post.return_value = resp

    provider.translate('hi', 'auto', 'zh-TW')
    params = provider.session.post.call_args.kwargs['params']
    assert params['to'] == 'zh-Hant', f"繁体未映射: {params['to']}"

    provider.session.post.reset_mock()
    provider.translate('hi', 'auto', 'ja')
    params = provider.session.post.call_args.kwargs['params']
    assert params['to'] == 'ja', f"日语不该被改写: {params['to']}"

    print("✅ 必应语种映射测试通过\n")


def test_non_json_response_is_classified():
    """
    网关返回 HTML 时要归一为可重试错误，而不是抛裸 ValueError

    裸 ValueError 会让上层把它和"代码写错了"混在一起，日志里查不到是哪个接口
    在什么时候回了什么。
    """
    print("=== 测试非JSON响应分类 ===")
    from unittest import mock

    from managers.provider_common import ProviderUnavailableError
    from managers.providers.edge_free import EdgeFreeProvider
    from managers.providers.google_free import GoogleFreeProvider

    for cls, verb in ((EdgeFreeProvider, 'post'), (GoogleFreeProvider, 'get')):
        provider = cls()
        resp = mock.Mock()
        resp.status_code = 200
        resp.text = '<html>bad gateway</html>'
        resp.json.side_effect = ValueError('no json')
        session = mock.Mock()
        getattr(session, verb).return_value = resp
        provider.session = session

        _assert_raises(
            ProviderUnavailableError,
            lambda p=provider: p.translate('hi', 'auto', 'zh'),
            f"{cls.__name__} 非JSON响应")

    print("✅ 非JSON响应分类测试通过\n")


def test_request_timeout_shares_remaining_budget():
    """
    剩余预算要在连接与读取之间分配

    requests 会把两个数各自用满：连接等满 remaining 之后再读满 remaining，
    一次请求就能把整条降级链拖到时限的两倍。
    """
    print("=== 测试剩余时限分配 ===")
    import time

    from managers.providers.base import TranslationProvider

    provider = TranslationProvider.__new__(TranslationProvider)
    provider.timeout = (5, 10)

    assert provider.request_timeout(None) == (5, 10)

    connect, read = provider.request_timeout(time.monotonic() + 1.0)
    assert connect <= 0.5 + 1e-6, f"连接超时不该吃掉整个剩余预算: {connect}"
    assert read <= 1.0 + 1e-6, f"读取超时超出剩余预算: {read}"

    connect, read = provider.request_timeout(time.monotonic() - 1.0)
    assert (connect, read) == (0.1, 0.1), f"时限已过仍给出大超时: {connect} {read}"

    print("✅ 剩余时限分配测试通过\n")


def main():
    """脚本运行器：用例名单由 _runner 按本模块命名空间发现，不再手抄"""
    print("开始翻译架构测试...\n")
    from _runner import run_all
    return run_all(globals(), "🎉 所有翻译架构测试通过！")


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
