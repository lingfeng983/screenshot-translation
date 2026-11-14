#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图管理器
负责处理屏幕截图相关功能
包括屏幕区域选择、半透明遮罩等
"""

from concurrent import futures

import threading
import time

from PIL import ImageGrab
from PyQt5.QtCore import (Qt, QRect, QRectF, QPoint, QPointF, QSize, QTimer,
                          pyqtSignal)
from PyQt5.QtGui import QPainter, QPen, QColor, QCursor, QPixmap
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

# 导入必要的模块
from . import capture_magnifier
from . import capture_output
from . import clipboard
from . import result_render
from . import vector_icons
from .capture_annotation import (
    DEFAULT_FONT_SIZE, DEFAULT_WIDTH, FONT_CHOICES, PRESET_COLORS, TOOL_SELECT,
    WIDTH_CHOICES)
from .capture_editor import CaptureEditor
from .capture_toolbar import CaptureToolbar
from .error_handler import error_handler
from .ocr import is_ocr_notice
from .overlay_toolbar import OverlayToolbar
from .pin_window import PinWindow
from .qt_dispatcher import get_qt_app, qt_dispatcher
from .text_block import group_paragraphs
from .thread_pool import thread_pool_manager
from .translation import is_translation_failure

import logging

logger = logging.getLogger('screenshot-translation')


class _RequestToken:
    """
    一次“选区 → 识别 → 翻译”请求的取消信号与整体时限

    后台任务跑完之前用户可能已经关掉遮罩或重画了选区：结果会被序号校验丢弃，
    但网络请求还在继续烧连接与额度，线程池 worker 也被占着。时限则是因为
    OCR 与翻译各有自己的重试与降级链，两段各自“合理”加起来就能到分钟级。
    """

    def __init__(self, budget_seconds):
        """
        Args:
            budget_seconds (float): 本次请求的总时限（秒）
        """
        self.cancel_event = threading.Event()
        self.deadline = time.monotonic() + budget_seconds

    def cancel(self):
        """标记本次请求不再需要"""
        self.cancel_event.set()

    @property
    def cancelled(self):
        """本次请求是否已被取消"""
        return self.cancel_event.is_set()


class _ScrollCaptureBanner(QWidget):
    """
    手动长截图期间的控制面板：实时进度 + 完成 / 取消

    遮罩为了不挡滚动、不进抓帧必须隐藏，用户因此看不到任何可点的地方；
    这个小横幅放在选区之外，既告诉用户已经拼了多长，也是唯一的结束入口。
    抓帧线程只写 ScrollProgress，这里用定时器读，不跨线程碰 Qt。
    """

    _REFRESH_MS = 200

    def __init__(self, finish_event, cancel_event, progress):
        super().__init__()
        self._finish = finish_event
        self._cancel = cancel_event
        self._progress = progress
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_StyledBackground, True)
        # 显示时不抢焦点：焦点留在被滚动的窗口上，滚轮/翻页键才继续生效
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet(
            'QWidget { background: #232629; border: 1px solid #3a3d42;'
            ' border-radius: 8px; }'
            'QLabel { border: none; background: transparent; color: #e8e8e8;'
            ' font-size: 12px; }'
            'QLabel#ScrollHint { color: #9aa4b2; font-size: 11px; }'
            'QPushButton { border: 1px solid #3f4a5e; border-radius: 5px;'
            ' background: #2b303a; color: #e8e8e8; font-size: 12px;'
            ' padding: 4px 12px; }'
            'QPushButton:hover { background: #363d4a; }'
            'QPushButton#ScrollFinish { background: #3b82f6;'
            ' border-color: #3b82f6; color: #ffffff; }'
            'QPushButton#ScrollFinish:hover { background: #5a95f8; }'
            'QPushButton#ScrollAbort { color: #ff6b6b; }'
            'QPushButton#ScrollAbort:hover { background: #5a2a2e;'
            ' border-color: #7a3238; }')

        self._status = QLabel(self)
        self._hint = QLabel(self)
        self._hint.setObjectName('ScrollHint')
        self._hint.setWordWrap(False)

        finish_button = QPushButton(vector_icons.icon('check', '#ffffff'),
                                    '完成', self)
        finish_button.setObjectName('ScrollFinish')
        finish_button.setToolTip('结束长截图并复制结果（也可再按一次截图快捷键）')
        finish_button.clicked.connect(self._on_finish)
        abort_button = QPushButton(vector_icons.icon('close', '#ff6b6b'),
                                   '取消', self)
        abort_button.setObjectName('ScrollAbort')
        abort_button.setToolTip('丢弃本次长截图')
        abort_button.clicked.connect(self._on_cancel)
        for button in (finish_button, abort_button):
            button.setCursor(QCursor(Qt.PointingHandCursor))
            button.setIconSize(QSize(14, 14))

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        buttons.addWidget(finish_button)
        buttons.addWidget(abort_button)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(14)
        top.addWidget(self._status)
        top.addStretch(1)
        top.addLayout(buttons)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(4)
        layout.addLayout(top)
        layout.addWidget(self._hint)

        # 文字每 200ms 变一次，宽度按最长文案顶死，免得横幅一直抖
        self._status.setMinimumWidth(self._text_width(
            self._status, '长截图  已拼接 40000 px · 400 段'))
        self._hint.setMinimumWidth(self._text_width(
            self._hint, '抓帧中断，点「完成」保留已拼接部分'))
        self._refresh()
        self.adjustSize()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(self._REFRESH_MS)

    @staticmethod
    def _text_width(label, text):
        """按样式表生效后的字体量文本宽度（没 polish 时度量偏大）"""
        label.ensurePolished()
        return label.fontMetrics().horizontalAdvance(text)

    def _refresh(self):
        state = self._progress
        if state is None:
            return
        self._status.setText(
            f'长截图  已拼接 {state.height} px · {state.segments} 段')
        self._hint.setText(state.hint)

    def _stop(self, event):
        self._timer.stop()
        if event is not None:
            event.set()
        self.hide()

    def _on_finish(self):
        self._stop(self._finish)

    def _on_cancel(self):
        self._stop(self._cancel)

    def stop(self):
        """结果已回投或被外部结束：停表，不再改文案"""
        self._timer.stop()


def desktop_bounds():
    """
    取遮罩要覆盖的桌面范围与抓拍方式

    有副屏时覆盖整个虚拟桌面（副屏在主屏左侧/上方时坐标可以为负），让整块桌面
    一起被冻结。只有在各屏像素倍率一致时才整体覆盖：倍率不一致时单一缩放系数
    无法把逻辑坐标换算成抓拍图里的像素，此时退回主屏（原行为）。

    Returns:
        tuple[QRect, bool] 或 None: (覆盖矩形（逻辑坐标）, 抓拍是否需要 all_screens)；
            取不到屏幕信息时返回 None
    """
    try:
        screens = QApplication.screens()
        if not screens:
            return None
        primary_geo = QRect((QApplication.primaryScreen() or screens[0]).geometry())
        scales = {round(float(s.devicePixelRatio() or 1.0), 3) for s in screens}
        if len(scales) > 1:
            logger.info(f"各显示器像素倍率不一致 {sorted(scales)}，遮罩只覆盖主屏")
            return primary_geo, False
        union = QRect(primary_geo)
        for screen in screens:
            union = union.united(screen.geometry())
        logger.info(f"遮罩覆盖桌面 {union.width()}x{union.height()}"
                    f"（原点 {union.x()},{union.y()}，共 {len(screens)} 块屏幕）")
        return union, union != primary_geo
    except Exception as e:
        logger.warning(f"计算桌面范围失败，退回主屏: {e}")
        return None


class ScreenshotManager:
    """
    截图管理器类
    负责处理截图相关功能
    """

    # 一次“识别+翻译”的总时限（秒）：OCR 与翻译链共用，超了就丢弃后续尝试
    _REQUEST_BUDGET = 40.0

    def __init__(self):
        """
        初始化截图管理器
        """
        self.ocr_manager = None
        self.translation_manager = None
        self.config_manager = None
        # 截图窗口的强引用，防止显示后被GC回收
        self._screenshot_widget = None
        # 最近一次选区，供失败后原地重试
        self._last_request = None
        # 最近一次确认的选区（遮罩局部逻辑坐标），供「重复上次区域」截图
        self._last_region = None
        # 滚动截图的结束/取消令牌：遮罩关闭时让后台抓帧立刻收手
        self._scroll_finish = None
        self._scroll_cancel = None
        # 在途请求的取消令牌：新请求与遮罩关闭都要让旧的立刻收手
        self._inflight = []
        self._token_lock = threading.Lock()

    # ---- 在途请求的取消与时限 ----

    def _new_request_token(self):
        """
        开始一次新的在途请求，并把此前所有请求标记为取消

        旧请求的结果本来就会因选区序号对不上被丢弃，让它跑完只是白占线程池
        与网络额度；关遮罩后再按一次快捷键尤其如此。

        Returns:
            _RequestToken: 本次请求的令牌
        """
        token = _RequestToken(self._REQUEST_BUDGET)
        with self._token_lock:
            for pending in self._inflight:
                pending.cancel()
            self._inflight = [token]
        return token

    def _finish_request(self, token):
        """请求跑完（成功或失败）后摘掉令牌，不再对它发取消"""
        if token is None:
            return
        with self._token_lock:
            self._inflight = [pending for pending in self._inflight
                              if pending is not token]

    def _cancel_inflight(self):
        """遮罩关闭：在途的识别与翻译立刻收手"""
        with self._token_lock:
            pending, self._inflight = self._inflight, []
        for token in pending:
            token.cancel()

    def set_config_manager(self, config_manager):
        """
        设置配置管理器（遮罩参数从 [Overlay] 段读取）

        Args:
            config_manager: 配置管理器实例
        """
        self.config_manager = config_manager

    def set_managers(self, ocr_manager, translation_manager):
        """
        设置OCR管理器和翻译管理器

        Args:
            ocr_manager: OCR管理器实例
            translation_manager: 翻译管理器实例
        """
        self.ocr_manager = ocr_manager
        self.translation_manager = translation_manager

    def start_screenshot(self):
        """
        启动截图流程（框选后识别并翻译）
        """
        logger.info("启动截图流程")

        try:
            # 统一通过调度器保证截图窗口在主线程创建
            qt_dispatcher.dispatch_to_main_thread(self._run_screenshot)
        except Exception:
            # 打包后没有控制台，print_exc 那路等于什么都没留
            logger.exception("启动截图流程失败")

    def start_capture(self, preset=None):
        """
        启动纯截图流程：框选后进入编辑态（标注/取字/滚动截图/输出），不跑翻译

        Args:
            preset: None 交互框选；'fullscreen' 整屏；('window', (l,t,r,b))
                活动窗口（物理像素）；QRect 重复上次区域（局部逻辑坐标）
        """
        logger.info("启动截图流程（仅截图）")

        try:
            qt_dispatcher.dispatch_to_main_thread(
                self._run_screenshot, True, preset)
        except Exception:
            logger.exception("启动纯截图流程失败")

    def start_capture_fullscreen(self):
        """全屏截图：直接把整块虚拟桌面送进编辑态"""
        self.start_capture('fullscreen')

    def start_capture_window(self):
        """活动窗口截图：在遮罩立起来之前取前台窗口，避免抓到遮罩自己"""
        from .window_enum import get_foreground_window_rect
        rect = get_foreground_window_rect()
        if rect is None:
            logger.warning("取不到前台窗口，退回交互框选")
            self.start_capture()
            return
        self.start_capture(('window', rect))

    def start_capture_repeat(self):
        """重复上次框选区域；没有历史时退回交互框选"""
        if self._last_region is None:
            logger.info("没有上次的选区，退回交互框选")
            self.start_capture()
            return
        self.start_capture(self._last_region)

    def _run_screenshot(self, capture_only=False, preset=None):
        """
        运行截图流程
        确保在主线程中运行，避免Qt线程安全问题

        Args:
            capture_only (bool): True 时框选后跳过识别翻译
            preset: 预设选区（见 start_capture）
        """
        try:
            # 获取QApplication实例（非主线程不会新建）
            app = get_qt_app()
            if app is None:
                logger.error("无法获取QApplication实例，截图流程终止")
                return

            # 已有遮罩时再次按热键视为取消：否则新遮罩会把带暗角的屏幕当作背景截取。
            # 但引用也可能只是残留（此前关闭时没清掉，或是被外部销毁了），
            # 这种情况下按键不能被吞掉，否则用户看到的就是"快捷键没反应"
            if self._screenshot_widget is not None:
                if self._scroll_finish is not None:
                    # 长截图期间遮罩刻意隐藏，isVisible() 为 False：此时不能
                    # 另起一个遮罩（抓帧还在跑）。把这次热键当成「完成长
                    # 截图」：置位结束事件，让后台任务按原路径收手并回投结果，
                    # 恢复遮罩/清在途标记只走那一处。
                    # 这样即使横幅因选区过大没显示，也有恒定的结束入口。
                    logger.info("长截图进行中，本次热键作为完成长截图处理")
                    self._scroll_finish.set()
                    return
                if self._widget_is_live(self._screenshot_widget):
                    logger.info("截图遮罩已显示，本次热键作为取消处理")
                    self._release_screenshot_widget()
                    return
                logger.warning("发现残留的截图窗口引用，已丢弃并继续本次截图")
                self._screenshot_widget = None
                self._last_request = None

            # 创建截图窗口，保留强引用避免被GC回收；传入配置以读取遮罩参数
            self._screenshot_widget = ScreenshotWidget(
                config_manager=self.config_manager, capture_only=capture_only,
                preset=preset)

            # 连接信号
            widget = self._screenshot_widget
            widget.region_selected.connect(self._on_region_selected)
            widget.retranslate_requested.connect(self._request_retranslate)
            widget.retry_requested.connect(self._on_retry_requested)
            widget.screenshot_canceled.connect(self._release_screenshot_widget)
            widget.region_committed.connect(self._on_region_committed)
            widget.ocr_requested.connect(self._on_ocr_requested)
            widget.scroll_requested.connect(self._on_scroll_requested)

            # 显示截图窗口：遮罩自己已经把几何设成整个桌面，跨屏时只能用 show()
            # （showFullScreen 只会把窗口铺满它所在的那一块显示器）
            if self._screenshot_widget.covers_multiple_screens():
                self._screenshot_widget.show()
            else:
                self._screenshot_widget.showFullScreen()
            # 遮罩必须抢到键盘焦点，ESC/Enter 等按键才会送到 keyPressEvent，
            # 否则遮罩只能靠鼠标操作退出
            self._screenshot_widget.activateWindow()
            self._screenshot_widget.raise_()
            self._screenshot_widget.setFocus()

            logger.info("截图窗口已显示")
            # 遮罩是全屏置顶窗口：此时弹模态错误框会把用户卡在
            # “遮罩关不掉、框又看不见”的位置，失败文案改由遮罩内展示
            error_handler.set_modal_allowed(False)
            # 不在此处调用 app.exec_()：主事件循环已在运行，嵌套调用会立即返回
        except Exception as e:
            logger.exception(f"截图流程失败: {e}")
            pending = self._screenshot_widget
            if pending is None or not self._widget_is_live(pending):
                # 遮罩没能立起来，恢复弹窗（否则错误提示从此静默）
                error_handler.set_modal_allowed(True)

    @staticmethod
    def _widget_is_live(widget):
        """
        遮罩窗口是否仍然有效（未被销毁且正在显示）

        Args:
            widget: 截图窗口实例

        Returns:
            bool: 底层 C++ 对象已销毁或窗口不可见时返回 False
        """
        try:
            return bool(widget.isVisible())
        except RuntimeError:
            # 已销毁的包装对象：访问任何属性都会抛 RuntimeError
            return False

    def _release_screenshot_widget(self):
        """关闭并释放截图窗口引用（窗口已被销毁时可安全调用）"""
        # 长截图是独立后台任务，遮罩一关就没有结果去处了，直接丢弃
        if self._scroll_cancel is not None:
            self._scroll_cancel.set()
        self._scroll_finish = None
        self._scroll_cancel = None
        # 遮罩都没了，在途的识别与翻译再无去处，立刻让它们收手
        self._cancel_inflight()
        # 全屏遮罩不在了，错误提示可以正常弹窗
        error_handler.set_modal_allowed(True)
        widget = self._screenshot_widget
        self._screenshot_widget = None
        self._last_request = None
        if widget is not None:
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                logger.debug("截图窗口已被销毁，跳过关闭")

    _MAX_BLOCK_REQUESTS = 8   # 逐块翻译的请求数上限，超过则退回整段翻译
    _BLOCK_TRANSLATE_WORKERS = 4  # 逐块兜底翻译的并发数（上限受块数约束）

    def _submit_or_fail(self, widget, func, *args):
        """
        提交后台任务，线程池拒收时当场给出可重试提示

        submit 返回 None 意味着没有任何线程会来回投结果：不处理的话遮罩
        就停在小转圈上转个没完，用户只能 ESC 重来。

        Args:
            widget: 当前遮罩窗口
            func (Callable): 池线程里跑的函数
            *args: 传给 func 的参数

        Returns:
            bool: 是否提交成功
        """
        if thread_pool_manager.submit(func, *args) is not None:
            return True
        logger.warning("后台任务提交失败（线程池不可用或已关闭）")
        widget.show_failure('后台任务无法启动，请按 R 重试')
        return False

    def _on_region_selected(self, image, region):
        """
        遮罩选区确定：后台识别与翻译，结果回投到遮罩内展示

        Args:
            image (PIL.Image.Image): 选区图像（物理像素）
            region (QRect): 选区在屏幕上的逻辑矩形
        """
        logger.info("选区已确定，后台执行OCR与翻译")
        widget = self._screenshot_widget
        if widget is None:
            return
        self._last_request = (image, region)
        target_lang = widget.current_target_lang()
        self._submit_or_fail(
            widget, self._process_region, widget, image, region, target_lang,
            False, widget.current_request_id(), self._new_request_token())

    def _on_region_committed(self, rect):
        """记下本次确认的选区，供「重复上次区域」直接复用"""
        try:
            self._last_region = QRect(rect)
        except Exception:
            self._last_region = None

    # ---- 纯截图专项：OCR 取字 / 滚动截图 ----
    # 两者都跑在线程池里，结果回投主线程；遮罩关闭后迟到结果按序号丢弃。

    def _on_ocr_requested(self, image):
        widget = self._screenshot_widget
        if widget is None or image is None:
            return
        request_id = widget.current_request_id()
        if thread_pool_manager.submit(
                self._run_ocr, widget, image, request_id) is None:
            logger.warning("OCR 取字任务提交失败")
            widget.on_ocr_finished('')
            return
        widget.set_capture_busy(True)

    def _run_ocr(self, widget, image, request_id):
        try:
            from . import capture_ocr
            # 复用主应用的 OCRManager，省掉一次模型加载且不碰网络
            text = capture_ocr.extract_text(image,
                                            ocr_manager=self.ocr_manager)
        except Exception:
            logger.exception("本地 OCR 取字失败")
            text = ''
        try:
            qt_dispatcher.dispatch_to_main_thread(
                self._apply_ocr_result, widget, text, request_id)
        except Exception:
            # 连回投都失败时遮罩的忙碌态由 widget 上的超时兜底复位
            logger.exception("本地 OCR 取字结果回投失败")

    def _apply_ocr_result(self, widget, text, request_id):
        if widget is None or widget is not self._screenshot_widget:
            return
        try:
            if request_id == widget.current_request_id():
                widget.on_ocr_finished(text)
        except RuntimeError:
            logger.debug("遮罩已销毁，丢弃迟到的 OCR 结果")

    def _on_scroll_requested(self, context, image):
        widget = self._screenshot_widget
        if widget is None or image is None:
            return
        from .capture_scroll import POLL_MS, ScrollProgress
        request_id = widget.current_request_id()
        options = {
            'poll_ms': widget.capture_option_int('scroll_poll_ms', POLL_MS,
                                                 40, 1000),
            'max_height': widget.capture_option_int(
                'scroll_max_height', 12000, 1000, 40000),
        }
        finish_event = threading.Event()
        cancel_event = threading.Event()
        progress = ScrollProgress(image.height)
        self._scroll_finish = finish_event
        self._scroll_cancel = cancel_event
        if thread_pool_manager.submit(
                self._run_scroll, widget, context, image, request_id,
                options, finish_event, cancel_event, progress) is None:
            logger.warning("滚动截图任务提交失败")
            self._scroll_finish = None
            self._scroll_cancel = None
            widget.on_scroll_finished(None)
            return
        widget.show_scroll_banner(finish_event, cancel_event, progress)

    def _run_scroll(self, widget, context, image, request_id, options,
                    finish_event, cancel_event, progress):
        try:
            from . import capture_scroll
            long_image = capture_scroll.capture_scrolling(
                context, image, finish_event=finish_event,
                cancel_event=cancel_event, progress=progress, **options)
        except Exception:
            logger.exception("滚动截图失败")
            long_image = None
        try:
            qt_dispatcher.dispatch_to_main_thread(
                self._apply_scroll_result, widget, long_image, request_id,
                cancel_event)
        except Exception:
            logger.exception("滚动截图结果回投失败")
            # 回投不出去时 _apply_scroll_result 永远不会跑：令牌必须当场作废
            # （只作废本轮的，别误清新一轮），否则 _run_screenshot 会把此后
            # 所有截图热键当成「滚动中」一直吞掉。
            if self._scroll_cancel is cancel_event:
                self._scroll_finish = None
                self._scroll_cancel = None
            try:
                # 再试一次把恢复动作投回主线程；若连这次都失败（事件循环已
                # 退出），就只能靠 widget 上的兜底定时器复位编辑态了。
                qt_dispatcher.dispatch_to_main_thread(
                    self._apply_scroll_result, widget, None, request_id,
                    cancel_event, True)
            except Exception:
                logger.exception("滚动截图恢复调度再次失败")

    def _apply_scroll_result(self, widget, long_image, request_id, cancel_event,
                             force=False):
        # 先校验归属：迟到的旧结果不能把新一轮滚动的令牌清掉。
        # 只比 widget 不够——同一个遮罩上先取消再开一轮长截图时，widget 仍是
        # 同一个，旧结果到达就会把新轮的 finish/cancel 令牌清空，此后「完成」
        # 与「取消」都失灵。令牌身份才是这一轮的唯一凭据。
        if widget is None or widget is not self._screenshot_widget:
            return
        # force 只用于「回投失败后当场清过令牌」的兜底恢复
        if not force and self._scroll_cancel is not cancel_event:
            logger.debug("迟到的长截图结果，保留新一轮的滚动令牌")
            return
        # 任务已收手：清掉在途标记
        self._scroll_finish = None
        self._scroll_cancel = None
        try:
            if request_id == widget.current_request_id():
                widget.on_scroll_finished(long_image,
                                          canceled=cancel_event.is_set())
        except RuntimeError:
            logger.debug("遮罩已销毁，丢弃迟到的滚动截图结果")

    def _on_retry_requested(self):
        """失败后按 R 重试：重走完整链路并跳过翻译缓存"""
        widget = self._screenshot_widget
        if widget is None or self._last_request is None:
            return
        image, region = self._last_request
        logger.info("按用户请求重试识别与翻译")
        widget.set_result_busy(True)
        self._submit_or_fail(
            widget, self._process_region, widget, image, region,
            widget.current_target_lang(), True, widget.current_request_id(),
            self._new_request_token())

    def _request_retranslate(self, target_lang, force_refresh):
        """
        工具条触发重新翻译：只重走翻译，不重新OCR

        Args:
            target_lang (str 或 None): 目标语言
            force_refresh (bool): 是否跳过翻译缓存
        """
        widget = self._screenshot_widget
        if widget is None or not widget.has_result():
            return
        payload = widget.current_payload()
        widget.set_result_busy(True)
        self._submit_or_fail(
            widget, self._retranslate, widget, payload, target_lang,
            force_refresh, self._new_request_token())

    def _retranslate(self, widget, payload, target_lang, force_refresh, token=None):
        """在池线程重走翻译链路，完成后回主线程更新结果"""
        updated = dict(payload)
        updated['target_lang'] = self._effective_lang(target_lang)
        try:
            updated['translated_text'] = self._translate_all(
                payload['original_text'], target_lang, force_refresh, token)
            # 把整段译文传给行级对齐：重译（跳过缓存）时不再重复请求同一段文本
            updated['block_texts'] = self._translate_blocks(
                payload['blocks'], payload['original_text'], target_lang,
                force_refresh, whole=updated['translated_text'], token=token)
            updated['error'] = self._translation_failure_message(
                updated['translated_text'], updated['block_texts'])
        except Exception as e:
            logger.error(f"重新翻译失败: {e}")
            updated['error'] = str(e)
        finally:
            self._finish_request(token)
        if token is not None and token.cancelled:
            logger.info("请求已被取消，不回投重译结果")
            return
        qt_dispatcher.dispatch_to_main_thread(self._apply_result, widget, updated)

    def _apply_result(self, widget, payload):
        """把结果交给遮罩展示；遮罩已关闭或选区已重画时直接丢弃"""
        if self._screenshot_widget is not widget:
            logger.info("遮罩已关闭，丢弃迟到的识别结果")
            return
        if payload.get('request_id') != widget.current_request_id():
            logger.info("选区已重新框选，丢弃过期识别结果")
            return
        if payload.get('error'):
            widget.show_failure(payload['error'])
        else:
            widget.show_result(payload)

    def _process_region(self, widget, image, region, target_lang, force_refresh,
                        request_id=None, token=None):
        """
        在池线程完成识别与翻译，任何异常都转成失败提示回投主线程

        Args:
            widget: 发起本次请求的遮罩窗口
            image (PIL.Image.Image): 选区图像
            region (QRect): 选区屏幕矩形
            target_lang (str 或 None): 目标语言，None 用全局配置
            force_refresh (bool): 是否跳过翻译缓存
            request_id (int 或 None): 发起请求时的选区序号
            token (_RequestToken 或 None): 本次请求的取消/时限令牌
        """
        payload = {
            'original_text': '', 'translated_text': '', 'blocks': [],
            'block_texts': [], 'image': image, 'region': region,
            'languages': self._target_languages(),
            'target_lang': self._effective_lang(target_lang),
            'request_id': request_id, 'error': None,
        }
        try:
            original_text, blocks = self._recognize(image, token)
            payload['original_text'] = original_text
            payload['blocks'] = blocks
            # OCR 降级提示（图里没字/引擎不可用）是给用户看的话，不是原文：
            # 送翻译只会白等一次网络请求，还会把提示文本当译文展示
            if is_ocr_notice(original_text):
                qt_dispatcher.dispatch_to_main_thread(
                    self._apply_result, widget, payload)
                return
            if token is not None and token.cancelled:
                logger.info("遮罩已关闭，放弃本次识别结果的后半段流程")
                return
            payload['translated_text'] = self._translate_all(
                original_text, target_lang, force_refresh, token)
            payload['block_texts'] = self._translate_blocks(
                blocks, original_text, target_lang, force_refresh,
                whole=payload['translated_text'], token=token)
            # 一条译文都没拿到时不能"展示原图"蒙过去：按失败处理，给可重试卡片
            payload['error'] = self._translation_failure_message(
                payload['translated_text'], payload['block_texts'])
        except Exception as e:
            error_handler.handle_error(e, "截图处理")
            payload['error'] = str(e)
        finally:
            self._finish_request(token)

        if token is not None and token.cancelled:
            logger.info("请求已被取消，不回投结果")
            return
        qt_dispatcher.dispatch_to_main_thread(self._apply_result, widget, payload)

    def _recognize(self, image, token=None):
        """
        识别选区：优先拿带坐标的行块

        Args:
            image (PIL.Image.Image): 选区图像
            token (_RequestToken 或 None): 本次请求的取消/时限令牌

        Returns:
            tuple[str, list[TextBlock]]: (整段文本, 行块列表)
        """
        if self.ocr_manager is None:
            return '[未初始化OCR管理器]', []
        deadline = token.deadline if token is not None else None
        cancel_event = token.cancel_event if token is not None else None
        text, blocks = self.ocr_manager.perform_ocr_with_blocks(
            image, deadline, cancel_event)
        logger.info(f"OCR完成：{len(text)} 字符，{len(blocks)} 行块")
        return text, blocks

    def _translate_all(self, text, target_lang, force_refresh=False, token=None):
        """翻译整段文本，失败时返回空串而不抛出"""
        if self.translation_manager is None or not (text or '').strip():
            return ''
        try:
            kwargs = {}
            if token is not None:
                # 共用同一条链的时限与取消信号：逐块并发翻译时不会各等各的
                kwargs['cancel_event'] = token.cancel_event
                kwargs['deadline'] = token.deadline
            return self.translation_manager.perform_translation(
                text, force_refresh=force_refresh, target_lang=target_lang,
                **kwargs)
        except Exception as e:
            logger.error(f"翻译失败: {e}")
            return ''

    def _translation_failure_message(self, whole, block_texts):
        """
        判定本次到底有没有拿到可用译文

        降级链全失败时 perform_translation 返回的是 "[翻译失败 …]" 标记文本，
        原样贴回选区看起来就像译文；这里把它归为失败，改走可重试的失败卡片。

        Args:
            whole (str): 整段译文（可能是失败标记）
            block_texts (list[str]): 行级译文

        Returns:
            str 或 None: 全部失败时给失败文案，任一处有真译文时返回None
        """
        candidates = [whole] + [t for t in (block_texts or []) if t]
        if any(t.strip() and not is_translation_failure(t) for t in candidates):
            return None
        if any(is_translation_failure(t) for t in candidates):
            return '翻译失败：所有接口都没有返回译文'
        return None

    def _translate_blocks(self, blocks, full_text, target_lang, force_refresh=False,
                          whole=None, token=None):
        """
        行级译文：整段一次请求后按行对齐，对不齐时逐块请求兜底

        Args:
            blocks (list[TextBlock]): 行块列表
            full_text (str): 整段原文（行块拼接）
            target_lang (str 或 None): 目标语言
            force_refresh (bool): 是否跳过翻译缓存
            whole (str 或 None): 已算好的整段译文；None 时自行请求
            token (_RequestToken 或 None): 本次请求的取消/时限令牌

        Returns:
            list[str]: 与 blocks 等长的译文；拿不到对齐结果时返回[]
        """
        if not blocks or self.translation_manager is None:
            return []

        if whole is None:
            whole = self._translate_all(full_text, target_lang, force_refresh, token)
        # 失败标记不参与按行对齐：单块选区时它"行数正好相等"，会被当译文贴上原文
        if is_translation_failure(whole):
            whole = ''
        lines = [line.strip() for line in (whole or '').splitlines() if line.strip()]
        if len(lines) == len(blocks):
            return lines

        if len(blocks) > self._MAX_BLOCK_REQUESTS:
            logger.info(f"译文行数 {len(lines)} 与块数 {len(blocks)} 不符，"
                        f"超过逐块请求上限，降级为整体气泡展示")
            return []

        logger.info(f"译文行数 {len(lines)} 与块数 {len(blocks)} 不符，改为逐块翻译")
        return self._translate_blocks_parallel(blocks, target_lang, force_refresh, token)

    def _translate_blocks_parallel(self, blocks, target_lang, force_refresh, token=None):
        """
        逐块翻译并发执行（返回值与 blocks 等长，顺序不变）

        块与块之间没有依赖，串行发 8 个请求实测要 2.1 秒，并发后压到
        0.8 秒左右；请求共用同一个 requests.Session，连接池可复用。
        这里自建临时线程池而不用全局池：本方法本身就跑在全局池的工作线程里，
        从池里再提交并等待会把自己的 worker 占死。

        Args:
            blocks (list[TextBlock]): 行块列表
            target_lang (str 或 None): 目标语言
            force_refresh (bool): 是否跳过翻译缓存
            token (_RequestToken 或 None): 本次请求的取消/时限令牌

        Returns:
            list[str]: 与 blocks 等长的译文；单块失败时为对应的空串
        """
        if not blocks:
            return []

        def translate(block):
            """单块翻译：失败只影响这一块，不拖垮整次结果"""
            if token is not None and token.cancelled:
                return ''
            try:
                result = self._translate_all(
                    block.text, target_lang, force_refresh, token)
                # 失败标记贴到图上就是一行 "[翻译失败 …]"，用户会以为是译文
                return '' if is_translation_failure(result) else result
            except Exception as e:
                logger.warning(f"行块翻译失败: {e}")
                return ''

        workers = min(len(blocks), self._BLOCK_TRANSLATE_WORKERS)
        if workers <= 1:
            return [translate(block) for block in blocks]
        with futures.ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(translate, blocks))

    def _effective_lang(self, target_lang):
        """请求未指定语言时回填全局配置，供工具条正确显示当前语言"""
        if target_lang:
            return target_lang
        if self.translation_manager is not None:
            return getattr(self.translation_manager, 'target_lang', None)
        return None

    def _target_languages(self):
        """可选目标语言列表 (代码, 显示名)"""
        try:
            if self.translation_manager:
                return self.translation_manager.get_target_languages()
        except Exception:
            pass
        from .providers.base import TARGET_LANGUAGES
        return TARGET_LANGUAGES

class ScreenshotWidget(QWidget):
    """
    截图窗口类
    负责显示半透明遮罩和处理鼠标事件
    参考snipaste的截图效果
    """

    # 信号：选区确定（携带选区图像与屏幕矩形）
    region_selected = pyqtSignal(object, object)
    # 信号：请求重新翻译（目标语言, 是否跳过缓存）
    retranslate_requested = pyqtSignal(object, bool)
    # 信号：失败后请求重试
    retry_requested = pyqtSignal()
    # 信号：截图取消
    screenshot_canceled = pyqtSignal()
    # 信号：选区已确认（携带遮罩局部逻辑矩形），供「重复上次区域」
    region_committed = pyqtSignal(object)
    # 信号：请求本地 OCR 取字（携带选区原图）
    ocr_requested = pyqtSignal(object)
    # 信号：请求长截图（携带抓帧上下文与首帧图）
    scroll_requested = pyqtSignal(object, object)

    def __init__(self, config_manager=None, capture_only=False, preset=None):
        """
        初始化截图窗口

        Args:
            config_manager: 配置管理器实例，读取 [Overlay] / [Capture] 段参数；
                不传时使用默认值
            capture_only (bool): True 时框选后进入编辑态，不发起识别与翻译
            preset: 预设选区（见 ScreenshotManager.start_capture）
        """
        super().__init__()
        self.capture_only = bool(capture_only)
        self.config_manager = config_manager
        self._preset = preset

        # 纯截图编辑态
        self._capture_edit = False
        self._capture_editor = None
        self._capture_toolbar = None
        self._capture_busy = False
        # 长截图期间遮罩隐藏，靠这条横幅给进度与完成/取消入口
        self._scroll_banner = None
        # 后台结果没回投时的兜底复位（子 QTimer，随窗口一起销毁）
        self._capture_watchdog = None
        # 框选辅助：光标位置、窗口悬停高亮、按下点（区分单击选窗口）
        self._cursor_pos = QPoint()
        self._window_hover_rect = None
        self._press_pos = None
        # 轻量状态提示（OCR/滚动截图结果）
        self._capture_toast = ''
        self._toast_timer = None
        # 角标（选区四角标记 / 结果态手柄）的悬停放大动画
        self._hover_mark = None
        self._hover_mark_progress = 0.0
        self._hover_timer = None
        # 滚轮连调线宽/字号时攒一下再落盘
        self._pref_timer = None

        # 设置窗口属性
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        # 不使用 setWindowOpacity：它会把挖空的选区一起压暗。
        # 暗角亮度完全由 paintEvent 里绘制的半透明层控制，选区保持原屏亮度。
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 无边框窗口默认 NoFocus，收不到按键；设为 StrongFocus 才能响应 ESC 退出
        self.setFocusPolicy(Qt.StrongFocus)

        # 从配置读取可调参数（缺失/非法时回退默认值）
        self._dim_opacity = self._read_int_config(
            config_manager, 'dim_opacity', self._DEFAULT_DIM_OPACITY,
            lo=0, hi=180)
        self._snap_threshold = self._read_int_config(
            config_manager, 'snap_threshold', self._DEFAULT_SNAP_THRESHOLD,
            lo=0, hi=48)

        # 初始化变量
        self.begin = QPoint()
        self.end = QPoint()
        self.is_selecting = False
        # 结果态下拖动边/角调整选区用；松手立即重新识别翻译
        self._drag_mode = None          # 'move' / 'resize' / None
        self._resize_edges = frozenset()
        self._drag_origin = QPoint()
        self._drag_start_rect = QRect()
        # 全屏截图背景，初始化失败时保持为None，供后续判空
        self.full_screen = None
        # 冻结整屏的位图（抓拍失败时为None）；遮罩全程画它，不再透出实时桌面
        self._frozen_pixmap = None

        # 覆盖范围与坐标基准：默认整个虚拟桌面（所有显示器，副屏在主屏左侧/
        # 上方时桌面原点为负）。先给出兜底值：即使探测抛异常也不会因缺属性报错
        self.screen_width = 0
        self.screen_height = 0
        self._grab_scale = 1.0
        self._grab_scale_y = 1.0
        self._desktop_rect = QRect(0, 0, 0, 0)
        self._screen_rects = []
        self._cover_all_screens = False
        self._resolve_desktop()

        # 磁吸状态：启动时枚举一次可见窗口矩形，拖拽期间不重复枚举。
        from .window_enum import get_visible_window_rects
        self._snap_window_rects = self._window_rects_local(
            get_visible_window_rects())
        self._active_snap_lines = None  # 当前激活的参考线

        # 结果态：识别/翻译完成后就地覆盖展示
        self._result = None          # 最近一次成功的 payload
        self._result_rect = None     # 选区逻辑矩形（结果态绘制用）
        self._crop_pixmap = None     # 选区冻结背景，带 devicePixelRatio 供遮罩绘制
        self._scale = 1.0            # 逻辑坐标 → 图像物理像素的横向倍率
        self._scale_y = 1.0          # 纵向倍率（DPI 缩放信息缺失时可与横向不同）
        self._blocks = []            # 逻辑坐标行块
        self._paragraphs = []        # [(屏幕矩形, [块下标]), ...]
        self._hover_paragraph = None  # 悬停命中的段落下标
        self._pinned_paragraph = None  # 单击钉住的段落下标
        self._mode = result_render.MODE_TRANSLATED
        self._loading = False
        self._error = None
        # 加载动画：角度由定时器推进，只重绘胶囊那一小块
        self._spinner_angle = 0
        self._spinner_timer = None
        self._spinner_rect = None
        self._toolbar = None
        # 每次确认选区递增，用于丢弃上一次选区的迟到结果
        self._request_id = 0

        try:
            # 遮罩铺满整个桌面：跨屏窗口不能用 showFullScreen（它只会占住一块屏），
            # 所以几何在这里定好，显示由调用方 show()
            self.setGeometry(self._desktop_rect)

            # 截取桌面作为背景。
            # 这里不能传 bbox：Pillow 的 bbox 是“屏幕坐标”，传入后会被减去
            # 虚拟桌面原点（all_screens=True 时原点是 SM_XVIRTUALSCREEN，副屏在
            # 主屏左侧时是负数），bbox=(0,0,宽,高) 会裁到主屏内容再补黑边。
            # all_screens=True 不传 bbox 得到的就是整张虚拟桌面，与覆盖范围一致。
            self.full_screen = ImageGrab.grab(
                all_screens=self._cover_all_screens)
            # 高分屏下 ImageGrab 拿到的是物理像素，记下横纵倍率供裁剪换算
            if self.full_screen and self.screen_width:
                self._grab_scale = self.full_screen.width / self.screen_width
                scale_y = self.full_screen.height / max(1, self.screen_height)
                self._grab_scale_y = scale_y
                if abs(scale_y - self._grab_scale) > 0.02:
                    # 横纵倍率对不上：抓拍范围与遮罩范围不一致，裁剪框会整体偏移，
                    # 记下来便于排查（不中断截图）
                    logger.warning(f"抓拍倍率横纵不一致（{self._grab_scale:.3f} / "
                                   f"{scale_y:.3f}），截图坐标可能偏移："
                                   f"图 {self.full_screen.size}，"
                                   f"桌面 {self.screen_width}x{self.screen_height}")
            # 冻结整屏：遮罩显示期间一直画这张抓拍图。透明窗口若靠“挖空选区”
            # 露出实时桌面，选区里自刷新的内容（视频、时钟、进度条）会继续动，
            # 用户框到的画面与最终识别到的画面也可能不是同一帧。
            self._frozen_pixmap = result_render.pil_to_qpixmap(
                self.full_screen, dpr=self._grab_scale) if self.full_screen else None

            # 设置光标
            self.setCursor(QCursor(Qt.CrossCursor))

            logger.info(f"截图窗口初始化，大小: {self.screen_width}x{self.screen_height}"
                        f"（桌面原点 {self._desktop_rect.x()},{self._desktop_rect.y()}，"
                        f"抓拍倍率 {self._grab_scale:.3f}，"
                        f"冻结整屏: {'成功' if self._frozen_pixmap else '失败'}）")
            if preset is not None:
                # 等窗口显示、事件循环转起来再落选区：工具条需要可见的宿主
                QTimer.singleShot(0, lambda: self._apply_preset(preset))
        except Exception:
            logger.exception("初始化截图窗口失败")

    # ---- [Capture] 配置读取 ----

    def capture_option(self, option, fallback=''):
        """读取 [Capture] 段字符串配置；配置缺失/异常时返回 fallback"""
        try:
            if self.config_manager is None:
                return fallback
            return self.config_manager.get_capture_option(
                option, fallback=str(fallback))
        except Exception:
            return fallback

    def capture_option_int(self, option, default, lo, hi):
        """读取 [Capture] 段整数配置并夹到合法范围"""
        try:
            value = int(str(self.capture_option(option, default)).strip())
            return max(lo, min(hi, value))
        except Exception:
            return default

    def capture_option_bool(self, option, default=False):
        """读取 [Capture] 段布尔配置"""
        raw = str(self.capture_option(option, 'true' if default else 'false'))
        return raw.strip().lower() in ('true', '1', 'yes', 'on')

    def _apply_preset(self, preset):
        """
        套用预设选区并直接确认（跳过拖拽框选）

        Args:
            preset: 'fullscreen' / ('window', (l,t,r,b) 物理像素) / QRect
        """
        rect = None
        try:
            if isinstance(preset, str) and preset == 'fullscreen':
                rect = QRect(0, 0, self.screen_width, self.screen_height)
            elif isinstance(preset, tuple) and len(preset) == 2 \
                    and preset[0] == 'window':
                local = self._window_rects_local([preset[1]])
                if local:
                    l, t, r, b = local[0]
                    rect = QRect(l, t, max(1, r - l), max(1, b - t))
            elif isinstance(preset, QRect):
                rect = QRect(preset)
        except Exception:
            logger.exception("套用预设选区失败")
            rect = None

        if rect is None or rect.isEmpty():
            logger.warning("预设选区无效，退回交互框选")
            return
        # 夹进桌面范围，避免窗口在屏幕外时裁出空图
        rect = rect.intersected(QRect(0, 0, self.screen_width,
                                      self.screen_height))
        if rect.width() <= self._MIN_SELECT_EDGE \
                or rect.height() <= self._MIN_SELECT_EDGE:
            return
        self.begin = QPoint(rect.left(), rect.top())
        self.end = QPoint(rect.left() + rect.width(),
                          rect.top() + rect.height())
        logger.info(f"套用预设选区 {rect.width()}x{rect.height()}")
        self._confirm_selection()

    @staticmethod
    def _read_int_config(config_manager, option, default, lo, hi):
        """
        从 [Overlay] 段读取整数配置并夹取到合法范围

        Args:
            config_manager: 配置管理器实例（可为None）
            option (str): 配置项名
            default (int): 读取失败/未设置时的默认值
            lo (int): 最小值
            hi (int): 最大值

        Returns:
            int: 夹取后的配置值
        """
        try:
            raw = config_manager.get_overlay_option(option, fallback=str(default))
            value = int(str(raw).strip())
            return max(lo, min(hi, value))
        except Exception:
            return default

    def _resolve_desktop(self):
        """
        确定遮罩覆盖的桌面范围与各显示器在局部坐标里的位置

        局部坐标 = 虚拟桌面坐标 - 桌面原点：副屏在主屏左侧/上方时桌面原点为负，
        遮罩自己的坐标系从这个原点开始，Win32 拿到的矩形要减去它才能对上。
        """
        try:
            bounds = desktop_bounds()
            if bounds is not None:
                self._desktop_rect, self._cover_all_screens = bounds
            self._screen_rects = [
                QRect(s.geometry()).translated(-self._desktop_rect.x(),
                                                -self._desktop_rect.y())
                for s in QApplication.screens()]
        except Exception as e:
            logger.warning(f"确定桌面范围失败: {e}")

        if self._desktop_rect.isEmpty():
            # 探测失败：退回过时的 desktop()，至少保证遮罩有正确的尺寸
            try:
                screen = QApplication.desktop()
                self._desktop_rect = QRect(0, 0, screen.width(), screen.height())
            except Exception as e:
                logger.warning(f"取桌面尺寸失败: {e}")
        if not self._screen_rects:
            self._screen_rects = [QRect(0, 0, self._desktop_rect.width(),
                                        self._desktop_rect.height())]

        self.screen_width = self._desktop_rect.width()
        self.screen_height = self._desktop_rect.height()

    def covers_multiple_screens(self):
        """
        遮罩是否覆盖了多块显示器

        跨屏窗口只能用 show()：showFullScreen() 会把窗口铺满它所在的那一块屏，
        等于把另一块屏重新露出来（又会开始刷新）。

        Returns:
            bool: 覆盖的显示器是否多于一块
        """
        return len(self._screen_rects) > 1

    # 遮罩主题色（与 managers/styles.py 的深色蓝主题一致）
    _ACCENT = QColor("#3b82f6")               # 强调蓝：选区边框
    _BUBBLE_BG = QColor(30, 36, 48, 235)      # 气泡底：主题面板色
    _BUBBLE_TEXT = QColor("#93c5fd")          # 气泡文字：淡蓝

    # 加载动画：选区居中的小圆环。只转圈、不带文字——等待本身不需要解释
    _SPINNER_BOX = 44                         # 胶囊直径（像素）
    _SPINNER_RING_INSET = 11                  # 圆环距胶囊边缘的留白
    _SPINNER_WIDTH = 2.5                      # 圆环线宽
    _SPINNER_ARC = 280                        # 弧长（度）：留缺口才看得出在转
    _SPINNER_STEP = 20                        # 每帧转过的角度
    _SPINNER_INTERVAL = 33                    # 帧间隔（毫秒，约 30fps）

    # 默认可调参数（实际值从 [Overlay] 配置段读取）
    _DEFAULT_DIM_OPACITY = 90                 # 暗角透明度 (0~180)
    _DEFAULT_SNAP_THRESHOLD = 12              # 磁吸阈值（像素）

    # 小于该宽/高的选区视为误触（单击或抖动）
    _MIN_SELECT_EDGE = 10
    # 结果态拖动时边/角的命中范围（像素）
    _HANDLE_HIT = 10
    # 角标悬停动画：命中半径、帧间隔、每帧进度（约 100 ms 走完）
    _MARK_HOVER_HIT = 16
    _MARK_FRAME_MS = 16
    _MARK_STEP = 0.16

    def paintEvent(self, event):
        """
        绘制事件：柔和暗角 + 蓝色发光选区 + 角标记 + 尺寸气泡，
        结果态改为就地覆盖译文 + 工具条 + 气泡卡片
        """
        try:
            painter = QPainter(self)
        except Exception as e:
            # 连画布都建不出来：留着空帧比抛出去好（异常会终止整个进程）
            logger.error(f"遮罩创建画布失败: {e}")
            return
        if not painter.isActive():
            logger.error("遮罩画布未能创建，本帧跳过绘制")
            return
        try:
            self._paint(painter)
            if self._capture_toast:
                self._draw_pill(
                    painter, self._result_rect or self.rect(),
                    self._capture_toast)
        except Exception as e:
            # paintEvent 里抛异常等于把进程带走（Qt 不接 Python 异常，也不打印栈）
            logger.exception(f"遮罩绘制失败: {e}")
        finally:
            painter.end()

    def _paint(self, painter):
        """遮罩一帧的实际绘制内容（异常由 paintEvent 兜住）"""
        painter.setRenderHint(QPainter.Antialiasing, True)

        # 底层先铺冻结的整屏抓拍：遮罩必须是一张静止画面，否则背后自刷新的内容
        # 会在选区里继续动（“界面没锁住”）。
        self._draw_frozen_screen(painter)

        # 柔和暗角（未选择时也铺一层，提示处于截图模式）
        dim_color = QColor(0, 0, 0, self._dim_opacity)
        painter.fillRect(self.rect(), dim_color)

        # 框选拖拽中画选区（此时还没有结果可展示）
        if (self._loading or self._error or self._result is not None) \
                and not self.is_selecting:
            self._draw_result(painter)
            return

        if not self.is_selecting:
            self._draw_hint(painter)
            if self.capture_only and not self._capture_edit:
                self._draw_window_hover(painter)
                self._draw_capture_magnifier(painter)
            return

        rect = self._get_rect()

        # 磁吸：拖拽中实时计算吸附结果（原始选区仅用于计算，显示/裁剪用吸附后矩形）
        snapped, guides = self._snap_rect(rect)
        self._active_snap_lines = guides if guides else None
        rect = snapped

        # 1) 亮出选区：从同一张冻结图里按选区重绘（100%亮度，无窗口级压暗）。
        #    这里不能再用 CompositionMode_Clear 挖空——挖空露出的是实时桌面，
        #    等于把冻结的整屏又开了一个“会动的窗口”。
        self._draw_frozen_region(painter, rect)

        # 2) 绘制装饰
        painter.setBrush(Qt.NoBrush)

        # 3) 外发光：宽而淡的蓝色描边
        glow = QColor(self._ACCENT)
        glow.setAlpha(55)
        painter.setPen(QPen(glow, 6))
        painter.drawRect(rect.adjusted(-2, -2, 2, 2))

        # 4) 主边框：2px 强调蓝
        painter.setPen(QPen(self._ACCENT, 2))
        painter.drawRect(rect)

        # 5) 四角白色标记（snipaste 风格，帮助确认选区边界）
        self._draw_corner_marks(painter, rect)

        # 6) 磁吸参考线（吸附到窗口/屏幕边缘时显示）
        self._draw_snap_guides(painter, rect)

        # 7) 尺寸气泡
        self._draw_size_bubble(painter, rect)

        # 8) 放大镜（纯截图）：跟随光标，取色与精确对齐用
        if self.capture_only:
            self._draw_capture_magnifier(painter)

    def _draw_frozen_screen(self, painter):
        """把冻结的整屏抓拍铺满遮罩（抓拍缺失时留空，退回透出实时桌面）"""
        if self._frozen_pixmap is None:
            return
        if abs(self._grab_scale_y - self._grab_scale) < 0.001:
            # 位图带 devicePixelRatio，按逻辑坐标 1:1 贴回，不做重采样
            painter.drawPixmap(0, 0, self._frozen_pixmap)
        else:
            # 横纵倍率不一致时 1:1 贴回会超出（或不足）遮罩高度，按遮罩缩放
            painter.drawPixmap(self.rect(), self._frozen_pixmap)

    def _draw_frozen_region(self, painter, rect):
        """
        按选区裁剪重绘冻结图：选区保持原屏亮度，且与暗角是同一帧静止画面

        Args:
            painter (QPainter): 当前绘制器
            rect (QRect): 选区（逻辑坐标）
        """
        if self._frozen_pixmap is None:
            # 抓拍失败（ImageGrab 不可用）：只能退回挖空，至少能让用户看清选区
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, Qt.transparent)
            painter.restore()
            return
        painter.save()
        painter.setClipRect(rect)
        if abs(self._grab_scale_y - self._grab_scale) < 0.001:
            painter.drawPixmap(0, 0, self._frozen_pixmap)
        else:
            # 横纵倍率不等时 1:1 贴图会让选区内容与暗角错位，按遮罩整幅贴回
            painter.drawPixmap(self.rect(), self._frozen_pixmap)
        painter.restore()

    def _draw_hint(self, painter):
        """未开始选择时的操作提示条（顶部居中）"""
        text = ("拖拽框选 · 单击选中窗口 · 右键或ESC取消"
                if self.capture_only
                else "拖拽选择要翻译的区域 · 右键或ESC取消")
        font = painter.font()
        font.setPointSize(10)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        area = self._hint_area()
        tw = metrics.boundingRect(text).width() + 28
        th = metrics.height() + 10
        bx = area.center().x() - tw // 2
        by = area.top() + max(int(area.height() * 0.06), 12)
        bubble = QRect(bx, by, tw, th)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._BUBBLE_BG)
        painter.drawRoundedRect(bubble, th // 2, th // 2)
        painter.setPen(QPen(self._BUBBLE_TEXT))
        painter.drawText(bubble, Qt.AlignCenter, text)

    def _hint_area(self):
        """
        操作提示条所在的那一块显示器

        多屏时居中在整个桌面会让气泡跨在屏幕接缝上，放在鼠标当前所在的那块屏
        更自然。

        Returns:
            QRect: 鼠标所在显示器的遮罩局部矩形（找不到时用整个桌面）
        """
        pos = QCursor.pos() - self._desktop_rect.topLeft()
        for rect in self._screen_rects:
            if rect.contains(pos):
                return rect
        return QRect(0, 0, self.screen_width, self.screen_height)

    def _draw_corner_marks(self, painter, rect):
        """选区四角的白色 L 形标记；鼠标靠近的角放大并转蓝（悬停动画）"""
        length = 14
        corners = (
            (rect.left(), rect.top(), 1, 1),
            (rect.right(), rect.top(), -1, 1),
            (rect.left(), rect.bottom(), 1, -1),
            (rect.right(), rect.bottom(), -1, -1),
        )
        for index, (x, y, dx, dy) in enumerate(corners):
            grow = (self._hover_mark_progress
                    if index == self._hover_mark else 0.0)
            if grow:
                color = QColor(96, 165, 250)
                color.setAlpha(min(255, int(170 + 85 * grow)))
            else:
                color = QColor(255, 255, 255, 220)
            painter.setPen(QPen(color, 2 + 1.2 * grow))
            arm = length * (1 + 0.5 * grow)
            painter.drawLine(x, y, int(x + dx * arm), y)
            painter.drawLine(x, y, x, int(y + dy * arm))

    def _draw_handles(self, painter, rect):
        """结果态的可拖拽手柄：鼠标靠近的那个放大发亮（悬停动画）"""
        size = 7.0
        for index, point in enumerate(self._mark_points(rect, 'handle')):
            grow = (self._hover_mark_progress
                    if index == self._hover_mark else 0.0)
            side = size + 4.0 * grow
            half = side / 2.0
            painter.setPen(
                QPen(QColor(255, 255, 255, min(255, int(230 + 25 * grow))),
                     1 + grow))
            fill = QColor(self._ACCENT)
            if grow:
                fill = fill.lighter(int(100 + 30 * grow))
            painter.setBrush(fill)
            painter.drawRect(
                QRectF(point.x() - half, point.y() - half, side, side))
        painter.setBrush(Qt.NoBrush)

    # ---- 角标悬停动画 ----

    def _mark_target(self):
        """
        当前该渲染哪种角标

        Returns:
            tuple[QRect 或 None, str 或 None]: (选区矩形, 'corner'/'handle')
        """
        if self.is_selecting:
            rect, _guides = self._snap_rect(self._get_rect())
            return rect, 'corner'
        if self._result is not None or self._error:
            return self._result_rect, 'handle'
        return None, None

    @staticmethod
    def _mark_points(rect, kind):
        """角标位置：选区四角（corner）或八向手柄（handle）"""
        if kind == 'corner':
            return [rect.topLeft(), rect.topRight(),
                    rect.bottomLeft(), rect.bottomRight()]
        cx = rect.center().x()
        cy = rect.center().y()
        return [rect.topLeft(), rect.topRight(),
                rect.bottomLeft(), rect.bottomRight(),
                QPoint(cx, rect.top()), QPoint(cx, rect.bottom()),
                QPoint(rect.left(), cy), QPoint(rect.right(), cy)]

    def _update_mark_hover(self, pos):
        """鼠标靠近角标 → 启动放大动画；离开 → 复原"""
        rect, kind = self._mark_target()
        index = None
        if rect is not None and not rect.isEmpty():
            for i, point in enumerate(self._mark_points(rect, kind)):
                if abs(point.x() - pos.x()) <= self._MARK_HOVER_HIT \
                        and abs(point.y() - pos.y()) <= self._MARK_HOVER_HIT:
                    index = i
                    break
        if index == self._hover_mark:
            return
        self._hover_mark = index
        self._start_mark_anim()

    def _start_mark_anim(self):
        """每帧推进一点悬停进度，走完即停表（静止时零开销）"""
        if self._hover_timer is None:
            self._hover_timer = QTimer(self)
            self._hover_timer.setInterval(self._MARK_FRAME_MS)
            self._hover_timer.timeout.connect(self._tick_mark_anim)
        if not self._hover_timer.isActive():
            self._hover_timer.start()

    def _tick_mark_anim(self):
        target = 1.0 if self._hover_mark is not None else 0.0
        delta = target - self._hover_mark_progress
        if abs(delta) <= self._MARK_STEP:
            self._hover_mark_progress = target
            if self._hover_timer is not None:
                self._hover_timer.stop()
        else:
            self._hover_mark_progress += (
                self._MARK_STEP if delta > 0 else -self._MARK_STEP)
        self.update()

    def _draw_snap_guides(self, painter, rect):
        """吸附激活时绘制蓝色虚线参考线，提示当前吸附目标"""
        guides = getattr(self, '_active_snap_lines', None)
        if not guides:
            return
        pen = QPen(QColor("#60a5fa"), 1, Qt.DashLine)
        painter.setPen(pen)
        for kind, pos in guides:
            if kind in ('x', 'x2'):
                painter.drawLine(pos, 0, pos, self.screen_height)
            else:
                painter.drawLine(0, pos, self.screen_width, pos)

    def _window_rects_local(self, rects):
        """
        把 Win32 窗口矩形换算成遮罩局部逻辑坐标

        GetWindowRect 给的是物理像素的虚拟桌面绝对坐标，而遮罩的选区、屏幕
        边界与吸附线全是 Qt 的逻辑坐标；混用的话高分屏下吸附线会整体偏掉
        (倍率-1) 倍，右边/下边的窗口边缘还会被 screen_width 判成"超出屏幕"
        而丢弃。先按抓取倍率换算，再减掉遮罩自身的逻辑原点。

        Args:
            rects (list[tuple[int, int, int, int]]): 物理像素的
                (left, top, right, bottom)

        Returns:
            list[tuple[int, int, int, int]]: 遮罩局部逻辑坐标矩形
        """
        scale_x = self._grab_scale or 1.0
        scale_y = self._grab_scale_y or 1.0
        origin_x, origin_y = self._desktop_rect.x(), self._desktop_rect.y()
        return [
            (int(round(l / scale_x)) - origin_x,
             int(round(t / scale_y)) - origin_y,
             int(round(r / scale_x)) - origin_x,
             int(round(b / scale_y)) - origin_y)
            for l, t, r, b in rects]

    def _snap_rect(self, rect):
        """
        磁吸：把选区四边吸附到附近窗口/屏幕边缘线（阈值内距离最小者优先）

        Args:
            rect (QRect): 原始选区

        Returns:
            tuple[QRect, list[tuple[str, int]]]: 吸附后选区与激活的参考线列表
        """
        # 收集候选边缘线：桌面外沿与每块显示器的边界 + 可见窗口四边
        # （垂直线与水平线分别归集）；多屏时接缝也是常用的对齐线
        xs = {0, self.screen_width}          # 垂直线
        ys = {0, self.screen_height}         # 水平线
        for screen_rect in self._screen_rects:
            xs.update((screen_rect.left(), screen_rect.right() + 1))
            ys.update((screen_rect.top(), screen_rect.bottom() + 1))
        for l, t, r, b in self._snap_window_rects:
            if l > 0:
                xs.add(l)
            if r < self.screen_width:
                xs.add(r)
            if t > 0:
                ys.add(t)
            if b < self.screen_height:
                ys.add(b)

        threshold = self._snap_threshold
        new_rect = QRect(rect)
        x_guides = []
        y_guides = []

        def _nearest(coord, lines):
            """返回阈值内最近的线位置，无则None"""
            best, best_d = None, threshold + 1
            for line in lines:
                d = abs(coord - line)
                if d < best_d:
                    best, best_d = line, d
            return best

        # 横向：左右一起吸再校验。窄选区两边可能吸到同一条线，塌成 0 宽的
        # 选区还不如不吸（用户在拖拽中会看到选区凭空消失）
        left_target = _nearest(rect.left(), xs)
        right_target = _nearest(rect.right() + 1, xs)
        candidate = QRect(rect)
        if left_target is not None:
            candidate.moveLeft(left_target)
        if right_target is not None:
            candidate.moveRight(right_target - 1)
        if candidate.width() > 0:
            new_rect = candidate
            if left_target is not None:
                x_guides.append(('x', left_target))
            if right_target is not None:
                x_guides.append(('x2', right_target))

        # 纵向同理
        top_target = _nearest(rect.top(), ys)
        bottom_target = _nearest(rect.bottom() + 1, ys)
        candidate = QRect(new_rect)
        if top_target is not None:
            candidate.moveTop(top_target)
        if bottom_target is not None:
            candidate.moveBottom(bottom_target - 1)
        if candidate.height() > 0:
            new_rect = candidate
            if top_target is not None:
                y_guides.append(('y', top_target))
            if bottom_target is not None:
                y_guides.append(('y2', bottom_target))

        return new_rect, x_guides + y_guides

    def _draw_size_bubble(self, painter, rect, hint=''):
        """
        尺寸提示气泡：优先放在选区上方居中，空间不足自动翻转到下方，
        并夹取在屏幕范围内不出界

        Args:
            painter (QPainter): 当前绘制器
            rect (QRect): 选区（逻辑坐标）
            hint (str): 追加在尺寸后面的操作提示（结果态拖动时用）
        """
        text = f"{rect.width()} × {rect.height()}"
        if hint:
            text = f"{text}   {hint}"
        font = painter.font()
        font.setPointSize(10)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        tw = metrics.boundingRect(text).width() + 20
        th = metrics.height() + 8

        bx = rect.center().x() - tw // 2
        by = rect.top() - th - 8
        if by < 4:
            # 上方空间不足：翻转到选区下方
            by = rect.bottom() + 8
        if by + th > self.screen_height - 4:
            by = self.screen_height - 4 - th
        bx = max(4, min(bx, self.screen_width - tw - 4))
        bubble = QRect(bx, by, tw, th)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._BUBBLE_BG)
        painter.drawRoundedRect(bubble, 6, 6)
        painter.setPen(QPen(self._BUBBLE_TEXT))
        painter.drawText(bubble, Qt.AlignCenter, text)

    # ------------------------------------------------------------------
    # 结果态：就地译文覆盖 + 工具条 + 气泡卡片
    # ------------------------------------------------------------------

    def show_result(self, payload):
        """
        展示一次识别/翻译结果

        Args:
            payload (dict): 含 image/region/blocks/block_texts/original_text/
                translated_text/languages/target_lang 的结果字典
        """
        image = payload.get('image')
        region = QRect(payload['region'])
        if image is None or region.width() <= 0 or region.height() <= 0:
            # 没有底图就没法就地展示（取色、绘制范围都来自它）
            self.show_failure(payload.get('error') or '选区图像不可用')
            return
        scale = image.width / max(1, region.width())
        scale_y = image.height / max(1, region.height())

        self._result = payload
        self._error = None
        self._loading = False
        self._stop_loading_spinner()
        self._result_rect = region
        self._scale = scale
        self._scale_y = scale_y
        self._crop_pixmap = result_render.pil_to_qpixmap(image, dpr=scale)
        self._blocks = result_render.to_logical_blocks(
            payload.get('blocks') or [], scale, scale_y)
        self._paragraphs = self._build_paragraphs()
        self._hover_paragraph = None
        self._pinned_paragraph = None

        if self.capture_only:
            self._enter_capture_edit()
        else:
            self._ensure_toolbar(payload)
        self.setCursor(QCursor(Qt.ArrowCursor))
        self.update()
        logger.info(f"遮罩结果已展示：{len(self._blocks)} 行块，"
                    f"{len(payload.get('block_texts') or [])} 行译文，模式 {self._mode}")

    def show_failure(self, message):
        """识别/翻译失败：保留选区并给出可重试的错误卡片"""
        self._result = None
        self._loading = False
        self._stop_loading_spinner()
        self._error = message or '识别失败'
        self._hide_toolbar()
        self.update()
        logger.warning(f"选区处理失败: {self._error}")

    def has_result(self):
        """当前是否已有可重译的结果"""
        return self._result is not None

    def current_payload(self):
        """当前结果字典（无结果时 None）"""
        return self._result

    def current_request_id(self):
        """最近一次选区的序号，用于丢弃过期结果"""
        return self._request_id

    def current_target_lang(self):
        """工具条上选择的目标语言；尚未选定时返回 None 用全局配置"""
        if self._toolbar is not None:
            return self._toolbar.current_language()
        return None

    def set_result_busy(self, busy):
        """
        请求进行中：禁用工具条，并在选区中央转圈

        重译/重试与首次识别共用同一个动画：界面里不再出现"翻译中"这类文字，
        等待只用一个转圈表达。
        """
        if self._toolbar is not None:
            self._toolbar.set_busy(busy)
        if busy:
            self._error = None
            self._loading = True
            self._start_loading_spinner()
            self.update()
        else:
            self._stop_loading_spinner()

    def _build_paragraphs(self):
        """把逻辑坐标行块聚成段落，返回 [(屏幕矩形, [块下标]), ...]"""
        if not self._blocks:
            return []
        origin = self._result_rect.topLeft()
        paragraphs = []
        for indices in group_paragraphs(self._blocks):
            left = min(self._blocks[i].left for i in indices)
            top = min(self._blocks[i].top for i in indices)
            right = max(self._blocks[i].right for i in indices)
            bottom = max(self._blocks[i].bottom for i in indices)
            rect = QRect(int(left) + origin.x(), int(top) + origin.y(),
                         int(right - left), int(bottom - top))
            paragraphs.append((rect, indices))
        return paragraphs

    def _active_paragraph(self):
        """当前展示卡片的段落：钉住的优先，其次悬停"""
        if self._pinned_paragraph is not None:
            return self._pinned_paragraph
        return self._hover_paragraph

    def _paragraph_at(self, pos):
        """命中测试：返回包含该屏幕坐标的段落下标，无则 None"""
        for index, (rect, _indices) in enumerate(self._paragraphs):
            if rect.contains(pos):
                return index
        return None

    def _draw_result(self, painter):
        """结果态绘制：冻结的选区底图 + 就地译文 + 高亮 + 卡片"""
        rect = self._result_rect
        if rect is None:
            return
        if self._drag_mode in ('move', 'resize'):
            # 拖动调整中：只预览新的选区范围（底图取自同一张冻结图），
            # 松手后才按新范围重裁，避免旧内容被拉变形
            self._draw_frozen_region(painter, rect)
            self._draw_border(painter, rect)
            self._draw_handles(painter, rect)
            hint = ('· 松开鼠标重新取景'
                    if self.capture_only and self._capture_edit
                    else '· 松开鼠标重新识别')
            self._draw_size_bubble(painter, rect, hint=hint)
            return
        if self._crop_pixmap is not None:
            if abs(self._scale_y - self._scale) < 0.001:
                # 横纵倍率一致：按逻辑尺寸 1:1 贴回，不做重采样
                painter.drawPixmap(rect.topLeft(), self._crop_pixmap)
            else:
                # 横纵倍率不一致：贴回会拉变形或高度对不上，按目标矩形缩放
                painter.drawPixmap(rect, self._crop_pixmap)
        else:
            # 结果未回投（加载/失败）时亮出选区原图：同一张冻结画面，避免透出实时桌面
            self._draw_frozen_region(painter, rect)

        if self._loading:
            self._draw_border(painter, rect)
            self._draw_loading_spinner(painter, rect)
            return
        if self._error:
            self._draw_border(painter, rect)
            self._draw_handles(painter, rect)
            anchor, title, body, footer = self._error_card()
            self._draw_card(painter, anchor, title, body, footer)
            return

        payload = self._result or {}
        texts = payload.get('block_texts') or []
        if texts and self._mode != result_render.MODE_ORIGINAL:
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            # 覆盖块可能比选区高（双语要留两行），裁到选区内不出界
            painter.save()
            painter.setClipRect(rect)
            result_render.paint_blocks(
                painter, payload.get('image'), self._blocks, texts,
                rect.topLeft(), self._mode, scale=self._scale,
                scale_y=self._scale_y)
            painter.restore()
        # 纯截图编辑态：把标注重放到冻结底图之上（选区局部坐标）
        if self.capture_only and self._capture_edit \
                and self._capture_editor is not None:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setClipRect(rect)
            painter.translate(rect.topLeft())
            self._capture_editor.paint(painter)
            painter.restore()

        self._draw_border(painter, rect)
        self._draw_handles(painter, rect)

        active = self._active_paragraph()
        if active is not None:
            self._draw_paragraph_highlight(painter, active)
        info = self._card_info()
        if info:
            self._draw_card(painter, *info)

    def _draw_border(self, painter, rect):
        """选区强调色边框"""
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(self._ACCENT, 2))
        painter.drawRect(rect)

    def _draw_paragraph_highlight(self, painter, index):
        """悬停/选中段落的蓝色高亮"""
        if index >= len(self._paragraphs):
            return
        rect, _ = self._paragraphs[index]
        rect = rect.adjusted(-2, -2, 2, 2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 122, 255, 46))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#60a5fa"), 1, Qt.DashLine))
        painter.drawRect(rect)

    def _error_card(self):
        """失败态卡片内容"""
        return (self._result_rect, '识别失败',
                str(self._error), '按 R 重试 · ESC 退出')

    def _card_info(self):
        """
        气泡卡片内容：命中段落时展示该段原文/译文，行级译文缺失时展示整段

        Returns:
            tuple 或 None: (anchor, title, body, footer)
        """
        payload = self._result
        if payload is None or self.capture_only:
            return None
        texts = payload.get('block_texts') or []
        title = self._card_title()

        index = self._active_paragraph()
        if index is not None and texts and index < len(self._paragraphs):
            rect, indices = self._paragraphs[index]
            original = '\n'.join(self._blocks[i].text for i in indices)
            translated = '\n'.join(texts[i] for i in indices if i < len(texts))
            return (rect, title, self._card_body(original, translated),
                    '单击取消选中 · Ctrl+C 复制该段')

        if texts:
            return None   # 就地覆盖已足够，不再压一张卡片

        body = self._card_body(payload.get('original_text', ''),
                              payload.get('translated_text', ''))
        return (self._result_rect, title, body,
                'Ctrl+C 复制 · T 切换模式 · P 钉住 · Ctrl+S 保存')

    def _card_title(self):
        """卡片标题：目标语言名"""
        if self._toolbar is not None:
            name = self._toolbar.current_language_text()
            if name:
                return f"译为 {name}"
        return result_render.MODE_LABELS.get(self._mode, '翻译结果')

    def _card_body(self, original, translated):
        """按当前模式组合卡片正文"""
        original = (original or '').strip()
        translated = (translated or '').strip()
        if self._mode == result_render.MODE_ORIGINAL:
            return original or translated
        if self._mode == result_render.MODE_BILINGUAL:
            return f"{original}\n{translated}".strip()
        return translated or original

    def _draw_card(self, painter, anchor, title, body, footer):
        """绘制结果卡片（自动定位到选区/工具条下方，越界翻转）"""
        width = min(result_render.CARD_MAX_WIDTH,
                    max(280, anchor.width() if anchor else 280))
        size, lines = result_render.card_content_size(
            painter, body, title, footer, width)
        rect = result_render.card_geometry(
            self._card_anchor(anchor), self.rect(), size)
        result_render.paint_card(painter, rect, title, lines, footer)

    def _card_anchor(self, anchor):
        """卡片避开工具条：把选区下沿延伸到工具条底部（工具条永远盖在卡片之上）"""
        rect = QRect(anchor)
        if self._toolbar is not None and not self._toolbar.isHidden():
            bottom = self._toolbar.geometry().bottom()
            if bottom > rect.bottom():
                rect.setBottom(bottom)
        return rect

    def _draw_loading_spinner(self, painter, anchor):
        """
        选区居中的加载动画：一个深色小圆盘 + 旋转的弧

        比原来那句"识别并翻译中…"更安静，也省掉一行要看的中文；
        圆盘位置按选区中心算好并记下来，定时器只重绘这一小块，
        不必每帧把整张冻结桌面重画一遍。
        """
        size = self._SPINNER_BOX
        x = anchor.center().x() - size // 2
        y = anchor.center().y() - size // 2
        x = max(4, min(x, self.screen_width - size - 4))
        y = max(4, min(y, self.screen_height - size - 4))
        bubble = QRect(x, y, size, size)
        # 局部重绘范围：多留 2px 给抗锯齿边缘
        self._spinner_rect = bubble.adjusted(-2, -2, 2, 2)

        painter.save()
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._BUBBLE_BG)
            painter.drawRoundedRect(bubble, size // 2, size // 2)

            inset = self._SPINNER_RING_INSET
            ring = QRectF(bubble.adjusted(inset, inset, -inset, -inset))

            track = QColor(self._BUBBLE_TEXT)
            track.setAlpha(56)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(track, self._SPINNER_WIDTH))
            painter.drawEllipse(ring)

            arc = QPen(self._BUBBLE_TEXT, self._SPINNER_WIDTH)
            arc.setCapStyle(Qt.RoundCap)
            painter.setPen(arc)
            # Qt 的角度单位是 1/16 度；起点取负角度，视觉上是顺时针转
            painter.drawArc(
                ring, int(-self._spinner_angle * 16), int(-self._SPINNER_ARC * 16))
        finally:
            painter.restore()

    def _start_loading_spinner(self):
        """开始转圈（重复调用不会叠加定时器）"""
        if self._spinner_timer is None:
            self._spinner_timer = QTimer(self)
            self._spinner_timer.setInterval(self._SPINNER_INTERVAL)
            self._spinner_timer.timeout.connect(self._tick_loading_spinner)
        if not self._spinner_timer.isActive():
            self._spinner_angle = 0
            self._spinner_rect = None
            self._spinner_timer.start()

    def _tick_loading_spinner(self):
        """推进一帧：有上次的圆盘位置就只刷那一块"""
        self._spinner_angle = (self._spinner_angle + self._SPINNER_STEP) % 360
        rect = self._spinner_rect
        if rect is not None and not rect.isEmpty():
            self.update(rect)
        else:
            self.update()

    def _stop_loading_spinner(self):
        """停表：结果回来（成功或失败）后不再空转"""
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
        self._spinner_rect = None

    def _draw_pill(self, painter, anchor, text):
        """选区居中的状态胶囊（加载中/提示）"""
        font = painter.font()
        font.setPointSize(10)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        tw = metrics.boundingRect(text).width() + 26
        th = metrics.height() + 12
        x = anchor.center().x() - tw // 2
        y = anchor.center().y() - th // 2
        x = max(4, min(x, self.screen_width - tw - 4))
        y = max(4, min(y, self.screen_height - th - 4))
        bubble = QRect(x, y, tw, th)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._BUBBLE_BG)
        painter.drawRoundedRect(bubble, th // 2, th // 2)
        painter.setPen(QPen(self._BUBBLE_TEXT))
        painter.drawText(bubble, Qt.AlignCenter, text)

    # ---- 纯截图编辑态 ----

    def _enter_capture_edit(self):
        """框选确认后进入编辑态：标注 / 取字 / 滚动截图 / 输出"""
        if self._capture_editor is None:
            editor = CaptureEditor(self)
            editor.set_color(self.capture_option('pen_color', PRESET_COLORS[0]))
            editor.set_width(self.capture_option_int(
                'pen_width', DEFAULT_WIDTH,
                WIDTH_CHOICES[0], WIDTH_CHOICES[-1]))
            editor.set_font_size(self.capture_option_int(
                'font_size', DEFAULT_FONT_SIZE,
                FONT_CHOICES[0], FONT_CHOICES[-1]))
            editor.on_state_changed = self._on_capture_state_changed
            # 滚轮/选中图元改了线宽字号时同步工具条下拉
            editor.on_style_changed = self._on_capture_style_changed
            self._capture_editor = editor
        else:
            # 重新取景：旧标注属于旧画面，全部丢弃
            had_shapes = bool(self._capture_editor.shapes)
            self._capture_editor.reset_for_new_selection()
            if had_shapes:
                self._show_capture_toast('选区已重新取景，原标注已清空')
        self._capture_edit = True
        self._window_hover_rect = None
        self._ensure_capture_toolbar()
        self._show_toolbar()
        self.update()

    def _ensure_capture_toolbar(self):
        """创建编辑态工具条（复用同一实例，保留工具/颜色选择）"""
        editor = self._capture_editor
        if self._capture_toolbar is not None:
            self._capture_toolbar.set_busy(False)
            return
        toolbar = CaptureToolbar(
            self, tool=editor.tool, color=editor.color,
            width=editor.width, font_size=editor.font_size)
        toolbar.tool_changed.connect(self._capture_set_tool)
        toolbar.color_changed.connect(self._capture_set_color)
        toolbar.width_changed.connect(self._capture_set_width)
        toolbar.font_size_changed.connect(self._capture_set_font_size)
        toolbar.undo_clicked.connect(editor.undo)
        toolbar.redo_clicked.connect(editor.redo)
        toolbar.clear_clicked.connect(editor.clear)
        toolbar.eyedropper_clicked.connect(self._capture_toggle_eyedropper)
        toolbar.ocr_clicked.connect(self._capture_ocr)
        toolbar.scroll_clicked.connect(self._capture_scroll)
        toolbar.copy_clicked.connect(lambda: self._copy_image('original'))
        toolbar.save_clicked.connect(lambda: self._save_image('original'))
        toolbar.pin_clicked.connect(self._pin_result)
        toolbar.cancel_clicked.connect(self._cancel_screenshot)
        self._capture_toolbar = toolbar
        self._on_capture_state_changed(
            bool(editor.undo_stack), bool(editor.redo_stack))

    def _on_capture_state_changed(self, can_undo, can_redo):
        if self._capture_toolbar is not None:
            self._capture_toolbar.set_undo_state(can_undo, can_redo)

    def _on_capture_style_changed(self, kind, value):
        """线宽/字号变化：同步工具条下拉的选中项，并安排一次偏好落盘"""
        toolbar = self._capture_toolbar
        if toolbar is not None:
            if kind == 'width':
                toolbar.set_width(value)
            else:
                toolbar.set_font_size(value)
        self._schedule_persist_pref()

    def _schedule_persist_pref(self):
        """滚轮连调时攒一下再写 ini，避免每拨一格都落一次盘"""
        if self._pref_timer is None:
            self._pref_timer = QTimer(self)
            self._pref_timer.setSingleShot(True)
            self._pref_timer.timeout.connect(self._persist_capture_pref)
        self._pref_timer.start(500)

    def _persist_capture_pref(self):
        """把当前颜色/线宽/字号写回 [Capture]，下次截图保持一致"""
        if self.config_manager is None or self._capture_editor is None:
            return
        editor = self._capture_editor
        try:
            with self.config_manager.batch():
                self.config_manager.set_capture_option('pen_color', editor.color)
                self.config_manager.set_capture_option('pen_width', str(editor.width))
                self.config_manager.set_capture_option('font_size', str(editor.font_size))
        except Exception:
            logger.debug("保存截图偏好失败，忽略")

    def _capture_set_tool(self, key):
        if self._capture_editor is not None:
            self._capture_editor.set_tool(key)
        if self._capture_toolbar is not None:
            self._capture_toolbar.set_tool(key)
            self._capture_toolbar.set_eyedropper(False)
        self.setCursor(QCursor(
            Qt.ArrowCursor if key == 'select' else Qt.CrossCursor))
        self.update()

    def _capture_set_color(self, hex_color):
        if self._capture_editor is not None:
            self._capture_editor.set_color(hex_color)
        if self._capture_toolbar is not None:
            self._capture_toolbar.set_color(hex_color)
        self._persist_capture_pref()
        self.update()

    def _capture_set_width(self, width):
        if self._capture_editor is not None:
            self._capture_editor.set_width(width)
        self._persist_capture_pref()
        self.update()

    def _capture_set_font_size(self, size):
        if self._capture_editor is not None:
            self._capture_editor.set_font_size(size)
        self._persist_capture_pref()
        self.update()

    def _capture_toggle_eyedropper(self):
        editor = self._capture_editor
        if editor is None:
            return
        editor.set_eyedropper(not editor.eyedropper_mode)
        if self._capture_toolbar is not None:
            self._capture_toolbar.set_eyedropper(editor.eyedropper_mode)
        self.setCursor(QCursor(
            Qt.CrossCursor if editor.eyedropper_mode else Qt.ArrowCursor))

    def _capture_ocr(self):
        """工具条「取字」：本地 OCR，不联网"""
        if self._capture_busy:
            return
        image = (self._result or {}).get('image')
        if image is None:
            return
        self.set_capture_busy(True)
        self.ocr_requested.emit(image)

    def _capture_scroll(self):
        """工具条「滚动截图」：让开遮罩，由用户自己滚动，后台定时抓帧拼接"""
        if self._capture_busy:
            return
        image = (self._result or {}).get('image')
        rect = self._result_rect
        if image is None or rect is None:
            return
        from .capture_scroll import MAX_DURATION_SECONDS
        scale_x = self._grab_scale or 1.0
        scale_y = self._grab_scale_y or 1.0
        context = {
            # 相对整屏抓拍图的物理像素裁剪框：与 _confirm_selection 同算法
            'crop_box': (
                max(0, int(rect.left() * scale_x)),
                max(0, int(rect.top() * scale_y)),
                int((rect.left() + rect.width()) * scale_x),
                int((rect.top() + rect.height()) * scale_y)),
            'all_screens': bool(self._cover_all_screens),
        }
        # 当次会话由用户决定何时结束，兜底时限得放到抓帧线程的上限之外
        self.set_capture_busy(
            True, timeout_ms=int(MAX_DURATION_SECONDS * 1000) + 15000)
        # 遮罩是置顶全屏窗口，不藏起来就会盖住要滚动的页面，
        # 抓帧也正好需要干净的桌面
        self.hide()
        self.scroll_requested.emit(context, image)

    def show_scroll_banner(self, finish_event, cancel_event, progress):
        """显示长截图控制面板；放在选区之外，避免被长图的裁剪框收进去"""
        self.hide_scroll_banner()
        banner = _ScrollCaptureBanner(finish_event, cancel_event, progress)
        size = banner.sizeHint()
        width, height = size.width(), size.height()
        origin = self._desktop_rect.topLeft()
        selection = QRect(self._result_rect or self.rect()).translated(origin)
        screen = QApplication.primaryScreen()
        area = (screen.availableGeometry() if screen is not None
                else QRect(0, 0, self.screen_width, self.screen_height))
        candidates = (
            QPoint(area.right() - width - 12, area.top() + 12),
            QPoint(area.left() + 12, area.top() + 12),
            QPoint(area.right() - width - 12, area.bottom() - height - 12),
            QPoint(area.left() + 12, area.bottom() - height - 12),
        )
        for pos in candidates:
            if not QRect(pos, QSize(width, height)).intersects(selection):
                banner.move(pos)
                banner.show()
                self._scroll_banner = banner
                return
        # 选区几乎占满屏幕：没有不污染抓帧的位置，退化为截图快捷键结束
        banner.stop()
        banner.deleteLater()
        logger.info("选区过大，不显示长截图控制面板，请用截图快捷键结束")

    def hide_scroll_banner(self):
        """收起长截图控制面板（结果回投、遮罩关闭时都要调）"""
        banner = self._scroll_banner
        self._scroll_banner = None
        if banner is not None:
            banner.stop()
            banner.hide()
            banner.deleteLater()

    def _restore_if_capture_stuck(self):
        """结果迟迟没回投：恢复遮罩显示与编辑态，避免永久隐藏/忙碌"""
        if not self._capture_busy:
            return
        logger.warning("截图后台任务结果未回投，超时复位编辑态")
        if not self.isVisible():
            self.show()
            self.raise_()
            self.activateWindow()
            self.setFocus()
        self.hide_scroll_banner()
        self.set_capture_busy(False)
        self._show_capture_toast('处理超时，请重试')

    def set_capture_busy(self, busy, timeout_ms=30000):
        """
        OCR / 长截图进行中：禁用重复发起，并显示忙碌提示

        Args:
            busy (bool): 是否进入忙碌态
            timeout_ms (int): 结果迟迟不回投时的兜底复位时限
        """
        self._capture_busy = bool(busy)
        if self._capture_toolbar is not None:
            self._capture_toolbar.set_busy(busy)
        self._capture_toast = '处理中…' if busy else ''
        if busy:
            if self._capture_watchdog is None:
                # 子 QTimer 随窗口一起销毁，不会在窗口已关闭后回调到废对象
                self._capture_watchdog = QTimer(self)
                self._capture_watchdog.setSingleShot(True)
                self._capture_watchdog.timeout.connect(
                    self._restore_if_capture_stuck)
            self._capture_watchdog.start(int(timeout_ms))
        elif self._capture_watchdog is not None:
            self._capture_watchdog.stop()
        self.update()

    def on_ocr_finished(self, text):
        """OCR 结果回投（主线程）：弹可编辑结果窗"""
        self.set_capture_busy(False)
        if not text:
            self._show_capture_toast('未识别到文字')
            return
        try:
            from . import capture_ocr
            capture_ocr.show_result(self, text)
        except Exception:
            logger.exception("展示 OCR 结果失败")
        self._show_capture_toast(f'已提取 {len(text)} 个字符')

    def _restore_after_scroll(self):
        """长截图期间遮罩是隐藏的：取消/失败时把编辑态完整恢复回来"""
        self.show()
        self.raise_()
        # 隐藏期间键盘焦点已经丢了，不重新抢回来的话 ESC / Ctrl+S 全部失灵
        self.activateWindow()
        self.setFocus()

    def on_scroll_finished(self, image, canceled=False):
        """长截图结果回投（主线程）：成功后直接展示长图，而不是回到截图页面

        取消或没拼出图时仍恢复原编辑态，方便重新选区或重试；成功时把长图
        交给独立预览窗（可滚动/缩放/复制/保存），遮罩随即收起并释放。
        """
        self.hide_scroll_banner()
        self.set_capture_busy(False)
        if canceled or image is None or image.width <= 0 or image.height <= 0:
            self._restore_after_scroll()
            self._show_capture_toast('已取消长截图' if canceled else '长截图未完成')
            return
        pixmap = result_render.pil_to_qpixmap(image)
        if pixmap is None:
            self._restore_after_scroll()
            self._show_capture_toast('长图生成失败')
            return
        clipboard.set_pixmap_later(pixmap, on_done=None)
        if self.capture_option_bool('quick_save'):
            # 只写盘、不跑「保存后动作」：遮罩马上要关，那里面排的关闭回调
            # 会打在一个已经销毁的窗口上。保存入口仍在结果窗的「保存…」按钮。
            try:
                capture_output.save_pixmap(
                    pixmap,
                    fmt=self.capture_option('format', 'png'),
                    quality=self.capture_option_int('jpg_quality', 90, 1, 100),
                    directory=self.capture_option('save_dir', '') or None,
                    template=self.capture_option('filename_template', ''),
                    quick=True, parent=None, title='保存长截图')
            except Exception:
                logger.exception('长截图快速保存失败')

        try:
            from .scroll_result import show_scroll_result
            window = show_scroll_result(pixmap, config_manager=self.config_manager)
        except Exception:
            logger.exception('展示长截图结果窗失败，退回截图编辑态')
            self._restore_after_scroll()
            self._show_capture_toast('长图预览打开失败，长图已复制到剪贴板')
            return
        if window is None:
            self._restore_after_scroll()
            self._show_capture_toast('长图生成失败')
            return
        # 结果窗是独立窗口，不随遮罩关闭。这里借 screenshot_canceled 让管理器
        # 释放遮罩引用并恢复弹窗能力（它是唯一的「遮罩已结束」信号）。
        self._hide_toolbar()
        self.hide()
        self.screenshot_canceled.emit()
        self.close()
        logger.info(f'长截图完成：{image.width}×{image.height}，已切换到结果图预览')

    def _save_capture_pixmap(self, pixmap, title='保存截图'):
        """统一走 capture_output：格式/质量/目录/模板/快速保存/保存后动作"""
        path = capture_output.save_pixmap(
            pixmap,
            fmt=self.capture_option('format', 'png'),
            quality=self.capture_option_int('jpg_quality', 90, 1, 100),
            directory=self.capture_option('save_dir', '') or None,
            template=self.capture_option('filename_template', ''),
            quick=self.capture_option_bool('quick_save'),
            parent=self, title=title)
        if path:
            action = self.capture_option('after_save', 'none')
            if action in ('copy', 'copy_close'):
                clipboard.set_pixmap_later(pixmap, on_done=None)
            if action in ('close', 'copy_close'):
                QTimer.singleShot(0, self._cancel_screenshot)
        return path

    def _show_capture_toast(self, message, duration_ms=1800):
        self._capture_toast = message
        self.update()
        if self._toast_timer is None:
            self._toast_timer = QTimer(self)
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(self._clear_capture_toast)
        self._toast_timer.start(duration_ms)

    def _clear_capture_toast(self):
        self._capture_toast = ''
        self.update()

    # ---- 框选辅助：窗口自动识别 / 放大镜 ----

    def _update_window_hover(self, pos):
        """命中包含光标的最小窗口矩形，供单击直接选中整个窗口"""
        self._window_hover_rect = None
        if not self.capture_only or self._capture_edit:
            return
        best = None
        try:
            for l, t, r, b in self._snap_window_rects:
                rect = QRect(l, t, r - l, b - t)
                if rect.contains(pos) and (best is None or
                        rect.width() * rect.height()
                        < best.width() * best.height()):
                    best = rect
        except Exception:
            best = None
        self._window_hover_rect = best

    def _draw_window_hover(self, painter):
        rect = self._window_hover_rect
        if rect is None:
            return
        painter.setBrush(QColor(59, 130, 246, 38))
        painter.setPen(QPen(QColor(96, 165, 250, 220), 2))
        painter.drawRect(rect)
        self._draw_size_bubble(painter, rect, hint='· 单击选中窗口')

    def _draw_capture_magnifier(self, painter):
        if not self.capture_option_bool('magnifier', True):
            return
        capture_magnifier.draw_magnifier(
            painter, self._cursor_pos, self.full_screen,
            self._grab_scale or 1.0, self.rect())

    def _ensure_toolbar(self, payload):
        """结果首次可用时创建工具条，之后复用同一实例"""
        if self._toolbar is not None:
            self._toolbar.set_busy(False)
            self._show_toolbar()
            return
        toolbar = OverlayToolbar(self, payload.get('languages') or [],
                                 current_lang=payload.get('target_lang'),
                                 mode=self._mode,
                                 capture_only=self.capture_only)
        toolbar.language_changed.connect(
            lambda lang: self.retranslate_requested.emit(lang, True))
        toolbar.mode_changed.connect(self._set_mode)
        toolbar.text_copy_clicked.connect(self._copy_text_kind)
        toolbar.image_save_clicked.connect(self._save_image)
        toolbar.image_copy_clicked.connect(self._copy_image)
        toolbar.pin_clicked.connect(self._pin_result)
        toolbar.close_clicked.connect(self._cancel_screenshot)
        self._toolbar = toolbar
        self._show_toolbar()

    def _show_toolbar(self):
        """显示并重新摆放工具条（翻译结果条 / 截图编辑条二选一）"""
        toolbar = self._capture_toolbar if self.capture_only else self._toolbar
        if toolbar is None:
            return
        toolbar.adjustSize()
        available = max(120, self.screen_width - 8)
        width = toolbar.fit_width(available)
        height = toolbar.sizeHint().height()
        rect = self._result_rect or self.rect()

        x = rect.center().x() - width // 2
        x = max(4, min(x, self.screen_width - width - 4))
        y = rect.bottom() + 8
        if y + height > self.screen_height - 4:
            y = rect.top() - height - 8
        toolbar.setGeometry(x, max(4, y), width, height)
        toolbar.show()
        toolbar.raise_()

    def _hide_toolbar(self):
        """选择/失败态下收起工具条（保留实例与语言/工具选择）"""
        for toolbar in (self._toolbar, self._capture_toolbar):
            if toolbar is not None:
                toolbar.hide()

    def _set_mode(self, mode):
        """切换三态展示并同步工具条选中态"""
        if mode not in result_render.MODES:
            return
        self._mode = mode
        if self._toolbar is not None:
            self._toolbar.set_mode(mode, emit=False)
        self.update()

    def _cycle_mode(self):
        """T 键：原文 → 双语 → 译文 循环"""
        order = result_render.MODES
        index = order.index(self._mode) if self._mode in order else 0
        self._set_mode(order[(index + 1) % len(order)])

    def _result_text(self):
        """
        当前应复制到剪贴板的文本

        选中/悬停某段时只复制该段，否则复制整段结果。
        """
        payload = self._result or {}
        texts = payload.get('block_texts') or []
        index = self._active_paragraph()
        if index is not None and texts and index < len(self._paragraphs):
            original = '\n'.join(self._blocks[i].text
                                 for i in self._paragraphs[index][1])
            translated = '\n'.join(texts[i] for i in self._paragraphs[index][1]
                                   if i < len(texts))
            return self._card_body(original, translated)
        return self._card_body(payload.get('original_text', ''),
                              payload.get('translated_text', ''))

    def _result_text_kind(self, kind):
        """
        取指定类型的文本：'original' 原文 / 'translated' 译文

        与 Ctrl+C 一致：选中/悬停某段时只取该段，否则取整段结果。

        Args:
            kind (str): 'original' 或 'translated'

        Returns:
            str: 可直接写入剪贴板的文本（可能为空）
        """
        payload = self._result or {}
        index = self._active_paragraph()
        if index is not None and index < len(self._paragraphs):
            indices = self._paragraphs[index][1]
            if kind == 'translated':
                texts = payload.get('block_texts') or []
                return '\n'.join(texts[i] for i in indices if i < len(texts))
            return '\n'.join(self._blocks[i].text for i in indices)
        if kind == 'translated':
            return (payload.get('translated_text') or '').strip()
        return (payload.get('original_text') or '').strip()

    def _copy_text_kind(self, kind):
        """工具条按钮：提取原文/译文文本到剪贴板"""
        text = self._result_text_kind(kind)
        if not text:
            return

        def done(ok):
            """确认写入生效才记日志并收起遮罩，失败由 clipboard 模块记警告"""
            if ok:
                label = '译文' if kind == 'translated' else '原文'
                logger.info(f"已复制{label}文本到剪贴板")
                QTimer.singleShot(0, self._finish_after_copy)

        clipboard.set_text_later(text, on_done=done)

    def _copy_result(self):
        """
        复制当前展示文本到剪贴板（Ctrl+C）

        剪贴板被其他程序占用时写入会静默失败，故走带生效校验的重试写入；
        重试交给事件循环排（不在这Sleep），遮罩不会因为复制卡一下。
        """
        text = self._result_text()
        if not text:
            return

        def done(ok):
            """确认写入生效才记"已复制"，失败由 clipboard 模块记警告日志"""
            if ok:
                logger.info("已复制翻译结果到剪贴板")
                QTimer.singleShot(0, self._finish_after_copy)

        clipboard.set_text_later(text, on_done=done)

    def _render_original_pixmap(self):
        """
        选区原图（不含译文覆盖）

        Returns:
            QPixmap 或 None
        """
        payload = self._result
        if payload is None or payload.get('image') is None:
            return None
        return result_render.pil_to_qpixmap(payload['image'])

    def _render_result_pixmap(self, mode=None):
        """
        合成结果图（选区底图 + 就地覆盖的译文），按图像物理像素绘制

        Args:
            mode (str 或 None): 显式指定覆盖模式；None 表示跟随当前展示模式

        Returns:
            QPixmap 或 None
        """
        payload = self._result
        if payload is None:
            return None
        image = payload['image']
        if image is None:
            return None
        mode = mode or self._mode
        base = result_render.pil_to_qpixmap(image)
        if base is None:
            logger.error("结果图转换失败（PIL → QPixmap），放弃本次导出")
            return None

        pixmap = QPixmap(image.width, image.height)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        if not painter.isActive():
            logger.error("结果图画布创建失败，放弃本次导出")
            return None
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            # 底图按需从 PIL 转换：常驻一份 1:1 QPixmap 等于把选区再存一遍
            painter.drawPixmap(0, 0, base)
            texts = payload.get('block_texts') or []
            if texts and mode != result_render.MODE_ORIGINAL:
                result_render.paint_blocks(
                    painter, image, payload.get('blocks') or [], texts,
                    QPoint(0, 0), mode, scale=1.0)
        except Exception as e:
            logger.exception(f"结果图合成失败: {e}")
            return None
        finally:
            painter.end()
        # 倍率必须在画完之后声明：setDevicePixelRatio 会改变 QPainter 的坐标系
        # （逻辑坐标 = 物理像素 / 倍率），先声明再按物理坐标绘制会把内容放大
        # 倍率倍、只剩左上 1/倍率² 的区域落在图内——150% 屏上保存/钉住的图
        # 就只剩原选区左上角那一块。绘制期间不声明，内容才是 1:1 物理像素。
        if self._scale > 1.0:
            pixmap.setDevicePixelRatio(self._scale)
        return pixmap

    def _image_pixmap(self, kind):
        """
        取可导出的图片：'original' 原图 / 'translated' 译图（整段译文）

        Args:
            kind (str): 'original' 或 'translated'

        Returns:
            QPixmap 或 None
        """
        if self.capture_only and self._capture_editor is not None:
            # 编辑态：标注已烧录进合成图，复制/保存/钉住都用这一张
            return self._capture_editor.finish_pixmap()
        if kind == 'translated':
            return self._render_result_pixmap(mode=result_render.MODE_TRANSLATED)
        return self._render_original_pixmap()

    def _pin_result(self):
        """把当前结果钉在屏幕上（原选区位置，1:1 像素）"""
        pixmap = (self._image_pixmap('original')
                  if self.capture_only else self._render_result_pixmap())
        if pixmap is None:
            return
        rect = self._result_rect or self.rect()
        # 遮罩局部坐标 → 屏幕坐标：副屏在主屏左侧/上方时桌面原点为负，
        # 不换算的话钉住的图会整体偏掉一个桌面原点
        PinWindow(
            pixmap,
            top_left=rect.topLeft() + self._desktop_rect.topLeft(),
            title='钉住的截图' if self.capture_only else '钉住的翻译结果',
            config_manager=self.config_manager)
        logger.info("结果已钉到屏幕")

    def _save_result(self):
        """保存当前展示模式的结果图（Ctrl+S）"""
        self._save_pixmap(self._render_result_pixmap())

    def _save_image(self, kind):
        """工具条按钮：保存原图/译图（纯截图走 [Capture] 配置）"""
        pixmap = self._image_pixmap(kind)
        if pixmap is None:
            return
        if self.capture_only:
            self._save_capture_pixmap(pixmap, title='保存截图')
        elif kind == 'translated':
            self._save_pixmap(pixmap, prefix='翻译结果', title='保存译图')
        else:
            self._save_pixmap(pixmap, prefix='截图原图', title='保存原图')

    def _copy_image(self, kind):
        """工具条按钮：复制原图/译图到剪贴板"""
        pixmap = self._image_pixmap(kind)
        if pixmap is None:
            return

        def done(ok):
            """确认写入生效才记日志并收起遮罩，失败由 clipboard 模块记警告"""
            if ok:
                label = '译图' if kind == 'translated' else '原图'
                logger.info(f"已复制{label}到剪贴板")
                QTimer.singleShot(0, self._finish_after_copy)

        clipboard.set_pixmap_later(pixmap, on_done=done)

    def _save_pixmap(self, pixmap, prefix='翻译结果', title='保存翻译结果'):
        """
        把图片存成 PNG

        Args:
            pixmap (QPixmap 或 None): 要保存的图片；None 时直接返回
            prefix (str): 默认文件名前缀
            title (str): 文件对话框标题
        """
        if pixmap is None:
            return
        from datetime import datetime
        from PyQt5.QtWidgets import QFileDialog
        import os

        default_dir = os.path.expanduser('~')
        name = datetime.now().strftime(f'{prefix}_%Y%m%d_%H%M%S.png')
        path, _selected = QFileDialog.getSaveFileName(
            self, title, os.path.join(default_dir, name),
            'PNG 图片 (*.png)')
        if not path:
            return
        if pixmap.save(path, 'PNG'):
            logger.info(f"图片已保存: {path}")
        else:
            logger.error(f"图片保存失败: {path}")

    def _handle_result_ctrl_key(self, event):
        """结果态下的 Ctrl 组合键"""
        key = event.key()
        if self.capture_only:
            # 纯截图没有文本可复制/无可对比的译图，复制的就是原图
            if key == Qt.Key_C:
                self._copy_image('original')
            elif key == Qt.Key_S:
                self._save_image('original')
            return
        if key == Qt.Key_C:
            self._copy_result()
        elif key == Qt.Key_S:
            self._save_result()

    def _handle_result_key(self, event):
        """结果态下的单键操作：T 切模式、1/2/3 直选、P 钉住、R 重试"""
        key = event.key()
        if self.capture_only:
            # 纯截图只有一种形态：保留钉住，模式切换/重试没有意义
            if key == Qt.Key_P:
                self._pin_result()
            return
        if key == Qt.Key_T:
            self._cycle_mode()
        elif key in (Qt.Key_1, Qt.Key_2, Qt.Key_3):
            index = int(key) - int(Qt.Key_1)
            self._set_mode(result_render.MODES[index])
        elif key == Qt.Key_P:
            self._pin_result()
        elif key == Qt.Key_R:
            self.retry_requested.emit()

    def mousePressEvent(self, event):
        """
        鼠标按下事件：编辑态先交给标注控制器，结果态再做边/角命中与段落命中
        """
        if event.button() == Qt.LeftButton:
            self._press_pos = QPoint(event.pos())
            # 编辑态先认选区的边/角：任何工具下抓住边框都是缩放选区，
            # 而不是在边框上落一笔标注
            if self.capture_only and self._capture_edit \
                    and self._begin_result_drag(event.pos(), resize_only=True):
                return
            if self.capture_only and self._capture_edit \
                    and self._capture_editor is not None \
                    and self._capture_editor.mouse_press(event.pos(), event.button()):
                return
            if self._begin_result_drag(event.pos()):
                return
            if self._handle_result_press(event.pos()):
                return
            self.begin = event.pos()
            self.end = event.pos()
            self.is_selecting = True
            self._active_snap_lines = None
            self.update()
        elif event.button() == Qt.RightButton:
            if self.capture_only and self._capture_edit \
                    and self._capture_editor is not None \
                    and self._capture_editor.mouse_press(event.pos(), event.button()):
                return
            # 右键：结果态退回整段展示，选择态直接取消
            if self._result is not None and self._pinned_paragraph is not None:
                self._pinned_paragraph = None
                self.update()
            else:
                self._cancel_screenshot()

    def _handle_result_press(self, pos):
        """
        结果/失败/加载态下的左键点击

        命中段落则切换选中；点在选区外则开始重新框选。

        Returns:
            bool: 是否已消费该事件
        """
        if not (self._result or self._error or self._loading):
            return False
        if self._result is not None:
            index = self._paragraph_at(pos)
            if index is not None:
                self._pinned_paragraph = None if self._pinned_paragraph == index else index
                self.update()
                return True
        # 选区之外：开始一次新的框选，工具条让位，结果数据保留以防误触
        self._hide_toolbar()
        self.setCursor(QCursor(Qt.CrossCursor))
        return False

    def mouseMoveEvent(self, event):
        """
        鼠标移动事件：编辑态画笔/草图优先，其次拖拽选区、结果态调整与悬停
        """
        self._cursor_pos = QPoint(event.pos())
        self._update_mark_hover(event.pos())
        if self._drag_mode:
            # 选区拖动优先于标注：按住边框缩放时不能被画笔接走
            self._apply_adjust_drag(event.pos())
            self.update()
            return
        if self.capture_only and self._capture_edit \
                and self._capture_editor is not None \
                and self._capture_editor.mouse_move(event.pos()):
            return
        if self.capture_only and not self._capture_edit:
            self._update_window_hover(event.pos())
            if not self.is_selecting:
                self.update()
        if self.is_selecting:
            self.end = event.pos()
            self.update()
        elif self._result is not None or self._error:
            if self._result is not None:
                index = self._paragraph_at(event.pos())
                if index != self._hover_paragraph:
                    self._hover_paragraph = index
                    self.update()
            self._update_result_cursor(event.pos())

    def wheelEvent(self, event):
        """
        滚轮：截图编辑态调整线宽 / 字号

        有选中图元时改该图元，没选中时改当前工具的默认值；其它状态滚轮无关，
        交回默认处理。
        """
        if self.capture_only and self._capture_edit \
                and self._capture_editor is not None:
            delta = event.angleDelta().y()
            if delta:
                self._capture_editor.wheel(1 if delta > 0 else -1)
            event.accept()
            return
        event.ignore()

    def mouseReleaseEvent(self, event):
        """
        鼠标释放事件：框选松手立即识别；拖动选区松手立即按新范围重新取景/识别
        """
        if event.button() != Qt.LeftButton:
            return
        if self.capture_only and self._capture_edit \
                and self._capture_editor is not None \
                and self._capture_editor.mouse_release(event.pos(), event.button()):
            return
        if self.is_selecting:
            self.end = event.pos()
            self.is_selecting = False
            self._active_snap_lines = None
            # 单击（几乎没拖）且悬停在某个窗口上：直接选中整个窗口
            if self.capture_only and self._window_hover_rect is not None \
                    and self._press_pos is not None:
                drag = (abs(self.end.x() - self._press_pos.x())
                        + abs(self.end.y() - self._press_pos.y()))
                if drag < self._MIN_SELECT_EDGE:
                    window = self._window_hover_rect
                    self.begin = QPoint(window.left(), window.top())
                    self.end = QPoint(window.left() + window.width(),
                                      window.top() + window.height())
            self._confirm_selection()
            return
        if not self._drag_mode:
            return

        start_rect = QRect(self._drag_start_rect)
        dragged_rect = QRect(self._result_rect or self._get_rect())
        self._drag_mode = None
        self._resize_edges = frozenset()
        self._active_snap_lines = None
        if dragged_rect.width() > self._MIN_SELECT_EDGE \
                and dragged_rect.height() > self._MIN_SELECT_EDGE:
            if dragged_rect != start_rect:
                # 松开即按新范围重裁：截图态重新取景，翻译态重新识别，
                # 都不把旧内容搬到新位置上去
                self._confirm_selection()
                return
        else:
            # 拖得太小：回到拖动前的范围，别把已有结果裁成一条缝
            self._result_rect = QRect(start_rect)
            self._set_selection_rect(start_rect)
        self._show_toolbar()
        self._update_result_cursor(event.pos())
        self.update()

    def _begin_result_drag(self, pos, resize_only=False):
        """
        结果态按下：命中边/角或选区内部时进入拖动调整

        Args:
            pos (QPoint): 按下的位置（遮罩局部逻辑坐标）
            resize_only (bool): 只认边/角。编辑态用它把「抓边框缩放选区」
                从标注工具手里抢过来，选区内部仍旧交给标注/平移

        Returns:
            bool: 是否已进入拖动；False 表示事件应继续交给段落命中/新框选
        """
        if self._loading or not (self._result is not None or self._error):
            return False
        rect = self._result_rect
        if rect is None:
            return False
        zone = self._hit_zone(pos, rect)
        if zone is None:
            return False
        if resize_only and zone[0] != 'resize':
            return False
        if zone[0] == 'move' and self._result is not None \
                and self._paragraph_at(pos) is not None:
            # 段落点击优先：按在段落上是查看该段卡片，不是拖动整个选区
            return False
        self._drag_mode, self._resize_edges = zone
        self._drag_origin = QPoint(pos)
        self._drag_start_rect = QRect(rect)
        if self._capture_editor is not None:
            # 选区要重取景，选中图元的手柄先收起来，免得两套手柄叠在一起
            self._capture_editor._clear_selection(update=False)
        self._hide_toolbar()
        self._update_result_cursor(pos)
        return True

    def _hit_zone(self, pos, rect=None):
        """结果选区命中测试：边/角 → resize，内部 → move"""
        if rect is None:
            rect = self._result_rect or self._get_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return None
        hit = self._HANDLE_HIT
        within_x = rect.left() - hit <= pos.x() <= rect.right() + hit
        within_y = rect.top() - hit <= pos.y() <= rect.bottom() + hit
        dx_left = abs(pos.x() - rect.left())
        dx_right = abs(pos.x() - rect.right())
        dy_top = abs(pos.y() - rect.top())
        dy_bottom = abs(pos.y() - rect.bottom())

        edges = set()
        if within_y:
            if dx_left <= hit and dx_left <= dx_right:
                edges.add('l')
            elif dx_right <= hit:
                edges.add('r')
        if within_x:
            if dy_top <= hit and dy_top <= dy_bottom:
                edges.add('t')
            elif dy_bottom <= hit:
                edges.add('b')
        if edges:
            return 'resize', frozenset(edges)
        if rect.contains(pos):
            return 'move', frozenset()
        return None

    def _apply_adjust_drag(self, pos):
        """按拖拽位移缩放/移动选区，并夹在桌面内、至少保留 2 像素"""
        dx = pos.x() - self._drag_origin.x()
        dy = pos.y() - self._drag_origin.y()
        start = self._drag_start_rect
        edges = self._resize_edges

        if self._drag_mode == 'move':
            rect = QRect(start).translated(dx, dy)
            if rect.left() < 0:
                rect.moveLeft(0)
            if rect.top() < 0:
                rect.moveTop(0)
            if rect.right() >= self.screen_width:
                rect.moveRight(self.screen_width - 1)
            if rect.bottom() >= self.screen_height:
                rect.moveBottom(self.screen_height - 1)
        else:
            left = start.left() + (dx if 'l' in edges else 0)
            right = start.right() + (dx if 'r' in edges else 0)
            top = start.top() + (dy if 't' in edges else 0)
            bottom = start.bottom() + (dy if 'b' in edges else 0)
            left = max(0, min(left, self.screen_width - 1))
            right = max(0, min(right, self.screen_width - 1))
            top = max(0, min(top, self.screen_height - 1))
            bottom = max(0, min(bottom, self.screen_height - 1))
            # 拖过对边时停住而不是翻转：翻转会让选区在手里"跳"一下
            if left > right - 1:
                if 'l' in edges:
                    left = right - 1
                else:
                    right = left + 1
            if top > bottom - 1:
                if 't' in edges:
                    top = bottom - 1
                else:
                    bottom = top + 1
            rect = QRect(QPoint(left, top), QPoint(right, bottom))

        snapped, guides = self._snap_rect(rect)
        self._active_snap_lines = guides or None
        self._result_rect = QRect(snapped)
        self._set_selection_rect(snapped)

    @staticmethod
    def _cursor_for_edges(edges):
        """边集合 → 对应方向的缩放光标"""
        horizontal = 'l' in edges or 'r' in edges
        vertical = 't' in edges or 'b' in edges
        if horizontal and vertical:
            if ('l' in edges and 't' in edges) \
                    or ('r' in edges and 'b' in edges):
                return Qt.SizeFDiagCursor
            return Qt.SizeBDiagCursor
        if horizontal:
            return Qt.SizeHorCursor
        return Qt.SizeVerCursor

    def _update_result_cursor(self, pos):
        """结果态光标反馈：拖动中/可缩放边角/可移动内部/选区外重新框选"""
        if self._loading:
            return
        if self._drag_mode == 'move':
            self.setCursor(QCursor(Qt.SizeAllCursor))
            return
        if self._drag_mode == 'resize':
            self.setCursor(QCursor(self._cursor_for_edges(self._resize_edges)))
            return
        if self._result is None and self._error is None:
            return
        zone = self._hit_zone(pos)
        if zone is None:
            self.setCursor(QCursor(Qt.CrossCursor))
        elif zone[0] == 'resize':
            self.setCursor(QCursor(self._cursor_for_edges(zone[1])))
        elif self.capture_only and self._capture_edit:
            editor = self._capture_editor
            if editor is not None and (editor.eyedropper_mode
                                       or editor.tool != TOOL_SELECT):
                # 选区内部是落笔/取色的地方，保持工具自己的光标
                return
            self.setCursor(QCursor(Qt.SizeAllCursor))
        elif self._result is not None and self._paragraph_at(pos) is not None:
            self.setCursor(QCursor(Qt.ArrowCursor))
        else:
            self.setCursor(QCursor(Qt.SizeAllCursor))

    def _set_selection_rect(self, rect):
        """把选区矩形写回 begin/end 两个端点（end 为开区间上界）"""
        self.begin = QPoint(rect.left(), rect.top())
        self.end = QPoint(rect.left() + rect.width(), rect.top() + rect.height())

    def _confirm_selection(self):
        """确认当前选区：有效则发起识别，太小则取消（框选松手与结果态重识别共用）"""
        self._drag_mode = None
        self._resize_edges = frozenset()
        # 用吸附后的矩形裁剪，与用户在遮罩上看到的选择完全一致
        raw = self._get_rect()
        rect, _ = self._snap_rect(raw)
        self._active_snap_lines = None
        logger.info(f"选择区域: x={rect.x()}, y={rect.y()}, "
                    f"width={rect.width()}, height={rect.height()}")

        if rect.width() <= self._MIN_SELECT_EDGE \
                or rect.height() <= self._MIN_SELECT_EDGE:
            if self._loading:
                # 等结果期间的误触小拖拽：遮罩关掉等于把在途请求一起丢掉，
                # 用户看到的就是一片空白 + 白等几秒，这里保持加载画面不动
                self.setCursor(QCursor(Qt.ArrowCursor))
                self.update()
                return
            if self._result is not None:
                # 结果态下的误触小拖拽：回到原结果展示
                self._show_toolbar()
                self.setCursor(QCursor(Qt.ArrowCursor))
                self.update()
                return
            logger.info("选择区域太小，取消截图")
            self.screenshot_canceled.emit()
            self.close()
            return

        if self.full_screen is None:
            # 背景截图缺失时无法裁剪，直接取消，避免在Qt槽内抛出未捕获异常
            logger.warning("全屏背景截图不可用，取消截图")
            self._cancel_screenshot()
            return

        # 全屏图是物理像素，选区是逻辑坐标，按横纵缩放系数换算裁剪框。
        # QRect 的 right()/bottom() 是闭区间（= left+width-1），PIL 的裁剪框
        # 右/下沿是开区间：直接用 right()/bottom() 会少裁一像素，高分屏下
        # 每边少掉的不止一像素，识别首尾字符就会被切掉
        scale_x = self._grab_scale
        scale_y = self._grab_scale_y
        image_w, image_h = self.full_screen.size
        box = (
            max(0, min(image_w, int(round(rect.left() * scale_x)))),
            max(0, min(image_h, int(round(rect.top() * scale_y)))),
            max(0, min(image_w, int(round((rect.left() + rect.width()) * scale_x)))),
            max(0, min(image_h, int(round((rect.top() + rect.height()) * scale_y)))),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            # 缩放换算后落不到有效像素（例如选区贴在桌面最右/下沿之外）
            logger.warning(f"选区换算到抓拍图后为空: {box}，取消截图")
            self._cancel_screenshot()
            return
        # crop 与原图共享像素缓冲，必须 copy；整屏图保留到遮罩关闭，
        # 结果态拖动调整后要按新范围重新裁剪，不能提前释放
        screenshot = self.full_screen.crop(box).copy()
        logger.info(f"截图完成，大小: {screenshot.width}x{screenshot.height}")

        # 遮罩不关闭：先用本次裁剪图冻结选区做加载提示，结果回来后就地覆盖
        self._request_id += 1
        self._result = None
        self._error = None
        self._blocks = []
        self._paragraphs = []
        self._result_rect = QRect(rect)
        self._set_selection_rect(rect)
        self._scale = screenshot.width / max(1, rect.width())
        self._scale_y = screenshot.height / max(1, rect.height())
        self._crop_pixmap = result_render.pil_to_qpixmap(screenshot, dpr=self._scale)
        self.setCursor(QCursor(Qt.ArrowCursor))

        if self.capture_only:
            # 纯截图：不发起识别与翻译，裁剪图本身就是结果；进入编辑态
            self.region_committed.emit(QRect(rect))
            self.show_result({
                'original_text': '', 'translated_text': '',
                'blocks': [], 'block_texts': [],
                'image': screenshot, 'region': QRect(rect),
                'languages': [], 'target_lang': None,
                'request_id': self._request_id, 'error': None,
            })
            return

        # 遮罩不关闭：先用本次裁剪图冻结选区做加载提示，结果回来后就地覆盖
        self._loading = True
        self._hide_toolbar()
        self.region_selected.emit(screenshot, QRect(rect))
        self._start_loading_spinner()
        self.update()

    def keyPressEvent(self, event):
        """
        键盘按下事件
        """
        if self.capture_only and self._capture_edit \
                and self._capture_editor is not None \
                and self._capture_editor.key_press(event):
            # 编辑态的 Ctrl+Z / Ctrl+Y 已消费
            return
        if event.key() == Qt.Key_Escape:
            # 按下ESC键，取消截图
            self._cancel_screenshot()
        elif self._result is not None and event.modifiers() & Qt.ControlModifier:
            self._handle_result_ctrl_key(event)
        elif self._result is not None and not self.is_selecting:
            self._handle_result_key(event)
        elif self._error and event.key() == Qt.Key_R:
            self.retry_requested.emit()

    def closeEvent(self, event):
        """
        遮罩关闭：整屏与选区冻结图是整个遮罩最大的两块内存，立刻放掉

        Args:
            event (QCloseEvent): Qt 关闭事件对象
        """
        self._frozen_pixmap = None
        self._crop_pixmap = None
        self.full_screen = None
        self._stop_loading_spinner()
        if self._capture_editor is not None:
            self._capture_editor._stop_hover_anim()
        self._capture_editor = None
        self._capture_toolbar = None
        self.hide_scroll_banner()
        if self._capture_watchdog is not None:
            self._capture_watchdog.stop()
        if self._toast_timer is not None:
            self._toast_timer.stop()
            self._toast_timer = None
        if self._hover_timer is not None:
            self._hover_timer.stop()
            self._hover_timer = None
        if self._pref_timer is not None:
            self._pref_timer.stop()
            self._pref_timer = None
        super().closeEvent(event)

    def _finish_after_copy(self):
        """复制成功后收起遮罩：内容已在剪贴板，留着只会继续挡屏"""
        logger.info("内容已复制到剪贴板，关闭截图遮罩")
        self.screenshot_canceled.emit()
        self.close()

    def _cancel_screenshot(self):
        """
        取消截图
        """
        logger.info("用户取消截图")
        # 发送取消信号
        self.screenshot_canceled.emit()
        # 关闭窗口
        self.close()

    def _get_rect(self):
        """
        获取选择区域的矩形

        Returns:
            QRect: 选择区域
        """
        x = min(self.begin.x(), self.end.x())
        y = min(self.begin.y(), self.end.y())
        width = abs(self.begin.x() - self.end.x())
        height = abs(self.begin.y() - self.end.y())
        return QRect(x, y, width, height)




