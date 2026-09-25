#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一界面样式（QSS）

深色底 + 蓝色强调的现代化风格，配置窗口与结果对话框共用。
使用方式：窗口构造时调用 style.apply(app) 或 style.apply_to(widget)。
"""

# 主色调
ACCENT = "#3b82f6"        # 强调蓝
ACCENT_HOVER = "#60a5fa"  # 悬停蓝
BG_DARK = "#151a23"       # 窗口底色
BG_PANEL = "#1e2430"      # 分组面板底色
BG_INPUT = "#262d3b"      # 输入框底色
TEXT_MAIN = "#e5eaf2"     # 主文字
TEXT_SUB = "#8b95a7"      # 次要文字
BORDER = "#333d4f"        # 边框

STYLE_SHEET = f"""
QWidget {{
    background-color: {BG_DARK};
    color: {TEXT_MAIN};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
}}

QGroupBox {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 14px;
    padding: 10px 12px 12px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: {ACCENT_HOVER};
}}

QLabel {{ background: transparent; }}
QLabel#testResultLabel {{ color: {TEXT_SUB}; }}

QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QTextEdit:focus {{
    border: 1px solid {ACCENT};
}}

QPushButton {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 7px 18px;
    color: {TEXT_MAIN};
}}
QPushButton:hover {{ background-color: #2f3848; border-color: {ACCENT}; }}
QPushButton:pressed {{ background-color: #28303e; }}
QPushButton:disabled {{ color: {TEXT_SUB}; background-color: {BG_PANEL}; }}

QPushButton#primary {{
    background-color: {ACCENT};
    border: none;
    color: white;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background-color: #2f6fd8; }}

QComboBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 10px;
    min-width: 120px;
}}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {TEXT_SUB};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    selection-background-color: {ACCENT};
    selection-color: white;
    outline: none;
}}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background: {BG_INPUT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: none;
}}

QProgressBar {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    text-align: center;
    color: {TEXT_MAIN};
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 5px;
}}

QDialog {{ background-color: {BG_DARK}; }}
"""


def apply_to(widget):
    """把统一样式应用到指定窗口/对话框"""
    try:
        widget.setStyleSheet(STYLE_SHEET)
    except Exception as e:
        import logging
        logging.getLogger('screenshot-translation').warning(f"应用样式失败: {e}")
