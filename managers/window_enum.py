#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可见窗口矩形枚举（Windows）

供截图遮罩的"磁吸对齐窗口边缘"功能使用：
返回屏幕上所有可见、未最小化、非工具窗口的矩形（物理像素坐标）。

- Windows 以外的平台返回空列表（磁吸退化为仅对齐屏幕边缘）
- 枚举/查询失败同样返回空列表，绝不抛异常影响截图主流程
"""

import logging
import sys

logger = logging.getLogger('screenshot-translation')

# Win32 常量
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_DWMWA_CLOAKED = 14

# 全屏宿主窗口（如 MaxTo 的 WorkerW/宿主层）不算有效吸附目标
_SCREEN_COVERAGE_LIMIT = 0.96


def _is_windows():
    return sys.platform == 'win32'


def get_visible_window_rects(exclude_hwnd=0, screen_size=None):
    """
    枚举屏幕上可见窗口的矩形

    Args:
        exclude_hwnd (int): 需要排除的窗口句柄（如截图遮罩自身）
        screen_size (tuple[int, int]): (宽, 高)，用于过滤全屏宿主矩形；
            不传时用 GetSystemMetrics 探测

    Returns:
        list[tuple[int, int, int, int]]: (left, top, right, bottom) 列表
    """
    if not _is_windows():
        return []

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        rects = []

        # 屏幕面积（用于全屏宿主过滤）
        sw, sh = screen_size or (
            user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        screen_area = max(sw * sh, 1)

        # 回调：对每个顶层窗口收集矩形
        _CB = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _on_window(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                if hwnd == exclude_hwnd:
                    return True

                # 排除工具窗口（托盘浮窗、提示气泡等）
                exstyle = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
                if exstyle & _WS_EX_TOOLWINDOW:
                    return True

                # 排除最小化窗口
                if user32.IsIconic(hwnd):
                    return True

                # 排除 UWP 挂起窗口（DWM cloak）
                cloaked = wintypes.DWORD(0)
                try:
                    ctypes.windll.dwmapi.DwmGetWindowAttribute(
                        wintypes.HWND(hwnd), wintypes.DWORD(_DWMWA_CLOAKED),
                        ctypes.byref(cloaked), ctypes.sizeof(cloaked))
                    if cloaked.value:
                        return True
                except Exception:
                    pass  # dwmapi 不可用时跳过该检查

                rect = wintypes.RECT()
                # GetBorderRect 拿含阴影的边界，改用 DWM 精确边界，失败回退
                # 一般场景直接 GetWindowRect 足够
                if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True

                left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
                # 过滤无效与退化矩形
                if right - left < 40 or bottom - top < 40:
                    return True

                # 跳过覆盖全屏的宿主矩形（如 MaxTo 宿主层），保留真实窗口边界
                coverage = ((right - left) * (bottom - top)) / screen_area
                if coverage >= _SCREEN_COVERAGE_LIMIT:
                    return True

                rects.append((left, top, right, bottom))
            except Exception:
                pass  # 单个窗口处理失败不影响整体枚举
            return True

        _cb = _CB(_on_window)
        user32.EnumWindows(_cb, 0)

        # 二次遍历：排查宿主/工具窗的子窗口（MaxTo 等窗口管理器
        # 会把应用窗口变成宿主的子窗口，顶层枚举看不到）
        host_hwnds = []

        def _collect_host(hwnd, _lp):
            # 与顶层枚举同构：单个窗口查不到属性就跳过，不能让一次
            # GetWindowLongW 失败把整轮磁吸枚举带崩
            try:
                ex = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
                if (ex & _WS_EX_TOOLWINDOW) and user32.IsWindowVisible(hwnd):
                    r = wintypes.RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
                        if r.right - r.left >= 100 and r.bottom - r.top >= 100:
                            host_hwnds.append(hwnd)
            except Exception:
                pass
            return True

        _cb2 = _CB(_collect_host)
        user32.EnumWindows(_cb2, 0)

        # 对每个大尺寸宿主窗口枚举其子窗口
        for host in host_hwnds:
            try:
                child_rects = []

                def _on_child(hwnd, _lp, _host=host):
                    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                        return True
                    r = wintypes.RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
                        l, t, rr, b = r.left, r.top, r.right, r.bottom
                        if rr - l >= 40 and b - t >= 40:
                            cov = ((rr - l) * (b - t)) / screen_area
                            if cov < _SCREEN_COVERAGE_LIMIT:
                                child_rects.append((l, t, rr, b))
                    return True

                _ccb = _CB(_on_child)
                user32.EnumChildWindows(host, _ccb, 0)
                rects.extend(child_rects)
            except Exception:
                pass

        # 去重
        rects = list(dict.fromkeys(rects))

        logger.debug(f"枚举到 {len(rects)} 个可磁吸窗口")
        return rects

    except Exception as e:
        logger.warning(f"枚举窗口失败，磁吸退化: {e}")
        return []
