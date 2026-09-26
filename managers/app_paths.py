#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
应用路径定位

统一管理各类文件的存放位置，兼容两种运行形态：
- 源码运行（python main.py）：资源在项目 resources/，配置/缓存/日志在项目根
- PyInstaller 打包：只读资源在 _internal（sys._MEIPASS）；可写文件
  （配置/缓存/日志）统一放 ~/.screenshot-translation，产物目录保持只读，
  这样装到只读位置、整目录覆盖升级都不会丢配置
"""

import logging
import os
import shutil
import sys

logger = logging.getLogger('screenshot-translation')

# 打包态的可写数据目录名（放在用户目录下）
USER_DATA_DIR_NAME = '.screenshot-translation'


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


def data_dir() -> str:
    """
    可写数据目录

    - 打包态 → ~/.screenshot-translation：产物目录可能只读（Program Files），
      而且整目录覆盖升级会替换掉放在 exe 旁边的配置
    - 源码态 → 项目根，沿用开发者本地的 config.ini 与日志，不动工作区

    Returns:
        str: 已确保存在的目录路径
    """
    path = (os.path.join(os.path.expanduser('~'), USER_DATA_DIR_NAME)
            if _is_frozen() else app_root())
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        logger.warning(f"数据目录创建失败，回退到临时目录: {e}")
        path = os.path.join(os.environ.get('TEMP', os.getcwd()), USER_DATA_DIR_NAME)
        os.makedirs(path, exist_ok=True)
    return path


def migrate_legacy_data():
    """
    把旧版放在 exe 旁边的可写文件搬进用户目录（只补缺，不覆盖）

    升级路径上必须留这条：早期版本把配置写在 exe 同目录，用户已经填过密钥，
    直接改读取位置会让设置回到默认值。

    Returns:
        list[str]: 实际搬迁的文件名
    """
    if not _is_frozen():
        return []

    moved = []
    target = data_dir()
    source = app_root()
    for name in ('config.ini', 'translation_cache.json'):
        src = os.path.join(source, name)
        dst = os.path.join(target, name)
        if not os.path.exists(src) or os.path.exists(dst):
            continue
        try:
            shutil.copy2(src, dst)
            moved.append(name)
        except OSError as e:
            logger.warning(f"旧数据迁移失败 {name}: {e}")
    if moved:
        logger.info(f"已从程序目录迁移到 {target}: {', '.join(moved)}")
    return moved


def config_file() -> str:
    """用户配置文件路径（可写，不随打包更新覆盖）"""
    return os.path.join(data_dir(), 'config.ini')


def config_example_file() -> str:
    """配置模板路径（打包资源）"""
    return resource_path('config.example.ini')


def cache_file() -> str:
    """翻译缓存文件路径（可写）"""
    return os.path.join(data_dir(), 'translation_cache.json')


def log_file() -> str:
    """日志文件路径（可写）"""
    return os.path.join(data_dir(), 'screenshot_translation.log')


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
