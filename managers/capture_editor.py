#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图编辑态控制器

持有「框选完成 → 标注 → 输出」这一段的状态：当前工具/颜色/线宽、已落笔的
图元列表、撤销重做栈、正在绘制的草图、行内文字输入框。ScreenshotWidget 在
capture_only 模式下把鼠标与键盘事件转发进来，绘制时调用 paint()。

烧录语义：图元一旦 commit 就不提供单选/移动/删除，只能整步撤销。
选区整体平移时所有图元同步平移（含撤销栈里的快照，撤销后不会跳回原位）。
"""

import logging

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import QLineEdit

from . import capture_magnifier
from . import result_render
from .capture_annotation import (
    FONT_CHOICES, PRESET_COLORS, TOOLS, TOOL_ARROW, TOOL_ELLIPSE,
    TOOL_HIGHLIGHT, TOOL_LINE, TOOL_MOSAIC, TOOL_PEN, TOOL_RECT,
    TOOL_SELECT, TOOL_STEP, TOOL_TEXT, Shape, _MAX_SHAPES)

logger = logging.getLogger('screenshot-translation')

_UNDO_LIMIT = 50


class CaptureEditor:
    """编辑态控制器；host 为 ScreenshotWidget"""

    def __init__(self, host):
        self.host = host
        self.shapes = []
        self.undo_stack = []
        self.redo_stack = []
        self.tool = TOOL_SELECT
        self.color = PRESET_COLORS[0]
        self.width = 4
        self.font_size = FONT_CHOICES[1]
        self.eyedropper_mode = False
        self._draft = None
        self._next_step = 1
        self._text_input = None
        # 状态变化回调：宿主用来刷新工具条的撤销/重做可用态
        self.on_state_changed = None

    # ---- 坐标 ----

    def _local(self, pos):
        """屏幕局部逻辑坐标 → 选区局部逻辑坐标"""
        rect = self.host._result_rect
        if rect is None:
            return QPointF(pos.x(), pos.y())
        return QPointF(pos.x() - rect.left(), pos.y() - rect.top())

    # ---- 属性 ----

    def set_tool(self, key):
        if key not in TOOLS:
            return
        self._cancel_draft()
        self.tool = key
        self.eyedropper_mode = False

    def set_color(self, hex_color):
        self.color = hex_color

    def set_width(self, width):
        self.width = int(width)

    def set_font_size(self, size):
        self.font_size = int(size)

    def set_eyedropper(self, active):
        self._cancel_draft()
        self.eyedropper_mode = bool(active)

    # ---- 撤销 / 重做 ----

    def _snapshot(self):
        return list(self.shapes)

    def _push_undo(self):
        self.undo_stack.append(self._snapshot())
        if len(self.undo_stack) > _UNDO_LIMIT:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _commit(self, shape):
        if shape is None or len(self.shapes) >= _MAX_SHAPES:
            return
        self._push_undo()
        self.shapes.append(shape)
        self._notify()
        self.host.update()

    def undo(self):
        if not self.undo_stack:
            return False
        self.redo_stack.append(self._snapshot())
        self.shapes = self.undo_stack.pop()
        self._notify()
        self.host.update()
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        self.undo_stack.append(self._snapshot())
        self.shapes = self.redo_stack.pop()
        self._notify()
        self.host.update()
        return True

    def clear(self):
        if not self.shapes:
            return
        self._push_undo()
        self.shapes = []
        self._next_step = 1
        self._notify()
        self.host.update()

    def reset_for_new_selection(self):
        """选区被重新框选：标注属于旧画面，全部丢弃"""
        self._cancel_draft()
        self._close_text_input(commit=False)
        self.shapes = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._next_step = 1
        self._notify()

    def apply_translation(self, dx, dy):
        """选区整体平移：图元与撤销栈里的快照一起搬，避免撤销后错位"""
        if not self.shapes or (dx == 0 and dy == 0):
            return
        self.shapes = [s.translated(dx, dy) for s in self.shapes]
        self.undo_stack = [
            [s.translated(dx, dy) for s in snap] for snap in self.undo_stack]
        self.redo_stack = [
            [s.translated(dx, dy) for s in snap] for snap in self.redo_stack]

    def _notify(self):
        if self.on_state_changed:
            self.on_state_changed(bool(self.undo_stack), bool(self.redo_stack))

    # ---- 鼠标 ----

    def mouse_press(self, pos, button):
        """返回 True 表示事件已消费，宿主不要再处理"""
        if button == Qt.RightButton:
            if self._draft is not None or self.eyedropper_mode:
                self._cancel_draft()
                self.eyedropper_mode = False
                if self.on_state_changed:
                    self.on_state_changed(bool(self.undo_stack),
                                          bool(self.redo_stack))
                return True
            return False

        if self.eyedropper_mode:
            self._pick_color(pos)
            return True

        if self.tool == TOOL_SELECT:
            return False        # 交给宿主处理选区拖动/平移

        local = self._local(pos)
        if self.tool == TOOL_TEXT:
            self._open_text_input(pos, local)
            return True
        if self.tool == TOOL_STEP:
            self._commit(Shape(kind=TOOL_STEP, points=[local],
                               color=self.color, number=self._next_step))
            self._next_step += 1
            return True

        # 其余工具：开始一次拖拽草图
        self._draft = Shape(kind=self.tool, points=[local, QPointF(local)],
                            color=self.color, width=self.width,
                            font_size=self.font_size)
        self.host.update()
        return True

    def mouse_move(self, pos):
        if self._draft is None:
            return False
        local = self._local(pos)
        if self._draft.kind in (TOOL_PEN, TOOL_HIGHLIGHT):
            last = self._draft.points[-1]
            if abs(local.x() - last.x()) + abs(local.y() - last.y()) >= 1.5:
                self._draft.points.append(local)
        else:
            self._draft.points[-1] = local
        self.host.update()
        return True

    def mouse_release(self, pos, button):
        if button != Qt.LeftButton or self._draft is None:
            return False
        self.mouse_move(pos)
        draft = self._draft
        self._draft = None
        # 太小的拖拽视为误触，不落笔
        if len(draft.points) >= 2:
            a, b = draft.points[0], draft.points[-1]
            tiny = abs(a.x() - b.x()) < 3 and abs(a.y() - b.y()) < 3
            if draft.kind in (TOOL_PEN, TOOL_HIGHLIGHT):
                tiny = len(draft.points) < 2
            if not tiny:
                self._commit(draft)
        self.host.update()
        return True

    def _cancel_draft(self):
        if self._draft is not None:
            self._draft = None
            self.host.update()

    # ---- 取色 ----

    def _pick_color(self, pos):
        rgb = capture_magnifier.pixel_at(
            self.host.full_screen, pos.x(), pos.y(),
            self.host._grab_scale or 1.0)
        hex_color = capture_magnifier.color_hex(rgb)
        if hex_color:
            self.color = hex_color
            logger.info(f"取色: {hex_color}")
        self.eyedropper_mode = False
        self._notify()

    # ---- 文字输入 ----

    def _open_text_input(self, pos, local):
        self._close_text_input(commit=False)
        edit = QLineEdit(self.host)
        edit.setPlaceholderText('输入文字，回车确认')
        font = edit.font()
        font.setPixelSize(max(12, self.font_size))
        edit.setFont(font)
        anchor = self.host._result_rect
        if anchor is None or anchor.isEmpty():
            anchor = self.host.rect()
        width = max(180, min(420, int(anchor.width() * 0.6)))
        edit.setGeometry(int(pos.x()), int(pos.y()), width, self.font_size + 14)
        edit.setStyleSheet(
            'QLineEdit { background: rgba(255,255,255,235); color: #1f2430;'
            ' border: 2px solid #3b82f6; border-radius: 5px; padding: 2px 6px; }')
        edit.returnPressed.connect(lambda: self._close_text_input(commit=True))
        edit.editingFinished.connect(lambda: self._close_text_input(commit=True))
        self._text_input = (edit, QPointF(local))
        edit.show()
        edit.setFocus()

    def _close_text_input(self, commit=True):
        item = self._text_input
        if item is None:
            return
        edit, local = item
        self._text_input = None
        text = edit.text().strip()
        edit.hide()
        edit.deleteLater()
        if commit and text:
            self._commit(Shape(kind=TOOL_TEXT, points=[local], text=text,
                               color=self.color, font_size=self.font_size))
        # 输入框抢过焦点，必须还给遮罩，否则 ESC/Ctrl+Z 全部失灵
        self.host.setFocus()
        self.host.update()

    # ---- 键盘 ----

    def key_press(self, event):
        if self._text_input is not None:
            return False        # 正在输入文字，交给 QLineEdit
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        key = event.key()
        if ctrl and key == Qt.Key_Z:
            if event.modifiers() & Qt.ShiftModifier:
                return self.redo()
            return self.undo()
        if ctrl and key == Qt.Key_Y:
            return self.redo()
        return False

    # ---- 绘制与导出 ----

    def paint(self, painter):
        """编辑态：在冻结的选区底图之上重放全部图元（烧录前预览）"""
        image = (self.host._result or {}).get('image')
        crop_scale = self.host._scale or 1.0
        shapes = list(self.shapes)
        if self._draft is not None:
            shapes.append(self._draft)
        for shape in shapes:
            shape.draw(painter, draw_scale=1.0,
                       crop_scale=crop_scale, source=image)

    def finish_pixmap(self):
        """
        合成最终图：选区原图 + 全部标注，按物理像素 1:1 输出

        Returns:
            QPixmap or None
        """
        payload = self.host._result or {}
        image = payload.get('image')
        if image is None:
            return None
        base = result_render.pil_to_qpixmap(image)
        if base is None:
            return None
        pixmap = QPixmap(image.width, image.height)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        if not painter.isActive():
            return None
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.drawPixmap(0, 0, base)
            scale = self.host._scale or 1.0
            for shape in self.shapes:
                shape.draw(painter, draw_scale=scale,
                           crop_scale=scale, source=image)
        finally:
            painter.end()
        # 与 _render_result_pixmap 一致：绘制期间绝不声明 dpr，画完再说，
        # 否则高分屏上只会剩下左上角 1/倍率² 的内容
        if (self.host._scale or 1.0) > 1.0:
            pixmap.setDevicePixelRatio(self.host._scale)
        return pixmap
