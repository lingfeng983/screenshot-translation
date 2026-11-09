#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
结果钉屏浮窗

把"含译文覆盖"的结果图钉在屏幕上：置顶、无边框、可拖动、
滚轮缩放、双击或 ESC 关闭。有道截图翻译的"钉住"即此行为。
"""

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QKeySequence, QPainter
from PyQt5.QtWidgets import QMenu, QWidget

from . import clipboard

import logging

logger = logging.getLogger('screenshot-translation')

# 钉住的窗口没有 Qt 父级，需要模块级引用防止被 Python GC 回收
_pins = set()

MIN_SCALE = 0.4
MAX_SCALE = 4.0


class PinWindow(QWidget):
    """钉屏结果图窗口"""

    def __init__(self, pixmap, top_left=None, title='钉住的翻译结果',
                 config_manager=None):
        """
        Args:
            pixmap (QPixmap): 要显示的结果图（原始像素尺寸）
            top_left (QPoint 或 None): 初始屏幕位置，None 时居中显示
            title (str): 窗口标题（纯截图模式下为「钉住的截图」）
            config_manager: 读取 [Capture] 段的保存参数，可为 None
        """
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        # QWidget 默认 NoFocus：没有焦点策略的话 ESC / Ctrl+C / Ctrl+S 的
        # keyPressEvent 根本收不到真实按键，必须先声明可聚焦。
        self.setFocusPolicy(Qt.StrongFocus)
        self.setWindowTitle(title)

        self._source = pixmap
        self._config_manager = config_manager
        self._scale = 1.0
        self._drag_offset = None

        _pins.add(self)

        # QPixmap.width() 给的是物理像素，窗口的 resize/move 用的是逻辑坐标：
        # 不除 devicePixelRatio 的话高分屏上钉出来的图会比原选区大 (倍率-1) 倍
        dpr = self._source.devicePixelRatio() or 1.0
        self._base_size = QSize(max(1, round(self._source.width() / dpr)),
                                max(1, round(self._source.height() / dpr)))
        self.resize(self._base_size)
        if top_left is not None:
            self.move(top_left)
        else:
            self._center_on_screen()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def closeEvent(self, event):
        """关闭时释放模块级引用，避免钉住的图一直占内存"""
        _pins.discard(self)
        super().closeEvent(event)

    def _center_on_screen(self):
        """没有指定位置时放在主屏中央"""
        from PyQt5.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        self.move(geometry.center().x() - self.width() // 2,
                  geometry.center().y() - self.height() // 2)

    def paintEvent(self, event):
        """绘制结果图与一圈强调色描边"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), self._source)
        from PyQt5.QtGui import QPen, QColor
        painter.setPen(QPen(QColor('#3b82f6'), 2))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))

    def mousePressEvent(self, event):
        """按下即开始拖动"""
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        """拖动窗口"""
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        """结束拖动"""
        self._drag_offset = None

    def mouseDoubleClickEvent(self, event):
        """双击关闭"""
        self.close()

    def wheelEvent(self, event):
        """滚轮缩放，围绕视口中心放缩并同步窗口大小"""
        factor = 1.15 if event.delta() > 0 else 1 / 1.15
        target = max(MIN_SCALE, min(MAX_SCALE, self._scale * factor))
        if abs(target - self._scale) < 1e-6:
            return
        center = self.mapToGlobal(self.rect().center())
        self._scale = target
        self.resize(int(self._base_size.width() * target),
                    int(self._base_size.height() * target))
        self.move(center - self.rect().center())

    def keyPressEvent(self, event):
        """ESC 关闭，Ctrl+C 复制，Ctrl+S 保存"""
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.matches(QKeySequence.Copy):
            # 注意用 QKeySequence.Copy：QKeyEvent 上没有 Copy 属性，
            # 写 event.Copy 会在任意按键时抛 AttributeError
            self._copy()
        elif event.matches(QKeySequence.Save):
            self._save()
        else:
            super().keyPressEvent(event)

    def _copy(self):
        """复制原图到剪贴板（非阻塞，带生效校验）"""
        def copied(ok):
            if ok:
                logger.info("已复制钉住的图片到剪贴板")
        clipboard.set_pixmap_later(self._source, on_done=copied)

    def _save(self):
        """按 [Capture] 配置把钉住的图存盘"""
        from . import capture_output
        cfg = self._config_manager

        def option(name, fallback):
            try:
                return cfg.get_capture_option(name, fallback=fallback) if cfg \
                    else fallback
            except Exception:
                return fallback

        def int_option(name, default, lo, hi):
            try:
                return max(lo, min(hi, int(str(option(name, default)).strip())))
            except Exception:
                return default

        capture_output.save_pixmap(
            self._source,
            fmt=option('format', 'png'),
            quality=int_option('jpg_quality', 90, 1, 100),
            directory=option('save_dir', '') or None,
            template=option('filename_template', ''),
            quick=False, parent=self, title='保存钉住的截图')

    def contextMenuEvent(self, event):
        """右键菜单：复制 / 保存 / 置顶 / 透明度 / 关闭"""
        menu = QMenu(self)
        menu.addAction('复制图片', self._copy)
        menu.addAction('保存到文件…', self._save)
        menu.addSeparator()

        top_action = menu.addAction('置顶')
        top_action.setCheckable(True)
        top_action.setChecked(
            bool(self.windowFlags() & Qt.WindowStaysOnTopHint))
        top_action.triggered.connect(self._toggle_top)

        opacity_menu = menu.addMenu('透明度')
        for value in (100, 85, 70, 50):
            action = opacity_menu.addAction(f'{value}%')
            action.triggered.connect(
                lambda _checked=False, v=value: self.setWindowOpacity(v / 100))

        menu.addSeparator()
        menu.addAction('关闭', self.close)
        menu.exec_(event.globalPos())

    def _toggle_top(self, checked):
        """切换置顶：改 windowFlags 会隐藏窗口，必须重新 show"""
        was_visible = self.isVisible()
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if was_visible:
            self.show()
