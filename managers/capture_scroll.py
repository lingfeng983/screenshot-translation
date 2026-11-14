#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
滚动截图（长截图）—— 用户自己滚动

以框选区域为固定视口，程序不注入任何滚轮消息：用户自己滚动页面，后台按固定
间隔抓帧，用上一段底部条带在新帧里做垂直模板匹配求重叠量，把新增的部分拼成
一张长图。用户点「完成」（或再按一次截图快捷键）结束，点「取消」丢弃。

拼接部分（find_overlap / frame_diff / stitch_segments）是纯函数，不碰
Win32 也不碰 Qt，可以离线单测；抓帧隔离在平台函数里。
"""

import ctypes
import logging
import sys
import time
from ctypes import wintypes

from PIL import Image

logger = logging.getLogger('screenshot-translation')

# 用于匹配的底部条带高度（像素）
STRIP_HEIGHT = 80
# 相邻帧平均绝对差低于此值视为「画面没动」，不必再去匹配。
# 不能用「变化小」当标准：白底细线的页面滚 60px 平均差也只有 1 左右，
# 阈值给到 2 会把正常滚动误判成没动，长图只剩首帧。
SIMILAR_THRESHOLD = 0.8
# 匹配置信度：TM_SQDIFF_NORMED 越小越像，超过此值认为没找到重叠
MATCH_MAX_DISTANCE = 0.06
# 新增内容少于该高度就没有拼接价值
MIN_ADDED = 4
# 抓帧间隔：用户滚多久就抓多久，间隔太大容易一次滚过头出现断层
POLL_MS = 120
MAX_SEGMENTS = 400
# 兜底总时限：横幅万一没显示、快捷键也没按，线程不能永远轮询下去
MAX_DURATION_SECONDS = 600.0

_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SRCCOPY = 0x00CC0020
_CAPTUREBLT = 0x40000000
_DIB_RGB_COLORS = 0

_IDLE_HINT = '自己滚动页面，新增内容会自动接上'
_GROW_HINT = '继续滚动，或点「完成」结束'


class ScrollProgress:
    """抓帧线程 → 完成/取消横幅的共享状态：线程只写，主线程定时读"""

    def __init__(self, height=0):
        self.height = height
        self.segments = 1
        self.hint = _IDLE_HINT


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
        ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
        ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD),
    ]


def frame_diff(prev, cur):
    """
    两帧的平均绝对差（灰度，0~255）；尺寸不同时返回 255

    Args:
        prev (PIL.Image.Image): 前一帧
        cur (PIL.Image.Image): 当前帧

    Returns:
        float
    """
    import numpy as np
    if prev is None or cur is None or prev.size != cur.size:
        return 255.0
    a = np.asarray(prev.convert('L'), dtype=np.int16)
    b = np.asarray(cur.convert('L'), dtype=np.int16)
    return float(np.abs(a - b).mean())


def find_overlap(prev, cur, strip_height=STRIP_HEIGHT):
    """
    求 cur 中「新内容」的起始 y

    取 prev 底部 strip_height 行作为模板，用 cv2 的 TM_SQDIFF_NORMED 在
    cur 里做一次垂直匹配（模板宽度=帧宽，结果只有一列，即所有候选 y 的
    得分），取最像的位置。纯色区域也不会像 CCOEFF 那样给出伪高分。

    Args:
        prev (PIL.Image.Image): 前一帧
        cur (PIL.Image.Image): 当前帧
        strip_height (int): 模板高度

    Returns:
        int or None: 新内容起始 y；找不到可信重叠时 None
    """
    import cv2
    import numpy as np

    if prev is None or cur is None or prev.size != cur.size:
        return None
    a = np.asarray(prev.convert('RGB'))[-strip_height:]
    b = np.asarray(cur.convert('RGB'))
    h, w = a.shape[:2]
    if h < 8 or h >= b.shape[0] or w > b.shape[1]:
        return None
    try:
        result = cv2.matchTemplate(b, a, cv2.TM_SQDIFF_NORMED)
        min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(result)
    except Exception as e:
        logger.debug(f"模板匹配失败: {e}")
        return None
    if min_val > MATCH_MAX_DISTANCE:
        return None
    return int(min_loc[1]) + h


def stitch_segments(segments):
    """
    把 (图像, 高度) 段列表纵向拼成一张长图（纯拼接，无匹配）

    Args:
        segments (list[tuple[PIL.Image.Image, int]]): 每段图与其参与高度

    Returns:
        PIL.Image.Image or None
    """
    if not segments:
        return None
    width = segments[0][0].width
    height = sum(h for _img, h in segments)
    if width <= 0 or height <= 0:
        return None
    out = Image.new('RGB', (width, height))
    y = 0
    for image, seg_h in segments:
        part = image.crop((0, 0, width, min(seg_h, image.height)))
        out.paste(part, (0, y))
        y += seg_h
    return out


def _grab_region_win32(box, all_screens):
    """
    BitBlt 只抓选区那一小块（物理像素）

    整屏 ImageGrab 一帧要 50ms 上下，手动滚动期间要一直轮询抓帧，撑不住；
    这里只拷选区大小的位图，实测 7ms 左右。CAPTUREBLT 与 Pillow 保持一致，
    分层窗口才抓得到。

    Args:
        box (tuple[int, int, int, int]): 相对抓拍图左上角的裁剪框
        all_screens (bool): 抓拍图是否覆盖整块虚拟桌面（原点可能为负）

    Returns:
        PIL.Image.Image or None
    """
    left, top, right, bottom = (int(v) for v in box)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    # 句柄是指针宽度，不声明签名会被当成 C int 截断
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int,
                                             ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, wintypes.HDC,
                             ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                                wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]

    if all_screens:
        left += user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        top += user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)

    screen_dc = user32.GetDC(None)
    if not screen_dc:
        return None
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = (gdi32.CreateCompatibleBitmap(screen_dc, width, height)
              if mem_dc else None)
    if not bitmap:
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)
        return None
    try:
        previous = gdi32.SelectObject(mem_dc, bitmap)
        try:
            if not gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc,
                                left, top, _SRCCOPY | _CAPTUREBLT):
                return None
            header = _BITMAPINFOHEADER()
            header.biSize = ctypes.sizeof(header)
            header.biWidth = width
            header.biHeight = -height       # 负高度 = 自上而下，省一次翻转
            header.biPlanes = 1
            header.biBitCount = 32
            buffer = ctypes.create_string_buffer(width * height * 4)
            lines = gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer,
                                    ctypes.byref(header), _DIB_RGB_COLORS)
            if lines != height:
                return None
            return Image.frombytes('RGB', (width, height), buffer, 'raw',
                                   'BGRX', 0, 1)
        finally:
            # 位图仍被选进 mem_dc 时 DeleteObject 会失败并留下一个 GDI 对象；
            # 长截图是高频抓帧，BitBlt/GetDIBits 任一失败路径漏掉这一步都会
            # 持续累积句柄。恢复旧对象必须放在所有 return 之前的内层 finally。
            if previous:
                gdi32.SelectObject(mem_dc, previous)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)


def grab_frame(context):
    """
    按 context 抓一帧选区图

    Args:
        context (dict): {'crop_box': (l,t,r,b) 物理像素, 'all_screens': bool}

    Returns:
        PIL.Image.Image or None
    """
    box = tuple(context['crop_box'])
    all_screens = bool(context.get('all_screens'))
    if sys.platform == 'win32':
        try:
            image = _grab_region_win32(box, all_screens)
        except Exception as e:
            logger.warning(f"选区抓帧失败，退回整屏抓拍: {e}")
            image = None
        if image is not None:
            return image
    from PIL import ImageGrab
    try:
        full = ImageGrab.grab(all_screens=all_screens)
        return full.crop(box).copy()
    except Exception as e:
        logger.warning(f"滚动截图抓帧失败: {e}")
        return None


def capture_scrolling(context, first_image, *, poll_ms=POLL_MS,
                      max_height=12000, finish_event=None, cancel_event=None,
                      grabber=None, progress=None):
    """
    定时抓帧 + 拼接，直到用户结束；滚动由用户自己完成

    Args:
        context (dict): 抓帧上下文（crop_box / all_screens）
        first_image (PIL.Image.Image): 首帧选区图
        poll_ms (int): 抓帧间隔（毫秒）
        max_height (int): 长图总高度上限（像素）
        finish_event (threading.Event or None): 置位后结束并返回长图
        cancel_event (threading.Event or None): 置位后丢弃，返回 None
        grabber (callable or None): 注入的抓帧函数，便于测试
        progress (ScrollProgress or None): 供横幅读取的实时状态

    Returns:
        PIL.Image.Image or None: 拼接长图；取消时 None，没滚动时是首帧
    """
    if first_image is None:
        return None
    grab = grabber or grab_frame
    state = progress if progress is not None else ScrollProgress()

    # 第一段：首帧整张
    segments = [(first_image, first_image.height)]
    total = first_image.height
    reference = first_image
    state.height = total
    state.segments = 1
    state.hint = _IDLE_HINT
    interval = max(0.02, poll_ms / 1000.0)
    started = time.monotonic()

    while True:
        if cancel_event is not None and cancel_event.is_set():
            logger.info("用户取消长截图，丢弃已拼接内容")
            return None
        if finish_event is not None and finish_event.is_set():
            break
        if total >= max_height:
            state.hint = f'已达高度上限 {max_height} px'
            break
        if len(segments) >= MAX_SEGMENTS \
                or time.monotonic() - started > MAX_DURATION_SECONDS:
            state.hint = '已达本次长截图上限'
            break

        time.sleep(interval)
        frame = grab(context)
        if frame is None or frame.size != first_image.size:
            state.hint = '抓帧中断，点「完成」保留已拼接部分'
            break

        # 画面没动就继续等，用户想滚多久都行
        if frame_diff(reference, frame) < SIMILAR_THRESHOLD:
            continue

        overlap = find_overlap(reference, frame)
        if overlap is None:
            # 反向匹配能对上说明在往上滚；两边都对不上是一次滚过头了
            state.hint = ('正在往上滚，长图只向下增长'
                          if find_overlap(frame, reference) is not None
                          else '滚动过快出现断层，请往回滚一点')
            continue
        added = frame.height - overlap
        if added < MIN_ADDED:
            continue

        # 只把新增那一段接上去，避免重复内容
        segments.append((frame.crop((0, overlap, frame.width, frame.height)),
                         added))
        total += added
        reference = frame
        state.height = total
        state.segments = len(segments)
        state.hint = _GROW_HINT

    logger.info(f"长截图结束：{len(segments)} 段，{total} px")
    return stitch_segments(segments)
