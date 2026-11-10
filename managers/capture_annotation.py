#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图标注图元（烧录语义）

标注一旦落笔就合成进像素，不提供逐个选中/编辑历史图元的能力；内部保留
轻量图元列表支撑 Ctrl+Z 撤销「整步」操作，以及「最近一次落笔」的移动、缩放
与橡皮擦擦除（旧图元仍只能整步撤销）。

坐标约定：points 全部是「选区局部逻辑坐标」（选区左上角为原点），与
ScreenshotWidget 的 _result_rect / _blocks 同一套基准。绘制时给出
draw_scale（编辑态 1.0，导出为「逻辑 → 物理像素」倍率）；马赛克另需
crop_scale 才能从原始 PIL 抓拍图里取到正确的像素块。
"""

from dataclasses import dataclass, field, replace

from PyQt5.QtCore import QPointF, QRectF, QSizeF, Qt
from PyQt5.QtGui import QColor, QFont, QFontMetricsF, QPen, QPolygonF

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
TOOL_ERASER = 'eraser'

TOOLS = (TOOL_SELECT, TOOL_RECT, TOOL_ELLIPSE, TOOL_LINE, TOOL_ARROW,
         TOOL_PEN, TOOL_HIGHLIGHT, TOOL_TEXT, TOOL_STEP, TOOL_MOSAIC,
         TOOL_ERASER)
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
    TOOL_ERASER: '橡皮擦',
}

PRESET_COLORS = ('#ff3b30', '#ff9500', '#ffcc00', '#34c759',
                 '#00c7be', '#3b82f6', '#af52de', '#ffffff')
WIDTH_CHOICES = (2, 4, 7)
FONT_CHOICES = (14, 18, 24, 32)

_MOSAIC_BLOCK = 12          # 马赛克块边长（逻辑像素）
_HIGHLIGHT_ALPHA = 95       # 荧光笔透明度
_MAX_SHAPES = 400           # 防误操作把内存吃光


def _step_radius(scale):
    """序号圆点半径（局部逻辑像素）"""
    return max(6.0, 13.0 * max(0.2, scale))


def _point_segment_distance(point, start, end):
    """点到线段的距离（线性图元的命中判断用）"""
    dx = end.x() - start.x()
    dy = end.y() - start.y()
    if dx == 0 and dy == 0:
        return ((point.x() - start.x()) ** 2
                + (point.y() - start.y()) ** 2) ** 0.5
    t = ((point.x() - start.x()) * dx
         + (point.y() - start.y()) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    px = start.x() + t * dx
    py = start.y() + t * dy
    return ((point.x() - px) ** 2 + (point.y() - py) ** 2) ** 0.5


def _shape_text_box(points, text, font_size):
    """文字图元的排版外框（与 _draw_text 的留白保持一致）"""
    font = QFont('Microsoft YaHei UI')
    font.setPixelSize(max(8, int(font_size)))
    metrics = QFontMetricsF(font)
    lines = text.split('\n') or ['']
    line_h = metrics.height()
    box_w = max([metrics.horizontalAdvance(line) for line in lines] + [0.0]) + 12
    box_h = line_h * len(lines) + 12
    return QRectF(points[0], QSizeF(box_w, box_h))


@dataclass
class Shape:
    """一个已落笔的标注。约定：commit 之后整体替换，不就地改字段。"""
    kind: str
    points: list = field(default_factory=list)   # list[QPointF]，选区局部逻辑坐标
    color: str = PRESET_COLORS[0]
    width: int = WIDTH_CHOICES[1]
    font_size: int = FONT_CHOICES[1]
    text: str = ''
    number: int = 0
    scale: float = 1.0          # 序号圆点等固定尺寸图元的整体倍率
    # 马赛克结果缓存：首次绘制时按当前底图生成，之后直接复用
    _mosaic_cache: object = field(default=None, repr=False, compare=False)

    def translated(self, dx, dy):
        """整体平移（编辑态移动选区时同步搬动标注）"""
        return Shape(
            kind=self.kind,
            points=[QPointF(p.x() + dx, p.y() + dy) for p in self.points],
            color=self.color, width=self.width, font_size=self.font_size,
            text=self.text, number=self.number, scale=self.scale)

    def bounds(self):
        """
        图元外框（选区局部逻辑坐标）

        用于选中虚线框、手柄位置、橡皮擦与拖动的命中测试。

        Returns:
            QRectF: 图元外框
        """
        if not self.points:
            return QRectF()
        if self.kind == TOOL_TEXT:
            return _shape_text_box(self.points, self.text, self.font_size)
        if self.kind == TOOL_STEP:
            radius = _step_radius(self.scale)
            center = self.points[0]
            return QRectF(center.x() - radius, center.y() - radius,
                          radius * 2, radius * 2)
        # 用 min/max 自己算包围盒：QRectF.united 遇到零宽/零高的矩形会当成
        # 空矩形丢掉，直线、水平画笔这类图元的框会算错
        xs = [point.x() for point in self.points]
        ys = [point.y() for point in self.points]
        rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        pad = max(1.0, self.width * 0.5)
        if self.kind == TOOL_HIGHLIGHT:
            pad = max(3.0, self.width * 1.6)
        return rect.adjusted(-pad, -pad, pad, pad)

    def hit(self, point, tol=0.0):
        """
        命中测试（橡皮擦与拖动共用）

        Args:
            point (QPointF): 选区局部逻辑坐标
            tol (float): 命中容差（橡皮擦半径 / 拖动的宽容度）

        Returns:
            bool: 是否命中
        """
        if not self.points:
            return False
        box = self.bounds()
        if not box.adjusted(-tol, -tol, tol, tol).contains(point):
            return False
        if self.kind in (TOOL_TEXT, TOOL_STEP, TOOL_MOSAIC):
            return True
        radius = tol + max(1.0, self.width * 0.5)
        if self.kind == TOOL_RECT:
            # 矩形只认边框：否则框内的空白处也拖不动新图元
            inner = box.adjusted(radius, radius, -radius, -radius)
            return inner.isEmpty() or not inner.contains(point)
        if self.kind == TOOL_ELLIPSE:
            cx, cy = box.center().x(), box.center().y()
            rx = max(1e-6, box.width() / 2.0)
            ry = max(1e-6, box.height() / 2.0)
            distance = (((point.x() - cx) / rx) ** 2
                        + ((point.y() - cy) / ry) ** 2) ** 0.5
            band = max(radius / min(rx, ry), 0.08)
            return abs(distance - 1.0) <= band
        if len(self.points) == 1:
            return True
        for start, end in zip(self.points, self.points[1:]):
            if _point_segment_distance(point, start, end) <= radius:
                return True
        return False

    def transformed(self, target):
        """
        映射到新的外框（选中后拖动移动 / 拖手柄缩放）

        始终返回新图元、不改自身：撤销栈里存的是旧对象，快照因此不会跟着变。

        Args:
            target (QRectF): 目标外框（选区局部逻辑坐标）

        Returns:
            Shape: 变换后的新图元
        """
        box = self.bounds()
        if (box.width() <= 0 and box.height() <= 0) \
                or (target.width() <= 0 and target.height() <= 0):
            return self
        scale_x = target.width() / box.width() if box.width() else 1.0
        scale_y = target.height() / box.height() if box.height() else 1.0

        def _map(point):
            return QPointF(target.left() + (point.x() - box.left()) * scale_x,
                           target.top() + (point.y() - box.top()) * scale_y)

        if self.kind == TOOL_STEP:
            factor = max(0.2, min(6.0, self.scale * min(scale_x, scale_y)))
            return replace(self, points=[target.center()], scale=factor,
                           _mosaic_cache=None)
        if self.kind == TOOL_TEXT:
            size = max(8, int(round(self.font_size * scale_y)))
            return replace(self, points=[_map(self.points[0])], font_size=size,
                           _mosaic_cache=None)
        width = max(1, int(round(self.width * min(scale_x, scale_y))))
        return replace(self, points=[_map(point) for point in self.points],
                       width=width, _mosaic_cache=None)

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
        radius = max(6.0, _step_radius(self.scale) * draw_scale)
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
    from PIL import Image
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
    # 马赛克要的是硬边像素块：PIL 默认 BICUBIC 会把缩小再放大的结果插值成
    # 一片模糊，两次都必须显式用 NEAREST。
    # Pillow < 9.1 没有 Resampling 枚举（旧别名仍可用），兼容两种写法
    nearest = getattr(getattr(Image, 'Resampling', Image), 'NEAREST')
    pixelated = patch.resize((small_w, small_h), nearest).resize(
        (right - left, bottom - top), nearest)
    return pil_to_qpixmap(pixelated)
