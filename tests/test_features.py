#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
功能测试脚本
测试新添加的功能是否正常工作
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _rect_class():
    """QRect 延迟导入（测试脚本不在导入期拉 Qt）"""
    from PyQt5.QtCore import QRect
    return QRect


def test_retry_handler():
    """测试重试处理器"""
    print("=== 测试重试处理器 ===")
    from managers.retry_handler import RetryHandler

    def failing_function():
        raise Exception("故意失败的函数")

    def successful_function():
        return "成功执行"

    # 测试失败情况下的降级处理
    retry_handler = RetryHandler(max_attempts=2, base_delay=0.1)
    result = retry_handler.execute_with_retry(
        failing_function,
        fallback_result="降级结果"
    )
    print(f"重试失败后的结果: {result}")

    # 测试成功情况
    result = retry_handler.execute_with_retry(
        successful_function,
        fallback_result="降级结果"
    )
    print(f"成功执行的结果: {result}")
    print("✅ 重试处理器测试通过\n")

def test_cache_manager():
    """测试缓存管理器（用临时文件，避免污染项目里的真实缓存）"""
    print("=== 测试缓存管理器 ===")
    import tempfile
    from managers.cache_manager import CacheManager

    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_manager = CacheManager(cache_file=os.path.join(tmp_dir, 'cache.json'))

        # 测试缓存存储和获取
        test_text = "Hello World"
        test_translation = "你好世界"
        api_type = "libretranslate"

        # 缓存翻译结果
        cache_manager.cache_translation(test_text, api_type, test_translation)

        # 获取缓存结果
        cached_result = cache_manager.get_cached_translation(test_text, api_type)
        print(f"缓存的翻译结果: {cached_result}")

        # 测试统计信息
        stats = cache_manager.get_cache_stats()
        print(f"缓存统计: {stats}")

    print("✅ 缓存管理器测试通过\n")

def test_screenshot_widget():
    """测试截图遮罩窗口：初始化属性完整、可绘制、能获得焦点（ESC 退出依赖焦点）"""
    print("=== 测试截图遮罩窗口 ===")
    try:
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import Qt, QPoint
        from managers.screenshot import ScreenshotWidget
    except Exception as e:
        print(f"⚠️ 截图遮罩测试跳过 (导入失败): {e}\n")
        return

    app = QApplication.instance() or QApplication(sys.argv)
    widget = ScreenshotWidget()

    for attr in ('begin', 'end', 'is_selecting', 'full_screen',
                 'screen_width', 'screen_height', '_snap_window_rects'):
        assert hasattr(widget, attr), f"缺少初始化属性 {attr}"
    assert widget.full_screen is not None, "全屏背景未截取"
    assert widget.focusPolicy() == Qt.StrongFocus, "遮罩无焦点策略，ESC 无法退出"

    widget.grab()  # 未选择状态绘制提示条
    widget.begin, widget.end = QPoint(20, 20), QPoint(120, 90)
    widget.is_selecting = True
    widget.grab()  # 选区状态绘制边框/气泡/参考线
    print("✅ 截图遮罩窗口测试通过\n")

def test_tesseract_no_console_window():
    """Tesseract 子进程必须带 no-window 标志：无控制台的 exe 里否则会闪终端窗口"""
    print("=== 测试 Tesseract 子进程不弹控制台窗口 ===")
    import subprocess
    import sys

    from managers.ocr_providers.tesseract import (
        _HiddenSpawnSubprocess, _no_window_flags)

    calls = []

    class FakeSubprocess:
        PIPE = 'pipe-const'

        def run(self, args, **kwargs):
            calls.append(kwargs)
            return 'ok'

    proxy = _HiddenSpawnSubprocess(FakeSubprocess(), 0x08000000)
    proxy.run(['tesseract'], stdout=proxy.PIPE)
    assert calls and calls[0].get('creationflags') == 0x08000000, "未注入 no-window 标志"
    assert proxy.PIPE == 'pipe-const', "常量未透传"

    if sys.platform == 'win32':
        import pytesseract
        assert _no_window_flags() == getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        assert isinstance(pytesseract.pytesseract.subprocess, _HiddenSpawnSubprocess), \
            "pytesseract 未被代理，打包后翻译时会闪出终端窗口"
    print("✅ Tesseract 子进程窗口抑制测试通过\n")


