#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
程序化界面图标

用 QPainter 画矢量图标，不依赖图片资源或 emoji 字体；同一套动作图标
通过右下角"原/译"白字徽标区分原文与译文。先按两倍尺寸绘制再交给 QIcon，
高分屏下缩小显示仍保持清晰。
"""

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (
    QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF)

_SIZE = 24
_SCALE = 2
_DEFAULT_COLOR = '#e8e8e8'
_BADGE_TEXT_COLOR = '#ffffff'
_BADGE_CENTER = (18.0, 18.0)

_CACHE = {}


def icon(name, color=_DEFAULT_COLOR, badge=None, on_color=None):
    """
    取一个绘制好的 QIcon（按参数缓存）

    Args:
        name (str): text(文本)/copy(复制)/save(保存)/sun/moon
        color (str): 常态图标颜色，通常取当前主题文字色
        badge (str 或 None): 右下角徽标（'原'/'译'）
        on_color (str 或 None): 选中态颜色；给出时登记 QIcon 的 On 状态，
            让可选中按钮的图标在按下后仍看得清

    Returns:
        QIcon: 图标实例
    """
    drawer = _DRAWERS.get(name)
    if drawer is None:
        raise ValueError(f"未知图标: {name}")
    key = (name, color, badge, on_color)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    result = QIcon()
    result.addPixmap(_render(drawer, color, badge), QIcon.Normal, QIcon.Off)
    if on_color:
        on_pixmap = _render(drawer, on_color, badge)
        result.addPixmap(on_pixmap, QIcon.Normal, QIcon.On)
        result.addPixmap(on_pixmap, QIcon.Active, QIcon.On)
    _CACHE[key] = result
    return result


def _render(drawer, color, badge):
    """把绘制函数渲染成两倍尺寸的透明位图"""
    pixmap = QPixmap(_SIZE * _SCALE, _SIZE * _SCALE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.scale(_SCALE, _SCALE)
    try:
        drawer(painter, QColor(color))
        if badge:
            _draw_badge(painter, badge)
    finally:
        painter.end()
    return pixmap


def _stroke(painter, color, width=1.6):
    """统一描边样式（圆头圆角，缩小时不出现尖刺）"""
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)


def _draw_text(painter, color):
    """文本文档图标：一页纸 + 三条文本线"""
    _stroke(painter, color)
    painter.drawRoundedRect(QRectF(4.6, 3.4, 11.8, 16.2), 2, 2)
    for y in (8.0, 11.0, 14.0):
        painter.drawLine(QPointF(7.2, y), QPointF(13.8, y))


def _draw_copy(painter, color):
    """复制图标：两张叠放的圆角纸片"""
    _stroke(painter, color)
    painter.drawRoundedRect(QRectF(7.6, 4.4, 10.2, 11.4), 2, 2)
    painter.drawRoundedRect(QRectF(4.6, 7.6, 10.2, 11.4), 2, 2)


def _draw_save(painter, color):
    """保存图标：向下箭头 + 承接托盘"""
    _stroke(painter, color)
    painter.drawLine(QPointF(12, 3.8), QPointF(12, 13.4))
    painter.drawPolyline(QPolygonF([
        QPointF(7.8, 9.2), QPointF(12, 13.6), QPointF(16.2, 9.2)]))
    painter.drawPolyline(QPolygonF([
        QPointF(4.8, 12.6), QPointF(4.8, 18.4),
        QPointF(19.2, 18.4), QPointF(19.2, 12.6)]))


def _draw_badge(painter, text):
    """右下角徽标：透明底白字，区分原文/译文"""
    font = QFont('Microsoft YaHei UI')
    font.setPixelSize(11)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(_BADGE_TEXT_COLOR))
    center = QPointF(*_BADGE_CENTER)
    painter.drawText(
        QRectF(center.x() - 6, center.y() - 6, 12, 12),
        Qt.AlignCenter, text)


# ---- 截图编辑态工具图标 ----

def _draw_select(painter, color):
    """鼠标指针：选择/移动选区"""
    painter.setPen(QPen(color, 1.4))
    painter.setBrush(color)
    painter.drawPolygon(QPolygonF([
        QPointF(5.0, 3.2), QPointF(5.0, 18.4), QPointF(9.2, 14.6),
        QPointF(12.2, 20.6), QPointF(14.6, 19.4), QPointF(11.6, 13.6),
        QPointF(17.4, 13.2)]))


def _draw_rect(painter, color):
    _stroke(painter, color)
    painter.drawRoundedRect(QRectF(4.2, 5.4, 15.6, 13.2), 1.5, 1.5)


def _draw_ellipse(painter, color):
    _stroke(painter, color)
    painter.drawEllipse(QRectF(4.2, 5.4, 15.6, 13.2))


def _draw_line(painter, color):
    _stroke(painter, color)
    painter.drawLine(QPointF(4.5, 18.5), QPointF(19.5, 5.0))


def _draw_arrow(painter, color):
    _stroke(painter, color)
    painter.drawLine(QPointF(4.5, 18.5), QPointF(18.0, 6.0))
    painter.drawPolyline(QPolygonF([
        QPointF(12.4, 6.0), QPointF(18.0, 6.0), QPointF(18.0, 11.6)]))


def _draw_pen(painter, color):
    _stroke(painter, color)
    painter.drawPolyline(QPolygonF([
        QPointF(3.8, 17.8), QPointF(8.0, 9.0), QPointF(12.0, 15.0),
        QPointF(16.4, 6.2), QPointF(20.2, 8.4)]))


def _draw_highlight(painter, color):
    painter.setPen(Qt.NoPen)
    c = QColor(color)
    c.setAlpha(150)
    painter.setBrush(c)
    painter.drawRoundedRect(QRectF(3.6, 10.2, 16.8, 5.4), 2, 2)
    _stroke(painter, color)
    painter.drawLine(QPointF(4.2, 18.4), QPointF(19.8, 18.4))


def _draw_text_annot(painter, color):
    _stroke(painter, color, 2.0)
    painter.drawLine(QPointF(5.0, 5.6), QPointF(19.0, 5.6))
    painter.drawLine(QPointF(12.0, 5.6), QPointF(12.0, 19.0))
    painter.drawLine(QPointF(9.2, 19.0), QPointF(14.8, 19.0))


def _draw_step(painter, color):
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(QPointF(12.0, 12.0), 8.2, 8.2)
    font = QFont('Microsoft YaHei UI')
    font.setPixelSize(11)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor('#232629'))
    painter.drawText(QRectF(4.0, 4.0, 16.0, 16.0), Qt.AlignCenter, '1')


def _draw_mosaic(painter, color):
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    for row in range(3):
        for col in range(3):
            if (row + col) % 2 == 0:
                painter.drawRect(QRectF(4.0 + col * 5.4, 4.0 + row * 5.4,
                                        4.6, 4.6))


def _draw_eyedropper(painter, color):
    _stroke(painter, color)
    painter.drawPolyline(QPolygonF([
        QPointF(14.0, 4.2), QPointF(18.2, 8.4), QPointF(11.0, 15.6),
        QPointF(6.8, 11.4), QPointF(14.0, 4.2)]))
    painter.drawLine(QPointF(9.0, 13.4), QPointF(4.2, 19.4))


def _draw_undo(painter, color):
    _stroke(painter, color)
    painter.drawArc(QRectF(4.0, 5.0, 15.0, 14.0), 30 * 16, 250 * 16)
    painter.drawPolyline(QPolygonF([
        QPointF(4.0, 8.0), QPointF(4.0, 13.6), QPointF(9.4, 13.6)]))


def _draw_redo(painter, color):
    _stroke(painter, color)
    painter.drawArc(QRectF(5.0, 5.0, 15.0, 14.0), -100 * 16, 250 * 16)
    painter.drawPolyline(QPolygonF([
        QPointF(20.0, 8.0), QPointF(20.0, 13.6), QPointF(14.6, 13.6)]))


def _draw_trash(painter, color):
    """垃圾桶：清空全部标注（与「取消」的叉号区分开）"""
    _stroke(painter, color)
    painter.drawLine(QPointF(4.0, 7.0), QPointF(20.0, 7.0))
    painter.drawPolyline(QPolygonF([
        QPointF(9.2, 7.0), QPointF(9.2, 4.2), QPointF(14.8, 4.2),
        QPointF(14.8, 7.0)]))
    painter.drawPolyline(QPolygonF([
        QPointF(6.2, 7.0), QPointF(7.2, 20.0), QPointF(16.8, 20.0),
        QPointF(17.8, 7.0)]))
    _stroke(painter, color, 1.3)
    painter.drawLine(QPointF(10.2, 10.4), QPointF(10.6, 16.8))
    painter.drawLine(QPointF(13.8, 10.4), QPointF(13.4, 16.8))


def _draw_close(painter, color):
    """叉号：取消/退出"""
    _stroke(painter, color, 1.9)
    painter.drawLine(QPointF(6.0, 6.0), QPointF(18.0, 18.0))
    painter.drawLine(QPointF(18.0, 6.0), QPointF(6.0, 18.0))


def _draw_check(painter, color):
    _stroke(painter, color, 2.2)
    painter.drawPolyline(QPolygonF([
        QPointF(4.4, 12.6), QPointF(9.6, 17.4), QPointF(19.6, 6.2)]))


def _draw_ocr(painter, color):
    _stroke(painter, color)
    for y in (6.4, 10.0, 13.6):
        painter.drawLine(QPointF(4.0, y), QPointF(15.0, y))
    painter.drawEllipse(QRectF(13.0, 12.2, 7.0, 7.0))
    painter.drawLine(QPointF(19.0, 18.2), QPointF(21.2, 20.6))


def _draw_scroll(painter, color):
    _stroke(painter, color)
    painter.drawRoundedRect(QRectF(6.0, 3.4, 12.0, 17.2), 2, 2)
    painter.drawPolyline(QPolygonF([
        QPointF(9.4, 9.0), QPointF(12.0, 6.2), QPointF(14.6, 9.0)]))
    painter.drawPolyline(QPolygonF([
        QPointF(9.4, 15.0), QPointF(12.0, 17.8), QPointF(14.6, 15.0)]))


def _draw_pin(painter, color):
    _stroke(painter, color)
    painter.drawLine(QPointF(12.0, 13.0), QPointF(12.0, 20.4))
    painter.drawPolyline(QPolygonF([
        QPointF(6.6, 4.0), QPointF(17.4, 4.0), QPointF(19.0, 9.4),
        QPointF(12.0, 14.2), QPointF(5.0, 9.4), QPointF(6.6, 4.0)]))


def _draw_eraser(painter, color):
    """橡皮擦图标：斜置的胶块 + 底边擦拭线"""
    _stroke(painter, color)
    painter.save()
    painter.translate(12.0, 11.0)
    painter.rotate(-35.0)
    painter.drawRoundedRect(QRectF(-7.2, -4.4, 14.4, 8.8), 1.6, 1.6)
    painter.drawLine(QPointF(-7.2, 0.6), QPointF(7.2, 0.6))
    painter.restore()
    painter.drawLine(QPointF(3.6, 20.4), QPointF(20.4, 20.4))


_DRAWERS = {
    'text': _draw_text,
    'copy': _draw_copy,
    'save': _draw_save,
    'select': _draw_select,
    'rect': _draw_rect,
    'ellipse': _draw_ellipse,
    'line': _draw_line,
    'arrow': _draw_arrow,
    'pen': _draw_pen,
    'highlight': _draw_highlight,
    'text_annot': _draw_text_annot,
    'step': _draw_step,
    'mosaic': _draw_mosaic,
    'eyedropper': _draw_eyedropper,
    'undo': _draw_undo,
    'redo': _draw_redo,
    'trash': _draw_trash,
    'close': _draw_close,
    'check': _draw_check,
    'ocr': _draw_ocr,
    'scroll': _draw_scroll,
    'pin': _draw_pin,
    'eraser': _draw_eraser,
}
