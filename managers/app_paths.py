#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
应用路径定位

统一管理各类文件的存放位置，兼容两种运行形态：
- 源码运行（python main.py）：资源在项目 resources/，配置/缓存/日志在项目根
- PyInstaller 单文件 exe：只读资源解包在 sys._MEIPASS（临时目录，随用随删）；
  可写文件（配置/缓存/日志）必须放在 exe 同目录或用户目录，不能写进 _MEIPASS
"""

import os
import sys


def _is_frozen() -> bool:
    """是否运行在 PyInstaller 打包环境中"""
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def app_root() -> str:
    """
    应用根目录：
    - 源码运行 → 项目根（main.py 所在目录）
    - exe 运行 → exe 所在目录（用户可见、可写，存放配置与数据）
    """
    if _is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundled_root() -> str:
    """
    只读打包资源根目录：
    - 源码运行 → 项目根（resources/ 就在这里）
    - exe 运行 → sys._MEIPASS（PyInstaller 解包临时目录，含 add-data 资源）
    """
    if _is_frozen():
        return sys._MEIPASS
    return app_root()


def resource_path(*parts) -> str:
    """
    只读打包资源路径（图标等随 exe 分发的文件）

    优先在打包资源根查找，不存在时回退应用根（兼容源码直接运行）。
    """
    for root in (bundled_root(), app_root()):
        candidate = os.path.join(root, *parts)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(bundled_root(), *parts)


def config_file() -> str:
    """用户配置文件路径（可写，不随打包更新覆盖）"""
    return os.path.join(app_root(), 'config.ini')


def config_example_file() -> str:
    """配置模板路径（打包资源）"""
    return resource_path('config.example.ini')


def cache_file() -> str:
    """翻译缓存文件路径（可写）"""
    return os.path.join(app_root(), 'translation_cache.json')


def log_file() -> str:
    """日志文件路径（可写）"""
    return os.path.join(app_root(), 'screenshot_translation.log')


def tesseract_cmd_candidates() -> list:
    """
    tesseract.exe 候选路径（按优先级）：
    1. 打包资源内的便携版（exe 的 _MEIPASS/resources/tesseract/）
    2. 源码运行时的项目 resources/
    3. 系统安装路径
    """
    candidates = []
    bundled = os.path.join(bundled_root(), 'resources', 'tesseract', 'tesseract.exe')
    candidates.append(bundled)
    # 源码形态 bundled_root == app_root，去重即可
    local = os.path.join(app_root(), 'resources', 'tesseract', 'tesseract.exe')
    if local not in candidates:
        candidates.append(local)
    candidates.append(os.path.join(
        os.environ.get('ProgramFiles', ''), 'Tesseract-OCR', 'tesseract.exe'))
    candidates.append(os.path.join(
        os.environ.get('LOCALAPPDATA', ''), 'Tesseract-OCR', 'tesseract.exe'))
    return [c for c in candidates if c]


def tessdata_candidates() -> list:
    """tessdata 训练数据目录候选路径（按优先级）"""
    candidates = [
        os.path.join(bundled_root(), 'resources', 'tessdata'),
        os.path.join(app_root(), 'resources', 'tessdata'),
        os.path.join(os.getcwd(), 'tessdata'),
        os.path.join(os.environ.get('ProgramFiles', ''),
                     'Tesseract-OCR', 'tessdata'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''),
                     'Tesseract-OCR', 'tessdata'),
    ]
    # 去重保序
    seen, result = set(), []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result
