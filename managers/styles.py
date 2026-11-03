#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一界面样式（QSS）

界面固定使用浅色（白色）风格，QSS 由调色板解析生成：
    app.setStyleSheet(styles.style_sheet())
    styles.apply_to(dialog)          # 单独给某窗口应用

说明：截图遮罩、结果卡片、遮罩工具条是"浮在屏幕内容之上"的深色件，
颜色独立于本调色板（浅色底上白色卡片会看不清）。
"""

import logging

logger = logging.getLogger('screenshot-translation')

_LIGHT_PALETTE = {
    'window_bg': '#f2f4f8',
    'panel_bg': '#ffffff',
    'input_bg': '#ffffff',
    'input_hover': '#f6f8fc',
    'input_pressed': '#eef2f8',
    'text': '#1e2430',
    'text_sub': '#667085',
    'border': '#d8dee9',
    'track': '#e2e8f0',
    'accent': '#2563eb',
    'accent_hover': '#1d4ed8',
    'accent_pressed': '#1e40af',
    'danger': '#d92d20',       # 需要用户处理的异常状态文字
    'selection_text': '#ffffff',
}

# 遮罩/结果卡片等深色浮层的强调色（不随浅色界面变化）
ACCENT = '#3b82f6'
ACCENT_HOVER = '#60a5fa'
BG_DARK = '#151a23'
BG_PANEL = '#1e2430'
BG_INPUT = '#262d3b'
TEXT_MAIN = '#e5eaf2'
TEXT_SUB = '#8b95a7'
BORDER = '#333d4f'


def style_sheet():
    """生成界面 QSS"""
    return _build_style_sheet(_LIGHT_PALETTE)


def _build_style_sheet(p):
    """按调色板拼装 QSS"""
    return f"""
QWidget {{
    background-color: {p['window_bg']};
    color: {p['text']};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
}}

QDialog, QMainWindow {{ background-color: {p['window_bg']}; }}

QGroupBox {{
    background-color: {p['panel_bg']};
    border: 1px solid {p['border']};
    border-radius: 10px;
    /* margin-top 就是留给标题的一行：标题落在边框之上，不再压着卡片边线 */
    margin-top: 24px;
    padding: 12px 14px 11px 14px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 2px;
    padding: 0;
    color: {p['accent']};
    background: transparent;
}}

QLabel {{ background: transparent; }}
QLabel#testResultLabel, QLabel#hintLabel {{ color: {p['text_sub']}; }}
QLabel#hintLabel {{ font-size: 12px; }}
QLabel#hotkeyWarning {{
    color: {p['danger']};
    font-size: 12px;
}}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {p['input_bg']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    /* 与 QComboBox 同高（上下各 5px），同行控件底边对齐 */
    padding: 5px 8px;
    color: {p['text']};
    selection-background-color: {p['accent']};
    selection-color: {p['selection_text']};
}}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {{ border-color: {p['accent_hover']}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {p['accent']};
}}
QLineEdit:disabled, QTextEdit:disabled {{
    color: {p['text_sub']};
    background-color: {p['input_pressed']};
}}

QPushButton {{
    background-color: {p['input_bg']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    padding: 6px 16px;
    color: {p['text']};
}}
QPushButton:hover {{ background-color: {p['input_hover']}; border-color: {p['accent']}; }}
QPushButton:pressed {{ background-color: {p['input_pressed']}; }}
QPushButton:disabled {{ color: {p['text_sub']}; background-color: {p['panel_bg']}; }}

QPushButton#primary {{
    background-color: {p['accent']};
    border: none;
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background-color: {p['accent_hover']}; }}
QPushButton#primary:pressed {{ background-color: {p['accent_pressed']}; }}

QComboBox {{
    background-color: {p['input_bg']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    padding: 5px 10px;
    color: {p['text']};
    min-width: 120px;
}}
QComboBox:hover {{ border-color: {p['accent_hover']}; }}
QComboBox:focus {{ border-color: {p['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {p['text_sub']};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {p['panel_bg']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    color: {p['text']};
    selection-background-color: {p['accent']};
    selection-color: {p['selection_text']};
    outline: none;
    padding: 4px;
}}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {p['border']};
    border-radius: 4px;
    background: {p['input_bg']};
}}
QCheckBox::indicator:hover {{ border-color: {p['accent']}; }}
QCheckBox::indicator:checked {{
    background: {p['accent']};
    border-color: {p['accent']};
    image: none;
}}

QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{
    height: 4px;
    border-radius: 2px;
    background: {p['track']};
}}
QSlider::sub-page:horizontal {{
    background: {p['accent']};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px; height: 14px;
    margin: -6px 0;
    border-radius: 7px;
    background: {p['panel_bg']};
    border: 2px solid {p['accent']};
}}
QSlider::handle:horizontal:hover {{ background: {p['accent_hover']}; }}
QSlider::handle:horizontal:pressed {{ background: {p['accent_pressed']}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 2px 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {p['border']};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['text_sub']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ height: 10px; background: transparent; }}
QScrollBar::handle:horizontal {{
    background: {p['border']};
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

QProgressBar {{
    background: {p['input_bg']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    text-align: center;
    color: {p['text']};
}}
QProgressBar::chunk {{ background-color: {p['accent']}; border-radius: 5px; }}

QToolTip {{
    background-color: {p['panel_bg']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 4px;
    padding: 4px 7px;
}}

QMessageBox {{ background-color: {p['window_bg']}; }}
"""


def apply_to(widget):
    """
    把样式应用到指定窗口/对话框

    Args:
        widget: Qt 窗口对象

    Returns:
        bool: 是否应用成功
    """
    try:
        widget.setStyleSheet(style_sheet())
        return True
    except Exception as e:
        logger.warning(f"应用样式失败: {e}")
        return False


def apply_app(app):
    """
    把样式应用到整个应用（所有窗口与弹出框共用）

    Args:
        app (QApplication): 应用实例

    Returns:
        bool: 是否应用成功
    """
    try:
        app.setStyleSheet(style_sheet())
        return True
    except Exception as e:
        logger.warning(f"应用全局样式失败: {e}")
        return False
