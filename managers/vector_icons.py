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


_DRAWERS = {
    'text': _draw_text,
    'copy': _draw_copy,
    'save': _draw_save,
}
