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
（qwindowsclipboard.cpp setMimeData），这里再叠加 3 次、间隔 100ms；
若期间剪贴板一直不放锁，放弃并如实返回 False，由调用方决定是否提示。
"""

import logging
import time

from PyQt5.QtWidgets import QApplication

logger = logging.getLogger('screenshot-translation')

# 重试次数与间隔：剪贴板被短暂占用通常几十~几百毫秒内释放
_ATTEMPTS = 3
_DELAY = 0.1


def set_text(text, attempts=_ATTEMPTS):
    """
    写入文本到剪贴板，校验生效并按需重试

    Args:
        text (str): 要复制的文本
        attempts (int): 最多尝试次数

    Returns:
        bool: 是否确认写入成功；False 表示剪贴板始终被其他程序占用
    """
    if not text:
        return False
    clipboard = QApplication.clipboard()
    if clipboard is None:
        return False
    for attempt in range(1, attempts + 1):
        clipboard.setText(text)
        if clipboard.ownsClipboard():
            return True
        logger.debug(f"剪贴板写入未生效（第 {attempt}/{attempts} 次）")
        if attempt < attempts:
            time.sleep(_DELAY)
    logger.warning("复制文本到剪贴板失败：剪贴板被其他程序占用")
    return False


def set_pixmap(pixmap, attempts=_ATTEMPTS):
    """
    写入图片到剪贴板，校验生效并按需重试

    Args:
        pixmap (QPixmap): 要复制的图片
        attempts (int): 最多尝试次数

    Returns:
        bool: 是否确认写入成功；False 表示剪贴板始终被其他程序占用
    """
    if pixmap is None or pixmap.isNull():
        return False
    clipboard = QApplication.clipboard()
    if clipboard is None:
        return False
    for attempt in range(1, attempts + 1):
        clipboard.setPixmap(pixmap)
        if clipboard.ownsClipboard():
            return True
        logger.debug(f"剪贴板写入未生效（第 {attempt}/{attempts} 次）")
        if attempt < attempts:
            time.sleep(_DELAY)
    logger.warning("复制图片到剪贴板失败：剪贴板被其他程序占用")
    return False