def test_result_render_layout():
    """就地覆盖排版：借用的向下空间受下一行与选区下沿约束"""
    print("=== 测试译文就地覆盖排版 ===")
    from PyQt5.QtCore import QRect
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    import managers.result_render as rr
    from managers.text_block import TextBlock

    # 必须持有引用：QApplication 被回收后 Qt 字体度量会直接崩
    app = QApplication.instance() or QApplication(sys.argv)
    upper = TextBlock('one', 0, 0, 200, 20)
    lower = TextBlock('two', 0, 30, 200, 20)
    aside = TextBlock('x', 300, 40, 100, 20)
    bounds = QRect(0, 0, 400, 100)

    # 横向重叠的下一行决定下沿（下沿留出纵向外扩量）；不相干的行不挡
    assert (rr._block_bottom_limit(upper, [lower, aside], bounds)
            == lower.top - rr.COVER_PAD_Y)
    assert rr._block_bottom_limit(upper, [aside], bounds) == bounds.bottom()

    font = QFont()
    assert rr.bilingual_source_layout(font, 18, 12) == (0, 0), \
        "空间不足时应放弃原文行，保住译文"
    pixel_size, source_height = rr.bilingual_source_layout(font, 18, 40)
    assert source_height > 0 and pixel_size >= rr.FONT_MIN_PX
    print("✅ 译文就地覆盖排版测试通过\n")


class _RecordingPainter:
    """记录 drawRect/drawText 矩形的假 painter（只嵌 QFont 度量，不真的画）"""

    def __init__(self, font):
        self._font = font
        self.rects = []
        self.texts = []

    def font(self):
        return self._font

    def setFont(self, font):
        self._font = font

    def setPen(self, *args, **kwargs):
        pass

    def setBrush(self, *args, **kwargs):
        pass

    def drawRect(self, rect):
        QRect = _rect_class()
        self.rects.append(QRect(rect))

    def drawText(self, rect, flags, text):
        QRect = _rect_class()
        self.texts.append((QRect(rect), text))


def test_cover_pad_adaptive():
    """
    覆盖块横向外扩随行高自适应

    固定 2px 在大字号上盖不干净（笔画粗、ClearType 彩色边缘宽），原文首尾会
    从覆盖块两侧露出来；但外扩变宽后译文起点不能跟着往左移，否则就地覆盖
    就与原文错位了——两个不变量一起守。纵向不跟着变：行框本身已含升部/
    降部余量，变宽只会吃掉上一行的译文。
    """
    print("=== 测试覆盖块外扩自适应 ===")
    from PyQt5.QtCore import QPoint
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication
    from PIL import Image

    import managers.result_render as rr
    from managers.text_block import TextBlock

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    # 常见 UI 行高保持 2px（不改变原有观感）；大字号才变宽；有上下限
    assert rr.cover_pad_x(20) == 2, f"常规行高应仍为 2px: {rr.cover_pad_x(20)}"
    assert rr.cover_pad_x(50) > rr.cover_pad_x(20), "大字号外扩应更宽"
    assert rr.cover_pad_x(400) == rr.COVER_PAD_MAX
    assert rr.cover_pad_x(1) == rr.COVER_PAD_MIN
    values = [rr.cover_pad_x(h) for h in range(1, 300)]
    assert values == sorted(values), "外扩应随行高单调不减"
    assert rr.cover_pad_x(0) == rr.COVER_PAD_MIN
    assert rr.cover_pad_x(None) == rr.COVER_PAD_MIN
    assert rr.cover_pad_x('bad') == rr.COVER_PAD_MIN

    # 大行高块：横向覆盖矩形向外扩，但译文起点仍在原文左边界
    image = Image.new('RGB', (260, 80), (255, 255, 255))
    for x in range(60, 110):          # 块内放深色让前景色采样稳定
        for y in range(30, 40):
            image.putpixel((x, y), (20, 20, 20))
    block = TextBlock('测试译文', 60, 16, 140, 50)
    pad_x = rr.cover_pad_x(block.height)
    assert pad_x == rr.COVER_PAD_MAX, f"50px 行高应取到最大外扩: {pad_x}"

    painter = _RecordingPainter(QFont())
    rr.paint_blocks(painter, image, [block], ['译文段落'],
                    QPoint(0, 0), rr.MODE_TRANSLATED, scale=1.0)

    assert len(painter.rects) == 1, f"应只画一个覆盖块: {len(painter.rects)}"
    cover = painter.rects[0]
    assert cover.left() == block.left - pad_x, \
        f"覆盖块未向外扩: {cover.left()} != {block.left - pad_x}"
    assert cover.width() == block.width + 2 * pad_x, \
        f"覆盖块宽度应含两侧外扩: {cover.width()}"
    # 纵向恒为 COVER_PAD_Y：不跟着行高变宽，免得吃掉上一行译文
    assert cover.top() == block.top - rr.COVER_PAD_Y, \
        f"纵向外扩应变: {cover.top()} != {block.top - rr.COVER_PAD_Y}"

    assert painter.texts, "没有画出译文"
    text_rect, text = painter.texts[0]
    assert text_rect.left() == block.left, \
        f"译文起点应为块左边界 {block.left}，实际 {text_rect.left()}（外扩把文字推歪了）"
    assert text_rect.width() == block.width, \
        f"译文可用宽度应等于块宽 {block.width}，实际 {text_rect.width()}"
    assert text.strip(), "译文内容为空"

    print("✅ 覆盖块外扩自适应测试通过\n")


