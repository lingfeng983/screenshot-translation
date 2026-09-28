#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图管理器
负责处理屏幕截图相关功能
包括屏幕区域选择、半透明遮罩等
"""

from PIL import ImageGrab
from PyQt5.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QColor, QCursor, QPixmap
from PyQt5.QtWidgets import QApplication, QWidget

# 导入必要的模块
from . import result_render
from .ocr import is_ocr_notice
from .overlay_toolbar import OverlayToolbar
from .pin_window import PinWindow
from .qt_dispatcher import get_qt_app, qt_dispatcher
from .text_block import group_paragraphs
from .thread_pool import thread_pool_manager

import logging

logger = logging.getLogger('screenshot-translation')


class ScreenshotManager:
    """
    截图管理器类
    负责处理截图相关功能
    """

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
        启动截图流程
        """
        logger.info("启动截图流程")

        try:
            # 统一通过调度器保证截图窗口在主线程创建
            qt_dispatcher.dispatch_to_main_thread(self._run_screenshot)
        except Exception as e:
            logger.error(f"启动截图流程失败: {e}")
            import traceback
            traceback.print_exc()

    def _run_screenshot(self):
        """
        运行截图流程
        确保在主线程中运行，避免Qt线程安全问题
        """
        try:
            # 获取QApplication实例（非主线程不会新建）
            app = get_qt_app()
            if app is None:
                logger.error("无法获取QApplication实例，截图流程终止")
                return

            # 已有遮罩时再次按热键视为取消：否则新遮罩会把带暗角的屏幕当作背景截取
            if self._screenshot_widget is not None:
                logger.info("截图遮罩已显示，本次热键作为取消处理")
                self._release_screenshot_widget()
                return

            # 创建截图窗口，保留强引用避免被GC回收；传入配置以读取遮罩参数
            self._screenshot_widget = ScreenshotWidget(
                config_manager=self.config_manager)

            # 连接信号
            widget = self._screenshot_widget
            widget.region_selected.connect(self._on_region_selected)
            widget.retranslate_requested.connect(self._request_retranslate)
            widget.retry_requested.connect(self._on_retry_requested)
            widget.screenshot_canceled.connect(self._release_screenshot_widget)

            # 显示截图窗口
            self._screenshot_widget.showFullScreen()
            # 遮罩必须抢到键盘焦点，ESC/Enter 等按键才会送到 keyPressEvent，
            # 否则遮罩只能靠鼠标操作退出
            self._screenshot_widget.activateWindow()
            self._screenshot_widget.raise_()
            self._screenshot_widget.setFocus()

            logger.info("截图窗口已显示")
            # 不在此处调用 app.exec_()：主事件循环已在运行，嵌套调用会立即返回
        except Exception as e:
            logger.error(f"截图流程失败: {e}")
            import traceback
            traceback.print_exc()

    def _release_screenshot_widget(self):
        """关闭并释放截图窗口引用"""
        widget = self._screenshot_widget
        self._screenshot_widget = None
        self._last_request = None
        if widget is not None:
            widget.close()
            widget.deleteLater()

    _MAX_BLOCK_REQUESTS = 8   # 逐块翻译的请求数上限，超过则退回整段翻译

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
        thread_pool_manager.submit(
            self._process_region, widget, image, region, target_lang, False,
            widget.current_request_id())

    def _on_retry_requested(self):
        """失败后按 R 重试：重走完整链路并跳过翻译缓存"""
        widget = self._screenshot_widget
        if widget is None or self._last_request is None:
            return
        image, region = self._last_request
        logger.info("按用户请求重试识别与翻译")
        widget.set_result_busy(True)
        thread_pool_manager.submit(
            self._process_region, widget, image, region,
            widget.current_target_lang(), True, widget.current_request_id())

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
        thread_pool_manager.submit(
            self._retranslate, widget, payload, target_lang, force_refresh)

    def _retranslate(self, widget, payload, target_lang, force_refresh):
        """在池线程重走翻译链路，完成后回主线程更新结果"""
        updated = dict(payload)
        updated['target_lang'] = self._effective_lang(target_lang)
        try:
            updated['translated_text'] = self._translate_all(
                payload['original_text'], target_lang, force_refresh)
            # 把整段译文传给行级对齐：重译（跳过缓存）时不再重复请求同一段文本
            updated['block_texts'] = self._translate_blocks(
                payload['blocks'], payload['original_text'], target_lang,
                force_refresh, whole=updated['translated_text'])
            updated['error'] = None
        except Exception as e:
            logger.error(f"重新翻译失败: {e}")
            updated['error'] = str(e)
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
                        request_id=None):
        """
        在池线程完成识别与翻译，任何异常都转成失败提示回投主线程

        Args:
            widget: 发起本次请求的遮罩窗口
            image (PIL.Image.Image): 选区图像
            region (QRect): 选区屏幕矩形
            target_lang (str 或 None): 目标语言，None 用全局配置
            force_refresh (bool): 是否跳过翻译缓存
            request_id (int 或 None): 发起请求时的选区序号
        """
        payload = {
            'original_text': '', 'translated_text': '', 'blocks': [],
            'block_texts': [], 'image': image, 'region': region,
            'languages': self._target_languages(),
            'target_lang': self._effective_lang(target_lang),
            'request_id': request_id, 'error': None,
        }
        try:
            original_text, blocks = self._recognize(image)
            payload['original_text'] = original_text
            payload['blocks'] = blocks
            # OCR 降级提示（图里没字/引擎不可用）是给用户看的话，不是原文：
            # 送翻译只会白等一次网络请求，还会把提示文本当译文展示
            if is_ocr_notice(original_text):
                qt_dispatcher.dispatch_to_main_thread(
                    self._apply_result, widget, payload)
                return
            payload['translated_text'] = self._translate_all(
                original_text, target_lang, force_refresh)
            payload['block_texts'] = self._translate_blocks(
                blocks, original_text, target_lang, force_refresh,
                whole=payload['translated_text'])
        except Exception as e:
            from .error_handler import error_handler
            error_handler.handle_error(e, "截图处理")
            payload['error'] = str(e)

        qt_dispatcher.dispatch_to_main_thread(self._apply_result, widget, payload)

    def _recognize(self, image):
        """
        识别选区：优先拿带坐标的行块

        Args:
            image (PIL.Image.Image): 选区图像

        Returns:
            tuple[str, list[TextBlock]]: (整段文本, 行块列表)
        """
        if self.ocr_manager is None:
            return '[未初始化OCR管理器]', []
        text, blocks = self.ocr_manager.perform_ocr_with_blocks(image)
        logger.info(f"OCR完成：{len(text)} 字符，{len(blocks)} 行块")
        return text, blocks

    def _translate_all(self, text, target_lang, force_refresh=False):
        """翻译整段文本，失败时返回空串而不抛出"""
        if self.translation_manager is None or not (text or '').strip():
            return ''
        try:
            return self.translation_manager.perform_translation(
                text, force_refresh=force_refresh, target_lang=target_lang)
        except Exception as e:
            logger.error(f"翻译失败: {e}")
            return ''

    def _translate_blocks(self, blocks, full_text, target_lang, force_refresh=False,
                          whole=None):
        """
        行级译文：整段一次请求后按行对齐，对不齐时逐块请求兜底

        Args:
            blocks (list[TextBlock]): 行块列表
            full_text (str): 整段原文（行块拼接）
            target_lang (str 或 None): 目标语言
            force_refresh (bool): 是否跳过翻译缓存
            whole (str 或 None): 已算好的整段译文；None 时自行请求

        Returns:
            list[str]: 与 blocks 等长的译文；拿不到对齐结果时返回[]
        """
        if not blocks or self.translation_manager is None:
            return []

        if whole is None:
            whole = self._translate_all(full_text, target_lang, force_refresh)
        lines = [line.strip() for line in (whole or '').splitlines() if line.strip()]
        if len(lines) == len(blocks):
            return lines

        if len(blocks) > self._MAX_BLOCK_REQUESTS:
            logger.info(f"译文行数 {len(lines)} 与块数 {len(blocks)} 不符，"
                        f"超过逐块请求上限，降级为整体气泡展示")
            return []

        logger.info(f"译文行数 {len(lines)} 与块数 {len(blocks)} 不符，改为逐块翻译")
        return [self._translate_all(block.text, target_lang, force_refresh)
                for block in blocks]

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

    def __init__(self, config_manager=None):
        """
        初始化截图窗口

        Args:
            config_manager: 配置管理器实例，读取 [Overlay] 段的遮罩参数；
                不传时使用默认值
        """
        super().__init__()

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
        # 全屏截图背景，初始化失败时保持为None，供后续判空
        self.full_screen = None

        # 磁吸状态：启动时枚举一次可见窗口矩形，拖拽期间不重复枚举
        from .window_enum import get_visible_window_rects
        self._snap_window_rects = get_visible_window_rects()
        self._active_snap_lines = None  # 当前激活的参考线

        # 结果态：识别/翻译完成后就地覆盖展示
        self._result = None          # 最近一次成功的 payload
        self._result_rect = None     # 选区逻辑矩形（结果态绘制用）
        self._crop_pixmap = None     # 选区冻结背景，带 devicePixelRatio 供遮罩绘制
        self._scale = 1.0            # 逻辑坐标 → 图像物理像素的倍率
        self._blocks = []            # 逻辑坐标行块
        self._paragraphs = []        # [(屏幕矩形, [块下标]), ...]
        self._hover_paragraph = None  # 悬停命中的段落下标
        self._pinned_paragraph = None  # 单击钉住的段落下标
        self._mode = result_render.MODE_TRANSLATED
        self._loading = False
        self._error = None
        self._toolbar = None
        # 每次确认选区递增，用于丢弃上一次选区的迟到结果
        self._request_id = 0

        # 获取屏幕尺寸
        # 先给出兜底值：即使下面的初始化抛异常，paintEvent 也不会因缺属性而报错
        self.screen_width = 0
        self.screen_height = 0
        # 全屏截图相对逻辑坐标的像素倍率（高分屏 > 1）
        self._grab_scale = 1.0

        try:
            # 使用QApplication.screens()代替过时的desktop()
            screens = QApplication.screens()
            if screens:
                primary_screen = screens[0]
                self.screen_width = primary_screen.geometry().width()
                self.screen_height = primary_screen.geometry().height()
            else:
                # 兼容回退到desktop()
                screen = QApplication.desktop()
                self.screen_width = screen.width()
                self.screen_height = screen.height()

            # 设置窗口大小
            self.setGeometry(0, 0, self.screen_width, self.screen_height)

            # 截取全屏作为背景
            self.full_screen = ImageGrab.grab(bbox=(0, 0, self.screen_width, self.screen_height))
            # 高分屏下 ImageGrab 拿到的是物理像素，记下倍率供裁剪换算
            if self.full_screen and self.screen_width:
                self._grab_scale = self.full_screen.width / self.screen_width

            # 设置光标
            self.setCursor(QCursor(Qt.CrossCursor))

            logger.info(f"截图窗口初始化，大小: {self.screen_width}x{self.screen_height}")
        except Exception as e:
            logger.error(f"初始化截图窗口失败: {e}")
            import traceback
            traceback.print_exc()

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

    # 遮罩主题色（与 managers/styles.py 的深色蓝主题一致）
    _ACCENT = QColor("#3b82f6")               # 强调蓝：选区边框
    _BUBBLE_BG = QColor(30, 36, 48, 235)      # 气泡底：主题面板色
    _BUBBLE_TEXT = QColor("#93c5fd")          # 气泡文字：淡蓝

    # 默认可调参数（实际值从 [Overlay] 配置段读取）
    _DEFAULT_DIM_OPACITY = 90                 # 暗角透明度 (0~180)
    _DEFAULT_SNAP_THRESHOLD = 12              # 磁吸阈值（像素）

    def paintEvent(self, event):
        """
        绘制事件：柔和暗角 + 蓝色发光选区 + 角标记 + 尺寸气泡，
        结果态改为就地覆盖译文 + 工具条 + 气泡卡片
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # 柔和暗角（未选择时也铺一层，提示处于截图模式）
        dim_color = QColor(0, 0, 0, self._dim_opacity)
        painter.fillRect(self.rect(), dim_color)

        if (self._loading or self._error or self._result is not None) \
                and not self.is_selecting:
            # 绘制期异常会让 Qt 直接终止进程（不会打印栈），这里兜住并留痕
            try:
                self._draw_result(painter)
            except Exception as e:
                logger.error(f"结果绘制失败: {e}")
            return

        if not self.is_selecting:
            self._draw_hint(painter)
            return

        rect = self._get_rect()

        # 磁吸：拖拽中实时计算吸附结果（原始选区仅用于计算，显示/裁剪用吸附后矩形）
        snapped, guides = self._snap_rect(rect)
        self._active_snap_lines = guides if guides else None
        rect = snapped

        # 1) 挖空选区：显示真实屏幕（100%亮度，无窗口级压暗）
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(rect, Qt.transparent)

        # 2) 回到正常合成模式绘制装饰
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
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

    def _draw_hint(self, painter):
        """未开始选择时的操作提示条（顶部居中）"""
        text = "拖拽选择要翻译的区域 · 右键或ESC取消"
        font = painter.font()
        font.setPointSize(10)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        tw = metrics.boundingRect(text).width() + 28
        th = metrics.height() + 10
        bx = (self.screen_width - tw) // 2
        by = max(int(self.screen_height * 0.06), 12)
        bubble = QRect(bx, by, tw, th)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._BUBBLE_BG)
        painter.drawRoundedRect(bubble, th // 2, th // 2)
        painter.setPen(QPen(self._BUBBLE_TEXT))
        painter.drawText(bubble, Qt.AlignCenter, text)

    def _draw_corner_marks(self, painter, rect):
        """选区四角的白色L形标记"""
        pen = QPen(QColor(255, 255, 255, 220), 2)
        painter.setPen(pen)
        length = 14
        x1, y1, x2, y2 = rect.left(), rect.top(), rect.right(), rect.bottom()
        # 左上
        painter.drawLine(x1, y1, x1 + length, y1)
        painter.drawLine(x1, y1, x1, y1 + length)
        # 右上
        painter.drawLine(x2 - length, y1, x2, y1)
        painter.drawLine(x2, y1, x2, y1 + length)
        # 左下
        painter.drawLine(x1, y2 - length, x1, y2)
        painter.drawLine(x1, y2, x1 + length, y2)
        # 右下
        painter.drawLine(x2 - length, y2, x2, y2)
        painter.drawLine(x2, y2 - length, x2, y2)

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

    def _snap_rect(self, rect):
        """
        磁吸：把选区四边吸附到附近窗口/屏幕边缘线（阈值内距离最小者优先）

        Args:
            rect (QRect): 原始选区

        Returns:
            tuple[QRect, list[tuple[str, int]]]: 吸附后选区与激活的参考线列表
        """
        # 收集候选边缘线：屏幕边缘 + 可见窗口四边（垂直线与水平线分别归集）
        xs = {0, self.screen_width}          # 垂直线
        ys = {0, self.screen_height}         # 水平线
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
        guides = []

        def _nearest(coord, lines):
            """返回阈值内最近的线位置，无则None"""
            best, best_d = None, threshold + 1
            for line in lines:
                d = abs(coord - line)
                if d < best_d:
                    best, best_d = line, d
            return best

        # 左边
        target = _nearest(rect.left(), xs)
        if target is not None:
            new_rect.moveLeft(target)
            guides.append(('x', target))
        # 右边（moveLeft 后以右边缘独立判断，避免两边同吸互相挤压）
        target = _nearest(rect.right() + 1, xs)
        if target is not None:
            new_rect.moveRight(target - 1)
            guides.append(('x2', target))
        # 上边
        target = _nearest(rect.top(), ys)
        if target is not None:
            new_rect.moveTop(target)
            guides.append(('y', target))
        # 下边
        target = _nearest(rect.bottom() + 1, ys)
        if target is not None:
            new_rect.moveBottom(target - 1)
            guides.append(('y2', target))

        return new_rect, guides

    def _draw_size_bubble(self, painter, rect):
        """
        尺寸提示气泡：优先放在选区上方居中，空间不足自动翻转到下方，
        并夹取在屏幕范围内不出界
        """
        text = f"{rect.width()} × {rect.height()}"
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
        image = payload['image']
        region = QRect(payload['region'])
        scale = image.width / max(1, region.width())

        self._result = payload
        self._error = None
        self._loading = False
        self._result_rect = region
        self._scale = scale
        self._crop_pixmap = result_render.pil_to_qpixmap(image, dpr=scale)
        self._blocks = result_render.to_logical_blocks(payload.get('blocks') or [], scale)
        self._paragraphs = self._build_paragraphs()
        self._hover_paragraph = None
        self._pinned_paragraph = None

        self._ensure_toolbar(payload)
        self.setCursor(QCursor(Qt.ArrowCursor))
        self.update()
        logger.info(f"遮罩结果已展示：{len(self._blocks)} 行块，"
                    f"{len(payload.get('block_texts') or [])} 行译文，模式 {self._mode}")

    def show_failure(self, message):
        """识别/翻译失败：保留选区并给出可重试的错误卡片"""
        self._result = None
        self._loading = False
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
        """请求进行中：禁用工具条并重译期间给出提示"""
        if self._toolbar is not None:
            self._toolbar.set_busy(busy)
        if busy and self._error:
            self._error = None
            self._loading = True
            self.update()

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
        if self._crop_pixmap is not None:
            painter.drawPixmap(rect.topLeft(), self._crop_pixmap)
        else:
            # 结果未回投（加载/失败）时挖空选区，让真实屏幕保持原亮度
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, Qt.transparent)
            painter.restore()

        if self._loading:
            self._draw_border(painter, rect)
            self._draw_pill(painter, rect, '识别并翻译中…')
            return
        if self._error:
            self._draw_border(painter, rect)
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
                rect.topLeft(), self._mode, scale=self._scale)
            painter.restore()
        self._draw_border(painter, rect)

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
        if payload is None:
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

    def _ensure_toolbar(self, payload):
        """结果首次可用时创建工具条，之后复用同一实例"""
        if self._toolbar is not None:
            self._toolbar.set_busy(False)
            self._show_toolbar()
            return
        toolbar = OverlayToolbar(self, payload.get('languages') or [],
                                 current_lang=payload.get('target_lang'),
                                 mode=self._mode)
        toolbar.language_changed.connect(
            lambda lang: self.retranslate_requested.emit(lang, True))
        toolbar.mode_changed.connect(self._set_mode)
        toolbar.copy_clicked.connect(self._copy_result)
        toolbar.pin_clicked.connect(self._pin_result)
        toolbar.save_clicked.connect(self._save_result)
        toolbar.close_clicked.connect(self._cancel_screenshot)
        self._toolbar = toolbar
        self._show_toolbar()

    def _show_toolbar(self):
        """显示并重新摆放工具条"""
        if self._toolbar is None:
            return
        self._toolbar.adjustSize()
        available = max(120, self.screen_width - 8)
        width = self._toolbar.fit_width(available)
        height = self._toolbar.sizeHint().height()
        rect = self._result_rect or self.rect()

        x = rect.center().x() - width // 2
        x = max(4, min(x, self.screen_width - width - 4))
        y = rect.bottom() + 8
        if y + height > self.screen_height - 4:
            y = rect.top() - height - 8
        self._toolbar.setGeometry(x, max(4, y), width, height)
        self._toolbar.show()
        self._toolbar.raise_()

    def _hide_toolbar(self):
        """选择/失败态下收起工具条（保留实例与语言选择）"""
        if self._toolbar is not None:
            self._toolbar.hide()

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

    def _copy_result(self):
        """复制当前文本到剪贴板"""
        text = self._result_text()
        if not text:
            return
        QApplication.clipboard().setText(text)
        logger.info("已复制翻译结果到剪贴板")

    def _render_result_pixmap(self):
        """
        合成结果图（选区底图 + 就地覆盖的译文），按图像物理像素绘制

        Returns:
            QPixmap 或 None
        """
        payload = self._result
        if payload is None:
            return None
        image = payload['image']
        if image is None:
            return None
        pixmap = QPixmap(image.width, image.height)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # 底图按需从 PIL 转换：常驻一份 1:1 QPixmap 等于把选区再存一遍
        painter.drawPixmap(0, 0, result_render.pil_to_qpixmap(image))
        texts = payload.get('block_texts') or []
        if texts and self._mode != result_render.MODE_ORIGINAL:
            result_render.paint_blocks(
                painter, image, payload.get('blocks') or [], texts,
                QPoint(0, 0), self._mode, scale=1.0)
        painter.end()
        return pixmap

    def _pin_result(self):
        """把当前结果钉在屏幕上（原选区位置，1:1 像素）"""
        pixmap = self._render_result_pixmap()
        if pixmap is None:
            return
        rect = self._result_rect or self.rect()
        PinWindow(pixmap, top_left=rect.topLeft())
        logger.info("结果已钉到屏幕")

    def _save_result(self):
        """保存结果图为 PNG 文件"""
        pixmap = self._render_result_pixmap()
        if pixmap is None:
            return
        from datetime import datetime
        from PyQt5.QtWidgets import QFileDialog
        import os

        default_dir = os.path.expanduser('~')
        name = datetime.now().strftime('翻译结果_%Y%m%d_%H%M%S.png')
        path, _selected = QFileDialog.getSaveFileName(
            self, '保存翻译结果', os.path.join(default_dir, name),
            'PNG 图片 (*.png)')
        if not path:
            return
        if pixmap.save(path, 'PNG'):
            logger.info(f"结果图已保存: {path}")
        else:
            logger.error(f"结果图保存失败: {path}")

    def _handle_result_ctrl_key(self, event):
        """结果态下的 Ctrl 组合键"""
        key = event.key()
        if key == Qt.Key_C:
            self._copy_result()
        elif key == Qt.Key_S:
            self._save_result()

    def _handle_result_key(self, event):
        """结果态下的单键操作：T 切模式、1/2/3 直选、P 钉住、R 重试"""
        key = event.key()
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
        鼠标按下事件
        """
        if event.button() == Qt.LeftButton:
            if self._handle_result_press(event.pos()):
                return
            self.begin = event.pos()
            self.end = event.pos()
            self.is_selecting = True
            self._active_snap_lines = None
            self.update()
        elif event.button() == Qt.RightButton:
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
        鼠标移动事件：拖拽更新选区，结果态更新段落悬停
        """
        if self.is_selecting:
            self.end = event.pos()
            self.update()
        elif self._result is not None:
            index = self._paragraph_at(event.pos())
            if index != self._hover_paragraph:
                self._hover_paragraph = index
                self.update()

    def mouseReleaseEvent(self, event):
        """
        鼠标释放事件
        """
        if event.button() == Qt.LeftButton and self.is_selecting:
            self.end = event.pos()
            self.is_selecting = False
            self.update()
            self._confirm_selection()
            # 释放后清理参考线状态
            self._active_snap_lines = None

    def _confirm_selection(self):
        """确认当前选区：有效则发起识别，太小则取消（鼠标释放与Enter共用）"""
        # 用吸附后的矩形裁剪，与用户在遮罩上看到的选择完全一致
        raw = self._get_rect()
        rect, _ = self._snap_rect(raw)
        self._active_snap_lines = None
        logger.info(f"选择区域: x={rect.x()}, y={rect.y()}, "
                    f"width={rect.width()}, height={rect.height()}")

        if rect.width() <= 10 or rect.height() <= 10:
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

        # 全屏图是物理像素，选区是逻辑坐标，按缩放系数换算裁剪框
        scale = self._grab_scale
        box = (int(rect.left() * scale), int(rect.top() * scale),
               int(rect.right() * scale), int(rect.bottom() * scale))
        # crop 与原图共享像素缓冲，必须 copy 才能放下整屏图（1440p 约 11 MiB）
        screenshot = self.full_screen.crop(box).copy()
        self.full_screen = None
        logger.info(f"截图完成，大小: {screenshot.width}x{screenshot.height}")

        # 遮罩不关闭：先用本次裁剪图冻结选区做加载提示，结果回来后就地覆盖
        self._request_id += 1
        self._result = None
        self._error = None
        self._blocks = []
        self._paragraphs = []
        self._result_rect = QRect(rect)
        self._scale = screenshot.width / max(1, rect.width())
        self._crop_pixmap = result_render.pil_to_qpixmap(screenshot, dpr=self._scale)
        self._loading = True
        self.region_selected.emit(screenshot, QRect(rect))
        self.update()

    def mouseDoubleClickEvent(self, event):
        """
        鼠标双击事件
        双击确认选择
        """
        if event.button() == Qt.LeftButton and self.is_selecting:
            self.is_selecting = False
            self._confirm_selection()

    def keyPressEvent(self, event):
        """
        键盘按下事件
        """
        if event.key() == Qt.Key_Escape:
            # 按下ESC键，取消截图
            self._cancel_screenshot()
        elif self._result is not None and event.modifiers() & Qt.ControlModifier:
            self._handle_result_ctrl_key(event)
        elif self._result is not None and not self.is_selecting:
            self._handle_result_key(event)
        elif self._error and event.key() == Qt.Key_R:
            self.retry_requested.emit()
        elif event.key() == Qt.Key_Enter or event.key() == Qt.Key_Return:
            # 按下Enter键，确认截图（不再误传QKeyEvent给鼠标处理）
            if self.is_selecting:
                self.is_selecting = False
                self._confirm_selection()
        elif event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
            # 按下方向键，微调选择区域
            if self.is_selecting:
                delta = 1
                if event.modifiers() & Qt.ShiftModifier:
                    delta = 10

                if event.key() == Qt.Key_Up:
                    self.begin.setY(self.begin.y() - delta)
                    self.end.setY(self.end.y() - delta)
                elif event.key() == Qt.Key_Down:
                    self.begin.setY(self.begin.y() + delta)
                    self.end.setY(self.end.y() + delta)
                elif event.key() == Qt.Key_Left:
                    self.begin.setX(self.begin.x() - delta)
                    self.end.setX(self.end.x() - delta)
                elif event.key() == Qt.Key_Right:
                    self.begin.setX(self.begin.x() + delta)
                    self.end.setX(self.end.x() + delta)

                self.update()

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




