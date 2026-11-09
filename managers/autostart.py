#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开机自启动

写当前用户的注册表 Run 项（HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run），
不需要管理员权限，也不在启动文件夹里留 .lnk 垃圾文件。

命令按运行形态拼：打包态直接指向 exe；源码态用 pythonw.exe 拉起 main.py，
避免每次开机弹一个控制台黑窗。
"""

import logging
import os
import sys

from . import app_paths

logger = logging.getLogger('screenshot-translation')

# 注册表位置与值名（值名同时用于卸载时识别本程序）
_RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
_VALUE_NAME = 'ScreenshotTranslator'

# 最近一次操作失败原因，供设置页提示
last_error = ''


def is_supported():
    """
    当前系统是否支持注册表自启动

    Returns:
        bool: Windows 为 True
    """
    return os.name == 'nt'


def startup_command():
    """
    拼出自启动命令

    Returns:
        str: 带引号的完整命令行
    """
    if app_paths.is_frozen():
        return f'"{sys.executable}"'
    # 源码态优先用 pythonw：开机启动时不该出现控制台窗口
    python = sys.executable
    pythonw = os.path.join(os.path.dirname(python), 'pythonw.exe')
    if os.path.exists(pythonw):
        python = pythonw
    main_py = os.path.join(app_paths.app_root(), 'main.py')
    return f'"{python}" "{main_py}"'


def is_enabled():
    """
    注册表里是否已有本程序的自启动项

    Returns:
        bool: 存在且值非空时为 True
    """
    if not is_supported():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _type = winreg.QueryValueEx(key, _VALUE_NAME)
            return bool(str(value).strip())
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.debug(f"读取开机自启动项失败: {e}")
        return False


def apply(enabled):
    """
    写入或移除开机自启动项

    已处于目标状态时不重复写注册表：启动时每次都要同步一次，没必要反复
    触发注册表通知。

    Args:
        enabled (bool): True 开启，False 关闭

    Returns:
        bool: 操作是否成功；失败原因写入模块级 last_error
    """
    global last_error
    last_error = ''
    if not is_supported():
        # 非 Windows 上没有开机自启动这一说：关闭本身就是目标状态
        if not enabled:
            return True
        last_error = '当前系统不支持开机自启动设置'
        return False

    try:
        import winreg
        if not enabled:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                                    0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, _VALUE_NAME)
            except FileNotFoundError:
                pass
            logger.info("已关闭开机自启动")
            return True

        command = startup_command()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                                0, winreg.KEY_QUERY_VALUE) as key:
                current, _type = winreg.QueryValueEx(key, _VALUE_NAME)
                if current == command:
                    return True
        except FileNotFoundError:
            pass

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, command)
        logger.info(f"已开启开机自启动: {command}")
        return True
    except OSError as e:
        last_error = f"开机自启动设置失败: {e}"
        logger.warning(last_error)
        return False
