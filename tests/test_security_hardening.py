#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
安全/隐私/并发加固的回归用例

对应评审里的 P0/P1 修复：裸键拦截、长截图迟到结果、GDI 资源、配置备份、
旧数据明文副本、翻译缓存开关、免费第三方兜底开关、免费接口原文不出查询串。
所有用例离线、不注册全局热键、不发起网络请求。
"""

import ctypes
import logging
import os
import threading
from unittest import mock


# ---------------------------------------------------------------- 快捷键后端

def test_keyboard_backend_rejects_bare_key(monkeypatch):
    """键盘钩子后端不能把裸键注册成全局按键（原生后端本来就拒绝）"""
    from managers import hotkey as hotkey_module

    calls = []
    monkeypatch.setattr(hotkey_module.keyboard, 'add_hotkey',
                        lambda *a, **k: calls.append(a) or 'handle')
    manager = hotkey_module.HotkeyManager()
    manager.hotkey = 'a'
    assert manager._register_keyboard() is False
    assert calls == [], "裸键被交给 keyboard.add_hotkey 了"
    assert '缺少修饰键' in manager.last_error


def test_set_hotkey_empty_disables_registration(monkeypatch):
    """空值 = 主动禁用：注销当前注册，不把空串交给钩子库"""
    from managers.hotkey import HotkeyManager

    manager = HotkeyManager()
    manager.hotkey = 'alt+z'
    calls = []
    monkeypatch.setattr(manager, '_unregister_hotkey',
                        lambda: calls.append('unregister'))
    assert manager.set_hotkey('') is True
    assert manager.hotkey == ''
    assert calls == ['unregister']


# ---------------------------------------------------------------- 长截图令牌与 GDI

def test_stale_scroll_result_keeps_new_tokens(qapp):
    """旧一轮结果到达不能清掉新一轮的完成/取消令牌"""
    from managers.screenshot import ScreenshotManager

    class _FakeWidget:
        def __init__(self):
            self.finished = []

        def current_request_id(self):
            return 2

        def on_scroll_finished(self, image, canceled=False):
            self.finished.append((image, canceled))

    manager = ScreenshotManager()
    widget = _FakeWidget()
    manager._screenshot_widget = widget
    new_cancel = threading.Event()
    manager._scroll_finish = threading.Event()
    manager._scroll_cancel = new_cancel
    old_cancel = threading.Event()

    manager._apply_scroll_result(widget, None, 1, old_cancel)
    assert manager._scroll_cancel is new_cancel, '新令牌被旧结果清掉了'
    assert manager._scroll_finish is not None
    assert widget.finished == [], '旧结果不该通知遮罩'

    # 本轮结果正常走完：清令牌并通知遮罩
    manager._apply_scroll_result(widget, None, 2, new_cancel)
    assert manager._scroll_cancel is None
    assert manager._scroll_finish is None
    assert len(widget.finished) == 1

    # 回投失败后的兜底恢复（令牌已被当场清掉）也不能被身份检查挡住
    manager._scroll_cancel = None
    manager._apply_scroll_result(widget, None, 2, old_cancel, True)
    assert len(widget.finished) == 2, '兜底恢复被误判成过期结果'


def test_gdi_failure_path_restores_previous_bitmap(monkeypatch):
    """BitBlt 失败时也要把旧对象选回 DC，否则 DeleteObject 会漏 GDI 句柄"""
    from managers import capture_scroll

    user32 = mock.MagicMock()
    user32.GetDC.return_value = 1
    user32.ReleaseDC.return_value = 0
    user32.GetSystemMetrics.return_value = 0
    gdi32 = mock.MagicMock()
    gdi32.CreateCompatibleDC.return_value = 2
    gdi32.CreateCompatibleBitmap.return_value = 3
    gdi32.SelectObject.side_effect = [99, 0]      # 选入返回旧对象 99，恢复返回 0
    gdi32.BitBlt.return_value = 0                  # 模拟抓帧失败
    fake_windll = mock.Mock()
    fake_windll.user32 = user32
    fake_windll.gdi32 = gdi32
    monkeypatch.setattr(ctypes, 'windll', fake_windll, raising=False)

    assert capture_scroll._grab_region_win32((0, 0, 4, 4), False) is None
    assert gdi32.SelectObject.call_count == 2, '失败路径没恢复旧 GDI 对象'
    assert gdi32.SelectObject.call_args_list[1][0] == (2, 99)
    assert gdi32.DeleteObject.call_count == 1
    assert gdi32.DeleteDC.call_count == 1
    assert user32.ReleaseDC.call_count == 1


# ---------------------------------------------------------------- 配置与迁移

def test_privacy_defaults_migration_and_bad_values(tmp_path):
    """隐私开关默认值、老配置补齐、坏值回退"""
    from config import ConfigManager

    config_file = str(tmp_path / 'config.ini')
    manager = ConfigManager(config_file)
    assert manager.is_translation_cache_enabled() is True
    assert manager.is_clear_cache_on_exit() is False
    assert manager.is_free_fallback_allowed() is False
    assert manager.is_translation_warmup_enabled() is False

    manager._set('Translation', 'allow_free_fallback', 'maybe')
    assert manager.is_free_fallback_allowed() is False, '坏布尔值不该把兜底打开'

    old_file = tmp_path / 'old.ini'
    old_file.write_text('[Translation]\ntarget_lang = zh\n', encoding='utf-8')
    migrated = ConfigManager(str(old_file))
    assert migrated.is_translation_cache_enabled() is True
    assert migrated.is_free_fallback_allowed() is False
    assert 'allow_free_fallback = false' in old_file.read_text(encoding='utf-8'), \
        '老配置没有补齐新开关'


def test_migration_deletes_plaintext_source(tmp_path, monkeypatch, caplog):
    """迁移成功后删除安装目录里的明文副本；删不掉时明确告警"""
    from managers import app_paths

    home = tmp_path / 'home'
    appdir = tmp_path / 'app'
    target = home / '.screenshot-translation'
    target.mkdir(parents=True)
    appdir.mkdir()
    source = appdir / 'config.ini'
    source.write_text('[Translation]\nbing_api_key = SECRET\n', encoding='utf-8')
    monkeypatch.setattr(app_paths, '_is_frozen', lambda: True)
    monkeypatch.setattr(app_paths, 'app_root', lambda: str(appdir))
    monkeypatch.setattr(app_paths, 'data_dir', lambda: str(target))

    assert app_paths.migrate_legacy_data() == ['config.ini']
    assert not source.exists(), '安装目录里的明文密钥没删掉'
    assert (target / 'config.ini').read_text(encoding='utf-8') == \
        '[Translation]\nbing_api_key = SECRET\n'

    # 删除失败（Program Files 只读等）不能静默
    source.write_text('[Translation]\nbaidu_api_key = SECRET2\n', encoding='utf-8')
    (target / 'config.ini').unlink()
    real_remove = os.remove

    def fail_remove(path, *args, **kwargs):
        if str(path) == str(source):
            raise OSError('read-only')
        return real_remove(path, *args, **kwargs)

    monkeypatch.setattr(app_paths.os, 'remove', fail_remove)
    with caplog.at_level(logging.WARNING, logger='screenshot-translation'):
        assert app_paths.migrate_legacy_data() == ['config.ini']
    assert source.exists(), '删除失败时原件不该被强行清掉'
    assert '请手动删除' in caplog.text


def test_cache_disabling_purges_plaintext(tmp_path):
    """关闭缓存会清内存并删掉磁盘文件；禁用后不再写盘"""
    from managers.cache_manager import CacheManager

    path = str(tmp_path / 'cache.json')
    manager = CacheManager(cache_file=path)
    manager.cache_translation('截图原文', 'edge_free', '译文')
    manager.flush()
    assert os.path.exists(path)
    assert manager.get_cached_translation('截图原文', 'edge_free') == '译文'

    manager.set_enabled(False)
    assert not os.path.exists(path), '关闭缓存没删掉明文文件'
    assert manager.get_cached_translation('截图原文', 'edge_free') is None
    manager.cache_translation('again', 'edge_free', 'again')
    manager.flush()
    assert not os.path.exists(path), '禁用后又写了缓存文件'


def test_cache_file_permissions_tightened_before_replace(tmp_path, monkeypatch):
    """明文缓存落盘前收紧到 0o600，且收紧必须发生在原子替换之前"""
    from managers.cache_manager import CacheManager

    order = []
    real_chmod, real_replace = os.chmod, os.replace

    def record_chmod(file, mode):
        order.append(('chmod', mode))
        return real_chmod(file, mode)

    def record_replace(src, dst):
        order.append('replace')
        return real_replace(src, dst)

    monkeypatch.setattr(os, 'chmod', record_chmod)
    monkeypatch.setattr(os, 'replace', record_replace)

    path = str(tmp_path / 'cache.json')
    manager = CacheManager(cache_file=path)
    manager.cache_translation('截图原文', 'edge_free', '译文')
    manager.flush()

    assert ('chmod', 0o600) in order, '明文缓存没收紧文件权限'
    assert 'replace' in order
    assert order.index(('chmod', 0o600)) < order.index('replace'), \
        'chmod 在 replace 之后，明文有一段按默认权限可读的窗口'
    assert os.path.exists(path)


# ---------------------------------------------------------------- 翻译隐私与外发

def test_free_fallback_is_opt_in(tmp_path):
    """有配置对象时免费第三方兜底默认关闭，显式开启才参与链与缓存键"""
    from config import ConfigManager
    from managers.translation import TranslationManager

    config = ConfigManager(str(tmp_path / 'config.ini'))
    manager = TranslationManager(config)
    assert manager.allow_free_fallback is False
    assert manager._free_fallback_names() == ()
    assert 'google_free' not in manager._cache_names()

    config.set_free_fallback_allowed(True)
    manager.reload_config()
    assert manager._free_fallback_names() == manager._FREE_FALLBACKS
    assert 'google_free' in manager._cache_names()


def test_warm_up_respects_privacy_switch(tmp_path):
    """启动预热默认不发起任何请求"""
    from config import ConfigManager
    from managers.translation import TranslationManager

    config = ConfigManager(str(tmp_path / 'config.ini'))
    manager = TranslationManager(config)
    calls = []
    manager.perform_translation = lambda *a, **k: calls.append(a) or '译文'
    assert manager.warm_up() is False and calls == []

    config.set_translation_warmup_enabled(True)
    assert manager.warm_up() is True and calls, '显式开启后仍没预热'


def test_google_free_keeps_text_out_of_query_string():
    """原文放 POST 请求体，不出现在 URL 查询串里"""
    from managers.providers.google_free import GoogleFreeProvider

    provider = GoogleFreeProvider()
    response = mock.Mock()
    response.status_code = 200
    response.json.return_value = {'sentences': [{'trans': '你好'}]}
    session = mock.Mock()
    session.post.return_value = response
    session.get.side_effect = AssertionError('不该再发 GET 请求')
    provider.session = session

    assert provider.translate('Secret text', 'auto', 'zh') == '你好'
    kwargs = session.post.call_args.kwargs
    assert kwargs['data']['q'] == 'Secret text'
    assert 'q' not in kwargs['params'], '原文仍拼在查询串里'
    assert session.get.call_count == 0
