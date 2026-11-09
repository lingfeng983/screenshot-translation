#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新检查用例

覆盖版本比较、GitHub 响应解析、自动检查开关、托盘推送去重与手动检查打开
发布页。网络请求全部用假响应/假函数替换，离线可跑。
"""

import main
from managers import update_checker as uc


class _FakeResponse:
    """requests.get 的最小替身"""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def raise_for_status(self):
        """假响应永远成功"""

    def json(self):
        """返回预设 JSON"""
        return self._payload


class _FakeTray:
    """只记录托盘通知"""

    def __init__(self):
        self.notifications = []

    def show_notification(self, title, message):
        self.notifications.append((title, message))


def _config(tmp_path):
    """每条用例独立的配置文件，避免写开发者本地 config.ini"""
    from config import ConfigManager
    return ConfigManager(str(tmp_path / 'config.ini'))


def test_parse_version_and_compare():
    """版本号兼容 v 前缀/预发布后缀，按数字逐段比较"""
    assert uc.parse_version('v1.9.0') == (1, 9, 0)
    assert uc.parse_version('1.9.0-beta.2') == (1, 9, 0)
    assert uc.parse_version('') == ()
    assert uc.parse_version(None) == ()

    assert uc.is_newer('1.9.0', '1.8.0') is True
    assert uc.is_newer('v1.10.0', '1.9.9') is True
    assert uc.is_newer('1.8.0', '1.8.0') is False
    assert uc.is_newer('1.8', '1.8.0') is False, "缺省段应按 0 补齐"
    assert uc.is_newer('2.0.0', '1.9.9') is True
    assert uc.is_newer('bad', '1.8.0') is False, "无法解析时不能误报更新"


def test_current_version_is_parsable():
    """源码里的版本常量必须能被解析成数字版本"""
    assert uc.parse_version(uc.current_version())


def test_fetch_latest_release_parses_github_payload(monkeypatch):
    """GitHub 的 tag_name/html_url/body 映射到 ReleaseInfo"""
    import requests

    response = _FakeResponse({
        'tag_name': 'v1.9.0',
        'html_url': 'https://example.test/release',
        'body': '- 新功能',
    })
    seen = {}

    def fake_get(url, timeout=None, headers=None):
        seen.update(url=url, timeout=timeout, headers=headers or {})
        return response

    monkeypatch.setattr(requests, 'get', fake_get)
    info = uc.fetch_latest_release()

    assert info.version == '1.9.0'
    assert info.url == 'https://example.test/release'
    assert info.notes == '- 新功能'
    assert seen['url'] == uc.API_URL
    assert 'ScreenshotTranslator/' in seen['headers']['User-Agent']


def test_check_for_update_only_returns_newer(monkeypatch):
    """同版本/更旧版本都返回 None，只有更新时返回 ReleaseInfo"""
    fake = uc.ReleaseInfo('1.9.0', 'https://example.test/release')
    monkeypatch.setattr(uc, 'fetch_latest_release', lambda *a, **k: fake)

    assert uc.check_for_update('1.8.0') is fake
    assert uc.check_for_update('1.9.0') is None
    assert uc.check_for_update('2.0.0') is None


def test_auto_check_respects_config_switch(tmp_path, monkeypatch):
    """关闭"启动时检查更新"后自动检查不提交任务，手动检查不受限制"""
    from main import MainApp

    app = MainApp()
    app.config_manager = _config(tmp_path)
    app.config_manager.set_auto_check_update(False)

    submitted = []
    monkeypatch.setattr(main.thread_pool_manager, 'submit',
                        lambda *args: submitted.append(args) or object())

    app.check_updates()
    assert submitted == [], "关闭开关后启动检查仍然提交了请求"

    app.check_updates(interactive=True)
    assert len(submitted) == 1, "手动检查应不受自动检查开关限制"
    app._update_checking = False


def test_update_notification_is_pushed_once_per_version(tmp_path, monkeypatch):
    """自动检查发现新版只推送一次；同一版本再次检查不再打扰"""
    from main import MainApp

    app = MainApp()
    app.config_manager = _config(tmp_path)
    app.tray_manager = _FakeTray()
    app._update_checking = True
    monkeypatch.setattr(uc, 'current_version', lambda: '1.8.0')

    info = uc.ReleaseInfo('1.9.0', 'https://example.test/release')
    app._apply_update_result(info, '', False)
    assert len(app.tray_manager.notifications) == 1, "发现新版没有推送托盘通知"
    title, message = app.tray_manager.notifications[0]
    assert '1.9.0' in title and '1.8.0' in message
    assert app.config_manager.get_last_notified_version() == '1.9.0'

    app._update_checking = True
    app._apply_update_result(info, '', False)
    assert len(app.tray_manager.notifications) == 1, "同一版本被重复推送"


def test_worker_runs_check_and_pushes(tmp_path, monkeypatch):
    """后台检查线程返回新版后，结果回主线程推送通知"""
    from main import MainApp

    app = MainApp()
    app.config_manager = _config(tmp_path)
    app.tray_manager = _FakeTray()
    app._update_checking = True
    monkeypatch.setattr(uc, 'current_version', lambda: '1.8.0')
    monkeypatch.setattr(
        uc, 'check_for_update', lambda *a, **k: uc.ReleaseInfo('1.9.0', 'url'))

    # 用例在主线程直调 worker：qt_dispatcher 判定已在主线程即同步回投
    app._check_update_worker(False)

    assert len(app.tray_manager.notifications) == 1, "检查结果没有推送"
    assert app._update_checking is False, "检查结束后忙碌标记没有复位"


def test_auto_check_failure_is_silent(tmp_path, monkeypatch):
    """自动检查失败只写日志，不弹窗打扰；手动检查才提示"""
    from main import MainApp

    app = MainApp()
    app.config_manager = _config(tmp_path)
    app.tray_manager = _FakeTray()
    app._update_checking = True

    def fail(*args, **kwargs):
        raise RuntimeError('网络不可达')

    monkeypatch.setattr(uc, 'check_for_update', fail)
    app._check_update_worker(False)

    assert app.tray_manager.notifications == [], "自动检查失败不该弹托盘通知"
    assert app._update_checking is False


def test_interactive_check_opens_release_page(tmp_path, monkeypatch):
    """手动检查发现新版，确认后打开发布页"""
    from main import MainApp

    app = MainApp()
    app.config_manager = _config(tmp_path)
    app._update_checking = True
    monkeypatch.setattr(uc, 'current_version', lambda: '1.8.0')

    opened = []
    monkeypatch.setattr(main.webbrowser, 'open', lambda url: opened.append(url))
    monkeypatch.setattr(app, '_prompt_open_release_page', lambda info: True)

    info = uc.ReleaseInfo('1.9.0', 'https://example.test/release')
    app._apply_update_result(info, '', True)
    assert opened == ['https://example.test/release'], "确认后没有打开发布页"


def test_tray_menu_check_update_is_interactive():
    """托盘「检查更新」走交互式检查，能看到结果"""
    from managers.tray import SystemTrayManager

    class FakeApp:
        def __init__(self):
            self.calls = []

        def check_updates(self, interactive=False):
            self.calls.append(interactive)

    tray = SystemTrayManager(main_app=FakeApp())
    tray._on_check_update()
    assert tray.main_app.calls == [True]
