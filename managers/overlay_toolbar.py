#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图遮罩的选区浮动工具条

结果态下贴在选区下沿的深色小工具条：原文/双语/译文 三态切换、目标语言、
提取原文/译文文本、保存原图/译图、复制原图/译图、钉住、重新翻译、关闭。

用真实 QPushButton 而非自绘，命中测试与键盘可达性交给 Qt。图标由
vector_icons 程序化绘制，右下角"原/译"徽标区分同一动作的两个对象。
"""

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import QPushButton, QWidget, QHBoxLayout

from . import result_render, vector_icons
from .toolbar_combo import SmallComboBox, chrome_width, content_width

# 高度固定，宽度由内容决定；越宽时按 _TRIM_ORDER 逐级精简
TOOLBAR_HEIGHT = 34

_ICON_SIZE = 20
_ICON_BUTTON_SIZE = QSize(28, 26)

_TOOLBAR_QSS = """
QWidget#OverlayToolbar {
    background-color: rgba(35, 38, 41, 250);
    border: 1px solid #3a3d42;
    border-radius: 8px;
}
QPushButton {
    background: transparent;
    border: none;
    color: #e8e8e8;
    padding: 4px 9px;
    border-radius: 6px;
    font-size: 12px;
}
QPushButton[iconAction="true"] { padding: 2px 5px; }
QPushButton:hover { background-color: #2d2f33; }
QPushButton:pressed { background-color: #3c3f44; }
QPushButton:checked { background-color: #3b82f6; color: #ffffff; font-weight: 600; }
QComboBox {
    background-color: #262d3b;
    border: 1px solid #3a3d42;
    border-radius: 6px;
    padding: 2px 4px;
    color: #e8e8e8;
    font-size: 12px;
    /* 全局浅色样式给 QComboBox 留了 120px 最小宽度，浮层工具条要按内容收紧 */
    min-width: 0px;
}
/* 箭头区收窄，否则小下拉里一半宽度都给了箭头；
   箭头本身由 SmallComboBox 自绘，QSS 这里把样式表的箭头彻底关掉 */
QComboBox::drop-down { border: none; width: 12px; }
QComboBox::down-arrow { image: none; border: none; width: 0px; height: 0px; }
QComboBox QAbstractItemView {
    background-color: #1e2430;
    border: 1px solid #333d4f;
    color: #e5eaf2;
    selection-background-color: #3b82f6;
    outline: none;
}
"""


class OverlayToolbar(QWidget):
    """选区浮动工具条：把用户意图转成信号，不直接碰截图流程"""

    mode_changed = pyqtSignal(str)
    language_changed = pyqtSignal(str)
    text_copy_clicked = pyqtSignal(str)     # 'original' / 'translated'
    image_save_clicked = pyqtSignal(str)    # 'original' / 'translated'
    image_copy_clicked = pyqtSignal(str)    # 'original' / 'translated'
    pin_clicked = pyqtSignal()
    retranslate_clicked = pyqtSignal()
    close_clicked = pyqtSignal()

    def __init__(self, parent, languages, current_lang=...,
                 mode=result_render.MODE_TRANSLATED, capture_only=False):
        """
        Args:
            parent: 宿主遮罩窗口
            languages (list[tuple[str, str]]): (语言代码, 显示名) 列表
            current_lang: 初始选中语言；传 ... 表示不预置
            mode (str): 初始展示模式
            capture_only (bool): 纯截图模式：只保留保存/复制/钉住/关闭
        """
        super().__init__(parent)
        self.capture_only = bool(capture_only)
        self.setObjectName('OverlayToolbar')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_TOOLBAR_QSS)
        self.setFixedHeight(TOOLBAR_HEIGHT)
        # 工具条要能吃到自己的点击，不透给遮罩窗口
        self.setFocusPolicy(Qt.NoFocus)

        self._mode_buttons = {}
        self._trimmable = []
        self._icon_buttons = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        self.lang_combo = None
        self.text_original_button = None
        self.text_translated_button = None
        self.retranslate_button = None

        if not self.capture_only:
            for mode_key in result_render.MODES:
                button = self._make_button(result_render.MODE_LABELS[mode_key], checkable=True)
                button.clicked.connect(
                    lambda _checked, key=mode_key: self._on_mode_clicked(key))
                self._mode_buttons[mode_key] = button
                layout.addWidget(button)

            layout.addSpacing(6)

            self.lang_combo = SmallComboBox(self)
            self.lang_combo.setFocusPolicy(Qt.NoFocus)
            for code, name in languages:
                self.lang_combo.addItem(name if name else code, code)
            if current_lang is not ...:
                index = self.lang_combo.findData(current_lang)
                if index >= 0:
                    self.lang_combo.setCurrentIndex(index)
            # 收起时按语言名的实际宽度定宽（刚好放得下，不裁字）；
            # 完整语言名仍放进弹出列表。语言表为空时退回 2 个汉字的宽度，
            # 免得留一个极窄的空下拉
            lang_chrome = chrome_width(self.lang_combo)
            lang_content = content_width(self.lang_combo) or (
                self.lang_combo.fontMetrics().horizontalAdvance('简') * 2)
            self.lang_combo.set_fit_width(lang_content + lang_chrome)
            longest = max((name or code for code, name in languages),
                          key=len, default='')
            self.lang_combo.view().setMinimumWidth(
                self.lang_combo.fontMetrics().horizontalAdvance(longest)
                + lang_chrome)
            # 连接放在初始选择之后，避免创建工具条时就触发重译
            self.lang_combo.currentIndexChanged.connect(self._on_lang_changed)
            self.lang_combo.currentIndexChanged.connect(self._sync_lang_tooltip)
            self._sync_lang_tooltip()
            # 下拉是独立顶层窗口，选完可能不把键盘焦点还给遮罩：那样 ESC/T/R/Ctrl+C
            # 全部失灵，只能鼠标点回去
            self.lang_combo.activated.connect(self._reactivate_host)
            layout.addWidget(self.lang_combo)

            layout.addSpacing(6)

            self.text_original_button = self._make_icon_button(
                'text', '原', '提取原文到剪贴板', self.text_copy_clicked, 'original')
            layout.addWidget(self.text_original_button)

            self.text_translated_button = self._make_icon_button(
                'text', '译', '提取译文到剪贴板', self.text_copy_clicked, 'translated')
            layout.addWidget(self.text_translated_button)

            self.save_original_button = self._make_icon_button(
                'save', '原', '保存原图', self.image_save_clicked, 'original')
            layout.addWidget(self.save_original_button)

            self.save_translated_button = self._make_icon_button(
                'save', '译', '保存译图', self.image_save_clicked, 'translated')
            layout.addWidget(self.save_translated_button)

            self.copy_original_image_button = self._make_icon_button(
                'copy', '原', '复制原图到剪贴板', self.image_copy_clicked, 'original')
            layout.addWidget(self.copy_original_image_button)

            self.copy_translated_image_button = self._make_icon_button(
                'copy', '译', '复制译图到剪贴板', self.image_copy_clicked, 'translated')
            layout.addWidget(self.copy_translated_image_button)

            layout.addSpacing(4)
        else:
            self.save_original_button = self._make_icon_button(
                'save', None, '保存截图', self.image_save_clicked, 'original')
            layout.addWidget(self.save_original_button)

            # 纯截图：复制按钮等公共尾部之后再加，保证它是最后一个按钮
            self.copy_original_image_button = self._make_icon_button(
                'copy', None, '复制截图到剪贴板', self.image_copy_clicked, 'original')

            self.save_translated_button = None
            self.copy_translated_image_button = None
            layout.addSpacing(4)

        self.pin_button = self._make_button('钉住')
        self.pin_button.clicked.connect(self.pin_clicked.emit)
        layout.addWidget(self.pin_button)

        if not self.capture_only:
            self.retranslate_button = self._make_button('重译')
            self.retranslate_button.clicked.connect(self.retranslate_clicked.emit)
            layout.addWidget(self.retranslate_button)

        self.close_button = self._make_button('×')
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.clicked.connect(self.close_clicked.emit)
        layout.addWidget(self.close_button)

        if self.capture_only:
            # 纯截图时最后一个按钮必须是「复制到剪贴板」
            layout.addWidget(self.copy_original_image_button)
            # 宽度不够时依次折叠保存、钉住；关闭与复制两个按钮始终保留
            self._trimmable = [
                self.save_original_button,
                self.pin_button,
            ]
        else:
            self._trimmable = [
                self.lang_combo,
                self.copy_translated_image_button,
                self.copy_original_image_button,
                self.save_translated_button,
                self.save_original_button,
                self.text_translated_button,
                self.text_original_button,
                self.pin_button,
                self.retranslate_button,
            ]

        self.set_mode(mode, emit=False)

    def _make_button(self, text, checkable=False):
        """创建一个扁平小按钮（不抢焦点，避免遮罩失活）"""
        button = QPushButton(text, self)
        button.setCheckable(checkable)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def _make_icon_button(self, icon_name, badge, tooltip, signal, kind):
        """
        创建带"原/译"徽标的图标按钮

        Args:
            icon_name (str): vector_icons 里的图标名
            badge (str): 右下角徽标
            tooltip (str): 悬停提示（同时作为无障碍名称）
            signal (pyqtSignal): 点击时发出的信号
            kind (str): 随信号带出的对象类型（original/translated）
        """
        button = QPushButton(self)
        button.setIcon(vector_icons.icon(icon_name, badge=badge))
        button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        button.setFixedSize(_ICON_BUTTON_SIZE)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setProperty('iconAction', True)
        button.clicked.connect(lambda _checked=False, k=kind: signal.emit(k))
        self._icon_buttons[(icon_name, kind, badge)] = button
        return button

    def _on_mode_clicked(self, mode_key):
        """模式按钮点击：回到该模式并通知宿主"""
        self.set_mode(mode_key)

    def _on_lang_changed(self, _index):
        """语言下拉变化：把新的目标语言代码通知宿主"""
        self.language_changed.emit(self.lang_combo.currentData())

    def _sync_lang_tooltip(self, *_args):
        """收起时只显示两个汉字，完整语言名放进悬停提示"""
        text = self.current_language_text()
        if text:
            self.lang_combo.setToolTip(f'目标语言：{text}')

    def _reactivate_host(self, *_args):
        """选完语言后把键盘焦点交还遮罩窗口，让快捷键继续可用"""
        host = self.window()
        if host is not None:
            host.activateWindow()

    def set_mode(self, mode_key, emit=True):
        """
        设置当前展示模式（同步按钮选中态）

        Args:
            mode_key (str): 模式键
            emit (bool): 是否发出 mode_changed 信号
        """
        if mode_key not in self._mode_buttons:
            return
        for key, button in self._mode_buttons.items():
            button.setChecked(key == mode_key)
        if emit:
            self.mode_changed.emit(mode_key)

    def current_mode(self):
        """当前展示模式"""
        for key, button in self._mode_buttons.items():
            if button.isChecked():
                return key
        return result_render.MODE_TRANSLATED

    def current_language(self):
        """工具条上选中的目标语言代码"""
        if self.lang_combo is None:
            return None
        return self.lang_combo.currentData()

    def current_language_text(self):
        """工具条上选中的目标语言显示名"""
        if self.lang_combo is None:
            return ''
        return self.lang_combo.currentText()

    def set_busy(self, busy):
        """
        请求进行中：只禁用控件，不改按钮文案

        进度统一由遮罩上的小转圈表达；把按钮写成"翻译中…"既多一行要读的字，
        也会让工具条宽度跳一下。
        """
        if self.retranslate_button is not None:
            self.retranslate_button.setEnabled(not busy)
        if self.lang_combo is not None:
            self.lang_combo.setEnabled(not busy)

    def fit_width(self, max_width):
        """
        宽度受限时按精简顺序隐藏次要按钮

        每次调用都按当前可用宽度重算：工具条在同一个遮罩里会被多次摆放
        （换选区、切模式），只做单向隐藏的话，第一次窄选区省掉的按钮
        在后面的宽选区里也永远回不来。

        Args:
            max_width (int): 可用最大宽度

        Returns:
            int: 调整后的宽度
        """
        for widget in self._trimmable:
            widget.setVisible(True)
        for widget in self._trimmable:
            if self.sizeHint().width() <= max_width:
                break
            widget.setVisible(False)
        return self.sizeHint().width()

    def preferred_width(self):
        """当前内容需要的宽度"""
        return self.sizeHint().width()
