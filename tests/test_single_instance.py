#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单实例守卫回归测试

多开的后果是实打实的：第二个实例抢不到 alt+z（只能退到键盘钩子）、托盘上
两个图标，用户看到的就是"快捷键时灵时不灵"。这里盯住三件事：同名互斥量必须
拦住后来者、句柄要一直留着（一放锁就等于没守卫）、放行开关只对显式设了环境
变量的进程生效。
"""

import ctypes
import sys
import uuid

import pytest

from managers import single_instance as si

needs_win32 = pytest.mark.skipif(
    sys.platform != 'win32', reason='命名互斥量是 Windows 机制')


def _unique_name():
    """每次给一个没用过的名字，避免和真实实例或别的用例抢锁"""
    return f'Local\\StTestMutex-{uuid.uuid4().hex}'


@needs_win32
def test_second_guard_with_same_name_is_rejected():
    name = _unique_name()
    first = si.SingleInstanceGuard(name)
    assert first.acquire() is True, "首个持有者应被放行"

    assert si.SingleInstanceGuard(name).acquire() is False, \
        "同名互斥量已被持有，第二个实例必须被拦下"

    # 放掉句柄后才允许下一个接手：证明拦下来的是"锁还在别人手里"，
    # 而不是名字拼错导致每次都算冲突
    ctypes.windll.kernel32.CloseHandle(first._handle)
    assert si.SingleInstanceGuard(name).acquire() is True


@needs_win32
def test_acquire_is_idempotent_and_keeps_the_handle():
    guard = si.SingleInstanceGuard(_unique_name())
    assert guard.acquire() is True
    handle = guard._handle
    assert handle, "句柄必须留在守卫对象里：一被回收互斥量就跟着没了"
    assert guard.acquire() is True
    assert guard._handle == handle, "重复 acquire 不该换掉正在持有的锁"


def test_multi_instance_env_var_lets_the_guard_step_aside(monkeypatch):
    """冒烟用例与开发调试要能同时开多份：显式设了开关就必须放行"""
    name = _unique_name()
    assert si.SingleInstanceGuard(name).acquire() is True
    monkeypatch.setenv(si.ALLOW_MULTI_ENV, '1')
    assert si.SingleInstanceGuard(name).acquire() is True, \
        "开关生效时不该再拦后来者"


def test_already_running_message_points_at_the_tray():
    """提示要给出下一步动作（去托盘），而不是只说“已存在”"""
    assert '托盘' in si.ALREADY_RUNNING_TEXT
    assert 'Alt+Z' in si.ALREADY_RUNNING_TEXT
