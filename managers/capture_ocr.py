#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纯截图「提取文字」：本地 OCR 取字 + 可编辑结果窗

复用截图翻译同一套 OCRManager（RapidOCR 本地推理），不发起任何网络请求，
保持「仅截图」零网络流量的定位。识别在后台线程做，结果窗回投主线程显示。
"""

import logging

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout)

from . import clipboard
from .ocr import is_ocr_notice

logger = logging.getLogger('screenshot-translation')

# 非模态结果窗没有 Qt 父级之外的引用会被 GC 掉，模块级持有
_windows: set = set()


def extract_text(image, ocr_manager=None):
    """
    识别选区图中的文字，按阅读顺序拼成纯文本

    Args:
        image (PIL.Image.Image): 选区原图
        ocr_manager (OCRManager or None): 复用主应用的识别器（省一次模型加载）

    Returns:
        str: 识别文本；无文字/识别失败/提示文本一律返回空串
    """
    if image is None:
        return ''
    manager = ocr_manager
    if manager is None:
        from .ocr import OCRManager
        manager = OCRManager()
    try:
        text, blocks = manager.perform_ocr_with_blocks(image)
    except Exception:
        logger.exception("本地 OCR 取字异常")
        return ''
    if blocks:
        # 行块按 y 再 x 排序，输出符合阅读顺序（块级接口自带坐标）
        try:
            blocks = sorted(blocks, key=lambda b: (b.top, b.left))
            text = '\n'.join(block.text for block in blocks)
        except Exception:
            pass
    text = (text or '').strip()
    if not text or is_ocr_notice(text):
        return ''
    return text


def show_result(parent, text):
    """
    弹出可编辑的取字结果窗，并自动把文本写入剪贴板

    遮罩是全屏置顶窗口，模态框会被它压住，所以这里用置顶的非模态对话框。

    Args:
        parent (QWidget): 宿主遮罩
        text (str): 识别结果

    Returns:
        QDialog: 结果窗实例
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle('提取的文字')
    dialog.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.setMinimumSize(380, 260)
    dialog.setStyleSheet(
        'QDialog { background: #232629; }'
        'QTextEdit { background: #1b1e22; color: #e8e8e8; border: 1px solid #3a3d42;'
        ' border-radius: 6px; padding: 6px; font-size: 13px; }'
        'QPushButton { background: #2d2f33; color: #e8e8e8; border: 1px solid #3a3d42;'
        ' border-radius: 6px; padding: 5px 16px; }'
        'QPushButton:hover { background: #3b82f6; }')

    layout = QVBoxLayout(dialog)
    editor = QTextEdit(dialog)
    editor.setPlainText(text)
    editor.setReadOnly(False)
    layout.addWidget(editor)

    row = QHBoxLayout()
    row.addStretch(1)
    copy_button = QPushButton('复制', dialog)
    close_button = QPushButton('关闭', dialog)
    row.addWidget(copy_button)
    row.addWidget(close_button)
    layout.addLayout(row)

    def _copy():
        clipboard.set_text_later(editor.toPlainText(), on_done=None)

    copy_button.clicked.connect(_copy)
    close_button.clicked.connect(dialog.close)
    dialog.finished.connect(lambda _result: _windows.discard(dialog))

    # 取字即复制：用户按完按钮通常就是要粘贴
    clipboard.set_text_later(text, on_done=None)
    _windows.add(dialog)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
