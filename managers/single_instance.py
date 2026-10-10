#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进程单实例守卫

Windows 用命名互斥量判定：第一个实例拿到句柄并一直持有，后来的实例
`CreateMutexW` 同样成功，但 `GetLastError()` 是 `ERROR_ALREADY_EXISTS`——
这说明"已经有别的进程在跑"。句柄必须留在守卫对象里不关：互斥量随最后
一个句柄关闭而消失，句柄一被 GC 就等于悄悄放锁，多开又回来了。

为什么要在注册热键之前判定：两个实例都想要 alt+z 时，后起的那个只能退到
键盘钩子（甚至注册失败），托盘上还叠出两个图标，用户看到的就是
"快捷键时灵时不灵、图标有两个"。
"""

import ctypes
import logging
import os
import sys

logger = logging.getLogger('screenshot-translation')

ERROR_ALREADY_EXISTS = 183

# Local\ 命名空间按登录会话隔离：同机不同用户各跑一份是合理的，
# 全局命名空间反而会让切换用户后的新会话被前一个会话顶掉。
MUTEX_NAME = 'Local\\ScreenshotTranslator.SingleInstance'

# 开发/测试用：pytest 的冒烟用例会在正式实例在跑时再拉一个 main.py
ALLOW_MULTI_ENV = 'ST_ALLOW_MULTI_INSTANCE'

ALREADY_RUNNING_TEXT = (
    "截图翻译已经在运行中（系统托盘里有它的图标）。\n\n"
    "同一时间只能开一个实例：请直接使用托盘图标或快捷键 Alt+Z，"
    "不要重复启动。")


class SingleInstanceGuard:
    """一个命名互斥量对应一个守卫实例（默认全局只有一个）"""

    def __init__(self, name=MUTEX_NAME):
        """
        Args:
            name (str): 互斥量名；测试用独立名字，避免和真实实例抢锁
        """
        self.name = name
        self._handle = None

    def acquire(self):
        """
        抢占单实例锁

        Returns:
            bool: 当前进程是否被允许继续运行。非 Windows、环境变量放行、
                或系统调用本身失败时都返回 True——守卫失灵不该让程序
                连启动都启动不了。
        """
        if self._handle is not None:
            return True
        if os.environ.get(ALLOW_MULTI_ENV) == '1':
            logger.info(f"已允许多实例（{ALLOW_MULTI_ENV}=1）")
            return True
        if sys.platform != 'win32':
            return True

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.GetLastError.restype = ctypes.c_int
        # CloseHandle 的入参也必须声明：CreateMutexW 返回的是 64 位句柄，
        # 没有 argtypes 时 ctypes 按 C int（32 位）传，句柄高位被砍掉，
        # 关的其实是个无效值——真正的句柄永远漏着
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        try:
            handle = kernel32.CreateMutexW(None, False, self.name)
        except Exception as e:
            logger.warning(f"创建单实例互斥量异常，跳过多实例检查: {e}")
            return True
        if not handle:
            logger.warning("创建单实例互斥量失败，跳过多实例检查")
            return True

        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            try:
                kernel32.CloseHandle(handle)
            except Exception as e:
                logger.debug(f"关闭单实例句柄失败: {type(e).__name__}")
            logger.warning("检测到截图翻译已在运行，本进程不再启动")
            return False

        self._handle = handle
        return True


# 生产路径用的那一个：句柄活到进程结束
_default_guard = SingleInstanceGuard()


def acquire():
    """默认互斥量名的单实例检查，见 `SingleInstanceGuard.acquire`"""
    return _default_guard.acquire()


def notify_already_running():
    """
    告诉用户"程序已经在跑了"

    用 Win32 原生消息框而不是 QMessageBox：走到这一步时 Qt 事件循环还没起来，
    而打包后的 exe 是窗口程序（没有控制台），不弹框用户只会以为双击没反应。
    """
    try:
        if sys.platform == 'win32':
            ctypes.windll.user32.MessageBoxW(
                None, ALREADY_RUNNING_TEXT, "截图翻译", 0x00000000 | 0x00000040)
        else:
            print(ALREADY_RUNNING_TEXT)
    except Exception as e:
        logger.warning(f"显示单实例提示失败: {e}")
