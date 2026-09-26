#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
结果钉屏浮窗

把"含译文覆盖"的结果图钉在屏幕上：置顶、无边框、可拖动、
滚轮缩放、双击或 ESC 关闭。有道截图翻译的"钉住"即此行为。
"""

from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QKeySequence, QPainter, QPixmap
from PyQt5.QtWidgets import QWidget

# 钉住的窗口没有 Qt 父级，需要模块级引用防止被 Python GC 回收
_pins = set()

MIN_SCALE = 0.4
MAX_SCALE = 4.0


class PinWindow(QWidget):
    """钉屏结果图窗口"""

    def __init__(self, pixmap, top_left=None):
        """
        Args:
            pixmap (QPixmap): 要显示的结果图（原始像素尺寸）
            top_left (QPoint 或 None): 初始屏幕位置，None 时居中显示
        """
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle('钉住的翻译结果')

        self._source = pixmap
        self._scale = 1.0
        self._drag_offset = None

        _pins.add(self)

        self.resize(self._source.width(), self._source.height())
        if top_left is not None:
            self.move(top_left)
        else:
            self._center_on_screen()
        self.show()
        self.raise_()
        self.activateWindow()

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
        self.resize(int(self._source.width() * target),
                    int(self._source.height() * target))
        self.move(center - self.rect().center())

    def keyPressEvent(self, event):
        """ESC 关闭，Ctrl+C 复制图片到剪贴板"""
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.matches(QKeySequence.Copy):
            # 注意用 QKeySequence.Copy：QKeyEvent 上没有 Copy 属性，
            # 写 event.Copy 会在任意按键时抛 AttributeError
            from PyQt5.QtWidgets import QApplication
            QApplication.clipboard().setPixmap(self._source)
        else:
            super().keyPressEvent(event)
