#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图输出：格式、质量、目录、命名模板、快速保存

把「存成什么文件」从 ScreenshotWidget 里摘出来，便于单独测试。JPG 会先
铺白底再合成（PNG/WEBP 保留透明）；WEBP 插件缺失时自动退回 PNG。
"""

from datetime import datetime
import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QFileDialog

import logging

logger = logging.getLogger('screenshot-translation')

FORMATS = ('png', 'jpg', 'webp')
_QUALITY_DEFAULT = 90
_TEMPLATE_DEFAULT = '截图_%Y%m%d_%H%M%S'
_FILTERS = {
    'png': 'PNG 图片 (*.png)',
    'jpg': 'JPEG 图片 (*.jpg)',
    'webp': 'WebP 图片 (*.webp)',
}
_FORMAT_NAMES = {'png': 'PNG', 'jpg': 'JPG', 'webp': 'WEBP'}


def default_directory():
    """系统「图片」目录；取不到时退回用户主目录"""
    try:
        from PyQt5.QtCore import QStandardPaths
        path = QStandardPaths.writableLocation(
            QStandardPaths.PicturesLocation)
        if path and os.path.isdir(path):
            return path
    except Exception:
        pass
    return os.path.expanduser('~')


def build_filename(template=None, ext='png'):
    """按 strftime 模板生成文件名（模板非法时退回默认）"""
    template = template or _TEMPLATE_DEFAULT
    try:
        name = datetime.now().strftime(template)
    except Exception:
        name = datetime.now().strftime(_TEMPLATE_DEFAULT)
    # 模板可能自带扩展名，避免重复
    if name.lower().endswith(('.' + ext, '.jpeg')):
        name = os.path.splitext(name)[0]
    return f'{name}.{ext}'


def _flatten_for_jpg(pixmap):
    """JPG 不支持透明：铺白底再合成"""
    out = QPixmap(pixmap.width(), pixmap.height())
    out.fill(Qt.white)
    from PyQt5.QtGui import QPainter
    painter = QPainter(out)
    if not painter.isActive():
        return pixmap
    try:
        painter.drawPixmap(0, 0, pixmap)
    finally:
        painter.end()
    return out


def save_pixmap(pixmap, *, fmt='png', quality=_QUALITY_DEFAULT,
                directory=None, template=None, quick=False,
                parent=None, title='保存截图'):
    """
    把图片存盘

    Args:
        pixmap (QPixmap): 要保存的图片
        fmt (str): png / jpg / webp
        quality (int): JPG/WEBP 质量 (1~100)
        directory (str): 目标目录，空值时用系统图片目录
        template (str): 文件名 strftime 模板
        quick (bool): True 时不弹文件对话框，直接按模板写入
        parent (QWidget): 文件对话框父窗口
        title (str): 文件对话框标题

    Returns:
        str or None: 实际写入的路径；取消或失败返回 None
    """
    if pixmap is None or pixmap.isNull():
        return None
    fmt = (fmt or 'png').lower().lstrip('.')
    if fmt == 'jpeg':
        fmt = 'jpg'
    if fmt not in FORMATS:
        fmt = 'png'
    quality = max(1, min(100, int(quality or _QUALITY_DEFAULT)))
    directory = directory or default_directory()
    try:
        os.makedirs(directory, exist_ok=True)
    except Exception as e:
        logger.warning(f"截图目录不可用 {directory}: {e}，退回主目录")
        directory = os.path.expanduser('~')

    name = build_filename(template, fmt)
    default_path = os.path.join(directory, name)
    path = default_path
    if not quick:
        path, _selected = QFileDialog.getSaveFileName(
            parent, title, default_path, _FILTERS.get(fmt, _FILTERS['png']))
        if not path:
            return None
        # 用户手改了扩展名时按扩展名纠正格式
        suffix = os.path.splitext(path)[1].lower().lstrip('.')
        if suffix == 'jpeg':
            suffix = 'jpg'
        if suffix in FORMATS:
            fmt = suffix

    target = pixmap
    if fmt == 'jpg':
        target = _flatten_for_jpg(pixmap)
    codec = {'png': 'PNG', 'jpg': 'JPEG', 'webp': 'WEBP'}[fmt]
    try:
        ok = target.save(path, codec, quality) if fmt != 'png' \
            else target.save(path, 'PNG')
    except Exception as e:
        # 编解码插件缺失或路径异常时 Qt 偶尔直接抛，不能让截图流程断在这里
        logger.warning(f"保存 {fmt} 失败: {e}")
        ok = False
    if not ok and fmt == 'webp':
        # Qt 未编入 WEBP 插件：退成 PNG，别让用户白截一张
        fallback = os.path.splitext(path)[0] + '.png'
        logger.warning("WEBP 不可用，改用 PNG 保存")
        ok = target.save(fallback, 'PNG')
        if ok:
            path = fallback
    if ok:
        logger.info(f"截图已保存: {path}")
        return path
    logger.error(f"截图保存失败: {path}")
    return None