def _skip(reason):
    """跳过用例：pytest 下标记 skip，直接跑脚本时只打印"""
    try:
        import pytest
        pytest.skip(reason)
    except ImportError:
        print(f"⏭ 跳过：{reason}")


def _tesseract_unavailable_reason():
    """
    Tesseract 引擎/训练数据不可用时的原因（可用时返回 None）

    本用例的价值就在于用真引擎验真像素，引擎不在时跳过而不是假装通过。
    """
    try:
        from managers.ocr_providers import tesseract as tess_mod
    except Exception as e:
        return f"无法导入 Tesseract provider: {e}"
    if not tess_mod.pytesseract_available:
        return "pytesseract 未安装"
    try:
        langs = tess_mod.TesseractProvider(None).get_available_languages()
    except Exception as e:
        return f"Tesseract 引擎不可用: {e}"
    missing = {'eng', 'chi_sim'} - set(langs)
    if missing:
        return f"训练数据缺少 {sorted(missing)}: {langs}"
    return None


def _qpixmap_to_pil(pixmap):
    """QPixmap → PIL.Image（按 bytesPerLine 处理扫描行对齐）"""
    from PIL import Image
    from PyQt5.QtGui import QImage

    image = pixmap.toImage().convertToFormat(QImage.Format_RGB888)
    stride = image.bytesPerLine()
    ptr = image.constBits()
    ptr.setsize(stride * image.height())
    return Image.frombytes('RGB', (image.width(), image.height()),
                           bytes(ptr), 'raw', 'RGB', stride, 1)


def _text_pixels(image, band, threshold):
    """band 内比阈值暗的像素坐标列表（band = 左, 上, 右, 下，右/下不含）"""
    left, top, right, bottom = band
    px = image.load()
    hits = []
    for y in range(top, bottom):
        for x in range(left, right):
            r, g, b = px[x, y][:3]
            if (r + g + b) / 3 < threshold:
                hits.append((x, y))
    return hits


def _bounds(points):
    """点集的包围盒 (left, top, width, height)"""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


def _scan_band(rect, width, height, margin=10):
    """
    字迹扫描带：在绘制矩形外留一圈余量

    绘制矩形贴边（左对齐/右对齐）时首尾字形就压在矩形边上，直接用矩形当
    扫描带会把字迹截断；上下 10px 也刚好与相邻行的带子相接而不重叠。
    """
    return (max(0, rect.left() - margin),
            max(0, rect.top() - margin),
            min(width, rect.right() + 1 + margin),
            min(height, rect.bottom() + 1 + margin))


def _match_block(blocks, box):
    """按纵向中心就近 + 横向重叠，把字迹框匹配到识别块"""
    center_y = box[1] + box[3] / 2
    best = None
    for block in blocks:
        if (block.left + block.width < box[0] - 8
                or block.left > box[0] + box[2] + 8):
            continue
        dy = abs((block.top + block.height / 2) - center_y)
        if dy <= 18 and (best is None or dy < best[1]):
            best = (block, dy)
    return best[0] if best else None


