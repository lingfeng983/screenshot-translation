#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文本翻译页面

给"随手输入一段文本让翻译一下"准备的独立窗口：左侧输入、右侧看译文，
不需要截图。翻译走与截图翻译同一条链路（TranslationManager），因此
接口、降级与缓存策略完全一致。

界面固定浅色、只用一列纵向布局，尽量少控件。

默认「自动识别」：原文是中文就译成英文，原文是英文就译成中文；
也可以在右上角手动指定目标语言。
"""

import logging
import re

from PyQt5.QtCore import QEvent, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget
)

from . import clipboard, styles
from .providers.base import TARGET_LANGUAGES
from .qt_dispatcher import qt_dispatcher
from .thread_pool import thread_pool_manager
from .toolbar_combo import ChevronComboBox
from .translation import is_translation_failure

logger = logging.getLogger('screenshot-translation')

# 目标语言下拉里的「自动识别」项：按原文语种决定中译英 / 英译中
AUTO_LANGUAGE = 'auto'
# 汉字（基本区 + 扩展 A + 兼容表意文字）
_HAN_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')
# 拉丁字母（含常见带音标字母）
_LATIN_RE = re.compile(r'[A-Za-z\u00c0-\u024f]')
# 日文假名 / 韩文谚文：都不属于本页的中英互译，别把日文里的汉字误判成中文
_KANA_RE = re.compile(r'[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]')
_HANGUL_RE = re.compile(r'[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]')

# 加载动画：小圆环，26px 一帧，肉眼看着就是连续转动
_SPINNER_INTERVAL = 33      # 帧间隔（毫秒）
_SPINNER_STEP = 18          # 每帧转过的角度
_SPINNER_ARC = 280          # 弧长（度）：留一段缺口才看得出在转

# 自动翻译防抖：停止输入/粘贴后这么久没有新改动才开始翻译。
# 太短会把键盘连打拆成多次请求，太长则粘贴完要等；600ms 接近"说完一句话"的间隔。
_AUTO_TRANSLATE_DELAY = 600

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
        # 最近一次已提交的 (原文, 目标语言)：同一份文本不重复自动翻译
        self._last_request = None
        # 请求期间原文又变了：等结果回来再补一次自动翻译
        self._auto_pending = False

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

        # 标题行：页面名 + 目标语言（首项「自动识别」为默认）
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("文本翻译")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        to_label = QLabel("译为")
        to_label.setObjectName("sectionLabel")
        header.addWidget(to_label)
        self.to_label = to_label

        self.lang_combo = ChevronComboBox()
        self.lang_combo.addItem("自动识别", AUTO_LANGUAGE)
        for lang_code, lang_name in self._target_languages():
            self.lang_combo.addItem(lang_name, lang_code)
        self._select_initial_language()
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        header.addWidget(self.lang_combo)
        layout.addLayout(header)

        # 自动翻译防抖定时器：输入/粘贴停止后触发
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(_AUTO_TRANSLATE_DELAY)
        self._auto_timer.timeout.connect(self._on_auto_translate)

        # 原文输入
        self.source_edit = QPlainTextEdit()
        self.source_edit.setObjectName("sourceEdit")
        self.source_edit.setPlaceholderText(
            "在这里输入或粘贴要翻译的文本…\n"
            "（自动识别：中文→英文，英文→中文；Ctrl + Enter 立即翻译）")
        self.source_edit.installEventFilter(self)
        self.source_edit.textChanged.connect(self._on_source_changed)
        layout.addWidget(self.source_edit, 3)

        # 操作行：提示 + 清空 + 翻译
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.hint_label = QLabel("")
        self.hint_label.setObjectName("sectionLabel")
        self._refresh_hint()
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
        """默认选中「自动识别」；下拉里没有该项时回落到全局目标语言"""
        index = self.lang_combo.findData(AUTO_LANGUAGE)
        if index < 0:
            index = self.lang_combo.findData(self._fallback_language())
        if index >= 0:
            self.lang_combo.setCurrentIndex(index)

    def _fallback_language(self):
        """纯数字/符号看不出语种时用的兜底目标语言（跟随全局配置）"""
        try:
            current = self.main_app.config_manager.get_target_lang()
        except Exception:
            current = 'zh'
        return current if current in ('zh', 'en') else 'zh'

    @staticmethod
    def _detect_source_language(text):
        """
        按字符占比判断原文语种

        旧实现"出现一个汉字就算中文、否则出现一个字母就算英文"，中文夹英文、
        日文、韩文、纯数字符号都会判错。这里按汉字与拉丁字母的数量比判断，
        假名/谚文直接放弃自动识别（交给全局目标语言或用户手动选择）。

        Returns:
            str 或 None: 'zh' / 'en' / 看不出时 None
        """
        value = text or ''
        if _KANA_RE.search(value) or _HANGUL_RE.search(value):
            return None
        han = len(_HAN_RE.findall(value))
        latin = len(_LATIN_RE.findall(value))
        # 中文夹少量英文（"这是 Windows 快捷键"）仍按中文译英；反过来
        # 英文句子里夹一两个汉字（"这是 a test" 之类）按英文译中。
        if han and han * 2 >= max(latin, 1):
            return 'zh'
        if latin:
            return 'en'
        return None

    def _detect_target_language(self, text):
        """自动识别方向：中文→英文，英文→中文，无法判断时按全局配置"""
        source = self._detect_source_language(text)
        if source == 'zh':
            return 'en'
        if source == 'en':
            return 'zh'
        return self._fallback_language()

    def current_language(self, text=None):
        """本次请求的目标语言代码

        Args:
            text (str): 本次要翻译的原文；None 时取输入框当前内容。
                选中「自动识别」时按原文语种决定 zh / en。
        """
        selected = self.lang_combo.currentData() or AUTO_LANGUAGE
        if selected != AUTO_LANGUAGE:
            return selected
        return self._detect_target_language(
            self._source_text() if text is None else text)

    def _refresh_hint(self):
        """操作行提示：显示当前识别出的翻译方向，或手动指定的目标语言"""
        selected = self.lang_combo.currentData() or AUTO_LANGUAGE
        # 自动识别时「译为 自动识别」读着别扭，把标签收起来只留下拉
        self.to_label.setVisible(selected != AUTO_LANGUAGE)
        suffix = " · Ctrl + Enter 立即翻译"
        if selected != AUTO_LANGUAGE:
            name = dict(self._target_languages()).get(selected, selected)
            self.hint_label.setText(f"译为{name}{suffix}")
            return
        source = self._detect_source_language(self._source_text())
        if source is None:
            self.hint_label.setText(
                "自动识别 · 中文→英文 / 英文→中文" + suffix)
        else:
            from_name, to_name = (("中文", "英文") if source == 'zh'
                                 else ("英文", "中文"))
            self.hint_label.setText(f"已识别 {from_name} → 译为{to_name}{suffix}")

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

    def _on_source_changed(self):
        """原文变化（键入或粘贴）：刷新识别方向，停止输入后再自动翻译"""
        self._refresh_hint()
        if not self._source_text():
            self._auto_timer.stop()
            return
        self._auto_timer.start()

    def _on_auto_translate(self):
        """防抖结束：文本或语言没变就不重复请求，忙碌则等结果回来再补"""
        text = self._source_text()
        if not text:
            return
        if (text, self.current_language(text)) == self._last_request:
            return
        if self._busy:
            # 已有请求在飞：立刻再发一次只会被 _on_translate 挡掉，
            # 记下待办，等结果回来由 _apply_result 补发
            self._auto_pending = True
            return
        self._on_translate()

    def _on_language_changed(self, _index):
        """换语言：刷新方向提示，有原文就按新语言翻译，免得用户再点一次"""
        self._refresh_hint()
        if self._busy:
            # 结果还在飞：记下待办，等回填后按新语言补译一次，
            # 否则用户看到的是旧语言的译文却以为已经切换了
            self._auto_pending = True
            return
        if self._source_text():
            self._on_translate()

    def _on_clear(self):
        """清空原文与译文，并作废所有在飞请求

        只清控件不够：忙碌时点清空，旧请求回来仍会把译文贴回去（实测可复现），
        而 _busy 要等那次回填才复位，期间「翻译」按钮一直是灰的。做法与
        closeEvent 一致——递增请求序号让旧结果过期，同时立刻结束忙碌态。
        """
        self._auto_timer.stop()
        self._auto_pending = False
        self._request_id += 1
        self._set_busy(False)
        self._last_request = None
        self.source_edit.clear()
        self.result_edit.clear()
        self._refresh_hint()
        self._set_status('')
        self.copy_button.setEnabled(False)
        self.source_edit.setFocus()

    def _on_copy(self):
        """把译文复制到剪贴板（非阻塞重试，不把 GUI 线程卡在剪贴板占用上）"""
        text = self.result_edit.toPlainText().strip()
        if not text:
            return

        def done(ok):
            try:
                if ok:
                    self._set_status('已复制')
                    QTimer.singleShot(1200, self._clear_status)
                else:
                    self._set_status('复制失败，请手动选择文本复制', error=True)
            except RuntimeError:
                # 回调排在事件循环里，期间窗口可能已经关掉
                pass

        clipboard.set_text_later(text, on_done=done)

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
        # 自动识别在这里定型：同一份原文 + 同一个方向才允许去重复请求
        target_lang = self.current_language(text)
        self._set_busy(True)
        self._set_status('')

        submitted = thread_pool_manager.submit(
            self._run_translation, text, target_lang, request_id)
        if submitted is None:
            # 线程池关闭或拒收：不会有任何线程回投结果，必须当场复原
            self._set_busy(False)
            self._auto_pending = False
            self._set_status('后台任务无法启动，请稍后重试', error=True)
            return
        # 记下这份文本已提交：自动翻译据此避免同一内容重复请求
        self._last_request = (text, target_lang)

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
        # 请求期间用户改过原文或换过语言：这次结果已经对不上当前表单了。
        # 只排一次重译，绝不把原文或旧语言的译文贴进译文框——text 是本次
        # 请求带回来的译文，不能拿来判断要不要重译（曾经把原文当成译文显示）。
        if self._auto_pending:
            self._auto_pending = False
            pending_text = self._source_text()
            if pending_text and (pending_text, self.current_language(pending_text)) \
                    != self._last_request:
                self._auto_timer.start()
                self._set_status('内容已变化，正在重新翻译…')
                return
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
        self._auto_timer.stop()
        self._auto_pending = False
        self._request_id += 1
        self._set_busy(False)
        super().closeEvent(event)
