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
from PyQt5.QtGui import QPainter, QPixmap

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
    assert len(shapes) == len(TOOLS) - 1
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


def test_editor_translation_moves_undo_snapshots(qapp):
    """平移后撤销不能让标注跳回原位"""
    editor = CaptureEditor(_Host())
    _draw_rect(editor)
    _draw_rect(editor)
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
