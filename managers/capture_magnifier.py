#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
框选辅助：放大镜与屏幕取色

纯绘制函数，不持有窗口。放大镜从「冻结的整屏抓拍」取一小块放大显示，
所以放大出来的始终是那一帧静止画面，不会跟着实时桌面动。取色同样读
PIL 抓拍图（物理像素），坐标按抓取倍率换算，高分屏下不会取错点。
"""

from PyQt5.QtCore import QRect, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPen

from .result_render import pil_to_qpixmap

# 取样边长与放大倍数（逻辑像素）
SAMPLE_EDGE = 16
ZOOM = 7
BOX_SIZE = SAMPLE_EDGE * ZOOM
INFO_HEIGHT = 20
_BG = QColor(30, 36, 48, 238)
_FG = QColor('#e8e8e8')
_CROSS = QColor(255, 59, 48, 200)


def pixel_at(source, x, y, scale=1.0):
    """
    取冻结抓拍图上某点的颜色

    Args:
        source (PIL.Image.Image): 整屏冻结抓拍（物理像素）
        x (float): 遮罩局部逻辑 x
        y (float): 遮罩局部逻辑 y
        scale (float): 逻辑 → 物理像素倍率

    Returns:
        tuple[int, int, int] or None: (r, g, b)
    """
    if source is None:
        return None
    px = int(x * scale)
    py = int(y * scale)
    if px < 0 or py < 0 or px >= source.width or py >= source.height:
        return None
    try:
        rgb = source.convert('RGB').getpixel((px, py))
    except Exception:
        return None
    if isinstance(rgb, int):
        return (rgb, rgb, rgb)
    return tuple(rgb[:3])


def color_hex(rgb):
    """(r,g,b) → '#RRGGBB'；无颜色时返回空串"""
    if not rgb:
        return ''
    return '#%02x%02x%02x' % tuple(max(0, min(255, int(v))) for v in rgb)


def draw_magnifier(painter, pos, source, scale, screen_rect,
                   sample_edge=SAMPLE_EDGE, zoom=ZOOM):
    """
    在光标附近画放大镜（含十字线与坐标/RGB 信息条）

    Args:
        painter (QPainter): 遮罩画布
        pos (QPoint): 光标位置（遮罩局部逻辑坐标）
        source (PIL.Image.Image): 整屏冻结抓拍（物理像素）
        scale (float): 逻辑 → 物理像素倍率
        screen_rect (QRect): 遮罩可用范围，用于夹取放大镜位置
        sample_edge (int): 取样边长（逻辑像素）
        zoom (int): 放大倍数

    Returns:
        QRect or None: 本次绘制占用的矩形，供调用方做局部 update
    """
    if source is None or not painter.isActive():
        return None
    size = sample_edge * zoom
    # 取样框（逻辑坐标）
    half = sample_edge / 2
    lx, ly = pos.x() - half, pos.y() - half
    sx = int(lx * scale)
    sy = int(ly * scale)
    sw = max(1, int(sample_edge * scale))
    sh = max(1, int(sample_edge * scale))
    sx = max(0, min(sx, source.width - sw))
    sy = max(0, min(sy, source.height - sh))

    patch = source.crop((sx, sy, sx + sw, sy + sh))
    tile = pil_to_qpixmap(patch)
    if tile is None:
        return None

    # 位置：默认右上，越界时翻到另一侧
    bx = pos.x() + 18
    by = pos.y() - size - INFO_HEIGHT - 18
    if bx + size > screen_rect.right() - 4:
        bx = pos.x() - size - 18
    if by < screen_rect.top() + 4:
        by = pos.y() + 18
    bx = max(screen_rect.left() + 4,
             min(bx, screen_rect.right() - size - 4))
    by = max(screen_rect.top() + 4,
             min(by, screen_rect.bottom() - size - INFO_HEIGHT - 4))
    box = QRect(int(bx), int(by), size, size)
    info = QRect(box.left(), box.bottom(), size, INFO_HEIGHT)
    total = QRect(box.x(), box.y(), size, size + INFO_HEIGHT).adjusted(-2, -2, 2, 2)

    painter.save()
    try:
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_BG)
        painter.drawRect(QRect(total.x(), total.y(), total.width(), total.height()))
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.drawPixmap(QRectF(box), tile, QRectF(tile.rect()))

        # 中心十字线：标出当前取色点
        cx, cy = box.center().x(), box.center().y()
        painter.setPen(QPen(_CROSS, 1))
        painter.drawLine(cx, box.top(), cx, box.bottom())
        painter.drawLine(box.left(), cy, box.right(), cy)
        painter.setPen(QPen(_FG, 1))
        painter.drawRect(box.adjusted(0, 0, -1, -1))

        rgb = pixel_at(source, pos.x(), pos.y(), scale)
        text = f"{pos.x()}, {pos.y()}"
        if rgb:
            text = f"{text}  {color_hex(rgb)}  {rgb[0]},{rgb[1]},{rgb[2]}"
        painter.setPen(QPen(_FG))
        painter.drawText(info.adjusted(5, 0, -5, 0),
                         Qt.AlignLeft | Qt.AlignVCenter, text)
    finally:
        painter.restore()
    return total
