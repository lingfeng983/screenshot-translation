#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
滚动截图（长截图）

以框选区域为固定视口，向窗口注入滚轮消息，连续抓帧，用前一帧底部条带在
下一帧里做垂直模板匹配求重叠量，再把每帧新增的部分拼成一张长图。

拼接部分（find_overlap / frame_diff / stitch_segments）是纯函数，不碰
Win32 也不碰 Qt，可以离线单测；滚轮注入与抓帧隔离在平台函数里。
"""

import logging
import sys
import time

from PIL import Image

logger = logging.getLogger('screenshot-translation')

# 用于匹配的底部条带高度（像素）
STRIP_HEIGHT = 80
# 相邻帧平均绝对差低于此值视为「画面没动」（到底或被拦住）。
# 不能用「变化小」当标准：白底细线的页面滚 60px 平均差也只有 1 左右，
# 阈值给到 2 会把正常滚动误判成到底，长图只剩首帧。
SIMILAR_THRESHOLD = 0.8
# 连续几帧不动才判定结束，躲开一次偶然的静止（动画间隙）
STILL_FRAMES = 2
# 匹配置信度：TM_SQDIFF_NORMED 越小越像，超过此值认为没找到重叠
MATCH_MAX_DISTANCE = 0.06
# 新增内容少于该高度就没有拼接价值
MIN_ADDED = 4
MAX_FRAMES = 200
# 遮罩在滚动期间会隐藏，用户没有中途取消的入口，兜一个总时限避免长时间无响应
MAX_DURATION_SECONDS = 15.0

_MOUSEEVENTF_WHEEL = 0x0800
_WHEEL_DELTA = -120


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


def grab_frame(context):
    """
    按 context 抓一帧选区图

    Args:
        context (dict): {'crop_box': (l,t,r,b) 物理像素, 'all_screens': bool}

    Returns:
        PIL.Image.Image or None
    """
    from PIL import ImageGrab
    try:
        full = ImageGrab.grab(all_screens=bool(context.get('all_screens')))
        return full.crop(tuple(context['crop_box'])).copy()
    except Exception as e:
        logger.warning(f"滚动截图抓帧失败: {e}")
        return None


def scroll_down(center, step=3):
    """
    把光标移到视口中心并向下滚动 step 格（仅 Windows）

    Args:
        center (tuple[int, int]): 屏幕物理像素坐标
        step (int): 滚轮格数

    Returns:
        bool: 是否成功注入
    """
    if sys.platform != 'win32':
        return False
    import ctypes
    from ctypes import wintypes
    try:
        user32 = ctypes.windll.user32
        # 显式声明签名：SetCursorPos 入参是 int，mouse_event 的 dwData 是
        # DWORD、dwExtraInfo 是 ULONG_PTR，缺 argtypes 时全按 C int 传。
        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetCursorPos.restype = wintypes.BOOL
        user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                       wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_void_p]
        user32.SetCursorPos(int(center[0]), int(center[1]))
        delta = ctypes.c_uint32(_WHEEL_DELTA).value
        for _ in range(max(1, int(step))):
            user32.mouse_event(_MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
        return True
    except Exception as e:
        logger.warning(f"滚轮注入失败: {e}")
        return False


def _cursor_pos():
    """记录当前光标，滚动结束后还回去"""
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes
    try:
        user32 = ctypes.windll.user32
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        point = wintypes.POINT()
        if user32.GetCursorPos(ctypes.byref(point)):
            return (point.x, point.y)
    except Exception:
        pass
    return None


def _restore_cursor(pos):
    if not pos or sys.platform != 'win32':
        return
    import ctypes
    from ctypes import wintypes
    try:
        user32 = ctypes.windll.user32
        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetCursorPos.restype = wintypes.BOOL
        user32.SetCursorPos(int(pos[0]), int(pos[1]))
    except Exception:
        pass


def capture_scrolling(context, first_image, *, delay_ms=180, step=3,
                      max_height=12000, cancel_event=None, grabber=None,
                      scroller=None):
    """
    连续滚动 + 抓帧 + 拼接，返回长图

    Args:
        context (dict): 抓帧上下文（crop_box / all_screens / center）
        first_image (PIL.Image.Image): 首帧选区图
        delay_ms (int): 每轮滚动后等待渲染的毫秒数
        step (int): 每轮滚轮格数
        max_height (int): 长图总高度上限（像素）
        cancel_event (threading.Event or None): 置位后立刻收手
        grabber (callable or None): 注入的抓帧函数，便于测试
        scroller (callable or None): 注入的滚动函数，便于测试

    Returns:
        PIL.Image.Image or None: 拼接长图；完全无法滚动时至少返回首帧
    """
    if first_image is None:
        return None
    grab = grabber or grab_frame
    scroll = scroller or (lambda: scroll_down(context.get('center'), step))
    original_cursor = _cursor_pos()

    # 第一段：首帧整张
    segments = [(first_image, first_image.height)]
    total = first_image.height
    previous = first_image
    still = 0
    started = time.monotonic()

    try:
        for _index in range(MAX_FRAMES):
            if cancel_event is not None and cancel_event.is_set():
                break
            if time.monotonic() - started > MAX_DURATION_SECONDS:
                logger.info("滚动截图达到总时限，停止抓取")
                break
            if not scroll():
                logger.info("滚轮注入不可用，滚动截图只返回首帧")
                break
            time.sleep(max(0.0, delay_ms / 1000.0))
            if cancel_event is not None and cancel_event.is_set():
                break

            frame = grab(context)
            if frame is None or frame.size != first_image.size:
                break

            # 到底判定：连续两帧几乎不变
            if frame_diff(previous, frame) < SIMILAR_THRESHOLD:
                still += 1
                if still >= STILL_FRAMES:
                    logger.info("画面连续静止，判定已滚动到底")
                    break
                previous = frame
                continue
            still = 0

            overlap = find_overlap(previous, frame)
            added = (frame.height - overlap) if overlap is not None else 0
            if overlap is None or added < MIN_ADDED:
                # 找不到重叠或几乎没有新增内容：到底、被拦住、滚动过快或
                # 偶发匹配失败都可能，连续几次都这样才收手
                still += 1
                if still >= STILL_FRAMES:
                    logger.info("连续无新增内容，停止拼接")
                    break
                previous = frame
                continue
            still = 0

            # 只把新增那一段接上去，避免重复内容
            new_part = frame.crop((0, overlap, frame.width, frame.height))
            segments.append((new_part, added))
            total += added
            previous = frame
            if total >= max_height:
                logger.info(f"长图达到高度上限 {max_height}，停止滚动")
                break
    finally:
        _restore_cursor(original_cursor)

    if len(segments) == 1:
        logger.info("没有可拼接的新内容，返回首帧")
    return stitch_segments(segments)
