#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长截图结果预览窗

滚动截图结束后不再把用户扔回「框选/编辑」的截图页面，而是直接把拼好的
长图摆到一个可滚动、可缩放的结果窗口里。长图动辄上万像素高，一屏放不下，
所以默认按视口等比缩小，滚轮看全图、Ctrl+滚轮或按钮放大看细节。

窗口是非模态的：复制/保存都在原图（未缩放）上做，不受预览缩放影响。
"""

import logging

from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout)

from . import capture_output, clipboard, styles

logger = logging.getLogger('screenshot-translation')

# 窗口没有 Qt 父级（截图遮罩马上要关掉），必须有模块级强引用防 GC
_windows: set = set()

_MIN_ZOOM = 0.01
_MAX_ZOOM = 4.0
# 放大时给预览位图设上限：4 万像素的长图再放大 4 倍会吃掉几百 MB 显存
_MAX_PREVIEW_HEIGHT = 12000
_ZOOM_STEP = 1.25


class _PreviewArea(QScrollArea):
    """Ctrl + 滚轮缩放；普通滚轮照常滚动长图"""

    zoom_requested = pyqtSignal(bool)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.zoom_requested.emit(event.angleDelta().y() > 0)
            event.accept()
            return
        super().wheelEvent(event)


class LongCaptureResultWindow(QDialog):
    """长截图结果图预览：看全图、复制、保存、关闭"""

    def __init__(self, pixmap, config_manager=None, parent=None):
        """
        Args:
            pixmap (QPixmap): 拼接后的长图（原始像素，不按预览缩放）
            config_manager: 读取 [Capture] 段保存参数，可为 None
            parent (QWidget): 父窗口；通常传 None，避免随遮罩一起关闭
        """
        super().__init__(parent)
        self._source = pixmap
        self._config_manager = config_manager
        self._zoom = 1.0
        self._fit_zoom = 1.0
        self._fitted = False

        dpr = self._source.devicePixelRatio() or 1.0
        self._base_size = QSize(max(1, round(self._source.width() / dpr)),
                                max(1, round(self._source.height() / dpr)))
        self.setWindowTitle(
            f'长截图结果 · {self._base_size.width()}×{self._base_size.height()}')
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setMinimumSize(420, 360)
        styles.apply_to(self)
        self._init_ui()
        self._set_zoom(1.0)
        self._center_on_screen()

    def _init_ui(self):
        """一行信息、一行缩放/输出按钮、一块可滚动预览区"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.info_label = QLabel(
            f'长截图 {self._base_size.width()}×{self._base_size.height()}'
            ' · 已复制到剪贴板')
        self.info_label.setObjectName('hintLabel')
        layout.addWidget(self.info_label)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.fit_button = QPushButton('适合窗口')
        self.fit_button.clicked.connect(self._fit_to_window)
        bar.addWidget(self.fit_button)
        self.zoom_out_button = QPushButton('−')
        self.zoom_out_button.setFixedWidth(30)
        self.zoom_out_button.clicked.connect(
            lambda: self._set_zoom(self._zoom / _ZOOM_STEP))
        bar.addWidget(self.zoom_out_button)
        self.zoom_label = QLabel('100%')
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        bar.addWidget(self.zoom_label)
        self.zoom_in_button = QPushButton('+')
        self.zoom_in_button.setFixedWidth(30)
        self.zoom_in_button.clicked.connect(
            lambda: self._set_zoom(self._zoom * _ZOOM_STEP))
        bar.addWidget(self.zoom_in_button)
        self.full_button = QPushButton('100%')
        self.full_button.clicked.connect(lambda: self._set_zoom(1.0))
        bar.addWidget(self.full_button)
        bar.addStretch()

        self.copy_button = QPushButton('复制到剪贴板')
        self.copy_button.setObjectName('primary')
        self.copy_button.clicked.connect(self._copy)
        bar.addWidget(self.copy_button)
        self.save_button = QPushButton('保存…')
        self.save_button.clicked.connect(self._save)
        bar.addWidget(self.save_button)
        self.close_button = QPushButton('关闭')
        self.close_button.clicked.connect(self.close)
        bar.addWidget(self.close_button)
        layout.addLayout(bar)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.scroll_area = _PreviewArea()
        self.scroll_area.setObjectName('scrollPreview')
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setWidget(self.image_label)
        self.scroll_area.zoom_requested.connect(
            lambda zoom_in: self._set_zoom(
                self._zoom * (_ZOOM_STEP if zoom_in else 1 / _ZOOM_STEP)))
        # 深色长图贴在浅色底上才看得出边界；全局 QSS 的窗口底色太亮
        self.scroll_area.setStyleSheet(
            'QScrollArea#scrollPreview { background: #e9edf4; '
            'border: 1px solid #d8dee9; border-radius: 8px; }')
        layout.addWidget(self.scroll_area, 1)

    # ------------------------------------------------------------------
    # 缩放
    # ------------------------------------------------------------------

    def showEvent(self, event):
        """首次显示后视口尺寸才定下来，这时再算「适合窗口」的比例"""
        super().showEvent(event)
        if not self._fitted:
            self._fitted = True
            QTimer.singleShot(0, self._fit_to_window)

    def _fit_to_window(self):
        """整张长图等比缩到能一次看全（不放大超过 100%）"""
        viewport = self.scroll_area.viewport().size()
        available_w = max(1, viewport.width() - 16)
        available_h = max(1, viewport.height() - 16)
        zoom = min(available_w / max(1, self._base_size.width()),
                   available_h / max(1, self._base_size.height()), 1.0)
        self._fit_zoom = max(_MIN_ZOOM, zoom)
        self._set_zoom(self._fit_zoom)

    def _set_zoom(self, zoom):
        """按倍率重绘预览（只影响显示，复制/保存仍用原图）"""
        limit = min(_MAX_ZOOM,
                    _MAX_PREVIEW_HEIGHT / max(1, self._base_size.height()))
        zoom = max(_MIN_ZOOM, min(max(limit, _MIN_ZOOM), float(zoom)))
        self._zoom = zoom
        target = QSize(max(1, round(self._base_size.width() * zoom)),
                       max(1, round(self._base_size.height() * zoom)))
        scaled = self._source.scaled(
            target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.image_label.setPixmap(scaled)
        self.image_label.setFixedSize(scaled.size())
        self.zoom_label.setText(f'{round(zoom * 100)}%')

    def _center_on_screen(self):
        """窗口大小取屏幕的八成，长图再高也不会顶出屏幕"""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        width = min(1000, int(geometry.width() * 0.8))
        height = min(760, int(geometry.height() * 0.85))
        self.resize(max(self.minimumWidth(), width),
                    max(self.minimumHeight(), height))
        self.move(geometry.center().x() - self.width() // 2,
                  geometry.center().y() - self.height() // 2)

    # ------------------------------------------------------------------
    # 输出动作
    # ------------------------------------------------------------------

    def _option(self, name, fallback):
        """读 [Capture] 配置，管理器不可用时用默认值"""
        try:
            return self._config_manager.get_capture_option(
                name, fallback=fallback) if self._config_manager else fallback
        except Exception:
            return fallback

    def _copy(self):
        """复制未缩放的长图到剪贴板"""
        def done(ok):
            try:
                self._set_hint('已复制到剪贴板' if ok else '复制失败，请重试',
                               error=not ok)
            except RuntimeError:
                # 剪贴板回调排在事件循环里，期间窗口可能已被关掉
                pass

        clipboard.set_pixmap_later(self._source, on_done=done)

    def _save(self):
        """按 [Capture] 的格式/目录/模板把长图存盘"""
        try:
            quality = max(1, min(100, int(str(self._option('jpg_quality', 90)))))
        except (TypeError, ValueError):
            quality = 90
        path = capture_output.save_pixmap(
            self._source,
            fmt=self._option('format', 'png'),
            quality=quality,
            directory=self._option('save_dir', '') or None,
            template=self._option('filename_template', ''),
            quick=False, parent=self, title='保存长截图')
        self._set_hint(f'已保存：{path}' if path else '已取消保存')

    def _set_hint(self, text, error=False):
        """信息行文案（错误用红色）"""
        self.info_label.setText(text)
        self.info_label.setObjectName('hotkeyWarning' if error else 'hintLabel')
        style = self.info_label.style()
        style.unpolish(self.info_label)
        style.polish(self.info_label)

    def keyPressEvent(self, event):
        """ESC 关闭、Ctrl+C 复制、Ctrl+S 保存"""
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.matches(QKeySequence.Copy):
            self._copy()
        elif event.matches(QKeySequence.Save):
            self._save()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        """关闭时释放模块级引用，长图内存不再常驻"""
        _windows.discard(self)
        super().closeEvent(event)


def show_scroll_result(pixmap, config_manager=None):
    """
    打开（或替换）长截图结果窗

    同一时间只保留一个预览窗：连续截两次长图不该叠出一串窗口。

    Args:
        pixmap (QPixmap): 拼接后的长图
        config_manager: [Capture] 配置读取器，可为 None

    Returns:
        LongCaptureResultWindow 或 None
    """
    if pixmap is None or pixmap.isNull():
        return None
    for old in list(_windows):
        try:
            old.close()
        except RuntimeError:
            logger.debug('旧的长截图预览窗已销毁')
        _windows.discard(old)
    window = LongCaptureResultWindow(pixmap, config_manager=config_manager)
    _windows.add(window)
    window.show()
    window.raise_()
    window.activateWindow()
    logger.info(f'长截图结果窗已展示：{window.windowTitle()}')
    return window
