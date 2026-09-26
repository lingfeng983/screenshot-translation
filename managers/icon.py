#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
程序图标
用 Pillow 程序化绘制：蓝色渐变圆角方块 + 白色对话框气泡 + "译"字。
无需外部图标资源，generate 后按尺寸缓存。
"""

import logging
import os

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger('screenshot-translation')

# 主色：科技蓝 → 深蓝渐变
_COLOR_TOP = (41, 128, 236)
_COLOR_BOTTOM = (26, 92, 200)
_COLOR_BUBBLE = (255, 255, 255, 235)

_ICON_CACHE = {}


def _rounded_gradient(size):
    """生成带圆角遮罩的垂直渐变方块"""
    gradient = Image.new('RGB', (size, size))
    draw = ImageDraw.Draw(gradient)
    for y in range(size):
        t = y / max(size - 1, 1)
        color = tuple(
            int(_COLOR_TOP[i] + (_COLOR_BOTTOM[i] - _COLOR_TOP[i]) * t)
            for i in range(3))
        draw.line([(0, y), (size, y)], fill=color)

    # 圆角遮罩（Windows托盘常用完全圆角风格）
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255)
    gradient.putalpha(mask)
    return gradient


def _load_font(size):
    """按优先级加载一个可绘制中文的字体，失败返回None（Pillow回退默认字体）"""
    candidates = [
        'C:/Windows/Fonts/msyh.ttc',    # 微软雅黑
        'C:/Windows/Fonts/simhei.ttf',  # 黑体
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def generate_icon(size=64):
    """
    生成程序图标

    Args:
        size (int): 边长像素

    Returns:
        PIL.Image.Image: RGBA图标
    """
    if size in _ICON_CACHE:
        return _ICON_CACHE[size].copy()

    img = _rounded_gradient(size)
    draw = ImageDraw.Draw(img)

    # ---- 对话框气泡（白色圆角矩形 + 左下小尾巴）----
    bx0, by0 = int(size * 0.18), int(size * 0.20)
    bx1, by1 = int(size * 0.82), int(size * 0.66)
    draw.rounded_rectangle([bx0, by0, bx1, by1],
                           radius=int(size * 0.10), fill=_COLOR_BUBBLE)
    # 气泡尾巴（小三角）
    tail = [
        (int(size * 0.30), int(size * 0.64)),
        (int(size * 0.44), int(size * 0.64)),
        (int(size * 0.30), int(size * 0.80)),
    ]
    draw.polygon(tail, fill=_COLOR_BUBBLE)

    # ---- 气泡内的"译"字 ----
    font = _load_font(int(size * 0.34))
    text = "译"
    if font:
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            tx = (bx0 + bx1) / 2 - tw / 2 - bbox[0]
            ty = (by0 + by1) / 2 - th / 2 - bbox[1]
            draw.text((tx, ty), text, font=font, fill=(30, 90, 190))
        except Exception as e:
            logger.debug(f"图标文字绘制失败: {e}")

    _ICON_CACHE[size] = img
    logger.debug(f"图标已生成: {size}x{size}")
    return img.copy()


def generate_icon_ico(path='resources/icon.ico',
                      sizes=((16, 16), (24, 24), (32, 32), (48, 48),
                             (64, 64), (128, 128), (256, 256))):
    """
    生成多尺寸 .ico 文件（用于窗口图标/快捷方式/打包）

    注意：Pillow 保存 ICO 时会直接跳过比主图大的尺寸条目，
    所以必须以最大尺寸（256）为主图，其余尺寸以原生渲染从
    append_images 传入；否则只会写出比主图小的那几档（曾因此
    只剩 16×16，桌面/任务栏图标被系统拉大到模糊）。

    Args:
        path (str): 输出路径
        sizes (tuple): 包含的尺寸（均需 ≤ 主图尺寸）

    Returns:
        str: 输出路径，失败返回None
    """
    try:
        os_dir = os.path.dirname(path)
        if os_dir and not os.path.exists(os_dir):
            os.makedirs(os_dir)
        main_size = max(s[0] for s in sizes)
        main_img = generate_icon(main_size)
        rest = [generate_icon(s[0]) for s in sizes if s[0] != main_size]
        main_img.save(path, format='ICO',
                      sizes=sizes, append_images=rest)
        logger.info(f"ICO图标已生成: {path}（{len(sizes)} 个尺寸）")
        return path
    except Exception as e:
        logger.warning(f"生成ICO失败: {e}")
        return None


def generate_icon_png(path='resources/icon.png', size=256):
    """
    生成 PNG 图标文件

    Returns:
        str: 输出路径，失败返回None
    """
    import os
    try:
        os_dir = os.path.dirname(path)
        if os_dir and not os.path.exists(os_dir):
            os.makedirs(os_dir)
        generate_icon(size).save(path, format='PNG')
        logger.info(f"PNG图标已生成: {path}")
        return path
    except Exception as e:
        logger.warning(f"生成PNG失败: {e}")
        return None
