#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一界面样式（QSS）

内置两套主题，QSS 由调色板解析生成：
- dark  ：深色底 + 蓝色强调（默认）
- light ：浅色/白色底 + 蓝色强调

主题选择持久化在 config.ini 的 [UI] theme，配置窗口可实时切换预览。
使用方式：
    styles.set_current_theme('light')
    app.setStyleSheet(styles.style_sheet())
    styles.apply_to(dialog)          # 单独给某窗口应用

说明：截图遮罩、结果卡片、遮罩工具条是"浮在屏幕内容之上"的深色件，
风格独立于本主题，不随主题切换（浅色底上白色卡片会看不清）。
"""

import logging

logger = logging.getLogger('screenshot-translation')

# 主题标识与显示名（配置界面下拉框顺序）
THEME_DARK = 'dark'
THEME_LIGHT = 'light'
DEFAULT_THEME = THEME_DARK

_THEME_LABELS = (
    (THEME_DARK, '深色'),
    (THEME_LIGHT, '浅色（白色）'),
)

# 各主题调色板
_PALETTES = {
    THEME_DARK: {
        'window_bg': '#151a23',    # 窗口底色
        'panel_bg': '#1e2430',     # 分组面板/弹出层底色
        'input_bg': '#262d3b',     # 输入框底色
        'input_hover': '#2f3848',  # 输入框/按钮悬停
        'input_pressed': '#28303e',  # 按钮按下
        'text': '#e5eaf2',         # 主文字
        'text_sub': '#8b95a7',     # 次要文字
        'border': '#333d4f',       # 边框
        'track': '#333d4f',        # 滑条轨道
        'accent': '#3b82f6',       # 强调蓝
        'accent_hover': '#60a5fa',
        'accent_pressed': '#2f6fd8',
        'danger': '#f87171',       # 需要用户处理的异常状态文字
        'selection_text': '#ffffff',
    },
    THEME_LIGHT: {
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
    },
}

_current_theme = DEFAULT_THEME

# 兼容常量：深色主题的关键色（结果卡片标题等沿用）
ACCENT = _PALETTES[THEME_DARK]['accent']
ACCENT_HOVER = _PALETTES[THEME_DARK]['accent_hover']
BG_DARK = _PALETTES[THEME_DARK]['window_bg']
BG_PANEL = _PALETTES[THEME_DARK]['panel_bg']
BG_INPUT = _PALETTES[THEME_DARK]['input_bg']
TEXT_MAIN = _PALETTES[THEME_DARK]['text']
TEXT_SUB = _PALETTES[THEME_DARK]['text_sub']
BORDER = _PALETTES[THEME_DARK]['border']


def available_themes():
    """可选主题列表 [(标识, 显示名), ...]，供配置界面下拉框使用"""
    return list(_THEME_LABELS)


def normalize_theme(name):
    """
    把任意输入规范成合法主题标识

    Args:
        name (str): 主题标识（大小写/空白不敏感）

    Returns:
        str: 合法主题标识；无法识别时返回默认主题
    """
    value = str(name or '').strip().lower()
    return value if value in _PALETTES else DEFAULT_THEME


def current_theme():
    """当前生效的主题标识"""
    return _current_theme


def set_current_theme(name):
    """
    设置当前生效主题（进程级，供窗口构造时生成 QSS）

    Args:
        name (str): 主题标识

    Returns:
        str: 实际生效的主题标识
    """
    global _current_theme
    normalized = normalize_theme(name)
    if normalized != _current_theme:
        logger.info(f"界面主题切换: {_current_theme} -> {normalized}")
    _current_theme = normalized
    return _current_theme


def style_sheet(theme=None):
    """
    生成指定主题的 QSS

    Args:
        theme (str 或 None): 主题标识；None 用当前主题

    Returns:
        str: QSS 样式表
    """
    palette = _PALETTES[normalize_theme(theme or _current_theme)]
    return _build_style_sheet(palette)


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


def apply_to(widget, theme=None):
    """
    把样式应用到指定窗口/对话框

    Args:
        widget: Qt 窗口对象
        theme (str 或 None): 主题标识；None 用当前主题

    Returns:
        bool: 是否应用成功
    """
    try:
        widget.setStyleSheet(style_sheet(theme))
        return True
    except Exception as e:
        logger.warning(f"应用样式失败: {e}")
        return False


def apply_app(app, theme=None):
    """
    把样式应用到整个应用（所有窗口与弹出框共用）

    Args:
        app (QApplication): 应用实例
        theme (str 或 None): 主题标识；None 用当前主题

    Returns:
        bool: 是否应用成功
    """
    try:
        app.setStyleSheet(style_sheet(theme))
        return True
    except Exception as e:
        logger.warning(f"应用全局样式失败: {e}")
        return False
