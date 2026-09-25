#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图管理器
负责处理屏幕截图相关功能
包括屏幕区域选择、半透明遮罩等
"""

from PIL import ImageGrab
from PyQt5.QtCore import Qt, QRect, QPoint, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QColor, QCursor
from PyQt5.QtWidgets import QApplication, QWidget

# 导入必要的模块
from .progress_manager import progress_manager
from .qt_dispatcher import get_qt_app, qt_dispatcher
from .thread_pool import thread_pool_manager


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
        import logging
        logger = logging.getLogger('screenshot-translation')
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
        import logging
        logger = logging.getLogger('screenshot-translation')

        try:
            # 获取QApplication实例（非主线程不会新建）
            app = get_qt_app()
            if app is None:
                logger.error("无法获取QApplication实例，截图流程终止")
                return

            # 创建截图窗口前先释放上一次可能残留的窗口，保证同时只有一个遮罩
            self._release_screenshot_widget()

            # 创建截图窗口，保留强引用避免被GC回收；传入配置以读取遮罩参数
            self._screenshot_widget = ScreenshotWidget(
                config_manager=self.config_manager)

            # 连接信号
            self._screenshot_widget.screenshot_taken.connect(self._handle_screenshot)
            self._screenshot_widget.screenshot_canceled.connect(self._release_screenshot_widget)

            # 显示截图窗口
            self._screenshot_widget.showFullScreen()

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
        if widget is not None:
            widget.close()
            widget.deleteLater()

    def _handle_screenshot(self, image):
        """
        处理截图结果

        Args:
            image: PIL Image对象
        """
        print("截图完成，开始OCR识别")

        # 使用线程池处理OCR和翻译，避免阻塞UI
        thread_pool_manager.submit(self._process_screenshot, image)

        # 截图窗口已在鼠标释放时关闭，这里只释放引用，不能退出主事件循环
        self._release_screenshot_widget()

    def _show_translation_result(self, original_text, translated_text):
        """
        显示翻译结果

        Args:
            original_text (str): 原始文本
            translated_text (str): 翻译文本
        """
        import logging
        logger = logging.getLogger('screenshot-translation')

        try:
            # 统一通过调度器保证结果对话框在主线程显示
            qt_dispatcher.dispatch_to_main_thread(
                self._show_translation_dialog, original_text, translated_text
            )
        except Exception as e:
            logger.error(f"显示翻译结果失败: {e}")
            import traceback
            traceback.print_exc()

    def _show_translation_dialog(self, original_text, translated_text):
        """
        显示翻译结果对话框

        Args:
            original_text (str): 原始文本
            translated_text (str): 翻译文本
        """
        import logging
        logger = logging.getLogger('screenshot-translation')

        from PyQt5.QtWidgets import (
            QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox, QLabel,
            QPushButton, QHBoxLayout, QApplication, QComboBox
        )
        from PyQt5.QtCore import QTimer

        from .providers.base import TARGET_LANGUAGES
        from . import styles

        dialog = QDialog()
        dialog.setWindowTitle("翻译结果")
        styles.apply_to(dialog)
        dialog.resize(600, 400)

        layout = QVBoxLayout()

        # 原始文本
        original_label = QLabel("原始文本:")
        original_text_edit = QTextEdit()
        original_text_edit.setPlainText(original_text)
        original_text_edit.setReadOnly(True)

        # 翻译结果
        translated_label = QLabel("翻译结果:")
        translated_text_edit = QTextEdit()
        translated_text_edit.setPlainText(translated_text)
        translated_text_edit.setReadOnly(True)

        # 复制按钮行：复制译文 / 复制原文，写入系统剪贴板
        copy_layout = QHBoxLayout()

        copy_translated_button = QPushButton("复制译文")
        copy_original_button = QPushButton("复制原文")

        def _copy_to_clipboard(text, button, restore_text):
            """把文本写入系统剪贴板，并临时切换按钮文字给出反馈"""
            try:
                QApplication.clipboard().setText(text)
                button.setText("已复制 ✓")
                button.setEnabled(False)
                # 2秒后恢复按钮状态
                QTimer.singleShot(
                    2000,
                    lambda: (
                        button.setText(restore_text),
                        button.setEnabled(True),
                    ),
                )
            except Exception as e:
                logger.error(f"复制到剪贴板失败: {e}")

        copy_translated_button.clicked.connect(
            lambda: _copy_to_clipboard(
                translated_text_edit.toPlainText(),
                copy_translated_button, "复制译文"))
        copy_original_button.clicked.connect(
            lambda: _copy_to_clipboard(
                original_text_edit.toPlainText(),
                copy_original_button, "复制原文"))

        copy_layout.addWidget(copy_translated_button)
        copy_layout.addWidget(copy_original_button)
        copy_layout.addStretch()

        # 目标语言行：切换后立即用新语言重新翻译（不影响全局配置）
        lang_layout = QHBoxLayout()
        lang_label = QLabel("目标语言:")
        lang_combo = QComboBox()
        for lang_code, lang_name in TARGET_LANGUAGES:
            lang_combo.addItem(f"{lang_name} ({lang_code})", lang_code)
        if self.translation_manager:
            current_index = lang_combo.findData(self.translation_manager.target_lang)
            if current_index >= 0:
                lang_combo.setCurrentIndex(current_index)

        # 构造期间 setCurrentIndex 也会触发 currentIndexChanged，
        # 此时 _on_retranslate 尚未定义，用就绪标志拦住，只响应用户交互
        _dialog_ready = [False]

        def _on_lang_changed(index):
            """用户切换目标语言后立即用新语言重新翻译"""
            if not _dialog_ready[0]:
                return
            selected_lang = lang_combo.itemData(index)
            logger.info(f"用户切换目标语言: {selected_lang}，自动重新翻译")
            _on_retranslate()

        lang_combo.currentIndexChanged.connect(_on_lang_changed)
        lang_layout.addWidget(lang_label)
        lang_layout.addWidget(lang_combo)
        lang_layout.addStretch()

        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok)
        button_box.accepted.connect(dialog.accept)

        # 重新翻译：跳过缓存，走当前配置的主API→降级链，后台执行不卡UI
        retranslate_button = QPushButton("重新翻译")
        retranslate_button.setToolTip("跳过缓存，使用当前配置的翻译API重新翻译原文")

        def _on_retranslate():
            """在后台线程重新翻译原文，完成后回主线程更新结果框"""
            retranslate_button.setText("翻译中...")
            retranslate_button.setEnabled(False)
            # 重译期间锁定语言下拉框，避免连续切换导致结果错乱
            lang_combo.setEnabled(False)
            logger.info("用户请求重新翻译（跳过缓存）")

            def worker():
                try:
                    if self.translation_manager:
                        # 下拉框选中项仅作用于本次翻译，不改全局配置
                        selected_lang = lang_combo.currentData() or None
                        new_text = self.translation_manager.perform_translation(
                            original_text, force_refresh=True,
                            target_lang=selected_lang)
                    else:
                        new_text = "[未初始化翻译管理器]"
                except Exception as e:
                    logger.error(f"重新翻译失败: {e}")
                    new_text = f"[翻译失败: {e}]"
                # 回主线程更新UI
                qt_dispatcher.dispatch_to_main_thread(_apply_result, new_text)

            def _apply_result(new_text):
                """在主线程把新译文写回结果框并恢复按钮"""
                try:
                    translated_text_edit.setPlainText(new_text)
                    retranslate_button.setText("重新翻译")
                    retranslate_button.setEnabled(True)
                    lang_combo.setEnabled(True)
                except RuntimeError:
                    # 对话框已关闭时忽略迟到的结果
                    logger.debug("结果对话框已关闭，忽略迟到的翻译结果")

            thread_pool_manager.submit(worker)

        retranslate_button.clicked.connect(_on_retranslate)
        # 构造完成，之后切换语言下拉框才触发自动重译
        _dialog_ready[0] = True
        # 以 ActionRole 加入：点击不关闭对话框，显示在 Ok 左侧
        button_box.addButton(retranslate_button, QDialogButtonBox.ActionRole)
        button_box.accepted.connect(dialog.accept)

        # 添加到布局
        layout.addWidget(original_label)
        layout.addWidget(original_text_edit)
        layout.addWidget(translated_label)
        layout.addWidget(translated_text_edit)
        layout.addLayout(lang_layout)
        layout.addLayout(copy_layout)
        layout.addWidget(button_box)

        dialog.setLayout(layout)
        dialog.exec_()

    def _process_screenshot(self, image):
        """
        在后台线程中处理截图（带进度显示）

        Args:
            image: PIL Image对象
        """
        try:
            # 显示进度对话框
            progress_manager.show_progress("处理中", "正在处理截图...")

            # 执行OCR识别
            progress_manager.update_progress(20, "正在进行OCR识别...")
            if self.ocr_manager:
                recognized_text = self.ocr_manager.perform_ocr(image)
                print(f"OCR识别结果: {recognized_text}")
            else:
                # 如果没有OCR管理器，使用默认文本
                recognized_text = "[未初始化OCR管理器]"
                print("OCR管理器未初始化，使用默认文本")

            # 执行翻译
            progress_manager.update_progress(60, "正在进行文本翻译...")
            if self.translation_manager and recognized_text:
                translated_text = self.translation_manager.perform_translation(recognized_text)
                print(f"翻译结果: {translated_text}")
            else:
                # 如果没有翻译管理器，使用默认文本
                translated_text = f"[未初始化翻译管理器] {recognized_text}"
                print("翻译管理器未初始化，使用默认文本")

            # 完成处理
            progress_manager.update_progress(100, "处理完成")
            import time
            time.sleep(0.5)  # 让用户看到100%完成状态

            # 显示翻译结果
            self._show_translation_result(recognized_text, translated_text)

        except Exception as e:
            from .error_handler import error_handler
            error_handler.handle_error(e, "截图处理")
        finally:
            # 确保进度对话框被关闭
            progress_manager.hide_progress()

class ScreenshotWidget(QWidget):
    """
    截图窗口类
    负责显示半透明遮罩和处理鼠标事件
    参考snipaste的截图效果
    """

    # 信号：截图完成
    screenshot_taken = pyqtSignal(object)
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
        self.setAttribute(Qt.WA_ShowWithoutActivating)  # 显示时不激活窗口

        # 从配置读取可调参数（缺失/非法时回退默认值）
        self._dim_opacity = self._read_int_config(
            config_manager, 'dim_opacity', self._DEFAULT_DIM_OPACITY,
            lo=0, hi=180)
        self._snap_threshold = self._read_int_config(
            config_manager, 'snap_threshold', self._DEFAULT_SNAP_THRESHOLD,
            lo=0, hi=48)

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

        # 获取屏幕尺寸
        import logging
        logger = logging.getLogger('screenshot-translation')

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

            # 设置光标
            self.setCursor(QCursor(Qt.CrossCursor))

            logger.info(f"截图窗口初始化，大小: {self.screen_width}x{self.screen_height}")
        except Exception as e:
            logger.error(f"初始化截图窗口失败: {e}")
            import traceback
            traceback.print_exc()

    # 遮罩主题色（与 managers/styles.py 的深色蓝主题一致）
    _ACCENT = QColor("#3b82f6")               # 强调蓝：选区边框
    _BUBBLE_BG = QColor(30, 36, 48, 235)      # 气泡底：主题面板色
    _BUBBLE_TEXT = QColor("#93c5fd")          # 气泡文字：淡蓝

    # 默认可调参数（实际值从 [Overlay] 配置段读取）
    _DEFAULT_DIM_OPACITY = 90                 # 暗角透明度 (0~180)
    _DEFAULT_SNAP_THRESHOLD = 12              # 磁吸阈值（像素）

    def paintEvent(self, event):
        """
        绘制事件：柔和暗角 + 蓝色发光选区 + 角标记 + 尺寸气泡
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # 柔和暗角（未选择时也铺一层，提示处于截图模式）
        dim_color = QColor(0, 0, 0, self._dim_opacity)
        painter.fillRect(self.rect(), dim_color)

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

    def mousePressEvent(self, event):
        """
        鼠标按下事件
        """
        if event.button() == Qt.LeftButton:
            self.begin = event.pos()
            self.end = event.pos()
            self.is_selecting = True
            self._active_snap_lines = None
            self.update()
            print(f"鼠标按下，位置: ({self.begin.x()}, {self.begin.y()})")
        elif event.button() == Qt.RightButton:
            # 右键取消截图
            self._cancel_screenshot()

    def mouseMoveEvent(self, event):
        """
        鼠标移动事件
        """
        if self.is_selecting:
            self.end = event.pos()
            self.update()
            # 打印鼠标移动位置，方便调试
            if event.pos().x() % 50 == 0 and event.pos().y() % 50 == 0:  # 每50像素打印一次，避免日志过多
                print(f"鼠标移动，位置: ({self.end.x()}, {self.end.y()})")

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
        """确认当前选区：有效则发截图信号，太小则取消（鼠标释放与Enter共用）"""
        # 用吸附后的矩形裁剪，与用户在遮罩上看到的选择完全一致
        raw = self._get_rect()
        rect, _ = self._snap_rect(raw)
        self._active_snap_lines = None
        print(f"选择区域: x={rect.x()}, y={rect.y()}, width={rect.width()}, height={rect.height()}")

        # 确保选择区域有效
        if rect.width() > 10 and rect.height() > 10:
            if self.full_screen is None:
                # 背景截图缺失时无法裁剪，直接取消，避免在Qt槽内抛出未捕获异常
                print("全屏背景截图不可用，取消截图")
                self._cancel_screenshot()
                return

            # 截取选择区域
            screenshot = self.full_screen.crop((
                rect.left(),
                rect.top(),
                rect.right(),
                rect.bottom()
            ))

            print(f"截图完成，大小: {screenshot.width}x{screenshot.height}")

            # 发送信号
            self.screenshot_taken.emit(screenshot)

            # 关闭窗口
            self.close()
            print("截图窗口已关闭")
        else:
            # 选择区域太小，取消截图
            print("选择区域太小，取消截图")
            # 发送取消信号
            self.screenshot_canceled.emit()
            # 关闭窗口
            self.close()
            print("截图窗口已关闭")

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
        print("取消截图")
        # 发送取消信号
        self.screenshot_canceled.emit()
        # 关闭窗口
        self.close()
        print("截图窗口已关闭")

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