def test_inplace_cover_pixel_alignment():
    """
    就地覆盖的像素级回归：真实渲染像素 + 真实 Tesseract + 真实绘制

    守住三件用户直接看得见的事：
    1. 对齐：识别块相对实心字迹的偏差 ≤2px（坐标回映射漏算留白会偏到 6px）；
    2. 盖住：原文的**每一个可见像素**（含抗锯齿彩边）都落在绘制出的覆盖块里，
       不能从边上露出——这是就地覆盖的底线；
    3. 外扩：大字号行的横向外扩确实放宽了（固定 2px 会在大字号上露边）。

    渲染走离屏 QPixmap（真字体引擎、真 ClearType 像素），不弹窗、不抓屏，
    因此可在测试套件里自动跑；引擎不在时跳过。
    """
    print("=== 测试就地覆盖的像素级对齐 ===")
    reason = _tesseract_unavailable_reason()
    if reason:
        _skip(reason)
        return

    from PyQt5.QtCore import QPoint, QRect, Qt
    from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap
    from PyQt5.QtWidgets import QApplication

    import managers.result_render as rr
    from managers.ocr_providers.tesseract import TesseractProvider

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    # 小/中/大字号 × 贴左/居中/贴右，凑齐横向偏移能显现的几种位置
    lines = [
        (QRect(14, 20, 802, 34),
         '快捷键在设置里可以直接改，改完点保存', 15, Qt.AlignLeft),
        (QRect(14, 74, 802, 34),
         'The quick brown fox jumps over the lazy dog', 16, Qt.AlignHCenter),
        (QRect(14, 128, 802, 34),
         '译文必须盖住原文，不能错位', 18, Qt.AlignRight),
        (QRect(14, 182, 802, 52),
         '大号字体的边缘也要盖住', 34, Qt.AlignRight),
    ]
    width, height = 830, 250

    # ---- 1. 离屏渲染真实文本 ----
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor('#ffffff'))
    painter = QPainter(pixmap)
    painter.setPen(QColor('#202020'))
    for rect, text, size, align in lines:
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(size)
        painter.setFont(font)
        painter.drawText(rect, align | Qt.AlignVCenter, text)
    painter.end()
    image = _qpixmap_to_pil(pixmap)

    # ---- 2. 真实引擎识别 ----
    blocks = TesseractProvider(None).recognize_blocks(image)
    assert blocks, "Tesseract 未识别到任何行块"

    matched = []
    for index, (rect, text, _size, _align) in enumerate(lines):
        band = _scan_band(rect, width, height)
        # 128 为实心字迹，250 为含抗锯齿彩边的可见字迹（要盖住的就是后者）
        solid = _text_pixels(image, band, 128)
        assert solid, f"第{index}行没渲染出文字: {text!r}"
        box = _bounds(solid)
        # 扫描带必须完整包住字迹，否则量出来的框是被截断的
        assert (box[0] > band[0] and box[1] > band[1]
                and box[0] + box[2] < band[2]
                and box[1] + box[3] < band[3]), \
            f"第{index}行字迹贴到扫描带边缘，扫描带需留白: {box} vs {band}"

        block = _match_block(blocks, box)
        assert block is not None, \
            f"第{index}行没匹配到识别块（共识别 {len(blocks)} 块）: {text!r}"
        dx, dy = block.left - box[0], block.top - box[1]
        assert abs(dx) <= 2 and abs(dy) <= 2, \
            f"第{index}行块框与字迹错位 dx={dx} dy={dy}" \
            f"（坐标回映射漏算留白会偏离到 6px）"
        matched.append((index, block, text))

    # ---- 3. 真实绘制，逐像素确认原文没有露出 ----
    canvas = QPixmap(width, height)
    canvas.fill(QColor('#ffffff'))
    painter = QPainter(canvas)
    painted = rr.paint_blocks(
        painter, image, blocks, [f'译文第{i + 1}行' for i in range(len(blocks))],
        QPoint(0, 0), rr.MODE_TRANSLATED, scale=1.0)
    painter.end()
    assert painted, "没有绘制任何覆盖块"

    for index, block, text in matched:
        band = _scan_band(lines[index][0], width, height)
        visible = _text_pixels(image, band, 250)
        leaked = [p for p in visible
                  if not any(r.contains(p[0], p[1]) for r in painted)]
        assert not leaked, (
            f"第{index}行有 {len(leaked)} 个原文像素露在覆盖块外"
            f"（如 {leaked[:3]}）: {text!r}")

    # 大字号那行：画出来的覆盖块必须真用了自适应外扩（不是常量 2px）
    big = matched[-1][1]
    pad_x = rr.cover_pad_x(big.height)
    assert pad_x > rr.COVER_PAD_MIN, \
        f"行高 {big.height} 的外扩仍为固定值 {pad_x}px，大字号会露边"
    big_cover = max(painted, key=lambda rect: rect.top())
    assert big_cover.left() == big.left - pad_x, \
        f"大字行覆盖块左侧外扩不符: {big_cover.left()} != {big.left - pad_x}"
    assert big_cover.width() == big.width + 2 * pad_x, \
        f"大字行覆盖块宽度不符: {big_cover.width()} != {big.width + 2 * pad_x}"

    print(f"✅ 就地覆盖像素级对齐测试通过（{len(matched)} 行；"
          f"大字行高 {big.height} 外扩 {pad_x}px）\n")


