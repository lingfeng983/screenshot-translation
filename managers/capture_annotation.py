#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图标注图元（烧录语义）

标注一旦落笔就合成进像素，不提供选中/移动/删除单个对象的能力；内部保留
轻量图元列表只为一件事：支撑 Ctrl+Z 撤销「整步」操作。

坐标约定：points 全部是「选区局部逻辑坐标」（选区左上角为原点），与
ScreenshotWidget 的 _result_rect / _blocks 同一套基准。绘制时给出
draw_scale（编辑态 1.0，导出为「逻辑 → 物理像素」倍率）；马赛克另需
crop_scale 才能从原始 PIL 抓拍图里取到正确的像素块。
"""

from dataclasses import dataclass, field

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF

# 工具键与展示名（顺序即工具条顺序）
TOOL_SELECT = 'select'
TOOL_RECT = 'rect'
TOOL_ELLIPSE = 'ellipse'
TOOL_LINE = 'line'
TOOL_ARROW = 'arrow'
TOOL_PEN = 'pen'
TOOL_HIGHLIGHT = 'highlight'
TOOL_TEXT = 'text'
TOOL_STEP = 'step'
TOOL_MOSAIC = 'mosaic'

TOOLS = (TOOL_SELECT, TOOL_RECT, TOOL_ELLIPSE, TOOL_LINE, TOOL_ARROW,
         TOOL_PEN, TOOL_HIGHLIGHT, TOOL_TEXT, TOOL_STEP, TOOL_MOSAIC)
TOOL_LABELS = {
    TOOL_SELECT: '选择/移动选区',
    TOOL_RECT: '矩形',
    TOOL_ELLIPSE: '椭圆',
    TOOL_LINE: '直线',
    TOOL_ARROW: '箭头',
    TOOL_PEN: '画笔',
    TOOL_HIGHLIGHT: '荧光笔',
    TOOL_TEXT: '文字',
    TOOL_STEP: '序号',
    TOOL_MOSAIC: '马赛克',
}

PRESET_COLORS = ('#ff3b30', '#ff9500', '#ffcc00', '#34c759',
                 '#00c7be', '#3b82f6', '#af52de', '#ffffff')
WIDTH_CHOICES = (2, 4, 7)
FONT_CHOICES = (14, 18, 24, 32)

_MOSAIC_BLOCK = 12          # 马赛克块边长（逻辑像素）
_HIGHLIGHT_ALPHA = 95       # 荧光笔透明度
_MAX_SHAPES = 400           # 防误操作把内存吃光


@dataclass
class Shape:
    """一个已落笔的标注。约定：commit 之后不再修改字段。"""
    kind: str
    points: list = field(default_factory=list)   # list[QPointF]，选区局部逻辑坐标
    color: str = PRESET_COLORS[0]
    width: int = WIDTH_CHOICES[1]
    font_size: int = FONT_CHOICES[1]
    text: str = ''
    number: int = 0
    # 马赛克结果缓存：首次绘制时按当前底图生成，之后直接复用
    _mosaic_cache: object = field(default=None, repr=False, compare=False)

    def translated(self, dx, dy):
        """整体平移（编辑态移动选区时同步搬动标注）"""
        return Shape(
            kind=self.kind,
            points=[QPointF(p.x() + dx, p.y() + dy) for p in self.points],
            color=self.color, width=self.width, font_size=self.font_size,
            text=self.text, number=self.number)

    @staticmethod
    def _scaled(points, scale):
        return [QPointF(p.x() * scale, p.y() * scale) for p in points]

    def draw(self, painter, draw_scale=1.0, crop_scale=None, source=None):
        """
        把一个图元画到目标画布

        Args:
            painter (QPainter): 目标画布（已设好 Antialiasing）
            draw_scale (float): 局部逻辑坐标 → 目标画布坐标的倍率
            crop_scale (float or None): 局部逻辑坐标 → PIL 源图物理像素的倍率
            source (PIL.Image.Image or None): 马赛克取像素用的原始抓拍图
        """
        if not self.points:
            return
        pts = self._scaled(self.points, draw_scale)
        color = QColor(self.color)
        width = max(1, self.width * draw_scale)

        painter.save()
        try:
            if self.kind == TOOL_MOSAIC:
                self._draw_mosaic(painter, pts, draw_scale, crop_scale, source)
            elif self.kind == TOOL_TEXT:
                self._draw_text(painter, pts, color, draw_scale)
            elif self.kind == TOOL_STEP:
                self._draw_step(painter, pts, color, draw_scale)
            elif self.kind in (TOOL_PEN, TOOL_HIGHLIGHT):
                self._draw_stroke(painter, pts, color, width)
            elif self.kind in (TOOL_RECT, TOOL_ELLIPSE):
                self._draw_outline(painter, pts, color, width)
            elif self.kind in (TOOL_LINE, TOOL_ARROW):
                self._draw_line(painter, pts, color, width)
        finally:
            painter.restore()

    # ---- 各图元的绘制 ----

    def _draw_outline(self, painter, pts, color, width):
        rect = QRectF(pts[0], pts[-1]).normalized()
        if rect.isEmpty():
            return
        painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        if self.kind == TOOL_RECT:
            painter.drawRect(rect)
        else:
            painter.drawEllipse(rect)

    def _draw_line(self, painter, pts, color, width):
        start, end = pts[0], pts[-1]
        if start == end:
            return
        painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(start, end)
        if self.kind == TOOL_ARROW:
            self._draw_arrow_head(painter, start, end, color, width)

    @staticmethod
    def _draw_arrow_head(painter, start, end, color, width):
        """在终点画一个实心箭头（长度随线宽增长，但夹在合理范围）"""
        from math import atan2, cos, sin
        angle = atan2(end.y() - start.y(), end.x() - start.x())
        size = max(9.0, min(26.0, width * 3.2))
        wing = 0.42
        left = QPointF(end.x() - size * cos(angle - wing),
                       end.y() - size * sin(angle - wing))
        right = QPointF(end.x() - size * cos(angle + wing),
                        end.y() - size * sin(angle + wing))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([end, left, right]))

    def _draw_stroke(self, painter, pts, color, width):
        if self.kind == TOOL_HIGHLIGHT:
            c = QColor(color)
            c.setAlpha(_HIGHLIGHT_ALPHA)
            width = max(6.0, width * 3.2)
            color = c
        if len(pts) < 2:
            return
        painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolyline(QPolygonF(pts))

    def _draw_text(self, painter, pts, color, draw_scale):
        if not self.text:
            return
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(max(8, int(self.font_size * draw_scale)))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        lines = self.text.split('\n') or ['']
        line_h = metrics.height()
        widths = [metrics.horizontalAdvance(line) for line in lines]
        pad = 6 * draw_scale
        box_w = max(widths) + pad * 2
        box_h = line_h * len(lines) + pad * 2
        origin = QPointF(pts[0].x(), pts[0].y())
        box = QRectF(origin, origin + QPointF(box_w, box_h))

        # 半透明底衬：浅底深字压在截图上才读得清
        bg = QColor(255, 255, 255, 210)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(box, 4 * draw_scale, 4 * draw_scale)

        painter.setPen(QPen(color))
        for index, line in enumerate(lines):
            painter.drawText(
                QPointF(origin.x() + pad,
                        origin.y() + pad + metrics.ascent() + index * line_h),
                line)

    def _draw_step(self, painter, pts, color, draw_scale):
        radius = max(10.0, 13.0 * draw_scale)
        center = pts[0]
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(center, radius, radius)
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(max(9, int(radius * 1.15)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor('#ffffff')))
        painter.drawText(
            QRectF(center.x() - radius, center.y() - radius,
                   radius * 2, radius * 2),
            Qt.AlignCenter, str(self.number))

    def _draw_mosaic(self, painter, pts, draw_scale, crop_scale, source):
        """把矩形区域缩到 1/N 再放大回来，形成像素块"""
        rect = QRectF(pts[0], pts[-1]).normalized()   # 目标画布坐标
        if rect.isEmpty():
            return
        if source is None or crop_scale is None:
            # 没有底图信息（纯单元测试）：退化成灰色块，至少看出打了码
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(120, 120, 120, 220))
            painter.drawRect(rect)
            return
        # 取源图的框必须用「未缩放」的局部逻辑坐标：编辑态 draw_scale=1
        # （目标是逻辑坐标），导出时 draw_scale=物理倍率，两者目标画布不同，
        # 但源图始终是同一张物理像素抓拍，只认局部逻辑坐标 * crop_scale。
        local = QRectF(self.points[0], self.points[-1]).normalized()
        if self._mosaic_cache is None:
            self._mosaic_cache = _build_mosaic_tile(local, crop_scale, source)
        tile = self._mosaic_cache
        if tile is not None:
            painter.drawPixmap(rect, tile, QRectF(tile.rect()))


def _build_mosaic_tile(local_rect, crop_scale, source):
    """
    从 PIL 源图裁出标注区域并做成马赛克贴图

    Args:
        local_rect (QRectF): 选区局部逻辑坐标下的标注矩形
        crop_scale (float): 局部逻辑 → 源图物理像素的倍率
        source (PIL.Image.Image): 选区原始抓拍图（物理像素）

    Returns:
        QPixmap or None
    """
    from .result_render import pil_to_qpixmap

    left = int(max(0, local_rect.left() * crop_scale))
    top = int(max(0, local_rect.top() * crop_scale))
    right = int(min(source.width, local_rect.right() * crop_scale))
    bottom = int(min(source.height, local_rect.bottom() * crop_scale))
    if right <= left or bottom <= top:
        return None
    block = max(2, int(_MOSAIC_BLOCK * crop_scale))
    small_w = max(1, (right - left) // block)
    small_h = max(1, (bottom - top) // block)
    patch = source.crop((left, top, right, bottom))
    pixelated = patch.resize((small_w, small_h)).resize(
        (right - left, bottom - top))
    return pil_to_qpixmap(pixelated)
