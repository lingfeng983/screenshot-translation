#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图编辑态控制器

持有「框选完成 → 标注 → 输出」这一段的状态：当前工具/颜色/线宽、已落笔的
图元列表、撤销重做栈、正在绘制的草图、行内文字输入框。ScreenshotWidget 在
capture_only 模式下把鼠标与键盘事件转发进来，绘制时调用 paint()。

标注默认仍然烧录进像素，但最近一次落笔的图元保持可编辑：可以拖着移动、拖
手柄缩放、滚轮调线宽/字号，左键点到别处即取消选中；橡皮擦可以把自己画上去
的图元擦掉。选区整体平移时所有图元同步平移（含撤销栈里的快照，撤销后不会
跳回原位）。
"""

import logging
from dataclasses import replace

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QCursor, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QLineEdit

from . import capture_magnifier
from . import result_render
from .capture_annotation import (
    FONT_CHOICES, PRESET_COLORS, TOOLS, TOOL_ARROW, TOOL_ELLIPSE, TOOL_ERASER,
    TOOL_HIGHLIGHT, TOOL_LINE, TOOL_PEN, TOOL_RECT, TOOL_SELECT, TOOL_STEP,
    TOOL_TEXT, WIDTH_CHOICES, Shape, _MAX_SHAPES)

logger = logging.getLogger('screenshot-translation')

_UNDO_LIMIT = 50

# 选中框：手柄边长、命中半径、缩放后的最小外框
_HANDLE_SIZE = 8.0
_HANDLE_HIT = 9.0
_MIN_BOX = 6.0
# 手柄悬停放大动画：帧间隔与每帧进度（约 100ms 走完）
_HOVER_INTERVAL = 16
_HOVER_STEP = 0.16

# 受线宽影响的图元（文字改字号、序号改整体倍率）
_WIDTH_KINDS = (TOOL_RECT, TOOL_ELLIPSE, TOOL_LINE, TOOL_ARROW,
                TOOL_PEN, TOOL_HIGHLIGHT)
# 手柄名 → 缩放光标
_HANDLE_CURSORS = {
    'nw': Qt.SizeFDiagCursor, 'se': Qt.SizeFDiagCursor,
    'ne': Qt.SizeBDiagCursor, 'sw': Qt.SizeBDiagCursor,
    'n': Qt.SizeVerCursor, 's': Qt.SizeVerCursor,
    'e': Qt.SizeHorCursor, 'w': Qt.SizeHorCursor,
}

# 按一下已有标注就能选中它的工具（画笔/高亮/马赛克/文字/序号/橡皮擦例外，
# 它们的命中区会跟“继续画”打架）
_CLICK_SELECT_TOOLS = (TOOL_SELECT, TOOL_RECT, TOOL_ELLIPSE, TOOL_LINE,
                       TOOL_ARROW)
# 手按已有的标注去选中时的命中宽容度
_PICK_TOL = 3.0


def _cycle_value(current, choices, direction):
    """在预设档位里上/下一档（当前值不在档位表里时先并进去再取邻居）"""
    ordered = list(choices)
    if current not in ordered:
        ordered = sorted(set(ordered) | {current})
    index = ordered.index(current) + (1 if direction > 0 else -1)
    index = max(0, min(len(ordered) - 1, index))
    return ordered[index]


def _resize_box(box, handle, point):
    """按手柄与当前光标算出新外框（保持最小尺寸，不翻转）"""
    left, top, right, bottom = box.left(), box.top(), box.right(), box.bottom()
    if handle and 'w' in handle:
        left = min(point.x(), right - _MIN_BOX)
    if handle and 'e' in handle:
        right = max(point.x(), left + _MIN_BOX)
    if handle and 'n' in handle:
        top = min(point.y(), bottom - _MIN_BOX)
    if handle and 's' in handle:
        bottom = max(point.y(), top + _MIN_BOX)
    return QRectF(QPointF(left, top), QPointF(right, bottom))


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
        # 最近一次落笔的图元：可移动/缩放/滚轮调粗细
        self._selected = None
        # 拖动中的移动或缩放：{'mode','handle','box','origin'}
        self._transform = None
        # 橡皮擦一次按下到松开算一步撤销
        self._erase_active = False
        self._erase_pushed = False
        # 选中框手柄的悬停放大动画
        self._hover_handle = None
        self._hover_progress = 0.0
        self._hover_timer = None
        # 状态变化回调：宿主用来刷新工具条的撤销/重做与线宽/字号
        self.on_state_changed = None
        self.on_style_changed = None

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
        self._end_transform()
        self._clear_selection(update=False)
        self.tool = key
        self.eyedropper_mode = False
        self.host.update()

    def set_color(self, hex_color):
        self.color = hex_color
        shape = self._selected_shape()
        if shape is not None and shape.color != hex_color:
            self._replace_selected(replace(shape, color=hex_color))

    def set_width(self, width):
        width = max(1, int(width))
        self.width = width
        shape = self._selected_shape()
        if shape is not None and shape.kind in _WIDTH_KINDS \
                and shape.width != width:
            self._replace_selected(replace(shape, width=width))
        self._emit_style('width', width)
        self._notify()

    def set_font_size(self, size):
        size = max(8, int(size))
        self.font_size = size
        shape = self._selected_shape()
        if shape is not None and shape.kind == TOOL_TEXT \
                and shape.font_size != size:
            self._replace_selected(replace(shape, font_size=size))
        self._emit_style('font', size)
        self._notify()

    def set_eyedropper(self, active):
        self._cancel_draft()
        self._end_transform()
        self._clear_selection(update=False)
        self.eyedropper_mode = bool(active)

    def _emit_style(self, kind, value):
        """线宽/字号变化：通知宿主同步工具条下拉"""
        callback = self.on_style_changed
        if callback:
            callback(kind, value)

    # ---- 选中态 ----

    def _selected_shape(self):
        """当前选中的图元（下标失效时返回 None）"""
        if self._selected is None:
            return None
        if not 0 <= self._selected < len(self.shapes):
            self._selected = None
            return None
        return self.shapes[self._selected]

    def _clear_selection(self, update=True):
        if self._selected is None and self._hover_handle is None:
            return
        self._selected = None
        self._set_hover_handle(None)
        if update:
            self.host.update()

    def _replace_selected(self, shape):
        """替换选中图元（新对象，撤销快照里的旧对象不受影响）"""
        index = self._selected
        if index is None or not 0 <= index < len(self.shapes):
            return
        self._push_undo()
        self.shapes[index] = shape
        self._notify()
        self.host.update()

    @staticmethod
    def _handle_points(box):
        """选中框的八个手柄：四角 + 四边中点"""
        cx = (box.left() + box.right()) / 2.0
        cy = (box.top() + box.bottom()) / 2.0
        return (
            ('nw', QPointF(box.left(), box.top())),
            ('n', QPointF(cx, box.top())),
            ('ne', QPointF(box.right(), box.top())),
            ('e', QPointF(box.right(), cy)),
            ('se', QPointF(box.right(), box.bottom())),
            ('s', QPointF(cx, box.bottom())),
            ('sw', QPointF(box.left(), box.bottom())),
            ('w', QPointF(box.left(), cy)),
        )

    def _handle_at(self, box, point):
        """光标落在哪个手柄上；都不命中返回 None"""
        for name, target in self._handle_points(box):
            if abs(target.x() - point.x()) <= _HANDLE_HIT \
                    and abs(target.y() - point.y()) <= _HANDLE_HIT:
                return name
        return None

    def _shape_at(self, local, tol=_PICK_TOL):
        """光标下的图元下标（后画的在上，优先命中它）"""
        for index in range(len(self.shapes) - 1, -1, -1):
            if self.shapes[index].hit(local, tol):
                return index
        return None

    def _begin_manipulation(self, local):
        """
        选中图元上的按下：手柄 → 缩放，命中图元 → 移动

        Returns:
            bool: 是否进入拖动（False 表示这次按下用来画新图元）
        """
        shape = self._selected_shape()
        if shape is None:
            return False
        box = shape.bounds()
        if box.width() <= 0 and box.height() <= 0:
            return False
        handle = self._handle_at(box, local)
        if handle:
            mode = 'scale'
        elif shape.hit(local, 2.0):
            mode = 'move'
        else:
            return False
        self._cancel_draft()
        # 快照延迟到真正挪动第一下时才拍：单击不拖动不该留一步空撤销
        self._transform = {'mode': mode, 'handle': handle,
                           'box': QRectF(box), 'origin': QPointF(local),
                           'pushed': False}
        self._notify()
        return True

    def _apply_transform(self, local):
        data = self._transform
        index = self._selected
        if data is None or index is None or not 0 <= index < len(self.shapes):
            return
        box = QRectF(data['box'])
        if data['mode'] == 'move':
            box.translate(local.x() - data['origin'].x(),
                          local.y() - data['origin'].y())
        else:
            box = _resize_box(box, data['handle'], local)
        shape = self.shapes[index]
        if box == shape.bounds():
            return
        if not data.get('pushed'):
            # 整次移动/缩放只算一步撤销
            self._push_undo()
            data['pushed'] = True
        self.shapes[index] = shape.transformed(box)
        self.host.update()

    def _end_transform(self):
        self._transform = None

    # ---- 手柄悬停动画 ----

    def _set_hover_handle(self, handle):
        if handle == self._hover_handle:
            return
        self._hover_handle = handle
        self._start_hover_anim()

    def _start_hover_anim(self):
        """逐帧推进手柄悬停进度，到位即停表（静止时零开销）"""
        if self._hover_timer is None:
            timer = QTimer()
            timer.setInterval(_HOVER_INTERVAL)
            timer.timeout.connect(self._tick_hover_anim)
            self._hover_timer = timer
        if not self._hover_timer.isActive():
            self._hover_timer.start()

    def _stop_hover_anim(self):
        """遮罩关闭时收掉悬停动画的定时器"""
        if self._hover_timer is not None:
            self._hover_timer.stop()
            self._hover_timer = None

    def _tick_hover_anim(self):
        target = 1.0 if self._hover_handle else 0.0
        delta = target - self._hover_progress
        if abs(delta) <= _HOVER_STEP:
            self._hover_progress = target
            if self._hover_timer is not None:
                self._hover_timer.stop()
        else:
            self._hover_progress += _HOVER_STEP if delta > 0 else -_HOVER_STEP
        self.host.update()

    def _update_hover(self, local):
        """选中状态下：更新手柄悬停动画与光标反馈"""
        shape = self._selected_shape()
        if shape is None:
            return
        handle = self._handle_at(shape.bounds(), local)
        self._set_hover_handle(handle)
        setter = getattr(self.host, 'setCursor', None)
        if setter is None:
            return
        if handle:
            setter(QCursor(_HANDLE_CURSORS.get(handle, Qt.SizeAllCursor)))
        elif shape.hit(local, 2.0):
            setter(QCursor(Qt.SizeAllCursor))
        else:
            setter(QCursor(Qt.CrossCursor))

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
        # 新落笔即选中：可以马上移动/缩放/滚轮调粗细
        self._selected = len(self.shapes) - 1
        self._notify()
        self.host.update()

    def undo(self):
        if not self.undo_stack:
            return False
        self._end_transform()
        self._clear_selection(update=False)
        self.redo_stack.append(self._snapshot())
        self.shapes = self.undo_stack.pop()
        self._notify()
        self.host.update()
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        self._end_transform()
        self._clear_selection(update=False)
        self.undo_stack.append(self._snapshot())
        self.shapes = self.redo_stack.pop()
        self._notify()
        self.host.update()
        return True

    def clear(self):
        if not self.shapes:
            return
        self._end_transform()
        self._clear_selection(update=False)
        self._push_undo()
        self.shapes = []
        self._next_step = 1
        self._notify()
        self.host.update()

    def reset_for_new_selection(self):
        """选区被重新框选：标注属于旧画面，全部丢弃"""
        self._cancel_draft()
        self._close_text_input(commit=False)
        self._end_transform()
        self._clear_selection(update=False)
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

    # ---- 滚轮：线宽 / 字号 ----

    def wheel(self, steps):
        """
        滚轮调整线宽或字号

        有选中图元时改该图元（文字改字号、其余改线宽、序号改整体倍率），
        没有选中时改当前工具的默认值。

        Args:
            steps (int): 正数向上滚（变粗/变大），负数向下滚

        Returns:
            bool: 是否消费了这次滚轮
        """
        if not steps:
            return False
        direction = 1 if steps > 0 else -1
        shape = self._selected_shape()
        if shape is not None:
            if shape.kind == TOOL_TEXT:
                self.set_font_size(
                    _cycle_value(shape.font_size, FONT_CHOICES, direction))
            elif shape.kind == TOOL_STEP:
                scale = max(0.4, min(4.0, shape.scale + 0.1 * direction))
                self._replace_selected(replace(shape, scale=scale))
            else:
                self.set_width(
                    _cycle_value(shape.width, WIDTH_CHOICES, direction))
            return True
        if self.tool == TOOL_TEXT:
            self.set_font_size(
                _cycle_value(self.font_size, FONT_CHOICES, direction))
        else:
            self.set_width(_cycle_value(self.width, WIDTH_CHOICES, direction))
        return True

    # ---- 鼠标 ----

    def mouse_press(self, pos, button):
        """返回 True 表示事件已消费，宿主不要再处理"""
        if button == Qt.RightButton:
            if self._transform is not None or self._selected is not None:
                self._end_transform()
                self._clear_selection()
                self._notify()
                return True
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

        local = self._local(pos)

        # 选中态优先：手柄 → 缩放，命中图元 → 移动；点到别处即取消选中
        if self._selected_shape() is not None \
                and self._begin_manipulation(local):
            return True
        if self._selected is not None:
            self._clear_selection()

        # 取消选中之后又能一下点回来：点到已有图元 → 选中它并直接拖动
        if self.tool in _CLICK_SELECT_TOOLS:
            index = self._shape_at(local)
            if index is not None:
                self._selected = index
                self._begin_manipulation(local)
                self._notify()
                return True

        if self.tool == TOOL_SELECT:
            # 点空白取消选中后，把这次按下交回宿主去拖选区
            return False

        if self.tool == TOOL_ERASER:
            self._start_erase(local)
            return True
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
        local = self._local(pos)
        if self._transform is not None:
            self._apply_transform(local)
            return True
        if self._erase_active:
            self._erase_at(local)
            return True
        if self._draft is not None:
            if self._draft.kind in (TOOL_PEN, TOOL_HIGHLIGHT):
                last = self._draft.points[-1]
                if abs(local.x() - last.x()) + abs(local.y() - last.y()) >= 1.5:
                    self._draft.points.append(local)
            else:
                self._draft.points[-1] = local
            self.host.update()
            return True
        if self.tool != TOOL_SELECT and self._selected_shape() is not None:
            # 选中态接管移动事件：只管手柄悬停动画与光标。select 工具要留给
            # 宿主拖选区，这里不能吞事件，否则选区整体平移会卡住
            self._update_hover(local)
            return True
        if self.tool == TOOL_SELECT and self._hover_handle is not None:
            self._set_hover_handle(None)
        return False

    def mouse_release(self, pos, button):
        if button != Qt.LeftButton:
            return False
        if self._transform is not None:
            self._transform = None
            self._notify()
            self.host.update()
            return True
        if self._erase_active:
            self._erase_active = False
            self._erase_pushed = False
            self._notify()
            self.host.update()
            return True
        if self._draft is None:
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

    # ---- 橡皮擦 ----

    def _start_erase(self, local):
        self._selected = None
        self._erase_active = True
        self._erase_pushed = False
        self._erase_at(local)

    def _erase_at(self, local):
        """擦掉擦头覆盖到的图元；整次擦除只算一步撤销"""
        tolerance = 6.0 + self.width * 1.2
        kept = [shape for shape in self.shapes
                if not shape.hit(local, tolerance)]
        if len(kept) == len(self.shapes):
            return
        if not self._erase_pushed:
            self._push_undo()
            self._erase_pushed = True
        self.shapes = kept
        self._notify()
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
            # QLineEdit 不消费 ESC，事件会冒泡到遮罩的 keyPressEvent；不在这里
            # 拦住的话，按 ESC 取消的是整张截图（连同已画好的标注），而不是
            # 仅仅放弃这次文字输入。
            if event.key() == Qt.Key_Escape:
                self._close_text_input(commit=False)
                return True
            return False        # 其余按键（含 Ctrl+Z 的文本撤销）交给 QLineEdit
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        key = event.key()
        if ctrl and key == Qt.Key_Z:
            if event.modifiers() & Qt.ShiftModifier:
                return self.redo()
            return self.undo()
        if ctrl and key == Qt.Key_Y:
            return self.redo()
        if key == Qt.Key_Escape and self._selected is not None:
            # ESC 先取消选中，再按一次才是退出截图
            self._clear_selection()
            return True
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
        if self._draft is None:
            self._paint_selection(painter)

    def _paint_selection(self, painter):
        """选中图元的虚线外框 + 八向手柄（鼠标靠近的手柄放大发亮）"""
        shape = self._selected_shape()
        if shape is None:
            return
        box = shape.bounds()
        if box.width() <= 0 and box.height() <= 0:
            return
        painter.save()
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(QColor(96, 165, 250, 220), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(box.adjusted(-2, -2, 2, 2))
            for name, point in self._handle_points(box):
                grow = self._hover_progress if name == self._hover_handle else 0.0
                side = _HANDLE_SIZE + 3.0 * grow
                half = side / 2.0
                painter.setPen(
                    QPen(QColor(255, 255, 255, int(210 + 45 * grow)), 1))
                fill = QColor(59, 130, 246)
                fill.setAlpha(int(235 + 20 * grow))
                painter.setBrush(fill)
                painter.drawRect(QRectF(point.x() - half, point.y() - half,
                                        side, side))
            painter.setBrush(Qt.NoBrush)
        finally:
            painter.restore()

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
