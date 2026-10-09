#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows 原生全局快捷键后端（RegisterHotKey）

为什么不只用键盘钩子（keyboard 库）：低级键盘钩子由 Windows 在回调超时
（LowLevelHooksTimeout，默认 300ms）时**静默摘掉**，钩子一没，快捷键就再也
收不到按键，而且没有任何报错，只能重启程序；锁屏/远程桌面切换、UAC 提权
窗口获得焦点等场景同样会让钩子收不到键。这些都不是调用方能捕获的异常，
用户看到的现象就是"快捷键经常失效"。

RegisterHotKey 由系统在解析按键后直接投递 WM_HOTKEY 到本进程的消息队列，
没有钩子超时这一说，也不需要额外依赖。快捷键串的写法与 keyboard 库一致
（'alt+z'、'Ctrl+Shift+A'），因此两个后端可以互换。
"""

import ctypes
import logging
import sys

from ctypes import wintypes

from PyQt5.QtCore import QAbstractNativeEventFilter
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger('screenshot-translation')

# RegisterHotKey 修饰键掩码
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
# 长按不重复触发（只按一次就投一次 WM_HOTKEY）
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

# 本进程的热键ID：WM_HOTKEY 的 wParam 用它区分，取值本身无含义
HOTKEY_ID = 0x4A5A

# RegisterHotKey 失败的常见 GetLastError 码
ERROR_HOTKEY_ALREADY_REGISTERED = 1400   # 已被其它进程注册
ERROR_INVALID_PARAMETER = 87             # 组合本身非法（如与其它进程重复注册同款）
ERROR_ALREADY_REGISTERED = 1409          # 本机实测跨进程占用返回的是这个码

# 修饰键别名
_MODIFIER_ALIASES = {
    'alt': MOD_ALT,
    'ctrl': MOD_CONTROL,
    'control': MOD_CONTROL,
    'shift': MOD_SHIFT,
    'win': MOD_WIN,
    'windows': MOD_WIN,
    'super': MOD_WIN,
    'meta': MOD_WIN,
}

# 具名键 -> 虚拟键码（单字符键交给 VkKeyScanW，跟随当前键盘布局）
_NAMED_KEYS = {
    'space': 0x20, 'enter': 0x0D, 'return': 0x0D, 'tab': 0x09, 'esc': 0x1B,
    'escape': 0x1B, 'backspace': 0x08, 'delete': 0x2E, 'insert': 0x2D,
    'home': 0x24, 'end': 0x23, 'page up': 0x21, 'pageup': 0x21,
    'page down': 0x22, 'pagedown': 0x22, 'up': 0x26, 'down': 0x28,
    'left': 0x25, 'right': 0x27, 'pause': 0x13, 'caps lock': 0x14,
    '`': 0xC0, 'grave': 0xC0, '-': 0xBD, 'minus': 0xBD, '=': 0xBB,
    'plus': 0xBB, 'equal': 0xBB, ',': 0xBC, 'comma': 0xBC, '.': 0xBE,
    'period': 0xBE, '/': 0xBF, 'slash': 0xBF, ';': 0xBA, 'semicolon': 0xBA,
    "'": 0xDE, 'quote': 0xDE, '\\': 0xDC, 'backslash': 0xDC,
    '[': 0xDB, ']': 0xDD,
}
for _index in range(1, 25):
    _NAMED_KEYS[f'f{_index}'] = 0x6F + _index   # F1 = 0x70 ... F24 = 0x87
del _index

_user32 = None
_user32_last_err = None


def user32():
    """
    取 user32 句柄（非 Windows 返回 None，调用方据此整体降级）

    Returns:
        user32 句柄或 None
    """
    global _user32
    if _user32 is None:
        if sys.platform != 'win32':
            return None
        _user32 = ctypes.windll.user32
    return _user32


def user32_error_capturing():
    """
    带错误码捕获的 user32 句柄，专给 RegisterHotKey 用

    use_last_error=True 让 ctypes 在调用点把系统错误码抄进线程局部存储，
    之后隔多少无关 API 都不会丢——翻译冲突提示靠的就是这个码。
    """
    global _user32_last_err
    if _user32_last_err is None:
        if sys.platform != 'win32':
            return None
        _user32_last_err = ctypes.WinDLL('user32', use_last_error=True)
    return _user32_last_err


def parse_hotkey(hotkey):
    """
    解析快捷键字符串

    要求"至少一个修饰键 + 一个主键"：不带修饰键的热键会被 RegisterHotKey
    注册成全局按键，把该键从所有程序里抢走。

    Args:
        hotkey (str): 快捷键字符串，如 'alt+z'、'Ctrl + Shift + A'

    Returns:
        tuple[int, int] 或 None: (修饰键掩码, 虚拟键码)；无法解析时 None
    """
    if not hotkey or not isinstance(hotkey, str):
        return None

    modifiers = 0
    main_key = None
    for part in (p.strip().lower() for p in hotkey.split('+')):
        if not part:
            continue
        if part in _MODIFIER_ALIASES:
            modifiers |= _MODIFIER_ALIASES[part]
            continue
        if main_key is not None:
            return None      # 两个主键不是合法组合
        main_key = part

    if not modifiers or main_key is None:
        return None

    vk_code = _key_to_vk(main_key)
    if vk_code is None:
        return None
    return modifiers | MOD_NOREPEAT, vk_code


def _key_to_vk(key_name):
    """
    键名转虚拟键码

    单字符键交给 VkKeyScanW：它跟随当前键盘布局，用户在非美式布局下
    也能用自己键盘上的那个键（对应键盘库的 'z' 这类写法）。

    Args:
        key_name (str): 小写键名

    Returns:
        int 或 None: 虚拟键码；无法识别时 None
    """
    if key_name in _NAMED_KEYS:
        return _NAMED_KEYS[key_name]

    if len(key_name) == 1:
        handle = user32()
        if handle is not None:
            scan = handle.VkKeyScanW(ord(key_name))
            if scan != -1:
                return scan & 0xFF
        if key_name.isascii() and key_name.isalnum():
            return ord(key_name.upper())
    return None


def _is_windows_msg_event(event_type):
    """Qt 传进来的事件类型是否为 Windows 原生消息（PyQt5 给 bytes）"""
    return event_type in (b'windows_generic_MSG', 'windows_generic_MSG')


class _HotkeyEventFilter(QAbstractNativeEventFilter):
    """
    把 WM_HOTKEY 转成回调的原生事件过滤器

    回调异常绝不外抛：这个过滤器挂在 QApplication 上，异常逃出去会顺着
    Qt 事件分发链一路抛，把事件循环搞乱。
    """

    def __init__(self, hotkey_id, callback):
        """
        Args:
            hotkey_id (int): 本进程的热键ID
            callback: 无参回调
        """
        super().__init__()
        self._hotkey_id = hotkey_id
        self._callback = callback

    def nativeEventFilter(self, event_type, message):
        """识别 WM_HOTKEY 并触发回调，其余消息原样放行"""
        try:
            if _is_windows_msg_event(event_type):
                msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == WM_HOTKEY and int(msg.wParam) == self._hotkey_id:
                    self._callback()
                    return True, 0
        except Exception as e:
            logger.error(f"处理快捷键消息失败: {e}")
        return False, 0


class NativeHotkeyBackend:
    """
    Windows 原生快捷键后端

    register() 失败（快捷键被别的程序占用、路径不可用等）时返回 False，
    由调用方回退到键盘钩子后端。
    """

    name = 'native'

    def __init__(self, user32_handle=None):
        """
        Args:
            user32_handle: user32 句柄，None 时按平台自动获取
                （测试注入假句柄用，非 Windows 上为 None）
        """
        self._user32_handle = user32_handle
        self._callback = None
        self._filter = None
        self._app = None
        self._registered = False
        # 最近一次失败原因，供上层提示用户
        self.last_error = ''

    def _handle(self):
        """取得 user32 句柄"""
        return self._user32_handle if self._user32_handle is not None else user32()

    @property
    def available(self):
        """当前环境是否支持原生注册（Windows 且能取到 user32）"""
        try:
            return self._handle() is not None
        except Exception:
            return False

    def register(self, hotkey, callback):
        """
        注册全局快捷键

        Args:
            hotkey (str): 快捷键字符串
            callback: 无参回调

        Returns:
            bool: 是否注册成功
        """
        self.unregister()
        parsed = parse_hotkey(hotkey)
        if parsed is None:
            self.last_error = f"无法解析快捷键: {hotkey}"
            logger.warning(self.last_error)
            return False

        # 注入句柄（测试假句柄）优先；真机统一走 use_last_error=True 句柄：
        # 失败码在调用点抄进线程局部，之后隔多少无关 API 都不会丢——
        # 翻译冲突提示靠的就是这个码
        used_err_capturing = False
        handle = self._user32_handle
        if handle is None:
            handle = user32_error_capturing()
            used_err_capturing = handle is not None
        if handle is None:
            self.last_error = "当前平台不支持 RegisterHotKey"
            return False

        app = QApplication.instance()
        if app is None:
            self.last_error = "QApplication 尚未创建"
            logger.warning(f"原生快捷键注册跳过：{self.last_error}")
            return False

        modifiers, vk_code = parsed
        try:
            # hwnd 传 None：WM_HOTKEY 投递到本线程消息队列，由 Qt 事件分发器处理
            if not handle.RegisterHotKey(None, HOTKEY_ID, modifiers, vk_code):
                self.last_error = self._register_failure_reason(
                    hotkey, self._failure_code(used_err_capturing))
                logger.warning(self.last_error)
                return False

            self._filter = _HotkeyEventFilter(HOTKEY_ID, self._on_message)
            app.installNativeEventFilter(self._filter)
            self._app = app
            self._callback = callback
            self._registered = True
            self.last_error = ''
            return True
        except Exception as e:
            self.last_error = f"原生快捷键注册异常: {e}"
            logger.warning(self.last_error)
            self.unregister()
            return False

    @property
    def conflict(self):
        """最近一次注册失败是否因快捷键被其它程序占用"""
        return '占用' in self.last_error

    def _failure_code(self, used_err_capturing):
        """
        取刚失败的 RegisterHotKey 的系统错误码

        use_last_error 句柄读 ctypes 线程局部抄码（不怕中间隔了别的 API）；
        注入的假句柄读它自带的 GetLastError（对真 windll 做属性探测本身就会
        触发 GetProcAddress 失败，把要读的错误码覆盖掉，所以只认假句柄）。
        """
        if used_err_capturing:
            return ctypes.get_last_error()
        handle = self._handle()
        if type(handle).__dict__.get('GetLastError') is not None:
            try:
                return handle.GetLastError() or 0
            except Exception:
                return 0
        return 0

    def _register_failure_reason(self, hotkey, code):
        """按系统错误码把注册失败翻译成给用户看的文案"""
        conflict_codes = (ERROR_HOTKEY_ALREADY_REGISTERED,
                          ERROR_INVALID_PARAMETER, ERROR_ALREADY_REGISTERED)
        if code in conflict_codes:
            return f"快捷键 {hotkey} 已被其它程序占用"
        return f"快捷键 {hotkey} 系统注册失败（错误码 {code or '未知'}）"

    def _on_message(self):
        """WM_HOTKEY 回调入口：再包一层异常保护，回调本身也不允许抛"""
        if self._callback is None:
            return
        try:
            self._callback()
        except Exception as e:
            logger.error(f"快捷键回调执行失败: {e}")

    def unregister(self):
        """注销快捷键并摘掉事件过滤器（可重复调用）"""
        if self._app is not None and self._filter is not None:
            try:
                self._app.removeNativeEventFilter(self._filter)
            except Exception as e:
                logger.debug(f"摘除快捷键事件过滤器失败: {e}")
        self._filter = None
        self._app = None
        self._callback = None

        if self._registered:
            try:
                handle = self._handle()
                if handle is not None:
                    handle.UnregisterHotKey(None, HOTKEY_ID)
            except Exception as e:
                logger.debug(f"注销原生快捷键失败: {e}")
        self._registered = False
