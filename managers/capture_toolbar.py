#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图编辑态浮动工具条

贴在选区下沿：标注工具、颜色/线宽/字号、撤销重做清空、取色、OCR 取字、
滚动截图，以及复制/保存/钉住/取消。用真实 QPushButton 而非自绘，命中测试
与键盘可达性交给 Qt；图标由 vector_icons 程序化绘制。

只发信号，不直接碰截图流程。
"""

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QHBoxLayout, QPushButton, QWidget)

from . import vector_icons
from .capture_annotation import (
    FONT_CHOICES, PRESET_COLORS, TOOLS, TOOL_LABELS, WIDTH_CHOICES)

# 工具键 → 图标名（文字标注用 T 图标，避免与「提取文本」的文档图标混淆）
_TOOL_ICONS = {
    'text': 'text_annot',
}

TOOLBAR_HEIGHT = 36
_ICON_SIZE = 20
_ICON_BUTTON_SIZE = QSize(28, 27)
_COLOR_BUTTON_SIZE = QSize(19, 19)

_QSS = """
QWidget#CaptureToolbar {
    background-color: rgba(35, 38, 41, 250);
    border: 1px solid #3a3d42;
    border-radius: 8px;
}
QPushButton {
    background: transparent;
    border: none;
    color: #e8e8e8;
    padding: 3px 7px;
    border-radius: 6px;
    font-size: 12px;
}
QPushButton[iconAction="true"] { padding: 2px 4px; }
QPushButton:hover { background-color: #2d2f33; }
QPushButton:pressed { background-color: #3c3f44; }
QPushButton:checked { background-color: #3b82f6; color: #ffffff; }
QPushButton[colorSwatch="true"] {
    border: 2px solid transparent;
    border-radius: 4px;
    padding: 0px;
}
QPushButton[colorSwatch="true"]:checked { border-color: #ffffff; }
QComboBox {
    background-color: #262d3b;
    border: 1px solid #3a3d42;
    border-radius: 6px;
    padding: 2px 4px;
    color: #e8e8e8;
    font-size: 12px;
}
QComboBox QAbstractItemView {
    background-color: #1e2430;
    border: 1px solid #333d4f;
    color: #e5eaf2;
    selection-background-color: #3b82f6;
    outline: none;
}
"""


class CaptureToolbar(QWidget):
    """编辑态工具条：把用户意图转成信号"""

    tool_changed = pyqtSignal(str)
    color_changed = pyqtSignal(str)
    width_changed = pyqtSignal(int)
    font_size_changed = pyqtSignal(int)
    undo_clicked = pyqtSignal()
    redo_clicked = pyqtSignal()
    clear_clicked = pyqtSignal()
    eyedropper_clicked = pyqtSignal()
    ocr_clicked = pyqtSignal()
    scroll_clicked = pyqtSignal()
    copy_clicked = pyqtSignal()
    save_clicked = pyqtSignal()
    pin_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()

    def __init__(self, parent, tool='select', color=PRESET_COLORS[0],
                 width=WIDTH_CHOICES[1], font_size=FONT_CHOICES[1]):
        super().__init__(parent)
        self.setObjectName('CaptureToolbar')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_QSS)
        self.setFixedHeight(TOOLBAR_HEIGHT)
        self.setFocusPolicy(Qt.NoFocus)

        self._tool_buttons = {}
        self._color_buttons = {}
        self._trimmable = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(2)

        # 1) 工具组
        for key in TOOLS:
            button = self._icon_button(
                _TOOL_ICONS.get(key, key), TOOL_LABELS[key], checkable=True)
            button.clicked.connect(
                lambda _checked=False, k=key: self.tool_changed.emit(k))
            self._tool_buttons[key] = button
            layout.addWidget(button)
        layout.addSpacing(5)

        # 2) 属性组：色块 + 吸管
        self.eyedropper_button = self._icon_button(
            'eyedropper', '吸取屏幕颜色', checkable=True)
        self.eyedropper_button.clicked.connect(
            lambda _checked=False: self.eyedropper_clicked.emit())
        layout.addWidget(self.eyedropper_button)
        for hex_color in PRESET_COLORS:
            swatch = QPushButton(self)
            swatch.setFixedSize(_COLOR_BUTTON_SIZE)
            swatch.setCheckable(True)
            swatch.setFocusPolicy(Qt.NoFocus)
            swatch.setCursor(Qt.PointingHandCursor)
            swatch.setToolTip(hex_color)
            swatch.setAccessibleName(f'颜色 {hex_color}')
            swatch.setProperty('colorSwatch', True)
            swatch.setStyleSheet(
                f'QPushButton {{ background-color: {hex_color}; }}')
            swatch.clicked.connect(
                lambda _checked=False, c=hex_color: self.color_changed.emit(c))
            self._color_buttons[hex_color] = swatch
            layout.addWidget(swatch)
        layout.addSpacing(5)

        self.width_combo = self._combo(
            [f'{w} px' for w in WIDTH_CHOICES], WIDTH_CHOICES, width,
            '线宽', self.width_changed)
        layout.addWidget(self.width_combo)
        self.font_combo = self._combo(
            [f'{s} px' for s in FONT_CHOICES], FONT_CHOICES, font_size,
            '字号', self.font_size_changed)
        layout.addWidget(self.font_combo)
        layout.addSpacing(5)

        # 3) 编辑操作
        self.undo_button = self._icon_button('undo', '撤销 (Ctrl+Z)')
        self.undo_button.clicked.connect(self.undo_clicked.emit)
        layout.addWidget(self.undo_button)
        self.redo_button = self._icon_button('redo', '重做 (Ctrl+Y)')
        self.redo_button.clicked.connect(self.redo_clicked.emit)
        layout.addWidget(self.redo_button)
        self.clear_button = self._icon_button('clear', '清空全部标注')
        self.clear_button.clicked.connect(self.clear_clicked.emit)
        layout.addWidget(self.clear_button)
        layout.addSpacing(5)

        # 4) 专项功能
        self.ocr_button = self._icon_button('ocr', '提取图中文字（本地 OCR）')
        self.ocr_button.clicked.connect(self.ocr_clicked.emit)
        layout.addWidget(self.ocr_button)
        self.scroll_button = self._icon_button('scroll', '滚动截图（长截图）')
        self.scroll_button.clicked.connect(self.scroll_clicked.emit)
        layout.addWidget(self.scroll_button)
        layout.addSpacing(5)

        # 5) 输出动作
        self.copy_button = self._icon_button('copy', '复制到剪贴板 (Ctrl+C)')
        self.copy_button.clicked.connect(self.copy_clicked.emit)
        layout.addWidget(self.copy_button)
        self.save_button = self._icon_button('save', '保存 (Ctrl+S)')
        self.save_button.clicked.connect(self.save_clicked.emit)
        layout.addWidget(self.save_button)
        self.pin_button = self._icon_button('pin', '钉到屏幕 (P)')
        self.pin_button.clicked.connect(self.pin_clicked.emit)
        layout.addWidget(self.pin_button)
        self.cancel_button = self._icon_button('clear', '取消 (ESC)')
        self.cancel_button.clicked.connect(self.cancel_clicked.emit)
        layout.addWidget(self.cancel_button)

        # 宽度不够时按「先省次要、后省核心」的顺序折叠
        self._trimmable = [
            self.font_combo,
            self.scroll_button,
            self.ocr_button,
            *[self._color_buttons[c] for c in PRESET_COLORS[4:]],
            self.width_combo,
            self._tool_buttons['mosaic'],
            self._tool_buttons['step'],
            self._tool_buttons['text'],
            self._tool_buttons['highlight'],
            self._tool_buttons['pen'],
            self.eyedropper_button,
            self.redo_button,
            self._tool_buttons['ellipse'],
            self._tool_buttons['line'],
            self._tool_buttons['arrow'],
        ]

        self.set_tool(tool)
        self.set_color(color)
        self.set_undo_state(False, False)

    # ---- 构造辅助 ----

    def _icon_button(self, icon_name, tooltip, checkable=False):
        button = QPushButton(self)
        button.setIcon(vector_icons.icon(icon_name))
        button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        button.setFixedSize(_ICON_BUTTON_SIZE)
        button.setCheckable(checkable)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setProperty('iconAction', True)
        return button

    def _combo(self, labels, values, current, tooltip, signal):
        combo = QComboBox()
        combo.setFocusPolicy(Qt.NoFocus)
        combo.setToolTip(tooltip)
        for label, value in zip(labels, values):
            combo.addItem(label, value)
        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.currentIndexChanged.connect(
            lambda _i, c=combo, s=signal: s.emit(c.currentData()))
        return combo

    # ---- 状态同步 ----

    def set_tool(self, key):
        """同步工具按钮选中态（不触发信号）"""
        if key not in self._tool_buttons:
            return
        for name, button in self._tool_buttons.items():
            button.setChecked(name == key)
        # 吸管只在「取色中」保持按下
        if key != 'eyedropper':
            self.eyedropper_button.setChecked(False)

    def set_eyedropper(self, active):
        """取色模式开关的视觉反馈"""
        self.eyedropper_button.setChecked(bool(active))

    def set_color(self, hex_color):
        for value, button in self._color_buttons.items():
            button.setChecked(value == hex_color)

    def set_undo_state(self, can_undo, can_redo):
        self.undo_button.setEnabled(bool(can_undo))
        self.redo_button.setEnabled(bool(can_redo))

    def set_busy(self, busy):
        """OCR / 滚动截图进行中：禁用会重复发起的按钮"""
        for button in (self.ocr_button, self.scroll_button, self.copy_button,
                       self.save_button, self.pin_button):
            button.setEnabled(not busy)

    # ---- 布局 ----

    def fit_width(self, max_width):
        """每次按当前可用宽度重算，保证宽选区里被藏掉的按钮能回来"""
        for widget in self._trimmable:
            widget.setVisible(True)
        for widget in self._trimmable:
            if self.sizeHint().width() <= max_width:
                break
            widget.setVisible(False)
        return self.sizeHint().width()

    def preferred_width(self):
        return self.sizeHint().width()
