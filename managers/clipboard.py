#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
剪贴板写入（附生效校验与重试）

Windows 的剪贴板是全局独占资源：其他进程持有/打开剪贴板期间，Qt 的
setText/setPixmap **既不抛异常也不返回失败**，只是静默不生效——用户看到的
现象是"点了复制，却粘贴不出东西"（测试里钉图的 Ctrl+C 就为此连挂过三次）。

校验信号用 QClipboard.ownsClipboard()（Qt 5.15 Windows 端实现为
OleIsCurrentClipboard(m_data) == S_OK，直接查询系统剪贴板状态）：

- 写入成功 → True；写入失败或内容随即被别的程序顶掉 → False；
- **不需要打开剪贴板**，被占用时也照常应答，实测 0ms——这点很关键：
  读回比对（text()/pixmap()）走 OleGetClipboard，剪贴板被占用时会吃满
  OLE 超时，实测一次 0.5~3.9 秒，放在主线程等于自造卡死，故弃用。

重试只覆盖"短暂占用"：Qt 内部对 OleSetClipboard 已有 3 次×100ms 的重试
（qwindowsclipboard.cpp setMimeData），这里再叠加 3 次；若期间剪贴板一直
不放锁，放弃并如实返回 False，由调用方决定是否提示。

两套入口：`set_text`/`set_pixmap` 用 time.sleep 同步重试，给测试与非 GUI
线程用；`set_text_later`/`set_pixmap_later` 把重试间隔交给 QTimer，是 GUI
线程该用的版本——复制发生在遮罩与钉图窗口自己的按键处理里，同步等待最坏
要把界面冻住 200 毫秒（叠上 Qt 内部的重试接近 1 秒），全屏遮罩下就是明显
的卡顿。
"""

import logging
import time

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger('screenshot-translation')

# 重试次数与间隔：剪贴板被短暂占用通常几十~几百毫秒内释放
_ATTEMPTS = 3
_DELAY = 0.1


def _write_and_check(text=None, pixmap=None):
    """写一次并校验是否生效（不等待）；拿不到剪贴板时返回False"""
    try:
        clipboard = QApplication.clipboard()
    except Exception as e:
        logger.warning(f"获取剪贴板失败: {e}")
        return False
    if clipboard is None:
        return False
    if text is not None:
        clipboard.setText(text)
    else:
        clipboard.setPixmap(pixmap)
    return clipboard.ownsClipboard()


def _set_sync(content, attempts):
    """同步重试：写一次校验一次，未生效则等 _DELAY 秒再来"""
    for attempt in range(1, attempts + 1):
        if _write_and_check(**content):
            return True
        logger.debug(f"剪贴板写入未生效（第 {attempt}/{attempts} 次）")
        if attempt < attempts:
            time.sleep(_DELAY)
    logger.warning("复制到剪贴板失败：剪贴板被其他程序占用")
    return False


def _set_later(content, on_done, attempts):
    """非阻塞重试：立即跑第一次，后续间隔用 QTimer 排进事件循环"""
    def attempt(remaining):
        """一次尝试：生效或次数用尽就回调，否则排下一次"""
        if _write_and_check(**content):
            if on_done:
                on_done(True)
            return
        logger.debug(f"剪贴板写入未生效（剩余 {remaining - 1} 次重试）")
        if remaining <= 1:
            logger.warning("复制到剪贴板失败：剪贴板被其他程序占用")
            if on_done:
                on_done(False)
            return
        QTimer.singleShot(int(_DELAY * 1000), lambda: attempt(remaining - 1))

    attempt(attempts)


def set_text(text, attempts=_ATTEMPTS):
    """
    写入文本到剪贴板，校验生效并按需重试（阻塞式）

    Args:
        text (str): 要复制的文本
        attempts (int): 最多尝试次数

    Returns:
        bool: 是否确认写入成功；False 表示剪贴板始终被其他程序占用
    """
    if not text:
        return False
    return _set_sync({'text': text}, attempts)


def set_pixmap(pixmap, attempts=_ATTEMPTS):
    """
    写入图片到剪贴板，校验生效并按需重试（阻塞式）

    Args:
        pixmap (QPixmap): 要复制的图片
        attempts (int): 最多尝试次数

    Returns:
        bool: 是否确认写入成功；False 表示剪贴板始终被其他程序占用
    """
    if pixmap is None or pixmap.isNull():
        return False
    return _set_sync({'pixmap': pixmap}, attempts)


def set_text_later(text, on_done=None, attempts=_ATTEMPTS):
    """
    非阻塞写入文本（GUI 线程用）：调用即返回，重试在事件循环里排着跑

    Args:
        text (str): 要复制的文本
        on_done (callable 或 None): 结果回调，接收一个 bool
        attempts (int): 最多尝试次数
    """
    if not text:
        if on_done:
            on_done(False)
        return
    _set_later({'text': text}, on_done, attempts)


def set_pixmap_later(pixmap, on_done=None, attempts=_ATTEMPTS):
    """
    非阻塞写入图片（GUI 线程用）：调用即返回，重试在事件循环里排着跑

    Args:
        pixmap (QPixmap): 要复制的图片
        on_done (callable 或 None): 结果回调，接收一个 bool
        attempts (int): 最多尝试次数
    """
    if pixmap is None or pixmap.isNull():
        if on_done:
            on_done(False)
        return
    _set_later({'pixmap': pixmap}, on_done, attempts)
