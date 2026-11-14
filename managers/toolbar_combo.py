#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下拉框：自绘 V 形箭头 + 工具条小下拉的按内容定宽

QSS 里用 border 拼三角的写法在 Qt 里只会画成一个小方块（全局浅色样式原来
就是这么写的），看着像一块灰点。这里统一由 paintEvent 画一条细的 V 形
箭头：ChevronComboBox 用于设置窗口/文本翻译页的浅色下拉，
SmallComboBox 在它之上再叠加「按内容收紧宽度」，用于深色浮层工具条。
"""

from PyQt5.QtCore import QPointF, QSize, Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QComboBox, QSizePolicy

# 浅色界面下拉箭头：常态灰、悬停蓝、禁用更淡
_LIGHT_ARROW = '#667085'
_LIGHT_ARROW_HOVER = '#2563eb'
_LIGHT_ARROW_DISABLED = '#b8c0cc'
# 深色浮层工具条下拉箭头
_DARK_ARROW = '#9aa4b2'
_DARK_ARROW_HOVER = '#cfd8e6'
_DARK_ARROW_DISABLED = '#5b6472'


class ChevronComboBox(QComboBox):
    """用 QPainter 画细 V 形箭头的下拉框（所有普通下拉统一用它）

    QSS 侧的 ::down-arrow 必须把尺寸清零（见 styles.py / 两条工具条 QSS），
    否则样式表画出的方块会和自绘箭头叠在一起。箭头位置靠
    ARROW_CENTER_OFFSET 对齐 ::drop-down 的中心，各类配色只需改类常量。
    """

    ARROW_COLOR = _LIGHT_ARROW
    ARROW_COLOR_HOVER = _LIGHT_ARROW_HOVER
    ARROW_COLOR_DISABLED = _LIGHT_ARROW_DISABLED
    # 全局浅色 QSS 里 ::drop-down 宽 22px，箭头落在它正中
    ARROW_CENTER_OFFSET = 11.0
    ARROW_HALF_WIDTH = 4.2
    ARROW_HALF_HEIGHT = 2.4
    ARROW_PEN_WIDTH = 1.6

    def paintEvent(self, event):
        """先让样式表画完底与文字，再补一个细 V 形箭头"""
        super().paintEvent(event)
        if not self.isEnabled():
            color = self.ARROW_COLOR_DISABLED
        elif self.underMouse():
            color = self.ARROW_COLOR_HOVER
        else:
            color = self.ARROW_COLOR
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            pen = QPen(QColor(color), self.ARROW_PEN_WIDTH)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            center_x = self.width() - self.ARROW_CENTER_OFFSET
            center_y = self.height() / 2.0 - 0.5
            half_w = self.ARROW_HALF_WIDTH
            half_h = self.ARROW_HALF_HEIGHT
            painter.drawLine(
                QPointF(center_x - half_w, center_y - half_h),
                QPointF(center_x, center_y + half_h))
            painter.drawLine(
                QPointF(center_x, center_y + half_h),
                QPointF(center_x + half_w, center_y - half_h))
        finally:
            painter.end()


def content_width(combo):
    """
    下拉里最宽条目的文本宽度

    先 ensurePolished：样式表里的字号（12px）没生效时用的是默认字体度量，
    算出来的值会偏大一大截。

    Args:
        combo (QComboBox): 目标下拉

    Returns:
        int: 文本像素宽度（色块图标不计入）
    """
    combo.ensurePolished()
    metrics = combo.fontMetrics()
    return max((metrics.horizontalAdvance(combo.itemText(i))
                for i in range(combo.count())), default=0)


def chrome_width(combo):
    """
    内容之外的固定占位：边框 + 内边距 + 箭头区

    sizeHint 已含这些样式占位，减去最宽条目的文本宽度就是纯占位宽度，随 DPI
    与样式表自动变化。必须先 ensurePolished：样式表里的字号还没生效时字体度量
    偏大，算出来的宽度会明显偏宽（老代码定宽偏宽就是栽在这里）。

    Args:
        combo (QComboBox): 目标下拉

    Returns:
        int: 占位像素宽度
    """
    combo.ensurePolished()
    # 走基类的 sizeHint：SmallComboBox 覆写过它，那是"最终定宽"而不是原始提示
    return max(QComboBox.sizeHint(combo).width() - content_width(combo), 0)


class SmallComboBox(ChevronComboBox):
    """工具条小下拉：沿用自绘箭头，宽度按内容收紧

    宽度不用 setFixedWidth：全局浅色样式给 QComboBox 设了 min-width，样式表
    polish 时会把最小/最大宽度改写回去（实测 setFixedWidth 被冲掉，两个下拉
    最后各自贴合自己的文本、宽度反而不齐）。这里改成固定 sizeHint，布局按提示
    取宽，样式表改不动。
    """

    # 深色工具条 QSS 里 ::drop-down 宽 12px，箭头落在它正中
    ARROW_COLOR = _DARK_ARROW
    ARROW_COLOR_HOVER = _DARK_ARROW_HOVER
    ARROW_COLOR_DISABLED = _DARK_ARROW_DISABLED
    ARROW_CENTER_OFFSET = 6.5
    ARROW_HALF_WIDTH = 3.0
    ARROW_HALF_HEIGHT = 1.75
    ARROW_PEN_WIDTH = 1.3

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fit_content_width = None
        # 宽度由 fit_content 定，高度跟随工具条一行（别写死本来的提示高度）
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

    def fit_content(self, content=None):
        """
        宽度收到刚好放得下内容

        Args:
            content (int): 内容宽度；None 表示取最宽条目的文本宽度
                          （颜色下拉这种只显示色块的，传色块边长）

        Returns:
            int: 定下来的宽度
        """
        return self.set_fit_width(
            (content_width(self) if content is None else content)
            + chrome_width(self))

    def set_fit_width(self, width):
        """固定成指定宽度（用于「线宽/字号两个下拉同宽」这类对齐需求）"""
        self._fit_content_width = int(width)
        # 最小宽度也顶住：QSS 的 min-width 与首次显示都会来改它
        self.setMinimumWidth(self._fit_content_width)
        self.updateGeometry()
        return self._fit_content_width

    def sizeHint(self):
        hint = super().sizeHint()
        if self._fit_content_width is None:
            return hint
        return QSize(self._fit_content_width, hint.height())

    def minimumSizeHint(self):
        """布局取宽时还会看最小提示；不一起收住的话，样式表的下限会把宽度顶回去"""
        hint = super().minimumSizeHint()
        if self._fit_content_width is None:
            return hint
        return QSize(self._fit_content_width, hint.height())

    def showEvent(self, event):
        """首次显示时 QComboBox 会按内容把最小宽度写回来，会顶开定好的宽度"""
        super().showEvent(event)
        if self._fit_content_width is not None:
            self.setMinimumWidth(self._fit_content_width)