def test_result_render_cache():
    """结果态排版缓存：同图重绘命中缓存，换选区图必须作废"""
    print("=== 测试结果态排版缓存 ===")
    from PIL import Image
    from PyQt5.QtCore import QPoint
    from PyQt5.QtGui import QPainter, QPixmap
    from PyQt5.QtWidgets import QApplication

    import managers.result_render as rr
    from managers.text_block import TextBlock

    app = QApplication.instance() or QApplication(sys.argv)
    blocks = [TextBlock('one', 4, 4, 120, 16), TextBlock('two', 4, 26, 150, 16)]
    texts = ['译文一 translated', '译文二 more translated text']
    image_a = Image.new('RGB', (220, 80), 'white')
    image_b = Image.new('RGB', (220, 80), 'black')
    canvas = QPixmap(220, 80)

    def paint(image):
        painter = QPainter(canvas)
        rr.paint_blocks(painter, image, blocks, texts, QPoint(0, 0),
                        rr.MODE_BILINGUAL, 1.0)
        painter.end()

    rr._LAYOUT_CACHE.clear()
    paint(image_a)
    first = dict(rr._LAYOUT_CACHE)
    assert len(first) == len(blocks), f"首帧应缓存{len(blocks)}块，实际{len(first)}"

    paint(image_a)
    assert all(rr._LAYOUT_CACHE[k] is v for k, v in first.items()), \
        "同一张图重绘应命中缓存，而不是重新取色排版"

    paint(image_b)
    assert all(rr._LAYOUT_CACHE[k] is not v for k, v in first.items()), \
        "换了选区图必须作废缓存，否则会用上一张图的配色画当前选区"

    assert rr.warm_up_fonts() is True, "字形预热失败会让结果态首帧回到数百毫秒"
    print("✅ 结果态排版缓存测试通过\n")


def test_ocr_provider_reuse():
    """OCR provider 必须复用：每次识别新建实例会多起一个 tesseract --version 进程"""
    print("=== 测试 OCR provider 实例复用 ===")
    from managers.ocr import OCRManager

    class FakeProvider:
        def __init__(self, config_manager=None):
            self.config_manager = config_manager

    manager = OCRManager()
    provider = manager._get_provider(FakeProvider)
    assert manager._get_provider(FakeProvider) is provider, "provider 每次都在新建"
    manager.reload_config()
    assert manager._get_provider(FakeProvider) is not provider, "配置重载后未丢弃旧实例"
    print("✅ OCR provider 实例复用测试通过\n")


