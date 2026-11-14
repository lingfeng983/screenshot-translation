#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发版脚本的只读安全用例

覆盖：预检排在所有写操作之前（脏工作树、缺 [Unreleased]、tag 不一致）、
CHANGELOG 版本化先于写版本号与构建、git 提交只用显式清单、安装包流式上传。
不执行构建、不碰真实 git 远端、不调用 GitHub API。
"""

import importlib.util
import logging
import os
import sys
import types
from urllib.error import HTTPError

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_script():
    """每次加载独立的模块对象，避免 monkeypatch 在用例间串味"""
    path = os.path.join(PROJECT_ROOT, 'scripts', 'publish_release.py')
    spec = importlib.util.spec_from_file_location(
        'publish_release_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_preflight(mod, monkeypatch, tmp_path, changelog):
    """把预检需要的只读依赖指向临时目录"""
    monkeypatch.setattr(mod, 'PROJECT_ROOT', str(tmp_path))
    with open(os.path.join(str(tmp_path), 'CHANGELOG.md'), 'w',
              encoding='utf-8') as handle:
        handle.write(changelog)
    os.makedirs(os.path.join(str(tmp_path), 'managers'), exist_ok=True)
    with open(os.path.join(str(tmp_path), 'managers', 'app_version.py'), 'w',
              encoding='utf-8') as handle:
        handle.write("APP_VERSION = '1.0.0'\n")
    monkeypatch.setattr(mod, 'check_origin', lambda: None)
    monkeypatch.setattr(mod, 'git_output', lambda args: '')
    monkeypatch.delenv('ST_SIGNTOOL_COMMAND', raising=False)


def args(**overrides):
    values = dict(version='1.2.0', skip_tests=True, no_build=False,
                  no_api=True, only_release=False)
    values.update(overrides)
    return types.SimpleNamespace(**values)


def test_preflight_rejects_dirty_tree_without_writing(monkeypatch, tmp_path):
    """工作区有未提交/未跟踪改动时立即退出，且没动任何文件"""
    mod = load_script()
    changelog = '# 更新日志\n\n## [Unreleased]\n\n- 待发\n'
    prepare_preflight(mod, monkeypatch, tmp_path, changelog)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return types.SimpleNamespace(
            stdout=' M main.py\n?? managers/new.py\n', returncode=0)

    monkeypatch.setattr(mod.subprocess, 'run', fake_run)

    try:
        mod.preflight('1.2.0', args())
        assert False, '脏工作树不该通过预检'
    except SystemExit as exc:
        assert '工作区不干净' in str(exc)
    assert any('status' in cmd for cmd in calls), '没有检查工作树状态'
    # 预检是只读的：CHANGELOG 与版本号文件保持原样
    with open(os.path.join(str(tmp_path), 'CHANGELOG.md'), encoding='utf-8') as f:
        assert f.read() == changelog
    with open(os.path.join(str(tmp_path), 'managers', 'app_version.py'),
              encoding='utf-8') as f:
        assert f.read() == "APP_VERSION = '1.0.0'\n"


def test_preflight_rejects_missing_unreleased_before_git_status(monkeypatch, tmp_path):
    """缺 [Unreleased] 的检查排在 git 状态之前，失败时不产生任何子进程副作用"""
    mod = load_script()
    prepare_preflight(mod, monkeypatch, tmp_path,
                      '# 更新日志\n\n## [v1.1.0] — 2026-01-01\n\n- 旧版\n')
    calls = []
    monkeypatch.setattr(mod.subprocess, 'run',
                        lambda cmd, **kw: calls.append(cmd) or
                        types.SimpleNamespace(stdout='', returncode=0))
    try:
        mod.preflight('1.2.0', args())
        assert False, '没有 [Unreleased] 段不该通过预检'
    except SystemExit as exc:
        assert 'CHANGELOG.md' in str(exc)
    assert calls == [], 'CHANGELOG 预检失败却已经开始跑 git 命令'


def test_preflight_rejects_tag_pointing_elsewhere(monkeypatch, tmp_path):
    """tag 指向别的提交时要在写操作之前拦下"""
    mod = load_script()
    prepare_preflight(mod, monkeypatch, tmp_path,
                      '# 更新日志\n\n## [Unreleased]\n\n- 待发\n')
    monkeypatch.setattr(mod.subprocess, 'run',
                        lambda cmd, **kw: types.SimpleNamespace(stdout='', returncode=0))
    monkeypatch.setattr(
        mod, 'git_output',
        lambda argv: ('a' * 40 if argv[:3] == ['git', 'rev-list', '-n']
                      else 'b' * 40 if argv[:2] == ['git', 'rev-parse'] else ''))
    try:
        mod.preflight('1.2.0', args())
        assert False, 'tag 与 HEAD 不一致不该通过预检'
    except SystemExit as exc:
        assert 'tag' in str(exc)


def test_main_versions_changelog_before_any_build(monkeypatch, tmp_path):
    """CHANGELOG 版本化排在写版本号/构建/出包之前（写操作顺序）"""
    mod = load_script()
    order = []
    for name in ('preflight', 'step_tests', 'step_changelog', 'step_app_version',
                 'step_build', 'step_assemble', 'step_sign', 'step_git',
                 'run_release_step'):
        def make(step=name):
            def record(*a, **k):
                order.append(step)
                return '- 说明' if step == 'step_changelog' else None
            return record
        monkeypatch.setattr(mod, name, make())
    monkeypatch.setattr(mod, 'read_token', lambda: '')
    monkeypatch.setattr(sys, 'argv',
                        ['publish_release.py', '--version', '1.2.0',
                         '--skip-tests', '--no-api'])
    assert mod.main() == 0
    assert order.index('preflight') == 0
    assert order.index('step_changelog') < order.index('step_app_version')
    assert order.index('step_app_version') < order.index('step_build')
    assert order.index('step_assemble') < order.index('step_git')


def test_step_git_adds_only_explicit_files(monkeypatch):
    """发版提交不再 git add -A，避免把未审查的在制品一起发布"""
    mod = load_script()
    calls = []
    monkeypatch.setattr(mod, 'run', lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(mod, 'git_output', lambda argv: '')
    monkeypatch.setattr(mod, 'current_branch', lambda: 'main')
    monkeypatch.setattr(
        mod.subprocess, 'run',
        lambda cmd, **kw: types.SimpleNamespace(returncode=0))
    mod.step_git('1.2.0', 'v1.2.0')
    assert calls[0][:3] == ['git', 'add', '--']
    assert '-A' not in calls[0]
    assert 'CHANGELOG.md' in calls[0] and 'managers/app_version.py' in calls[0]
    assert all('-A' not in cmd for cmd in calls)


def test_stream_upload_sends_chunks_without_buffering(monkeypatch, tmp_path):
    """安装包按块 send，Content-Length 由文件大小给出，令牌只放请求头"""
    import http.client

    mod = load_script()
    payload = b'MZ' + b'x' * (600 * 1024)
    artifact = tmp_path / 'ScreenshotTranslator-Setup.exe'
    artifact.write_bytes(payload)

    sent = []
    headers = {}

    class _FakeResponse:
        status = 201
        headers = {}

        def read(self):
            return b'{"name":"ScreenshotTranslator-Setup.exe","size":3}'

    class _FakeConnection:
        def __init__(self, host, port, timeout=None):
            headers['host'] = host
            headers['timeout'] = timeout

        def putrequest(self, method, target):
            headers['method'] = method
            headers['target'] = target

        def putheader(self, key, value):
            headers[key] = value

        def endheaders(self):
            headers['endheaders'] = True

        def send(self, chunk):
            sent.append(chunk)

        def getresponse(self):
            return _FakeResponse()

        def close(self):
            headers['closed'] = True

    monkeypatch.setattr(http.client, 'HTTPSConnection', _FakeConnection)
    result = mod.stream_upload(
        'https://uploads.github.com/repos/x/releases/1/assets?name=setup.exe',
        str(artifact), 'TOKEN', timeout=120)

    assert result['name'] == 'ScreenshotTranslator-Setup.exe'
    assert b''.join(sent) == payload
    assert len(sent) > 1, '没有分块，等于又把整包读进了内存'
    assert headers['Content-Length'] == str(len(payload))
    assert headers['Authorization'] == 'Bearer TOKEN'
    assert headers['method'] == 'POST'
    assert 'setup.exe' in headers['target']
    assert headers['closed'] is True
