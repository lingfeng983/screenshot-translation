#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新检查用例

覆盖版本比较、GitHub 响应解析（含安装包附件）、自动检查开关、托盘推送去重、
手动检查确认后自动下载与安装。网络请求全部用假响应/假函数替换，离线可跑。
"""

import os
import urllib.request

import pytest

import main
from managers import update_checker as uc
from managers import update_installer as ui

# 真连 GitHub 的用例默认不跑（与其他模块同一套开关）
ONLINE = os.environ.get('ST_RUN_ONLINE') == '1'
online = pytest.mark.skipif(not ONLINE, reason='需要真实网络：ST_RUN_ONLINE=1 才跑')


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


class _FakeStream:
    """流式下载响应的替身（支持 with 语法与 iter_content）"""

    def __init__(self, chunks, total=None, status_error=None):
        self._chunks = list(chunks)
        self.headers = {'Content-Length': str(total)} if total is not None else {}
        self._status_error = status_error

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def raise_for_status(self):
        """按需模拟 HTTP 错误"""
        if self._status_error:
            raise self._status_error

    def iter_content(self, size):
        """逐块吐出预设数据"""
        for chunk in self._chunks:
            yield chunk


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
    """GitHub 的 tag_name/html_url/body/assets 映射到 ReleaseInfo"""
    import requests

    response = _FakeResponse({
        'tag_name': 'v1.9.0',
        'html_url': 'https://example.test/release',
        'body': '- 新功能',
        'assets': [
            {'name': 'ScreenshotTranslator-Setup.exe',
             'browser_download_url': 'https://example.test/setup.exe',
             'size': 12345},
            {'name': 'notes.txt',
             'browser_download_url': 'https://example.test/notes.txt',
             'size': 10},
        ],
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
    # 附件里挑出安装包，供程序自己下载
    assert info.asset_url == 'https://example.test/setup.exe'
    assert info.asset_name == 'ScreenshotTranslator-Setup.exe'
    assert info.asset_size == 12345
    assert seen['url'] == uc.API_URL
    assert 'ScreenshotTranslator/' in seen['headers']['User-Agent']


def test_pick_installer_asset_prefers_setup_exe():
    """附件选择：优先 setup 命名的 exe，退而取第一个 exe，全都不是 exe 则空"""
    assert uc.pick_installer_asset([]) is None
    assert uc.pick_installer_asset([{'name': 'readme.md'}]) is None
    picked = uc.pick_installer_asset([
        {'name': 'portable.exe', 'browser_download_url': 'a'},
        {'name': 'ScreenshotTranslator-Setup.exe', 'browser_download_url': 'b'},
    ])
    assert picked['browser_download_url'] == 'b'
    fallback = uc.pick_installer_asset([
        {'name': 'portable.zip'}, {'name': 'app.exe', 'browser_download_url': 'c'}])
    assert fallback['browser_download_url'] == 'c'


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

    info = uc.ReleaseInfo('1.9.0', 'https://example.test/release',
                          asset_url='https://example.test/setup.exe')
    app._apply_update_result(info, '', False)
    assert len(app.tray_manager.notifications) == 1, "发现新版没有推送托盘通知"
    title, message = app.tray_manager.notifications[0]
    assert '1.9.0' in title and '1.8.0' in message
    assert app.config_manager.get_last_notified_version() == '1.9.0'

    app._update_checking = True
    app._apply_update_result(info, '', False)
    assert len(app.tray_manager.notifications) == 1, "同一版本被重复推送"


def test_auto_check_skips_release_without_installer(tmp_path, monkeypatch):
    """没有安装包附件的版本不推更新提醒（推了也装不上）"""
    from main import MainApp

    app = MainApp()
    app.config_manager = _config(tmp_path)
    app.tray_manager = _FakeTray()
    app._update_checking = True
    monkeypatch.setattr(uc, 'current_version', lambda: '1.8.0')

    app._apply_update_result(uc.ReleaseInfo('1.9.0'), '', False)
    assert app.tray_manager.notifications == []
    assert app.config_manager.get_last_notified_version() != '1.9.0'


def test_worker_runs_check_and_pushes(tmp_path, monkeypatch):
    """后台检查线程返回新版后，结果回主线程推送通知"""
    from main import MainApp

    app = MainApp()
    app.config_manager = _config(tmp_path)
    app.tray_manager = _FakeTray()
    app._update_checking = True
    monkeypatch.setattr(uc, 'current_version', lambda: '1.8.0')
    monkeypatch.setattr(
        uc, 'check_for_update',
        lambda *a, **k: uc.ReleaseInfo('1.9.0', 'url',
                                       asset_url='https://example.test/setup.exe'))

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


def test_interactive_check_starts_in_app_update(tmp_path, monkeypatch):
    """手动检查发现新版，同意后由程序自己下载安装（不再打开发布页）"""
    from main import MainApp

    app = MainApp()
    app.config_manager = _config(tmp_path)
    app._update_checking = True
    monkeypatch.setattr(uc, 'current_version', lambda: '1.8.0')
    monkeypatch.setattr(app, '_prompt_install_update', lambda info: True)
    started = []
    monkeypatch.setattr(app, '_start_update_download', started.append)

    info = uc.ReleaseInfo('1.9.0', 'https://example.test/release',
                          asset_url='https://example.test/setup.exe')
    app._apply_update_result(info, '', True)
    assert started == [info], "同意更新后没有开始下载安装包"


def test_interactive_check_declined_does_nothing(tmp_path, monkeypatch):
    """选了「稍后」就不下载、也不打开任何页面"""
    from main import MainApp

    app = MainApp()
    app.config_manager = _config(tmp_path)
    app._update_checking = True
    monkeypatch.setattr(uc, 'current_version', lambda: '1.8.0')
    monkeypatch.setattr(app, '_prompt_install_update', lambda info: False)
    started = []
    monkeypatch.setattr(app, '_start_update_download', started.append)

    app._apply_update_result(
        uc.ReleaseInfo('1.9.0', asset_url='https://example.test/setup.exe'), '', True)
    assert started == []


def test_interactive_check_without_asset_only_informs(tmp_path, monkeypatch):
    """发行版没挂安装包：只说明情况，不弹「立即更新」也不再索要下载地址"""
    from PyQt5.QtWidgets import QMessageBox

    from main import MainApp

    app = MainApp()
    app.config_manager = _config(tmp_path)
    app._update_checking = True
    monkeypatch.setattr(uc, 'current_version', lambda: '1.8.0')
    prompts = []
    monkeypatch.setattr(app, '_prompt_install_update', lambda info: prompts.append(info) or True)
    infos = []
    monkeypatch.setattr(
        QMessageBox, 'information',
        staticmethod(lambda *args, **kwargs: infos.append(args[2]) or QMessageBox.Ok))

    app._apply_update_result(uc.ReleaseInfo('1.9.0'), '', True)
    assert prompts == [], "没有安装包还弹了更新确认"
    assert infos and '1.9.0' in str(infos[0])


def test_update_without_asset_warns_and_skips_download(tmp_path, monkeypatch):
    """发行版没挂安装包时只提示，不发起下载"""
    from PyQt5.QtWidgets import QMessageBox

    from main import MainApp

    app = MainApp()
    warnings = []
    monkeypatch.setattr(
        QMessageBox, 'warning',
        staticmethod(lambda *args, **kwargs: warnings.append(args[2])
                     or QMessageBox.Ok))
    submitted = []
    monkeypatch.setattr(main.thread_pool_manager, 'submit',
                        lambda *a, **k: submitted.append(a) or object())

    app._start_update_download(uc.ReleaseInfo('1.9.0'))
    assert submitted == [], "没有安装包却发起下载"
    assert warnings and '9.0' in str(warnings[0]), "缺少可读提示"
    assert app._update_downloading is False


def test_download_writes_installer_and_reports_progress(tmp_path, monkeypatch):
    """下载：按块写入、按 Content-Length 上报进度、校验 MZ 头"""
    import requests

    payload = b'MZ' + b'\x00' * 510
    response = _FakeStream([payload[:256], payload[256:]], total=len(payload))
    seen = {}

    def fake_get(url, stream=None, timeout=None, headers=None):
        seen.update(url=url, stream=stream)
        return response

    monkeypatch.setattr(requests, 'get', fake_get)

    progress = []
    info = uc.ReleaseInfo('1.9.0', asset_url='https://example.test/setup.exe',
                          asset_name='ScreenshotTranslator-Setup.exe',
                          asset_size=len(payload))
    path = ui.download(info, directory=str(tmp_path),
                       progress=lambda d, t: progress.append((d, t)))

    assert seen['url'] == 'https://example.test/setup.exe'
    assert seen['stream'] is True, "必须流式下载，否则大包会整块进内存"
    assert os.path.basename(path) == 'ScreenshotTranslator-Setup.exe'
    with open(path, 'rb') as handle:
        assert handle.read() == payload
    assert progress[-1] == (len(payload), len(payload)), "进度没有走到 100%"


def test_download_requires_asset(tmp_path):
    """没有安装包直链时给出可读错误"""
    with pytest.raises(ui.UpdateError):
        ui.download(uc.ReleaseInfo('1.9.0'), directory=str(tmp_path))


def test_download_cancel_removes_partial_file(tmp_path, monkeypatch):
    """取消下载：抛 UpdateCancelled，且不留半截文件"""
    import threading as _threading

    import requests

    class _CancelAfterFirst:
        """第一块之后就把 cancel 置位"""

        def __init__(self):
            self.headers = {'Content-Length': '512'}

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def raise_for_status(self):
            """永远成功"""

        def iter_content(self, size):
            """先给一块，再触发取消"""
            yield b'MZ' + b'\x00' * 254
            cancel.set()
            yield b'\x00' * 256

    cancel = _threading.Event()
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _CancelAfterFirst())

    with pytest.raises(ui.UpdateCancelled):
        ui.download(uc.ReleaseInfo('1.9.0', asset_url='https://example.test/s.exe',
                                   asset_name='s.exe', asset_size=512),
                    directory=str(tmp_path), cancel=cancel)
    assert os.listdir(tmp_path) == [], "取消后残留了半截安装包"


def test_download_rejects_non_executable(tmp_path, monkeypatch):
    """拿到的是错误页（不是 MZ 头）时判失败，不能拿去安装"""
    import requests

    monkeypatch.setattr(requests, 'get',
                        lambda *a, **k: _FakeStream([b'<html>rate limited</html>']))
    with pytest.raises(ui.UpdateError):
        ui.download(uc.ReleaseInfo('1.9.0', asset_url='https://example.test/s.exe',
                                   asset_name='s.exe'),
                    directory=str(tmp_path))


def test_download_falls_back_to_system_trust_on_cert_error(tmp_path, monkeypatch):
    """requests 证书链校验失败时改用 urllib（系统信任库），不跳过校验"""
    import requests

    payload = b'MZ' + b'\x00' * 300

    class _FakeUrlopen:
        """urllib 响应的替身"""

        def __init__(self, data):
            self._data = data
            self._pos = 0
            self.headers = {'Content-Length': str(len(data))}

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self, size):
            chunk = self._data[self._pos:self._pos + size]
            self._pos += len(chunk)
            return chunk

    def fail(*args, **kwargs):
        raise requests.exceptions.SSLError(
            'certificate verify failed: unable to get local issuer certificate')

    monkeypatch.setattr(requests, 'get', fail)
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: _FakeUrlopen(payload))

    info = uc.ReleaseInfo('1.9.0', asset_url='https://example.test/s.exe',
                          asset_name='s.exe', asset_size=len(payload))
    path = ui.download(info, directory=str(tmp_path))
    with open(path, 'rb') as handle:
        assert handle.read() == payload


def test_download_rejects_digest_mismatch(tmp_path, monkeypatch):
    """发行版给了 sha256 就按它校验，对不上直接丢弃（自动装的东西必须是发布的那份）"""
    import hashlib

    import requests

    payload = b'MZ' + b'\x00' * 64
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _FakeStream([payload]))
    info = uc.ReleaseInfo(
        '1.9.0', asset_url='https://example.test/s.exe', asset_name='s.exe',
        asset_size=len(payload),
        asset_digest='sha256:' + hashlib.sha256(b'other').hexdigest())
    with pytest.raises(ui.UpdateError):
        ui.download(info, directory=str(tmp_path))
    assert os.listdir(tmp_path) == [], "校验失败的包没有清掉"

    # 摘要对得上时正常通过
    good = uc.ReleaseInfo(
        '1.9.0', asset_url='https://example.test/s.exe', asset_name='s.exe',
        asset_size=len(payload),
        asset_digest='sha256:' + hashlib.sha256(payload).hexdigest())
    path = ui.download(good, directory=str(tmp_path))
    assert os.path.getsize(path) == len(payload)


def test_launch_installer_uses_silent_flags(tmp_path, monkeypatch):
    """安装程序按静默+关闭本程序的参数启动"""
    installer = tmp_path / 'ScreenshotTranslator-Setup.exe'
    installer.write_bytes(b'MZ' + b'\x00' * 10)
    seen = {}

    class _FakePopen:
        pid = 4321

        def __init__(self, argv, **kwargs):
            seen['argv'] = argv

    monkeypatch.setattr(ui.subprocess, 'Popen', _FakePopen)
    ui.launch(str(installer))

    argv = seen['argv']
    assert argv[0].endswith('ScreenshotTranslator-Setup.exe')
    for flag in ('/SILENT', '/CLOSEAPPLICATIONS'):
        assert flag in argv, f"缺少安装参数 {flag}"


def test_install_update_launches_setup_then_quits(tmp_path, monkeypatch):
    """下载成功后：启动安装程序，并安排退出本程序（让 Setup 覆盖文件）"""
    from main import MainApp

    app = MainApp()
    launched = []
    monkeypatch.setattr(ui, 'launch', launched.append)
    scheduled = []
    monkeypatch.setattr(main.QTimer, 'singleShot',
                        staticmethod(lambda ms, fn: scheduled.append((ms, fn))))

    app._install_update('/tmp/ScreenshotTranslator-Setup.exe')
    assert launched == ['/tmp/ScreenshotTranslator-Setup.exe']
    assert scheduled and scheduled[0][1] == app.quit, "没有安排退出本体"


def test_download_failure_warns_and_resets(tmp_path, monkeypatch):
    """下载失败：提示可读错误，并把下载状态复位以便重试"""
    from PyQt5.QtWidgets import QMessageBox

    from main import MainApp

    app = MainApp()
    app._update_downloading = True
    warnings = []
    monkeypatch.setattr(
        QMessageBox, 'warning',
        staticmethod(lambda *args, **kwargs: warnings.append(args[2])
                     or QMessageBox.Ok))

    app._update_download_done('', '下载失败：连接被重置')
    assert warnings and '连接被重置' in str(warnings[0])
    assert app._update_downloading is False, "失败后没有复位，用户无法重试"


@online
def test_online_latest_release_has_installer_asset():
    """真实 GitHub 发行版必须挂在安装包，自动更新才有东西可下"""
    info = uc.fetch_latest_release()
    assert info.asset_url, "最新发行版没有安装包附件"
    assert info.asset_size > 0


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
