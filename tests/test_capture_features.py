#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「仅截图」升级专项测试：标注烧录、编辑态、放大镜、输出、滚动截图、
OCR 取字、新增快捷键与配置。

需要 QApplication 的用例统一用 conftest 的 qapp fixture（会话级唯一实例）。
"""

import os
import threading

import pytest
from PIL import Image, ImageDraw

from PyQt5.QtCore import QPoint, QPointF, QRect, Qt
from PyQt5.QtGui import QImage, QPainter, QPixmap

from managers import capture_magnifier, capture_output, capture_scroll
from managers import capture_ocr
from managers.capture_annotation import Shape, TOOLS
from managers.capture_editor import CaptureEditor
from managers.capture_toolbar import CaptureToolbar
from managers import vector_icons


# ---------------------------------------------------------------- 标注图元

def test_all_shape_kinds_draw(qapp):
    """十种工具里除 select 外都能离屏绘制；马赛克带真实底图"""
    source = Image.new('RGB', (300, 200), (30, 60, 90))
    shapes = [
        Shape('rect', [QPointF(5, 5), QPointF(80, 60)]),
        Shape('ellipse', [QPointF(10, 10), QPointF(90, 70)]),
        Shape('line', [QPointF(0, 0), QPointF(100, 100)]),
        Shape('arrow', [QPointF(0, 100), QPointF(100, 0)]),
        Shape('pen', [QPointF(1, 1), QPointF(20, 30), QPointF(40, 10)]),
        Shape('highlight', [QPointF(2, 2), QPointF(60, 40), QPointF(90, 20)]),
        Shape('text', [QPointF(10, 10)], text='中文标注'),
        Shape('step', [QPointF(50, 50)], number=7),
        Shape('mosaic', [QPointF(20, 20), QPointF(90, 80)]),
    ]
    assert len(shapes) == len(TOOLS) - 2, '橡皮擦是即时工具，不产生图元'
    pixmap = QPixmap(300, 200)
    pixmap.fill()
    painter = QPainter(pixmap)
    for shape in shapes:
        shape.draw(painter, draw_scale=1.0, crop_scale=1.0, source=source)
    painter.end()
    assert not pixmap.isNull()
    assert shapes[-1]._mosaic_cache is not None, '马赛克没有生成贴图缓存'


def test_shape_translation():
    shape = Shape('rect', [QPointF(10, 10), QPointF(50, 50)])
    moved = shape.translated(7, -3)
    assert moved.points[0] == QPointF(17, 7)
    assert moved.points[1] == QPointF(57, 47)
    assert shape.points[0] == QPointF(10, 10), '平移不该改动原图元'


def test_all_tool_icons_render(qapp):
    for name in TOOLS:
        assert not vector_icons.icon(name).isNull(), name
    for name in ('text_annot', 'eyedropper', 'undo', 'redo', 'clear',
                 'ocr', 'scroll', 'pin'):
        assert not vector_icons.icon(name).isNull(), name


# ---------------------------------------------------------------- 编辑控制器

class _Host:
    """CaptureEditor 的最小宿主（不真开窗口）"""

    def __init__(self, width=200, height=120, scale=1.0):
        self._result_rect = QRect(10, 10, width, height)
        self._scale = scale
        self._grab_scale = 1.0
        self.full_screen = Image.new('RGB', (400, 300), (10, 20, 30))
        self._result = {'image': Image.new('RGB', (width, height), 'white')}
        self.updates = 0

    def update(self):
        self.updates += 1

    def setFocus(self):
        pass

    def rect(self):
        return QRect(0, 0, 400, 300)


def _draw_rect(editor, start=(20, 20), end=(80, 60)):
    editor.set_tool('rect')
    assert editor.mouse_press(QPointF(*start), Qt.LeftButton) is True
    editor.mouse_move(QPointF(*end))
    assert editor.mouse_release(QPointF(*end), Qt.LeftButton) is True


def test_editor_undo_redo_clear(qapp):
    host = _Host()
    editor = CaptureEditor(host)
    _draw_rect(editor)
    assert len(editor.shapes) == 1
    assert editor.undo() and len(editor.shapes) == 0
    assert editor.redo() and len(editor.shapes) == 1
    editor.clear()
    assert editor.shapes == []
    # 新操作会清掉重做栈，但清空本身必须可撤销
    assert editor.redo() is False
    assert editor.undo() and len(editor.shapes) == 1, '清空应可撤销'


def test_editor_select_tool_yields_to_host(qapp):
    """select 工具不接管鼠标，事件要交回宿主做选区拖动"""
    editor = CaptureEditor(_Host())
    editor.set_tool('select')
    assert editor.mouse_press(QPointF(30, 30), Qt.LeftButton) is False
    assert editor.mouse_move(QPointF(40, 40)) is False


def test_editor_tiny_drag_is_ignored(qapp):
    editor = CaptureEditor(_Host())
    editor.set_tool('rect')
    editor.mouse_press(QPointF(30, 30), Qt.LeftButton)
    editor.mouse_release(QPointF(31, 31), Qt.LeftButton)
    assert editor.shapes == [], '小于 3px 的误触不该落笔'


def test_editor_last_shape_can_move_and_scale(qapp):
    """最近一次落笔的图元可以拖动移动、拖手柄缩放，点别处取消选中"""
    editor = CaptureEditor(_Host())
    _draw_rect(editor, (20, 20), (80, 60))      # 局部 (10,10)-(70,50)
    assert editor._selected == 0, '落笔后应自动选中'
    start = editor.shapes[0].points[0]

    # 从图元上边拖动（避开手柄）→ 整体平移
    editor.mouse_press(QPointF(35, 20), Qt.LeftButton)
    editor.mouse_move(QPointF(45, 30))
    editor.mouse_release(QPointF(45, 30), Qt.LeftButton)
    assert editor.shapes[0].points[0].x() == start.x() + 10
    assert editor.shapes[0].points[0].y() == start.y() + 10

    # 拖右下角手柄 → 放大（bounds 是选区局部坐标，测试的对话框原点在 (10,10)）
    box = editor.shapes[0].bounds()
    handle = QPointF(box.right() + 10, box.bottom() + 10)
    editor.mouse_press(handle, Qt.LeftButton)
    editor.mouse_move(QPointF(handle.x() + 20, handle.y() + 20))
    editor.mouse_release(QPointF(handle.x() + 20, handle.y() + 20),
                         Qt.LeftButton)
    assert editor.shapes[0].bounds().width() > box.width()
    assert len(editor.shapes) == 1, '拖手柄不应多出一个图元'

    # 左键点到别处 → 取消选中（单击不落笔）
    editor.mouse_press(QPointF(150, 100), Qt.LeftButton)
    editor.mouse_release(QPointF(150, 100), Qt.LeftButton)
    assert editor._selected is None
    assert len(editor.shapes) == 1, '单击空白不应误落笔'


def test_editor_click_shape_reselects_and_drags(qapp):
    """取消选中后，点一下已有标注又能选中并拖走"""
    host = _Host()
    editor = CaptureEditor(host)
    _draw_rect(editor, (20, 20), (80, 60))
    ox, oy = host._result_rect.left(), host._result_rect.top()

    editor.mouse_press(QPointF(150, 100), Qt.LeftButton)
    editor.mouse_release(QPointF(150, 100), Qt.LeftButton)
    assert editor._selected is None

    # bounds 是选区局部坐标，按下要换回屏幕坐标
    box = editor.shapes[0].bounds()
    grab = QPointF(box.left() + box.width() * 0.3 + ox, box.top() + oy)
    assert editor.mouse_press(grab, Qt.LeftButton) is True
    assert editor._selected == 0, '点已有标注应重新选中'
    editor.mouse_move(QPointF(grab.x() + 8, grab.y() + 6))
    editor.mouse_release(QPointF(grab.x() + 8, grab.y() + 6), Qt.LeftButton)
    assert editor.shapes[0].bounds().top() > box.top(), '选中后应能拖走'
    assert len(editor.shapes) == 1, '点标注不该再多画一个'


def test_editor_click_without_drag_adds_no_undo(qapp):
    """在标注上单击（没拖）不该留一步空撤销"""
    host = _Host()
    editor = CaptureEditor(host)
    _draw_rect(editor)
    depth = len(editor.undo_stack)
    box = editor.shapes[0].bounds()
    grab = QPointF(box.left() + box.width() * 0.3 + host._result_rect.left(),
                   box.top() + host._result_rect.top())
    editor.mouse_press(grab, Qt.LeftButton)
    editor.mouse_release(grab, Qt.LeftButton)
    assert len(editor.undo_stack) == depth, '单击留下了空的撤销步'


def test_editor_select_tool_keeps_region_drag_alive(qapp):
    """select 工具下即使还有选中图元，也不能吞掉宿主的选区拖动"""
    editor = CaptureEditor(_Host())
    _draw_rect(editor)
    editor.set_tool('select')
    editor._selected = 0        # 模拟残留选中
    assert editor.mouse_press(QPointF(150, 100), Qt.LeftButton) is False
    assert editor._selected is None, '点空白应取消选中'
    assert editor.mouse_move(QPointF(160, 110)) is False, \
        'select 工具下编辑器不能接管移动事件'


def test_editor_eraser_removes_shape(qapp):
    """橡皮擦擦掉自己画的标注，且整次擦除可一步撤销"""
    editor = CaptureEditor(_Host())
    _draw_rect(editor, (20, 20), (80, 60))
    assert len(editor.shapes) == 1
    editor.set_tool('eraser')
    editor.mouse_press(QPointF(20, 20), Qt.LeftButton)
    editor.mouse_release(QPointF(20, 20), Qt.LeftButton)
    assert editor.shapes == [], '橡皮擦没有擦掉标注'
    assert editor.undo() and len(editor.shapes) == 1


def test_editor_wheel_adjusts_width_and_font(qapp):
    """滚轮调选中图元的线宽，文字图元则调字号"""
    editor = CaptureEditor(_Host())
    _draw_rect(editor)
    before = editor.shapes[0].width
    editor.wheel(-1)
    assert editor.shapes[0].width < before, '滚轮没有调细线宽'
    assert editor.width == editor.shapes[0].width, '默认线宽没有跟着同步'

    editor.shapes = [Shape('text', [QPointF(5, 5)], text='x', font_size=18)]
    editor._selected = 0
    editor.wheel(1)
    assert editor.shapes[0].font_size > 18, '滚轮没有放大字号'


def test_editor_translation_moves_undo_snapshots(qapp):
    """平移后撤销不能让标注跳回原位"""
    editor = CaptureEditor(_Host())
    _draw_rect(editor)
    # 第二笔要错开：点到已有标注的边框会被选中（方便回头再调），不会另起一笔
    _draw_rect(editor, (100, 100), (150, 130))
    before = editor.shapes[0].points[0].x()
    editor.apply_translation(9, 4)
    assert editor.shapes[0].points[0].x() == before + 9
    assert editor.undo() is True
    # 撤销恢复的快照同样带平移
    assert editor.shapes[0].points[0].x() == before + 9


def test_editor_reset_for_new_selection(qapp):
    editor = CaptureEditor(_Host())
    _draw_rect(editor)
    editor.reset_for_new_selection()
    assert editor.shapes == [] and editor.undo_stack == []
    assert editor.undo() is False


def test_finish_pixmap_burns_annotations(qapp):
    """合成图必须与纯底图不同（标注确实烧录进像素）"""
    host = _Host(width=120, height=80)
    editor = CaptureEditor(host)
    plain = editor.finish_pixmap().toImage()
    _draw_rect(editor, (10, 10), (80, 60))
    composed = editor.finish_pixmap()
    assert composed is not None and not composed.isNull()
    assert composed.toImage() != plain, '标注没有烧录进导出图'


def test_finish_pixmap_high_dpr(qapp):
    """高分屏倍率下导出画布仍是完整物理像素"""
    host = _Host(width=100, height=60, scale=2.0)
    editor = CaptureEditor(host)
    _draw_rect(editor, (5, 5), (60, 40))
    pixmap = editor.finish_pixmap()
    assert pixmap.width() == 100 and pixmap.height() == 60


# ---------------------------------------------------------------- 放大镜/取色

def test_magnifier_pixel_and_color(qapp):
    source = Image.new('RGB', (100, 80), (10, 20, 30))
    assert capture_magnifier.pixel_at(source, 5, 5, 1.0) == (10, 20, 30)
    assert capture_magnifier.pixel_at(source, 50, 40, 2.0) is None, '越界应返回 None'
    assert capture_magnifier.color_hex((10, 20, 30)) == '#0a141e'
    assert capture_magnifier.color_hex(None) == ''


def test_magnifier_draws(qapp):
    source = Image.new('RGB', (400, 300), (10, 20, 30))
    pixmap = QPixmap(400, 300)
    pixmap.fill()
    painter = QPainter(pixmap)
    rect = capture_magnifier.draw_magnifier(
        painter, QPointF(50, 40), source, 1.0, pixmap.rect())
    painter.end()
    assert rect is not None and not rect.isEmpty()


# ---------------------------------------------------------------- 输出

def test_output_filename_template():
    assert capture_output.build_filename('截图_%Y%m%d_%H%M%S', 'png').endswith('.png')
    assert capture_output.build_filename(' shot ', 'jpg').endswith('.jpg')
    # 模板自带扩展名时不重复
    assert capture_output.build_filename('abc.png', 'png') == 'abc.png'


def test_output_quick_save_png_and_jpg(qapp, tmp_path):
    pixmap = QPixmap(40, 30)
    pixmap.fill(Qt.red)
    path = capture_output.save_pixmap(
        pixmap, fmt='png', directory=str(tmp_path), template='cap_%Y%m%d',
        quick=True)
    assert path and os.path.exists(path) and path.endswith('.png')
    jpg = capture_output.save_pixmap(
        pixmap, fmt='jpg', quality=80, directory=str(tmp_path),
        template='cap_jpg', quick=True)
    assert jpg and os.path.exists(jpg) and jpg.lower().endswith('.jpg')


def test_output_unknown_format_falls_back(qapp, tmp_path):
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.blue)
    path = capture_output.save_pixmap(
        pixmap, fmt='bmp', directory=str(tmp_path), template='cap', quick=True)
    assert path and path.endswith('.png')


def test_output_empty_pixmap_returns_none(tmp_path):
    assert capture_output.save_pixmap(None, quick=True) is None


# ---------------------------------------------------------------- 滚动截图

def _page():
    page = Image.new('RGB', (200, 600), 'white')
    draw = ImageDraw.Draw(page)
    for y in range(0, 600, 20):
        draw.line((0, y, 200, y), fill=(200 + y // 20, 30, 40), width=2)
        draw.text((10, y + 4), f'line {y // 20}', fill=(0, 0, 0))
    return page


def test_scroll_overlap_and_stitch():
    page = _page()
    frames = [page.crop((0, y, 200, y + 200)) for y in (0, 60, 120, 180)]
    assert capture_scroll.frame_diff(frames[0], frames[0]) == 0.0
    assert capture_scroll.frame_diff(frames[0], frames[1]) > 0.8
    for previous, current in zip(frames, frames[1:]):
        assert capture_scroll.find_overlap(previous, current) == 140
    assert capture_scroll.find_overlap(frames[0], None) is None
    stitched = capture_scroll.stitch_segments([
        (frames[0], 200), (frames[1].crop((0, 140, 200, 200)), 60)])
    assert stitched.size == (200, 260)


def test_scroll_end_to_end_with_injected_grabber():
    page = _page()
    frames = [page.crop((0, y, 200, y + 200)) for y in (0, 60, 120, 180)]
    # 前 3 次返回滚动后的帧，之后重复最后一帧表示到底
    sequence = iter(frames[1:] + [frames[-1], frames[-1]])
    context = {'crop_box': (0, 0, 200, 200), 'all_screens': False,
               'center': (100, 100)}
    long_image = capture_scroll.capture_scrolling(
        context, frames[0], delay_ms=0, step=1, max_height=5000,
        grabber=lambda _ctx: next(sequence, frames[-1]),
        scroller=lambda: True)
    assert long_image is not None
    assert long_image.height >= 380, '没有把滚动新增的内容拼上来'


def test_scroll_cancel_stops_immediately():
    frame = Image.new('RGB', (100, 80), 'white')
    cancel = threading.Event()
    cancel.set()
    context = {'crop_box': (0, 0, 100, 80), 'all_screens': False,
               'center': (10, 10)}
    long_image = capture_scroll.capture_scrolling(
        context, frame, delay_ms=0, cancel_event=cancel,
        grabber=lambda _ctx: frame, scroller=lambda: True)
    assert long_image is not None and long_image.height == 80


def test_scroll_max_height_respected():
    page = _page()
    frame = page.crop((0, 0, 200, 200))
    nxt = page.crop((0, 60, 200, 260))
    context = {'crop_box': (0, 0, 200, 200), 'all_screens': False,
               'center': (100, 100)}
    long_image = capture_scroll.capture_scrolling(
        context, frame, delay_ms=0, max_height=210,
        grabber=lambda _ctx: nxt, scroller=lambda: True)
    assert long_image.height <= 300


# ---------------------------------------------------------------- OCR 取字

def test_ocr_extract_text_orders_blocks():
    from managers.text_block import TextBlock

    class FakeOCR:
        def perform_ocr_with_blocks(self, image):
            return '乱序', [TextBlock('第二行', 0, 50, 100, 20),
                            TextBlock('第一行', 0, 10, 100, 20)]

    text = capture_ocr.extract_text(Image.new('RGB', (10, 10)), FakeOCR())
    assert text == '第一行\n第二行'


def test_ocr_notice_is_filtered():
    from managers.ocr import NO_TEXT_NOTICE

    class EmptyOCR:
        def perform_ocr_with_blocks(self, image):
            return NO_TEXT_NOTICE, []

    assert capture_ocr.extract_text(Image.new('RGB', (10, 10)), EmptyOCR()) == ''


def test_ocr_failure_prefix_is_filtered():
    class FailOCR:
        def perform_ocr_with_blocks(self, image):
            return '[OCR识别失败: 组件缺失]', []

    assert capture_ocr.extract_text(Image.new('RGB', (10, 10)), FailOCR()) == ''


# ---------------------------------------------------------------- 快捷键

def test_new_hotkey_actions_route_correctly(qapp):
    from managers.hotkey import HotkeyManager
    from managers.hotkey_native import (
        HOTKEY_ID, HOTKEY_ID_CAPTURE, HOTKEY_ID_TEXT,
        HOTKEY_ID_FULLSCREEN, HOTKEY_ID_WINDOW, HOTKEY_ID_REPEAT)

    assert len({HOTKEY_ID, HOTKEY_ID_CAPTURE, HOTKEY_ID_TEXT,
                HOTKEY_ID_FULLSCREEN, HOTKEY_ID_WINDOW, HOTKEY_ID_REPEAT}) == 6

    expected = {
        'fullscreen': ('ctrl+alt+f', 'start_capture_fullscreen'),
        'window': ('ctrl+alt+w', 'start_capture_window'),
        'repeat': ('ctrl+alt+r', 'start_capture_repeat'),
    }
    for action, (default, method) in expected.items():
        class Shot:
            def __init__(self):
                self.calls = []

            def __getattr__(self, name):
                return lambda *a, **k: self.calls.append(name)

        shot = Shot()
        manager = HotkeyManager(shot, None, action=action)
        assert manager._default_hotkey() == default
        manager._last_fire_ts = 0
        manager._fire()
        assert shot.calls == [method]


def test_unknown_hotkey_action_rejected(qapp):
    from managers.hotkey import HotkeyManager
    with pytest.raises(ValueError):
        HotkeyManager(None, None, action='nope')


# ---------------------------------------------------------------- 配置

def test_capture_config_defaults_and_percent_template(tmp_path):
    from config import ConfigManager
    path = str(tmp_path / 'config.ini')
    config = ConfigManager(path)
    assert config.get_capture_option('format') == 'png'
    assert config.get_capture_option('filename_template') == '截图_%Y%m%d_%H%M%S'
    assert config.get_fullscreen_hotkey() == 'ctrl+alt+f'
    assert config.get_window_hotkey() == 'ctrl+alt+w'
    assert config.get_repeat_hotkey() == 'ctrl+alt+r'

    config.set_capture_option('format', 'jpg')
    config.set_repeat_hotkey('ctrl+alt+q')
    config.save_config()
    reloaded = ConfigManager(path)
    assert reloaded.get_capture_option('format') == 'jpg'
    assert reloaded.get_repeat_hotkey() == 'ctrl+alt+q'
    assert reloaded.get_capture_option('filename_template') == '截图_%Y%m%d_%H%M%S'


def test_capture_section_migrates_old_config(tmp_path):
    """老 config.ini 缺 [Capture] 段时自动补齐，setter 不会 NoSectionError"""
    from config import ConfigManager
    path = tmp_path / 'config.ini'
    path.write_text('[Hotkey]\nglobal_hotkey = alt+z\n', encoding='utf-8')
    config = ConfigManager(str(path))
    assert config.config.has_section('Capture')
    config.set_capture_option('format', 'webp')
    assert ConfigManager(str(path)).get_capture_option('format') == 'webp'


# ---------------------------------------------------------------- 遮罩层

def test_capture_toolbar_signals_and_fit(qapp):
    toolbar = CaptureToolbar(None)
    wide = toolbar.fit_width(2000)
    narrow = toolbar.fit_width(400)
    assert wide > 0 and narrow <= wide, '窄屏折叠没有生效'
    tools = []
    toolbar.tool_changed.connect(lambda key: tools.append(key))
    toolbar._tool_buttons['arrow'].click()
    assert tools == ['arrow']
    assert toolbar._tool_buttons['arrow'].isChecked()


def test_toolbar_combos_fixed_width_and_color_dropdown(qapp):
    """线宽/字号下拉定宽：两个同宽、约 2 个汉字且不裁字；颜色是带色块的下拉"""
    toolbar = CaptureToolbar(None)
    han = toolbar.width_combo.fontMetrics().horizontalAdvance('简') * 2
    for combo in (toolbar.width_combo, toolbar.font_combo):
        assert combo.width() == toolbar.width_combo.width(), '两个下拉宽度不一致'
        assert combo.width() >= han, '下拉没有 2 个汉字宽'
        assert combo.width() <= han + 40, '下拉过宽，不再紧凑'
        assert combo.sizeHint().width() <= combo.width(), '下拉文本被裁切'

    combo = toolbar.color_combo
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert texts[-1].startswith('自定义'), '缺少自定义颜色项'
    assert not combo.itemIcon(0).isNull(), '预设色缺少色块图标'
    # 自定义色作为新条目插入并选中
    toolbar.set_color('#123456')
    assert combo.currentData() == '#123456'
    assert combo.currentIndex() < combo.count() - 1, '自定义颜色插到了末尾'


def test_overlay_toolbar_language_combo_fixed_width(qapp):
    from managers.overlay_toolbar import OverlayToolbar
    toolbar = OverlayToolbar(None, [('zh', '简体中文'), ('en', '英语')])
    combo = toolbar.lang_combo
    expect = combo.fontMetrics().horizontalAdvance('简') * 2 + 28
    assert combo.width() == expect
    # 收起宽度固定，但弹出列表要放得下完整语言名
    assert combo.view().minimumWidth() >= expect
    assert combo.toolTip(), '定宽后需要 tooltip 补偿显示'


def test_window_hover_picks_smallest_rect(qapp):
    from managers.screenshot import ScreenshotWidget
    widget = ScreenshotWidget(capture_only=True)
    try:
        widget._snap_window_rects = [(0, 0, 300, 300), (10, 10, 100, 100)]
        widget._update_window_hover(QPoint(20, 20))
        assert widget._window_hover_rect == QRect(10, 10, 90, 90)
        widget._update_window_hover(QPoint(290, 290))
        assert widget._window_hover_rect == QRect(0, 0, 300, 300)
        widget._update_window_hover(QPoint(390, 390))
        assert widget._window_hover_rect is None
    finally:
        widget.close()


def test_preset_fullscreen_and_repeat_region(qapp):
    from managers.screenshot import ScreenshotWidget
    widget = ScreenshotWidget(capture_only=True)
    try:
        widget.full_screen = Image.new('RGB', (200, 120), 'white')
        widget._grab_scale = 1.0
        widget._grab_scale_y = 1.0
        widget.screen_width = 200
        widget.screen_height = 120
        widget._desktop_rect = QRect(0, 0, 200, 120)
        widget._snap_threshold = 0
        widget._apply_preset('fullscreen')
        assert widget.current_payload()['image'].size == (200, 120)
        assert widget._capture_edit is True
    finally:
        widget.close()


def test_region_committed_recorded_by_manager(qapp):
    from managers.screenshot import ScreenshotManager
    manager = ScreenshotManager()
    manager._on_region_committed(QRect(5, 6, 70, 80))
    assert manager._last_region == QRect(5, 6, 70, 80)


def test_widget_mouse_events_route_to_editor(qapp):
    """真从遮罩的鼠标事件走到编辑控制器，验证事件分发链路"""
    from PyQt5.QtTest import QTest
    from managers.screenshot import ScreenshotWidget

    widget = ScreenshotWidget(capture_only=True)
    try:
        widget.full_screen = Image.new('RGB', (200, 120), 'white')
        widget._grab_scale = 1.0
        widget._grab_scale_y = 1.0
        widget.screen_width = 200
        widget.screen_height = 120
        widget._desktop_rect = QRect(0, 0, 200, 120)
        widget._snap_threshold = 0
        widget.begin = QPoint(10, 10)
        widget.end = QPoint(110, 70)
        widget._confirm_selection()

        widget._capture_set_tool('rect')
        QTest.mousePress(widget, Qt.LeftButton, Qt.NoModifier, QPoint(30, 30))
        QTest.mouseMove(widget, QPoint(80, 60))
        QTest.mouseRelease(widget, Qt.LeftButton, Qt.NoModifier, QPoint(80, 60))
        assert len(widget._capture_editor.shapes) == 1, \
            '遮罩没有把鼠标事件转给编辑控制器'

        # 编辑态禁止缩放选区（只允许 move），避免标注与内容错位
        assert widget._begin_result_drag(QPoint(10, 30)) is False
    finally:
        widget.close()


# ---------------------------------------------------------------- 评审回归
# 下面这些用例对应全量评审里逐条复现过的问题，改动前都会失败。


def test_output_jpg_high_dpr_keeps_full_content(qapp, tmp_path):
    """高分屏 dpr>1 时 JPG 铺白底不能只剩左上角 1/倍率² 的内容"""
    pixmap = QPixmap(200, 100)
    pixmap.fill(Qt.red)
    pixmap.setDevicePixelRatio(2.0)
    path = capture_output.save_pixmap(
        pixmap, fmt='jpg', directory=str(tmp_path),
        template='hidpi_dpr', quick=True)
    assert path and path.endswith('.jpg')
    image = QImage(path)
    assert not image.isNull()
    # 右半边曾经是白底，现在必须也是红色
    assert image.pixelColor(150, 50).red() > 200
    assert image.pixelColor(150, 50).green() < 60


def test_editor_escape_only_cancels_text_input(qapp):
    """文字输入框里按 ESC 只放弃这次输入，不能取消整张截图"""
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QWidget

    class Host(QWidget):
        """按 ScreenshotWidget.keyPressEvent 的真实顺序委派"""

        def __init__(self):
            super().__init__()
            self.cancelled = False
            self.editor = None
            self._result_rect = QRect(0, 0, 200, 120)
            self._scale = 1.0
            self._grab_scale = 1.0
            self.full_screen = Image.new('RGB', (200, 120), 'black')
            self._result = {}

        def update(self):
            pass

        def setFocus(self):
            pass

        def keyPressEvent(self, event):
            if self.editor is not None and self.editor.key_press(event):
                return
            if event.key() == Qt.Key_Escape:
                self.cancelled = True

    host = Host()
    editor = CaptureEditor(host)
    host.editor = editor
    try:
        editor.set_tool('text')
        assert editor.mouse_press(QPointF(20, 20), Qt.LeftButton) is True
        assert editor._text_input is not None
        QTest.keyClick(editor._text_input[0], Qt.Key_Escape)
        assert editor._text_input is None, 'ESC 没有关掉文字输入框'
        assert host.cancelled is False, 'ESC 把整张截图取消了'
        assert editor.shapes == [], 'ESC 不该误落笔'
    finally:
        host.close()


def test_mock_ocr_notice_is_recognized_as_notice(qapp):
    """mock 引擎的降级提示必须与判定表一致，不能被当原文或取字结果"""
    from managers.ocr import OCR_NOTICE_TEXTS, is_ocr_notice
    from managers.ocr_providers.mock import MockOCRProvider

    text = MockOCRProvider().recognize(None)
    assert text == OCR_NOTICE_TEXTS[0]
    assert is_ocr_notice(text) is True
    # 各家兜底路径的失败文案按前缀也要认出来
    assert is_ocr_notice('[OCR识别失败：组件缺失]') is True

    class FakeOCR:
        def perform_ocr_with_blocks(self, image):
            return text, []

    assert capture_ocr.extract_text(Image.new('RGB', (10, 10)), FakeOCR()) == ''


def test_mosaic_is_blocky_not_interpolated(qapp):
    """马赛克必须是硬边像素块，不能是默认 BICUBIC 插值后的模糊色"""
    source = Image.new('RGB', (120, 60))
    pixels = source.load()
    for x in range(120):
        for y in range(60):
            pixels[x, y] = (255, 0, 0) if x < 60 else (0, 0, 255)

    shape = Shape('mosaic', [QPointF(0, 0), QPointF(120, 60)])
    canvas = QPixmap(120, 60)
    canvas.fill(Qt.white)
    painter = QPainter(canvas)
    shape.draw(painter, draw_scale=1.0, crop_scale=1.0, source=source)
    painter.end()

    image = canvas.toImage()
    for x in range(120):
        color = image.pixelColor(x, 30)
        pure_red = (color.red() > 200 and color.green() < 60
                    and color.blue() < 60)
        pure_blue = (color.blue() > 200 and color.red() < 60
                     and color.green() < 60)
        assert pure_red or pure_blue, \
            f'x={x} 出现插值色 {color.name()}，马赛克没有用 NEAREST'


def test_pin_window_accepts_keyboard_focus(qapp):
    """钉图窗口必须可聚焦，否则 ESC/Ctrl+C/Ctrl+S 收不到按键"""
    from managers.pin_window import PinWindow

    pixmap = QPixmap(40, 30)
    pixmap.fill(Qt.red)
    pin = PinWindow(pixmap)
    try:
        assert pin.focusPolicy() == Qt.StrongFocus
        # 切换置顶会重建原生窗口：不能因此把窗口藏掉或丢掉焦点策略
        pin.show()
        pin._toggle_top(False)
        assert pin.isVisible(), '切换置顶后窗口被藏起来了'
        assert pin.focusPolicy() == Qt.StrongFocus
    finally:
        pin.close()


def test_semantically_equal_hotkeys_normalize_equal():
    """修饰键别名/顺序不同是同一个组合，重复校验不能只比字面串"""
    from managers.hotkey_native import parse_hotkey

    for first, second in (('ctrl+alt+z', 'alt+ctrl+z'),
                          ('control+alt+z', 'ctrl+alt+z'),
                          ('win+shift+a', 'windows+shift+a')):
        assert parse_hotkey(first) == parse_hotkey(second)


def test_output_filename_template_is_sanitized():
    """文件名模板不能带路径穿越或 Windows 非法字符"""
    import os

    name = capture_output.build_filename('../../evil', 'png')
    assert '/' not in name and '\\' not in name
    assert name == os.path.basename(name)
    assert name.endswith('.png')

    name = capture_output.build_filename('a:b*c?d|e', 'png')
    assert all(char not in name for char in '\\/:*?"<>|')
    assert name.endswith('.png')


def test_magnifier_pixel_handles_grayscale_and_palette(qapp):
    """取色不能依赖整图 convert('RGB')，灰度/调色板模式也要还原成 RGB"""
    gray = Image.new('L', (20, 20), 77)
    assert capture_magnifier.pixel_at(gray, 5, 5, 1.0) == (77, 77, 77)

    palette = Image.new('P', (20, 20), 1)
    palette.putpalette([0, 0, 0, 10, 20, 30] + [0] * (256 * 3 - 6))
    assert capture_magnifier.pixel_at(palette, 5, 5, 1.0) == (10, 20, 30)

    # LA 是双通道：必须还原成 3 通道，否则 color_hex/rgb[2] 会崩
    la = Image.new('LA', (20, 20), (77, 128))
    rgb = capture_magnifier.pixel_at(la, 5, 5, 1.0)
    assert rgb == (77, 77, 77)
    assert capture_magnifier.color_hex(rgb) == '#4d4d4d'


def test_scroll_cancel_banner_sets_event(qapp):
    """滚动截图期间的可点取消横幅要能置位取消事件并正常收起"""
    import threading
    from PyQt5.QtTest import QTest
    from managers.screenshot import ScreenshotWidget

    widget = ScreenshotWidget(capture_only=True)
    try:
        widget._result_rect = QRect(10, 10, 60, 40)
        event = threading.Event()
        widget.show_scroll_banner(event)
        banner = widget._scroll_banner
        assert banner is not None, '小选区下应有可点击的取消横幅'
        QTest.mouseClick(banner, Qt.LeftButton)
        assert event.is_set(), '点击横幅没有置位取消事件'
        widget.hide_scroll_banner()
        assert widget._scroll_banner is None
    finally:
        widget.close()


def test_nested_no_persist_batch_is_rejected(tmp_path):
    """persist=False 嵌套在会落盘的批次里必须显式拒绝，不能静默写盘"""
    from config import ConfigManager

    cm = ConfigManager(str(tmp_path / 'config.ini'))
    with pytest.raises(RuntimeError):
        with cm.batch():
            with cm.batch(persist=False):
                pass


def test_ocr_failure_keeps_error_local(qapp):
    """识别失败的异常只按本次请求返回，不写实例属性（两次截图并发不串台）"""
    from unittest.mock import patch
    from managers.ocr import OCRManager

    class FailingProvider:
        name = 'failing_probe'
        display_name = 'Failing'
        deterministic_results = False
        deadline = None

        def recognize(self, image):
            raise RuntimeError('boom')

    manager = OCRManager()
    with patch.object(manager, '_get_provider', return_value=FailingProvider()):
        result, error, provider_cls = manager._recognize_via(
            FailingProvider, Image.new('RGB', (4, 4)))
    assert error is not None, '本次异常要作为返回值交给调用方'
    assert not hasattr(manager, '_last_error'), '不该再有共享异常状态'


def test_language_switch_during_request_never_shows_source(qapp):
    """忙碌中换语言：回填时不能把原文当译文显示"""
    from managers.translate_window import TranslateWindow

    class FakeMain:
        translation_manager = None
        config_manager = None

    window = TranslateWindow(FakeMain())
    try:
        window.source_edit.setPlainText('Hello world')
        window._busy = True
        window._last_request = ('Hello world', 'zh')
        window._request_id = 1
        window._auto_pending = True      # 换语言时置位
        window._apply_result(1, '你好世界', '')
        shown = window.result_edit.toPlainText()
        assert shown != 'Hello world', '把原文当成译文显示了'
        assert shown in ('', '你好世界')

        # 没有待办时正常回填译文
        window._auto_pending = False
        window._apply_result(1, '你好世界', '')
        assert window.result_edit.toPlainText() == '你好世界'

        # 语言真的变了：旧语言的译文也不能回填，等补译
        en_index = window.lang_combo.findData('en')
        assert en_index >= 0
        window.lang_combo.setCurrentIndex(en_index)
        window._request_id = 2
        window._busy = True
        window._last_request = ('Hello world', 'zh')
        window._auto_pending = True
        window.result_edit.clear()
        window._apply_result(2, '你好世界', '')
        assert window.result_edit.toPlainText() == '', '语言已变却回填了旧译文'
    finally:
        window.close()


def test_scroll_dispatch_failure_clears_token(qapp):
    """滚动截图回投失败后必须作废令牌，否则截图热键会被永久吞掉"""
    from unittest.mock import patch
    from managers.screenshot import ScreenshotManager

    manager = ScreenshotManager()

    class Widget:
        def current_request_id(self):
            return 1

        def set_capture_busy(self, busy):
            pass

        def show_scroll_banner(self, event):
            pass

        def on_scroll_finished(self, image):
            pass

    widget = Widget()
    manager._screenshot_widget = widget
    cancel_event = threading.Event()
    manager._scroll_cancel = cancel_event

    with patch('managers.screenshot.qt_dispatcher') as dispatcher, \
            patch('managers.capture_scroll.capture_scrolling',
                  return_value=None):
        dispatcher.dispatch_to_main_thread.side_effect = RuntimeError('down')
        manager._run_scroll(widget, {}, Image.new('RGB', (8, 8)), 1, {},
                            cancel_event)

    assert manager._scroll_cancel is None, '回投失败后令牌残留'


def test_capture_hotkey_cancels_running_scroll(qapp):
    """滚动截图期间按截图热键应取消长截图，而不是另起一个遮罩"""
    from managers.screenshot import ScreenshotManager

    manager = ScreenshotManager()
    sentinel = object()
    manager._screenshot_widget = sentinel
    cancel_event = threading.Event()
    manager._scroll_cancel = cancel_event

    manager._run_screenshot(capture_only=True)

    assert cancel_event.is_set(), '热键没有取消长截图'
    assert manager._screenshot_widget is sentinel, '滚动期间又新建了遮罩'


def test_capture_watchdog_stops_when_idle(qapp):
    """忙碌态 watchdog 要随结果回投停表，不能一直挂着"""
    from managers.screenshot import ScreenshotWidget

    widget = ScreenshotWidget(capture_only=True)
    try:
        widget.set_capture_busy(True)
        assert widget._capture_watchdog is not None
        assert widget._capture_watchdog.isActive()
        widget.set_capture_busy(False)
        assert not widget._capture_watchdog.isActive()
    finally:
        widget.close()


def test_hotkey_identity_normalizes_aliases(qapp):
    """设置页查重用的标识函数：别名与顺序不同视为同一组合"""
    from main import _hotkey_identity

    assert _hotkey_identity('ctrl+alt+z') == _hotkey_identity('alt+ctrl+z')
    assert _hotkey_identity('control+alt+z') == _hotkey_identity('ctrl+alt+z')
    assert _hotkey_identity('win+shift+a') == _hotkey_identity('windows+shift+a')
    assert _hotkey_identity('') is None
    # 不同组合必须区分开
    assert _hotkey_identity('ctrl+alt+z') != _hotkey_identity('ctrl+alt+y')


def test_nested_no_persist_batches_allowed(tmp_path):
    """同为不落盘的嵌套没有写盘冲突，应当允许"""
    import os
    from config import ConfigManager

    path = str(tmp_path / 'config.ini')
    cm = ConfigManager(path)
    with cm.batch(persist=False):
        cm.set_target_lang('en')
        with cm.batch(persist=False):
            cm.set_minimize_to_tray(False)
    assert cm.get_target_lang() == 'en'
    assert cm.is_minimize_to_tray() is False
    assert not os.path.exists(path), '不落盘的批次仍然写了文件'