def test_app_paths_user_data():
    """打包态可写文件落 ~/.screenshot-translation，旧文件只补缺迁移"""
    print("=== 测试打包态数据目录 ===")
    import tempfile
    from managers import app_paths

    env_names = ('HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH')
    saved_env = {name: os.environ.get(name) for name in env_names}
    saved_frozen = getattr(sys, 'frozen', None)
    saved_meipass = getattr(sys, '_MEIPASS', None)
    saved_executable = sys.executable
    try:
        with tempfile.TemporaryDirectory() as home, \
                tempfile.TemporaryDirectory() as appdir:
            os.environ['HOME'] = home
            os.environ['USERPROFILE'] = home
            os.environ.pop('HOMEDRIVE', None)
            os.environ.pop('HOMEPATH', None)
            sys.frozen = True
            sys._MEIPASS = os.path.join(appdir, '_internal')
            os.makedirs(sys._MEIPASS, exist_ok=True)
            sys.executable = os.path.join(appdir, 'ScreenshotTranslator.exe')

            user_dir = os.path.join(home, '.screenshot-translation')
            assert app_paths.app_root() == appdir
            assert app_paths.data_dir() == user_dir, "打包态可写文件应落用户目录"
            for path in (app_paths.config_file(), app_paths.cache_file(),
                         app_paths.log_file()):
                assert path.startswith(user_dir), f"{path} 不在用户目录下"

            # 只读资源仍从 _internal 取，不受数据目录搬迁影响
            assert app_paths.bundled_root() == sys._MEIPASS

            # 旧版留在 exe 旁的配置要搬进用户目录，且第二次调用不覆盖已有文件
            with open(os.path.join(appdir, 'config.ini'), 'w', encoding='utf-8') as f:
                f.write('[Hotkey]\nglobal_hotkey = alt+z\n')
            assert app_paths.migrate_legacy_data() == ['config.ini']
            with open(app_paths.config_file(), encoding='utf-8') as f:
                assert 'alt+z' in f.read(), "迁移后内容不符"
            with open(app_paths.config_file(), 'w', encoding='utf-8') as f:
                f.write('[Hotkey]\nglobal_hotkey = ctrl+shift+a\n')
            assert app_paths.migrate_legacy_data() == [], "迁移不该覆盖用户已有配置"
            with open(app_paths.config_file(), encoding='utf-8') as f:
                assert 'ctrl+shift+a' in f.read()

            # 源码形态留在项目根，开发者本地 config 与工作区测试不受影响
            sys.frozen = False
            assert app_paths.data_dir() == app_paths.app_root()
            assert app_paths.migrate_legacy_data() == []
    finally:
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if saved_frozen is None:
            if hasattr(sys, 'frozen'):
                del sys.frozen
        else:
            sys.frozen = saved_frozen
        if saved_meipass is None:
            if hasattr(sys, '_MEIPASS'):
                del sys._MEIPASS
        else:
            sys._MEIPASS = saved_meipass
        sys.executable = saved_executable
    print("✅ 打包态数据目录测试通过\n")


def test_theme_system():
    """主题系统：深色/浅色 QSS 可生成、非法值回退、配置往返"""
    print("=== 测试主题系统 ===")
    import tempfile
    from managers import styles
    from config import ConfigManager

    keys = [k for k, _ in styles.available_themes()]
    assert keys == ['dark', 'light'], f"主题列表不符: {keys}"
    dark_qss = styles.style_sheet('dark')
    light_qss = styles.style_sheet('light')
    assert dark_qss != light_qss, "两套主题 QSS 不应相同"

    assert styles.set_current_theme('light') == 'light'
    assert styles.style_sheet() == light_qss, "无参时应使用当前主题"
    assert styles.current_theme() == 'light'
    assert styles.set_current_theme('不存在的主题') == 'dark', "非法主题应回退默认"

    # 关键控件样式必须齐备：浅色下缺样式会出现看不清的默认控件
    for selector in ('QSlider::handle:horizontal', 'QScrollBar::handle:vertical',
                     'QToolTip', 'QComboBox QAbstractItemView',
                     'QPushButton#primary', 'QGroupBox::title'):
        assert selector in light_qss, f"主题缺少控件样式: {selector}"
        assert selector in dark_qss, f"深色主题缺少控件样式: {selector}"

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'config.ini')
        cm = ConfigManager(path)
        assert cm.get_theme() == 'dark', "新配置默认应为深色主题"
        cm.set_theme('light')
        assert cm.get_theme() == 'light'
        cm.set_theme('bogus')
        assert cm.get_theme() == 'dark', "非法主题写入后读回应回退"
        with open(path, encoding='utf-8') as f:
            assert '[UI]' in f.read(), "配置里未生成 [UI] 段"
    print("✅ 主题系统测试通过\n")


