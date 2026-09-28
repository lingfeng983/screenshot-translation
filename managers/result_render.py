#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图遮罩的结果绘制

有道式"就地翻译"的视觉件：把译文贴回原文位置（贴图覆盖）、
结果气泡卡片几何、块内背景/前景色采样。

只依赖 QPainter/PIL，不持有窗口，便于离屏渲染验证与单元测试。
坐标约定：
- block / rect 参数为"被裁剪图坐标系"（选区左上角为原点）
- origin 为选区在屏幕上的位置，绘制时平移过去
"""

from collections import Counter
import weakref

from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen

from . import styles
from .text_block import TextBlock

# 展示模式：与工具条三段按钮一一对应
MODE_ORIGINAL = 'original'
MODE_BILINGUAL = 'bilingual'
MODE_TRANSLATED = 'translated'

MODES = (MODE_ORIGINAL, MODE_BILINGUAL, MODE_TRANSLATED)
MODE_LABELS = {MODE_ORIGINAL: '原文', MODE_BILINGUAL: '双语', MODE_TRANSLATED: '译文'}

# 覆盖块：外扩几像素把原文描边/抗锯齿边缘一起吃掉
COVER_PAD = 2
# 双语模式下原文行压缩到原行高的比例
BILINGUAL_SOURCE_RATIO = 0.72
# 覆盖文字的最小/最大字号（像素）
FONT_MIN_PX = 9
FONT_MAX_PX = 40

# 结果态排版缓存：只服务当前这一张选区图（见 _layout_cache_for）
_LAYOUT_CACHE = {}
_LAYOUT_OWNER = None
_LAYOUT_SCALE = None

# 结果气泡卡片
CARD_BG = QColor(35, 38, 41, 250)
CARD_BORDER = QColor('#3a3d42')
CARD_TEXT = QColor('#e8e8e8')
CARD_SUBTEXT = QColor('#9aa0a6')
CARD_RADIUS = 8
CARD_MAX_WIDTH = 420
CARD_MAX_HEIGHT = 300
CARD_GAP = 8
CARD_PADDING = 12
CARD_TITLE_HEIGHT = 20
CARD_FOOTER_HEIGHT = 22
CARD_BODY_PX = 12

# 卡片正文固定字号：调用方用同一个字号折行，绘制才不会溢出
CARD_BODY_WRAP_PX = CARD_BODY_PX


def to_logical_blocks(blocks, scale):
    """
    把图像像素坐标的行块换算成遮罩用的逻辑坐标

    Args:
        blocks (list[TextBlock]): 图像像素坐标下的行块
        scale (float): 逻辑坐标到图像物理像素的倍率（高分屏 > 1）

    Returns:
        list[TextBlock]: 逻辑坐标行块（文本保持不变）
    """
    if not scale or scale == 1.0:
        return list(blocks)
    return [TextBlock(b.text, int(round(b.left / scale)),
                      int(round(b.top / scale)),
                      int(round(b.width / scale)),
                      int(round(b.height / scale))) for b in blocks]


def pil_to_qpixmap(image, dpr=1.0):
    """
    PIL 图像转 QPixmap

    Args:
        image (PIL.Image.Image): RGB/RGBA 图像
        dpr (float): 设备像素比，设置后按逻辑尺寸绘制仍保持清晰

    Returns:
        QPixmap 或 None（转换失败）
    """
    try:
        from PyQt5.QtGui import QImage, QPixmap

        rgb = image.convert('RGBA') if image.mode == 'RGBA' else image.convert('RGB')
        fmt = QImage.Format_RGBA8888 if rgb.mode == 'RGBA' else QImage.Format_RGB888
        qimage = QImage(rgb.tobytes(), rgb.width, rgb.height,
                        rgb.width * len(rgb.mode), fmt)
        pixmap = QPixmap.fromImage(qimage.copy())
        pixmap.setDevicePixelRatio(dpr or 1.0)
        return pixmap
    except Exception:
        return None


# 字形预热覆盖的字号档：覆盖常见行高，避免首帧现加载字体
_WARM_UP_SIZES = (11, 14, 18, 22, 26, 32, 40)


def warm_up_fonts():
    """
    预热 Qt 的字形缓存与字体整形

    第一次把中文画到画布上时 Qt 要现加载字体文件并做文字整形，实测结果态
    首帧为此付出约 290ms；启动空闲时先离屏画一遍，用户截图后就不卡这一下。
    必须在 GUI 线程调用。
    """
    try:
        image = QImage(300, 60, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setPen(QColor('#ffffff'))
        for pixel_size in _WARM_UP_SIZES:
            font = QFont(painter.font())
            font.setPixelSize(pixel_size)
            painter.setFont(font)
            painter.drawText(2, min(58, pixel_size + 4), '译文字示例 sample 0123')
        painter.end()
    except Exception:
        return False
    return True


def card_content_size(painter, body_text, title=None, footer=None,
                      width=CARD_MAX_WIDTH):
    """
    按内容计算卡片尺寸（宽固定为可用宽度，高随正文行数变化）

    Args:
        painter (QPainter): 提供字体度量的画布
        body_text (str): 正文
        title (str 或 None): 标题
        footer (str 或 None): 脚注
        width (int): 卡片宽度上限

    Returns:
        tuple[QSize, list[str]]: (卡片尺寸, 折好的正文行)
    """
    from PyQt5.QtCore import QSize

    body_height = CARD_MAX_HEIGHT - 2 * 10
    if title:
        body_height -= CARD_TITLE_HEIGHT
    if footer:
        body_height -= CARD_FOOTER_HEIGHT
    body_rect = QRect(0, 0, max(40, width - 2 * CARD_PADDING), body_height)

    font = QFont(painter.font())
    font.setPixelSize(CARD_BODY_WRAP_PX)
    painter.setFont(font)
    _, lines, _ = fit_text(painter, body_text, body_rect, CARD_BODY_WRAP_PX,
                           min_px=CARD_BODY_WRAP_PX)
    line_height = QFontMetrics(font).lineSpacing()

    height = 20 + len(lines) * line_height
    if title:
        height += CARD_TITLE_HEIGHT
    if footer:
        height += CARD_FOOTER_HEIGHT
    return QSize(width, min(CARD_MAX_HEIGHT, max(60, height))), lines


def dominant_colors(image, rect, scale=1.0):
    """
    采样文本块的背景色与前景色

    背景色取块外扩环带像素的众数（截图里文本周围基本都是背景），
    前景色取块内与背景差异最大的像素众数。

    Args:
        image (PIL.Image.Image): 被裁剪的选区图像（RGB，物理像素）
        rect (QRect): 块在逻辑坐标下的矩形
        scale (float): 逻辑坐标到图像物理像素的倍率（高分屏缩放）

    Returns:
        tuple[QColor, QColor]: (背景色, 前景色)；采样失败时给安全的黑白对
    """
    try:
        width, height = image.size
        physical = QRect(int(rect.x() * scale), int(rect.y() * scale),
                         max(1, int(rect.width() * scale)),
                         max(1, int(rect.height() * scale)))
        outer = physical.adjusted(-4, -4, 4, 4)
        outer = outer.intersected(QRect(0, 0, width, height))
        ring = _border_pixels(image, outer)
        counts = Counter(ring)
        if not counts:
            return QColor('#ffffff'), QColor('#111111')
        bg_rgb = counts.most_common(1)[0][0]

        inner = physical.intersected(QRect(0, 0, width, height))
        fg_rgb = _foreground_pixel(image, inner, bg_rgb)

        bg = QColor(*bg_rgb)
        fg = QColor(*fg_rgb) if fg_rgb else _contrast_color(bg)
        # 采样出的前景与背景对比不足时（截图压缩、彩色底），退回按亮度选黑白
        if _color_distance(fg, bg) < 60:
            fg = _contrast_color(bg)
        return bg, fg
    except Exception:
        return QColor('#ffffff'), QColor('#111111')


def _border_pixels(image, rect):
    """取矩形四条边上一圈像素的 RGB 列表"""
    pixels = []
    x1, y1 = rect.left(), rect.top()
    x2, y2 = rect.right(), rect.bottom()
    if x2 <= x1 or y2 <= y1:
        return pixels
    step = max(1, (x2 - x1) // 64)
    load = image.load()
    for x in range(x1, x2 + 1, step):
        for y in (y1, y2):
            pixels.append(_rgb(load, x, y))
    step = max(1, (y2 - y1) // 64)
    for y in range(y1, y2 + 1, step):
        for x in (x1, x2):
            pixels.append(_rgb(load, x, y))
    return [p for p in pixels if p is not None]


def _rgb(load, x, y):
    """取单像素 RGB 三元组，越界返回None"""
    try:
        value = load[x, y]
    except Exception:
        return None
    if isinstance(value, int):
        return (value, value, value)
    return (value[0], value[1], value[2])


def _foreground_pixel(image, rect, bg_rgb):
    """块内与背景色差最大的采样点颜色（近似文字颜色）"""
    x1, y1 = rect.left(), rect.top()
    x2, y2 = rect.right(), rect.bottom()
    if x2 <= x1 or y2 <= y1:
        return None
    load = image.load()
    best, best_dist = None, -1.0
    step_x = max(1, (x2 - x1) // 48)
    step_y = max(1, (y2 - y1) // 24)
    for y in range(y1, y2 + 1, step_y):
        for x in range(x1, x2 + 1, step_x):
            rgb = _rgb(load, x, y)
            if rgb is None:
                continue
            dist = _distance(rgb, bg_rgb)
            if dist > best_dist:
                best, best_dist = rgb, dist
    return best


def _distance(a, b):
    """两点 RGB 距离"""
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _color_distance(one, other):
    """两个 QColor 的 RGB 距离"""
    return _distance(one.getRgb()[:3], other.getRgb()[:3])


def _luminance(color):
    """感知亮度（0~255）"""
    r, g, b = color.getRgb()[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def _contrast_color(bg):
    """给定背景色，返回对比足够的文字色"""
    return QColor('#111111') if _luminance(bg) > 140 else QColor('#f5f7fa')


def fit_text(painter, text, rect, base_px, min_px=FONT_MIN_PX):
    """
    把文本缩到能塞进矩形

    以块高为基准字号起排，放不下就逐档缩小；到最小字号仍放不下时
    返回截断为省略号的单行。

    Args:
        painter (QPainter): 目标画布（用它的字体度量）
        text (str): 待排文本
        rect (QRect): 可用区域
        base_px (int): 基准字号（像素）
        min_px (int): 最小字号

    Returns:
        tuple[QFont, list[str], bool]: (字体, 行列表, 是否被截断)
    """
    base_px = max(min_px, min(FONT_MAX_PX, int(base_px)))
    for pixel_size in range(base_px, min_px - 1, -1):
        font = QFont(painter.font())
        font.setPixelSize(pixel_size)
        lines, truncated = _wrap(font, text, rect.width(), rect.height())
        if lines and not truncated:
            return font, lines, False
        if lines and pixel_size == min_px:
            return font, lines, True
    font = QFont(painter.font())
    font.setPixelSize(min_px)
    lines, _ = _wrap(font, text, rect.width(), rect.height())
    return font, lines[:1], True


def _wrap(font, text, max_width, max_height):
    """
    按像素宽度折行

    Args:
        font (QFont): 字体（用它自己的度量，不依赖画布当前字体）
        text (str): 文本
        max_width (int): 行最大宽度
        max_height (int): 可用高度

    Returns:
        tuple[list[str], bool]: (行列表, 是否因超出高度而截断)
    """
    metrics = QFontMetrics(font)
    # 多行 drawText 按 lineSpacing 排布，用 height 估算会低估所需高度
    line_height = metrics.lineSpacing()
    if max_width <= 0 or line_height <= 0:
        return ([], True)

    max_lines = max(1, int(max_height // line_height))
    lines = []
    for paragraph in (text or '').split('\n'):
        words = _tokens(paragraph)
        current = ''
        for token in words:
            candidate = current + token
            if metrics.horizontalAdvance(candidate) <= max_width or not current:
                current = candidate
                continue
            lines.append(current.rstrip())
            current = token.lstrip()
            if metrics.horizontalAdvance(current) > max_width:
                current = _hard_break(metrics, current, max_width, lines)
        lines.append(current.rstrip())

    if len(lines) > max_lines:
        kept = lines[:max_lines]
        kept[max_lines - 1] = _ellipsis(metrics, kept[max_lines - 1], max_width)
        return kept, True
    return [line for line in lines if line], False


def _tokens(text):
    """切分排版单元：中日韩按字切，拉丁按词切（词后带原空格）"""
    tokens, buf = [], ''
    for char in text:
        if _is_cjk(char):
            if buf:
                tokens.append(buf)
                buf = ''
            tokens.append(char)
        elif char == ' ':
            buf += char
            tokens.append(buf)
            buf = ''
        else:
            buf += char
    if buf:
        tokens.append(buf)
    return tokens or ['']


def _is_cjk(char):
    """中日韩文字判定：这些字符可以直接在任意位置折行"""
    code = ord(char)
    return (0x3040 <= code <= 0x30FF or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF
            or 0xFF00 <= code <= 0xFF60)


def _hard_break(metrics, text, max_width, lines):
    """单个超长 token 硬切，返回剩下的部分"""
    current = ''
    for char in text:
        if metrics.horizontalAdvance(current + char) > max_width and current:
            lines.append(current)
            current = char
        else:
            current += char
    return current


def _ellipsis(metrics, text, max_width):
    """把一行截断成带省略号且不超过宽度的文本"""
    mark = '…'
    if metrics.horizontalAdvance(text) <= max_width:
        return text
    while text and metrics.horizontalAdvance(text + mark) > max_width:
        text = text[:-1]
    return text.rstrip() + mark


def _source_line_px(base_px):
    """双语模式下的原文行字号（相对译文基准字号压缩）"""
    return max(FONT_MIN_PX, int(base_px * BILINGUAL_SOURCE_RATIO))


def bilingual_source_layout(base_font, base_px, avail):
    """
    双语模式原文行的字号与行高

    一行文本的高度里要塞下"原文+译文"两行，所以原文行按可用高度压缩；
    压到连最小字号都放不下时返回 (0, 0)，调用方据此只保留译文。

    Args:
        base_font (QFont): 绘制用字体（取字族与样式）
        base_px (int): 译文基准字号
        avail (int): 本块可占用的总高度

    Returns:
        tuple[int, int]: (原文字号, 原文行高)；放不下原文行时为 (0, 0)
    """
    font = QFont(base_font)
    font.setPixelSize(_source_line_px(base_px))
    natural = max(1, QFontMetrics(font).height())
    height = max(FONT_MIN_PX, min(natural, int(avail * 0.45)))
    if height + FONT_MIN_PX > avail:
        return 0, 0
    return max(FONT_MIN_PX, int(font.pixelSize() * height / natural)), height


def _block_bottom_limit(block, following, bounds):
    """
    本块可向下借用的空间下沿

    只能借到选区内，且不能压到下方横向重叠的文本行。

    Args:
        block (TextBlock): 当前行块
        following (list[TextBlock]): 位置更靠后的行块
        bounds (QRect): 绘制允许覆盖到的画布区域

    Returns:
        int: 可用的下边界 y
    """
    limit = bounds.bottom()
    for other in following:
        if other.top <= block.bottom:
            continue
        overlap = (min(block.right, other.right)
                   - max(block.left, other.left))
        if overlap > max(4, block.width * 0.2):
            limit = min(limit, other.top - COVER_PAD)
    return max(block.bottom, limit)


def _layout_cache_for(image, scale):
    """
    取本选区图的排版缓存；换了图就整体作废

    缓存以"这张图对象本身"为归属：图一换（重新截图），旧缓存立即清空，
    避免用上一张选区的取色结果画当前选区。

    Args:
        image (PIL.Image.Image): 选区图像
        scale (float): 逻辑坐标到图像像素的倍率

    Returns:
        dict: 以 (块矩形, 译文, 模式, 可借用高度) 为键的排版结果
    """
    global _LAYOUT_CACHE, _LAYOUT_OWNER, _LAYOUT_SCALE

    owner = _LAYOUT_OWNER() if _LAYOUT_OWNER is not None else None
    if owner is not image or _LAYOUT_SCALE != scale:
        _LAYOUT_CACHE = {}
        _LAYOUT_SCALE = scale
        try:
            _LAYOUT_OWNER = weakref.ref(image)
        except TypeError:
            _LAYOUT_OWNER = None
    return _LAYOUT_CACHE


def _layout_block(painter, image, rect, text, mode, scale, avail):
    """
    单块的取色与排版（结果态每帧都要用到，代价高，按块缓存）

    取色要逐像素采样选区图，排版要从基准字号逐档试到能放下，
    一次重绘十几块就是几百毫秒；同一张选区图上的块在不换模式、
    不改译文时结果不变，所以缓存起来。

    Returns:
        tuple: (背景色, 前景色, 字体, 行列表, 原文字号, 原文行高, 行高)
    """
    bg, fg = dominant_colors(image, rect, scale)
    base_px = int(rect.height() * 0.78)
    font, lines, _ = fit_text(painter, text, QRect(0, 0, rect.width(), avail), base_px)
    source_px = source_height = 0
    if mode == MODE_BILINGUAL:
        source_px, source_height = bilingual_source_layout(
            painter.font(), base_px, avail)
        if source_height:
            font, lines, _ = fit_text(
                painter, text,
                QRect(0, 0, rect.width(), avail - source_height), base_px)
    line_height = max(1, QFontMetrics(font).lineSpacing())
    return bg, fg, font, lines, source_px, source_height, line_height


def paint_blocks(painter, image, blocks, texts, origin, mode, scale=1.0):
    """
    就地覆盖绘制

    译文可以向下借用原文行下方的空白（双语模式先给原文行留一行），
    长译文因此能完整换行显示而不是被截断成省略号。
    按从上到下顺序绘制，重叠时后绘制的块压住前一块溢出的部分。

    Args:
        painter (QPainter): 目标画布
        image (PIL.Image.Image): 选区图像（取色用，物理像素）
        blocks (list[TextBlock]): 行块（选区内的逻辑坐标）
        texts (list[str]): 与 blocks 等长的译文
        origin (QPoint): 选区在屏幕上的原点（逻辑坐标）
        mode (str): MODE_ORIGINAL / MODE_BILINGUAL / MODE_TRANSLATED
        scale (float): 逻辑坐标到图像像素的倍率

    Returns:
        list[QRect]: 实际绘制过的覆盖矩形（画布坐标）
    """
    if mode == MODE_ORIGINAL or not image:
        return []

    pairs = [(b, t) for b, t in zip(blocks, texts) if (t or '').strip()]
    pairs.sort(key=lambda item: item[0].top)

    # 绘制范围：译文再长也不能超出选区本身（画布坐标）
    bounds = QRect(origin.x(), origin.y(),
                   int(image.width / max(1.0, scale)),
                   int(image.height / max(1.0, scale)))

    cache = _layout_cache_for(image, scale)

    painted = []
    for index, (block, text) in enumerate(pairs):
        rect = QRect(block.left, block.top, block.width, block.height)
        limit = _block_bottom_limit(block,
                                    [b for b, _ in pairs[index + 1:]], bounds)
        # 可借用高度：从块顶到"下一行/选区下沿"之前
        avail = max(rect.height(), limit - rect.top())
        key = (rect.left(), rect.top(), rect.width(), rect.height(),
               text, mode, avail)
        layout = cache.get(key)
        if layout is None:
            layout = _layout_block(painter, image, rect, text, mode, scale, avail)
            cache[key] = layout
        bg, fg, font, lines, source_px, source_height, line_height = layout

        # 覆盖块高度按实际排版内容收敛，多出的留白会压住下面的原文行
        content = source_height + len(lines) * line_height
        target = QRect(rect).translated(origin.x(), origin.y())
        target = target.adjusted(-COVER_PAD, -COVER_PAD, COVER_PAD, COVER_PAD)
        target.setHeight(max(rect.height(), min(content, avail)) + 2 * COVER_PAD)

        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRect(target)

        painter.setPen(QPen(fg))
        y = target.top()
        if source_height:
            source_font = QFont(painter.font())
            source_font.setPixelSize(source_px)
            painter.setFont(source_font)
            painter.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 150)))
            metrics = QFontMetrics(source_font)
            source_text = _ellipsis(metrics, block.text, target.width() - 4)
            painter.drawText(QRect(target.left() + 2, y,
                                   target.width() - 4, source_height),
                             Qt.AlignLeft | Qt.AlignVCenter, source_text)
            y += source_height
            painter.setFont(font)
            painter.setPen(QPen(fg))

        for line in lines:
            if y + line_height > target.bottom() + 1:
                break
            painter.drawText(QRect(target.left() + 2, y,
                                   target.width() - 4, line_height),
                             Qt.AlignLeft | Qt.AlignVCenter, line)
            y += line_height
        painted.append(target)
    return painted


def card_geometry(anchor, screen_rect, size):
    """
    结果卡片位置：默认贴在选区下方居中，越界则翻转并夹回屏幕

    Args:
        anchor (QRect): 选区矩形（屏幕坐标）
        screen_rect (QRect): 可用屏幕区域
        size (QSize): 卡片尺寸

    Returns:
        QRect: 卡片矩形
    """
    x = anchor.center().x() - size.width() // 2
    y = anchor.bottom() + CARD_GAP + 4
    if y + size.height() > screen_rect.bottom():
        y = anchor.top() - CARD_GAP - 4 - size.height()
    if y < screen_rect.top():
        y = max(screen_rect.top(),
                min(screen_rect.bottom() - size.height(),
                    anchor.center().y() - size.height() // 2))
    x = max(screen_rect.left() + 4,
            min(x, screen_rect.right() - size.width() - 4))
    return QRect(x, y, size.width(), size.height())


def paint_card(painter, rect, title, body_lines, footer):
    """
    绘制深色结果卡片（圆角、描边、标题/正文/脚注三段）

    Args:
        painter (QPainter): 目标画布
        rect (QRect): 卡片矩形
        title (str): 标题（如目标语言）
        body_lines (list[str]): 已折好行的正文
        footer (str): 脚注提示（如快捷键）
    """
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QPen(CARD_BORDER, 1))
    painter.setBrush(CARD_BG)
    painter.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)

    content = QRect(rect).adjusted(CARD_PADDING, 10, -CARD_PADDING, -10)
    if footer:
        # 与 card_content_size 一致：给脚注留出固定高度，正文不与其重叠
        content.setBottom(content.bottom() - CARD_FOOTER_HEIGHT)
    if title:
        font = QFont(painter.font())
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor(styles.ACCENT_HOVER)))
        title_rect = QRect(content.left(), content.top(), content.width(), 16)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, title)
        content.setTop(content.top() + CARD_TITLE_HEIGHT)

    body_font = QFont(painter.font())
    body_font.setPixelSize(CARD_BODY_PX)
    painter.setFont(body_font)
    painter.setPen(QPen(CARD_TEXT))
    painter.drawText(content, Qt.AlignLeft | Qt.AlignTop, '\n'.join(body_lines))

    if footer:
        font = QFont(body_font)
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QPen(CARD_SUBTEXT))
        painter.drawText(QRect(content.left(), rect.bottom() - 22,
                               content.width(), 16),
                         Qt.AlignLeft | Qt.AlignVCenter, footer)
