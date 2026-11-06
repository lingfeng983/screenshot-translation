#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文本翻译页面

给"随手输入一段文本让翻译一下"准备的独立窗口：左侧输入、右侧看译文，
不需要截图。翻译走与截图翻译同一条链路（TranslationManager），因此
接口、降级与缓存策略完全一致。

界面固定浅色、只用一列纵向布局，尽量少控件。
"""

import logging

from PyQt5.QtCore import QEvent, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget
)

from . import clipboard, styles
from .providers.base import TARGET_LANGUAGES
from .qt_dispatcher import qt_dispatcher
from .thread_pool import thread_pool_manager
from .translation import is_translation_failure

logger = logging.getLogger('screenshot-translation')

# 加载动画：小圆环，26px 一帧，肉眼看着就是连续转动
_SPINNER_INTERVAL = 33      # 帧间隔（毫秒）
_SPINNER_STEP = 18          # 每帧转过的角度
_SPINNER_ARC = 280          # 弧长（度）：留一段缺口才看得出在转

# 页面专属样式（在全局浅色 QSS 之上叠加）
_PAGE_STYLE = """
QLabel#pageTitle { font-size: 16px; font-weight: 600; color: #1e2430; }
QLabel#sectionLabel { color: #667085; font-size: 12px; }
QLabel#statusHint { color: #667085; font-size: 12px; }
QLabel#statusError { color: #d92d20; font-size: 12px; }
QPlainTextEdit#sourceEdit, QPlainTextEdit#resultEdit {
    background: #ffffff;
    border: 1px solid #d8dee9;
    border-radius: 10px;
    padding: 10px 12px;
    font-size: 14px;
    color: #1e2430;
}
QPlainTextEdit#sourceEdit:hover, QPlainTextEdit#resultEdit:hover {
    border-color: #bfd3f5;
}
QPlainTextEdit#sourceEdit:focus { border-color: #2563eb; }
QPlainTextEdit#resultEdit { background: #f8fafc; }
QPushButton#primary:disabled { background: #b7cdf5; color: #ffffff; }
QFrame#divider { background: #dbe3ef; border: none; }
"""


class _Spinner(QWidget):
    """一个只转圈、不带文字的小加载指示器"""

    def __init__(self, parent=None, size=18):
        """
        Args:
            parent (QWidget): 父控件
            size (int): 控件边长（像素）
        """
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(_SPINNER_INTERVAL)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self):
        """开始转动并显示"""
        self.show()
        if not self._timer.isActive():
            self._angle = 0
            self._timer.start()

    def stop(self):
        """停止转动并隐藏"""
        self._timer.stop()
        self.hide()

    def _tick(self):
        """每帧转一点：只重绘本控件，不惊动整页"""
        self._angle = (self._angle + _SPINNER_STEP) % 360
        self.update()

    def paintEvent(self, event):
        """画一圈淡色轨道 + 一段旋转的高亮圆弧"""
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            ring = QRectF(2, 2, self.width() - 4, self.height() - 4)
            painter.setBrush(Qt.NoBrush)

            track = QPen(QColor(37, 99, 235, 46), 2)
            painter.setPen(track)
            painter.drawEllipse(ring)

            arc = QPen(QColor("#2563eb"), 2)
            arc.setCapStyle(Qt.RoundCap)
            painter.setPen(arc)
            # Qt 的角度单位是 1/16 度；起点取负角度让它顺时针转
            painter.drawArc(ring, int(-self._angle * 16), int(-_SPINNER_ARC * 16))
        finally:
            painter.end()


class TranslateWindow(QDialog):
    """
    文本翻译窗口

    单个窗口实例由主应用持有并复用（关掉再打开不会叠出多个）；
    翻译请求全部丢到线程池，界面只在主线程更新。
    """

    def __init__(self, main_app):
        """
        Args:
            main_app (MainApp): 主应用实例，用于取翻译管理器与配置
        """
        super().__init__()
        self.main_app = main_app
        self._request_id = 0
        # 当前在飞的请求是否还在等结果（决定关闭时是否要作废）
        self._busy = False

        self.setWindowTitle("文本翻译")
        # 去掉标题栏右上角的“？”（QDialog 默认带上下文帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(620, 560)
        self.setMinimumSize(460, 400)

        styles.apply_to(self)
        self.setStyleSheet(self.styleSheet() + _PAGE_STYLE)

        try:
            # 直接用应用级图标：主程序启动时已设好，不必再生成一遍
            app = QApplication.instance()
            if app is not None and not app.windowIcon().isNull():
                self.setWindowIcon(app.windowIcon())
        except Exception as e:
            logger.debug(f"设置翻译窗口图标失败: {e}")

        self._init_ui()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _init_ui(self):
        """构建页面控件"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # 标题行：页面名 + 目标语言
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("文本翻译")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        to_label = QLabel("译为")
        to_label.setObjectName("sectionLabel")
        header.addWidget(to_label)

        self.lang_combo = QComboBox()
        for lang_code, lang_name in self._target_languages():
            self.lang_combo.addItem(lang_name, lang_code)
        self._select_initial_language()
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        header.addWidget(self.lang_combo)
        layout.addLayout(header)

        # 原文输入
        self.source_edit = QPlainTextEdit()
        self.source_edit.setObjectName("sourceEdit")
        self.source_edit.setPlaceholderText(
            "在这里输入或粘贴要翻译的文本…\n（Ctrl + Enter 直接翻译）")
        self.source_edit.installEventFilter(self)
        layout.addWidget(self.source_edit, 3)

        # 操作行：提示 + 清空 + 翻译
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.hint_label = QLabel("Ctrl + Enter 翻译")
        self.hint_label.setObjectName("sectionLabel")
        action_row.addWidget(self.hint_label)
        action_row.addStretch()

        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(self._on_clear)
        action_row.addWidget(self.clear_button)

        self.translate_button = QPushButton("翻译")
        self.translate_button.setObjectName("primary")
        self.translate_button.setMinimumWidth(88)
        self.translate_button.clicked.connect(self._on_translate)
        action_row.addWidget(self.translate_button)
        layout.addLayout(action_row)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        divider.setFrameShape(QFrame.NoFrame)
        layout.addWidget(divider)

        # 译文标题行：小转圈 + 状态 + 复制
        result_header = QHBoxLayout()
        result_header.setSpacing(6)
        result_label = QLabel("译文")
        result_label.setObjectName("sectionLabel")
        result_header.addWidget(result_label)

        self.spinner = _Spinner(self)
        result_header.addWidget(self.spinner)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusHint")
        result_header.addWidget(self.status_label)
        result_header.addStretch()

        self.copy_button = QPushButton("复制")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._on_copy)
        result_header.addWidget(self.copy_button)
        layout.addLayout(result_header)

        # 译文输出（只读，可选中复制）
        self.result_edit = QPlainTextEdit()
        self.result_edit.setObjectName("resultEdit")
        self.result_edit.setReadOnly(True)
        self.result_edit.setPlaceholderText("译文会显示在这里")
        layout.addWidget(self.result_edit, 2)

    def _target_languages(self):
        """目标语言列表：优先取翻译管理器的，缺失时用内置表"""
        try:
            manager = getattr(self.main_app, 'translation_manager', None)
            if manager is not None:
                langs = manager.get_target_languages()
                if langs:
                    return langs
        except Exception as e:
            logger.debug(f"读取目标语言列表失败: {e}")
        return TARGET_LANGUAGES

    def _select_initial_language(self):
        """初始目标语言跟随全局配置（这里改只影响本窗口）"""
        try:
            current = self.main_app.config_manager.get_target_lang()
        except Exception:
            current = 'zh'
        index = self.lang_combo.findData(current)
        if index >= 0:
            self.lang_combo.setCurrentIndex(index)

    def current_language(self):
        """当前选中的目标语言代码"""
        return self.lang_combo.currentData() or 'zh'

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        """Ctrl + Enter 在输入框里直接触发翻译"""
        if obj is self.source_edit and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                    and event.modifiers() & Qt.ControlModifier:
                self._on_translate()
                return True
        return super().eventFilter(obj, event)

    def _on_language_changed(self, _index):
        """换语言：已有译文时按新语言重译一次，免得用户再点一次"""
        if self._busy:
            return
        if self.result_edit.toPlainText().strip() and self._source_text():
            self._on_translate()

    def _on_clear(self):
        """清空原文与译文"""
        self.source_edit.clear()
        self.result_edit.clear()
        self._set_status('')
        self.copy_button.setEnabled(False)
        self.source_edit.setFocus()

    def _on_copy(self):
        """把译文复制到剪贴板"""
        text = self.result_edit.toPlainText().strip()
        if not text:
            return
        if clipboard.set_text(text):
            self._set_status('已复制')
            QTimer.singleShot(1200, self._clear_status)
        else:
            self._set_status('复制失败，请手动选择文本复制', error=True)

    def _clear_status(self):
        """清掉"已复制"这类一次性提示（窗口已销毁则忽略）"""
        try:
            self._set_status('')
        except RuntimeError:
            # 延迟回调期间窗口被关掉并销毁：没有可更新的控件了
            pass

    def _on_translate(self):
        """发起一次翻译请求（原文为空时不动）"""
        text = self._source_text()
        if not text:
            self._set_status('请先输入要翻译的文本', error=True)
            self.source_edit.setFocus()
            return
        if self._busy:
            return

        manager = getattr(self.main_app, 'translation_manager', None)
        if manager is None:
            self._set_status('翻译管理器未就绪', error=True)
            return

        self._request_id += 1
        request_id = self._request_id
        self._set_busy(True)
        self._set_status('')

        submitted = thread_pool_manager.submit(
            self._run_translation, text, self.current_language(), request_id)
        if submitted is None:
            # 线程池关闭或拒收：不会有任何线程回投结果，必须当场复原
            self._set_busy(False)
            self._set_status('后台任务无法启动，请稍后重试', error=True)

    def _source_text(self):
        """原文（去掉首尾空白）"""
        return self.source_edit.toPlainText().strip()

    # ------------------------------------------------------------------
    # 后台翻译
    # ------------------------------------------------------------------

    def _run_translation(self, text, target_lang, request_id):
        """在池线程翻译，结果回主线程展示"""
        manager = getattr(self.main_app, 'translation_manager', None)
        if manager is None:
            self._deliver(request_id, '', '翻译管理器未就绪')
            return
        try:
            result = manager.perform_translation(text, target_lang=target_lang)
        except Exception as e:
            logger.exception(f"文本翻译失败: {e}")
            self._deliver(request_id, '', f"翻译失败：{e}")
            return

        if is_translation_failure(result):
            self._deliver(request_id, '', '翻译失败，请检查网络或接口配置')
            return
        self._deliver(request_id, result or '', '')

    def _deliver(self, request_id, text, error):
        """把结果投回主线程（窗口可能已经关掉了）"""
        qt_dispatcher.dispatch_to_main_thread(
            self._apply_result, request_id, text, error)

    def _apply_result(self, request_id, text, error):
        """主线程回填译文"""
        if request_id != self._request_id:
            logger.debug("翻译结果已过期，丢弃")
            return
        self._set_busy(False)
        if error:
            self._set_status(error, error=True)
            return
        self.result_edit.setPlainText(text)
        self.copy_button.setEnabled(bool(text.strip()))
        if not text.strip():
            self._set_status('没有拿到译文，请重试', error=True)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def _set_busy(self, busy):
        """翻译进行中：按钮禁用 + 小转圈"""
        self._busy = busy
        self.translate_button.setEnabled(not busy)
        self.lang_combo.setEnabled(not busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _set_status(self, text, error=False):
        """状态行文案（错误用红色）"""
        self.status_label.setText(text)
        self.status_label.setObjectName("statusError" if error else "statusHint")
        style = self.status_label.style()
        style.unpolish(self.status_label)
        style.polish(self.status_label)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def showEvent(self, event):
        """每次打开都把焦点放到输入框"""
        super().showEvent(event)
        self.source_edit.setFocus()

    def closeEvent(self, event):
        """关闭时作废在飞请求：迟到的译文不再回填已关闭的窗口"""
        self._request_id += 1
        self._set_busy(False)
        super().closeEvent(event)