def test_config_batch_and_sections():
    """配置写盘：批量上下文只落一次盘；写某段选项不得重置其它段（含已修复的回归）"""
    print("=== 测试配置批量写盘与配置段隔离 ===")
    import tempfile
    from config import ConfigManager

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'config.ini')
        # 故意不写 [OCR]：命中"段缺失时补默认值"的分支
        with open(path, 'w', encoding='utf-8') as f:
            f.write('[Translation]\napi_type = edge_free\n\n'
                    '[Overlay]\nsnap_threshold = 40\ndim_opacity = 20\n')
        cm = ConfigManager(path)

        real_write = cm._write_config
        writes = []
        cm._write_config = lambda: writes.append(1) or real_write()
        with cm.batch():
            cm.set_api_type('baidu')
            cm.set_target_lang('ja')
            cm.set_overlay_option('snap_threshold', '30')
        assert len(writes) == 1, f"批量上下文应只落盘一次，实际 {len(writes)}"
        assert cm.get_api_type() == 'baidu' and cm.get_target_lang() == 'ja'

        # 回归：写 OCR 选项时不得把用户调好的遮罩参数重置为默认值
        cm.set_ocr_option('tesseract_langs', 'eng')
        assert cm.get_overlay_option('snap_threshold') == '30', "写OCR选项重置了磁吸阈值"
        assert cm.get_overlay_option('dim_opacity') == '20', "写OCR选项重置了暗角透明度"

        # 扩展 provider 的通用读写通道
        cm.set_translation_option('probe_custom_field', '自定义值')
        assert cm.get_translation_option('probe_custom_field') == '自定义值'
        cm2 = ConfigManager(path)
        assert cm2.get_translation_option('probe_custom_field') == '自定义值', "通用字段未落盘"

        leftovers = [n for n in os.listdir(tmp) if n.endswith('.tmp')]
        assert not leftovers, f"原子写盘残留临时文件: {leftovers}"
    print("✅ 配置批量写盘与配置段隔离测试通过\n")


def test_non_retryable_error():
    """配置类错误不重试：否则每次缺失密钥都要白等两次退避（约3秒）"""
    print("=== 测试不可重试错误 ===")
    import time
    from managers.retry_handler import NonRetryableError, RetryHandler

    calls = []

    def boom():
        calls.append(1)
        raise NonRetryableError("API密钥未设置")

    handler = RetryHandler(max_attempts=3, base_delay=1.0)
    start = time.time()
    result = handler.execute_with_retry(boom, fallback_result="降级结果")
    elapsed = time.time() - start
    assert len(calls) == 1, f"不可重试错误被重试了 {len(calls)} 次"
    assert elapsed < 0.3, f"不可重试错误等待了 {elapsed:.2f}s"
    assert result == "降级结果"

    # 普通异常仍然要重试（网络抖动场景）
    retry_calls = []

    def flaky():
        retry_calls.append(1)
        raise RuntimeError("网络抖动")

    RetryHandler(max_attempts=2, base_delay=0.01).execute_with_retry(
        flaky, fallback_result="降级")
    assert len(retry_calls) == 2, f"普通异常应重试: {len(retry_calls)}"

    # 配置缺失走 ProviderConfigError（继承 NonRetryableError）
    from managers.provider_common import ProviderConfigError
    assert issubclass(ProviderConfigError, NonRetryableError)
    print("✅ 不可重试错误测试通过\n")


def test_pin_window_keys():
    """钉图窗口任意按键都不得抛异常（历史上误用 event.Copy 会直接抛 AttributeError）"""
    print("=== 测试钉图窗口按键 ===")
    from PyQt5.QtCore import QEvent, Qt
    from PyQt5.QtGui import QKeyEvent, QPixmap
    from PyQt5.QtWidgets import QApplication
    from managers.pin_window import PinWindow

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    pixmap = QPixmap(60, 40)
    pixmap.fill(Qt.red)
    pin = PinWindow(pixmap)
    try:
        for key in (Qt.Key_A, Qt.Key_Z, Qt.Key_Return, Qt.Key_C):
            pin.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))
        pin.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier))
        clipboard = QApplication.clipboard()
        assert not clipboard.pixmap().isNull(), "Ctrl+C 应把钉住的图放进剪贴板"
    finally:
        pin.close()
    print("✅ 钉图窗口按键测试通过\n")


def test_qt_dispatcher():
    """测试Qt线程调度器"""
    print("=== 测试Qt线程调度器 ===")
    from managers.qt_dispatcher import qt_dispatcher
    import threading

    def test_function():
        return f"在线程 {threading.get_ident()} 中执行"

    # 测试调度功能
    result = qt_dispatcher.dispatch_to_main_thread(test_function)
    print(f"调度结果: {result}")
    print("✅ Qt线程调度器测试通过\n")

def test_ocr_notice_not_translated():
    """OCR 降级提示文本不应被送翻译（图里没字/引擎不可用时直接展示提示）"""
    print("=== 测试OCR提示文本不送翻译 ===")
    from managers.ocr import is_ocr_notice

    assert is_ocr_notice("未识别到文字，请重新框选包含文字的区域")
    assert is_ocr_notice(
        "[OCR识别不可用：请检查OCR引擎配置（Tesseract未安装或密钥未填写）]")
    assert is_ocr_notice("  未识别到文字，请重新框选包含文字的区域  ")
    assert not is_ocr_notice("Hello World")
    assert not is_ocr_notice("")
    assert not is_ocr_notice(None)

    # 端到端：ScreenshotManager 收到提示文本时不调用翻译API
    from managers.screenshot import ScreenshotManager

    class RecordingTM:
        target_lang = 'zh'

        def __init__(self):
            self.calls = []

        def perform_translation(self, text, force_refresh=False,
                                target_lang=None):
            self.calls.append(text)
            return f"[译文]{text}"

    sm = ScreenshotManager()
    sm.translation_manager = RecordingTM()
    result = sm._translate_all(
        "未识别到文字，请重新框选包含文字的区域", 'zh')
    # _translate_all 只负责真实文本；提示文本应在 _process_region 层拦截，
    # 但这里直接调 _translate_all 应正常翻译（防御性检查：不影响真实文本）
    assert result == "[译文]未识别到文字，请重新框选包含文字的区域"

    # _process_region 层的拦截：提示文本完全不进翻译阶段。
    # 用假 OCR 返回提示文本，记录翻译调用次数。
    from PyQt5.QtCore import QRect
    from PIL import Image

    class FakeOCR:
        def perform_ocr_with_blocks(self, image):
            return "未识别到文字，请重新框选包含文字的区域", []

    sm.ocr_manager = FakeOCR()
    sm.translation_manager = RecordingTM()   # 重新计数（前面防御性检查调用过一次）

    class StubWidget:
        _id = 0

        def current_request_id(self):
            return 1

    img = Image.new('RGB', (40, 20), 'white')
    sm._process_region(StubWidget(), img, QRect(0, 0, 40, 20), 'zh', False, 1)
    assert len(sm.translation_manager.calls) == 0, (
        f"提示文本不应触发翻译API: {sm.translation_manager.calls}")
    print("✅ OCR提示文本不送翻译测试通过\n")


def test_tray_debounce():
    """托盘单击/双击防抖：双击只开设置，第二击迟到也不误触发截图"""
    print("=== 测试托盘防抖 ===")
    import time as _time
    from managers.tray import SystemTrayManager

    class FakeShot:
        def __init__(self):
            self.started = 0

        def start_screenshot(self):
            self.started += 1

    class FakeApp:
        def __init__(self):
            self.config_opened = 0

        def open_config_window(self):
            self.config_opened += 1

    tray = SystemTrayManager(main_app=FakeApp(), screenshot_manager=FakeShot())
    tray._click_window = 0.12

    # 双击：两次快速激活
    tray._on_tray_activate()
    tray._on_tray_activate()
    _time.sleep(0.3)
    assert tray.main_app.config_opened == 1, "双击应打开设置一次"
    assert tray.screenshot_manager.started == 0, "双击不应触发截图"

    # 单击：一次激活后等待
    tray._on_tray_activate()
    _time.sleep(0.3)
    assert tray.screenshot_manager.started == 1, "单击应触发截图"
    assert tray.main_app.config_opened == 1

    # 边界竞态：第二击先到（更新了 _last_activate_ts），定时器随后触发。
    # _fire_single_click 应发现“武装之后又有激活”而改开设置，
    # 而不是把第二击误判为新一轮单击导致“截图开了又关”
    tray._on_tray_activate()              # 第一击：武装定时器
    _time.sleep(0.06)
    tray._on_tray_activate()              # 第二击落在防抖窗口内：开设置
    _time.sleep(0.3)
    assert tray.screenshot_manager.started == 1
    assert tray.main_app.config_opened == 2
    print("✅ 托盘防抖测试通过\n")


def main():
    """主测试函数"""
    print("开始功能测试...\n")

    try:
        test_retry_handler()
        test_cache_manager()
        test_screenshot_widget()
        test_result_render_layout()
        test_cover_pad_adaptive()
        test_inplace_cover_pixel_alignment()
        test_result_render_cache()
        test_ocr_provider_reuse()
        test_app_paths_user_data()
        test_tesseract_no_console_window()
        test_theme_system()
        test_config_batch_and_sections()
        test_non_retryable_error()
        test_pin_window_keys()
        test_qt_dispatcher()
        test_ocr_notice_not_translated()
        test_tray_debounce()

        print("🎉 所有测试通过！新功能工作正常。")

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)