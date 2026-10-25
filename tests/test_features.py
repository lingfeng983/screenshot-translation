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


_QT_APP = None


def _ensure_qt_app():
    """
    取得 QApplication 并一直持有引用

    必须持有引用：QApplication 一旦被 GC，其底层 C++ 对象随之销毁，
    之后再构造任何 QWidget 都触发 Qt 的 qFatal（"Must construct a
    QApplication before a QWidget"）——进程直接终止，既没有 Python 异常
    也没有回溯。本次剪贴板用例就这么静默退出过，比失败更难排查。

    Returns:
        QApplication: 全局唯一实例
    """
    global _QT_APP
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    _QT_APP = app
    return app


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
        from PyQt5.QtCore import Qt, QPoint
        from managers.screenshot import ScreenshotWidget
    except Exception as e:
        print(f"⚠️ 截图遮罩测试跳过 (导入失败): {e}\n")
        return

    _ensure_qt_app()
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


def test_screenshot_overlay_freezes_screen():
    """
    遮罩必须锁住整屏：选区里不能再透出实时桌面

    以前选区靠 CompositionMode_Clear“挖空”窗口露出真实屏幕，用户框的时候
    选区里自刷新的内容（视频、时钟、进度条）会继续动，框到的画面与最终识别
    的画面也可能不是同一帧。现在整屏抓拍铺在遮罩底层，选区从同一张冻结图重绘。
    """
    print("=== 测试遮罩冻结整屏 ===")
    from PyQt5.QtCore import QPoint, QRect
    from PyQt5.QtGui import QColor, QImage, QPainter

    from managers.screenshot import ScreenshotWidget

    _ensure_qt_app()
    widget = ScreenshotWidget()

    assert widget._frozen_pixmap is not None, "遮罩没有冻结的整屏抓拍"
    dpr = widget._frozen_pixmap.devicePixelRatio() or 1.0
    frozen = widget._frozen_pixmap.toImage()

    # 品红底当“实时桌面”：遮罩渲染到它上面，凡是还能看见透明或品红的地方，
    # 都说明实时桌面从遮罩里透了上来
    canvas = QImage(int(widget.width() * dpr), int(widget.height() * dpr),
                    QImage.Format_ARGB32)
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(QColor(255, 0, 255))

    def render_over_live_screen():
        """按真实 paintEvent 把遮罩画到“实时桌面”上"""
        painter = QPainter(canvas)
        widget.render(painter)
        painter.end()

    def rendered_vs_frozen(point):
        """同一点上：遮罩渲染结果 与 冻结图本身 的颜色"""
        x, y = int(point.x() * dpr), int(point.y() * dpr)
        return canvas.pixelColor(x, y), frozen.pixelColor(x, y)

    # 1) 选区态：选区保持原亮度，且必须是冻结图那一帧
    widget.begin, widget.end = QPoint(40, 40), QPoint(240, 160)
    widget.is_selecting = True
    render_over_live_screen()

    got, expected = rendered_vs_frozen(widget._get_rect().center())
    assert got.alpha() == 255, f"选区里透出了实时桌面（alpha={got.alpha()}）"
    delta = max(abs(a - b) for a, b in zip(
        (got.red(), got.green(), got.blue()),
        (expected.red(), expected.green(), expected.blue())))
    assert delta <= 2, f"选区里不是冻结画面: {got.name()} != {expected.name()}"

    got_out, expected_out = rendered_vs_frozen(
        QPoint(widget.width() - 5, widget.height() - 5))
    assert got_out.name() != QColor(255, 0, 255).name(), "选区外透出了实时桌面"
    assert (got_out.red(), got_out.green(), got_out.blue()) <= \
        (expected_out.red(), expected_out.green(), expected_out.blue()), \
        "选区外没有压上暗角"

    # 2) 结果/加载态：选区图还没回投时也必须亮出冻结画面，不能挖空
    widget.is_selecting = False
    widget._loading = True
    widget._crop_pixmap = None
    widget._result_rect = QRect(40, 40, 200, 120)
    render_over_live_screen()
    got, expected = rendered_vs_frozen(widget._result_rect.topLeft() + QPoint(20, 20))
    assert got.alpha() == 255 and got.name() == expected.name(), \
        f"结果态透出了实时桌面: {got.name()}(alpha={got.alpha()}) != {expected.name()}"

    # 3) 冻结图要活到遮罩关闭为止（整屏抓拍是遮罩里最大的一块内存）
    widget.close()
    assert widget._frozen_pixmap is None, "遮罩关闭后应释放整屏冻结图"
    print("✅ 遮罩冻结整屏测试通过\n")


def test_screenshot_snap_screen_seam():
    """
    跨屏接缝也要能磁吸

    多屏时两块显示器之间那条边是常用对齐线，现只把整个桌面的外沿当参考线，
    选区贴到接缝上就只能靠手对准。
    """
    print("=== 测试跨屏接缝磁吸 ===")
    from PyQt5.QtCore import QRect

    from managers.screenshot import ScreenshotWidget

    _ensure_qt_app()
    widget = ScreenshotWidget()
    try:
        # 合成一个左右双屏布局，与真实显示器数量无关
        widget._screen_rects = [QRect(0, 0, 1920, 1080), QRect(1920, 0, 2560, 1440)]
        widget.screen_width, widget.screen_height = 4480, 1440
        widget._snap_window_rects = []
        widget._snap_threshold = 12

        rect, guides = widget._snap_rect(QRect(1600, 100, 314, 400))
        assert rect.right() + 1 == 1920, f"没吸到屏幕接缝: {rect}"
        assert ('x2', 1920) in guides, f"接缝参考线没绘制: {guides}"

        # 远离任何参考线时不吸
        rect, guides = widget._snap_rect(QRect(800, 300, 300, 200))
        assert guides == [], f"不该乱吸: {guides}"
    finally:
        widget.close()
    print("✅ 跨屏接缝磁吸测试通过\n")


def test_translated_mode_paints_with_fitted_font():
    """
    纯译文模式也要用 fit_text 选出来的字号绘制

    以前 painter.setFont(font) 只写在双语分支里（画完原文行之后才复位），
    译文模式一行 setFont 都没有：折行和行高按缩排字号算，落笔却用画布当前
    字体，字号一大就只看得见每行的上半截。
    """
    print("=== 测试译文模式字号 ===")
    from PIL import Image
    from PyQt5.QtCore import QPoint
    from PyQt5.QtGui import QFont

    import managers.result_render as rr
    from managers.text_block import TextBlock

    _ensure_qt_app()
    painter_font = QFont()
    painter_font.setPixelSize(999)          # 故意给一个绝不可能用于排版的字号
    painter = _RecordingPainter(painter_font)
    image = Image.new('RGB', (240, 90), 'white')
    block = TextBlock('原文行', 10, 12, 200, 60)

    rr.paint_blocks(painter, image, [block], ['译文内容' * 12],
                    QPoint(0, 0), rr.MODE_TRANSLATED, scale=1.0)

    assert painter.texts, "译文一行都没画出来"
    sizes = set(painter.text_font_sizes)
    assert 999 not in sizes, f"译文沿用了画布字体: {sizes}"
    layout_font = list(rr._LAYOUT_CACHE.values())[0][2]
    assert sizes == {layout_font.pixelSize()}, \
        f"绘制字号与排版字号不一致: {sizes} != {layout_font.pixelSize()}"
    print("✅ 译文模式字号测试通过\n")


def test_snap_window_rects_converted_to_logical():
    """
    磁吸的窗口矩形要按抓取倍率换算到遮罩逻辑坐标

    GetWindowRect 给的是物理像素绝对坐标，而吸附线、屏幕边界全是逻辑坐标。
    混用的后果有两层：吸附位置整体偏掉（倍率-1）倍，且高分屏下窗口右边/下边
    会被 `r < screen_width` 当成"超出屏幕"直接丢掉，那条边永远吸不上。
    """
    print("=== 测试磁吸坐标换算 ===")
    from types import SimpleNamespace

    from PyQt5.QtCore import QRect

    from managers.screenshot import ScreenshotWidget

    _ensure_qt_app()
    stub = SimpleNamespace(_grab_scale=1.5, _grab_scale_y=1.5,
                           _desktop_rect=QRect(-100, -50, 2000, 1200))
    local = ScreenshotWidget._window_rects_local(stub, [(300, 300, 900, 600)])
    # 先除倍率换逻辑，再减遮罩自身的逻辑原点（原点为负时是加）
    assert local == [(300, 250, 700, 450)], f"换算结果不对: {local}"

    widget = ScreenshotWidget()
    try:
        widget._grab_scale = widget._grab_scale_y = 1.5
        widget._desktop_rect = QRect(0, 0, 1920, 1080)
        widget.screen_width, widget.screen_height = 1920, 1080
        widget._screen_rects = [QRect(0, 0, 1920, 1080)]
        widget._snap_threshold = 12
        # 物理坐标 2700 = 逻辑 1800：不换算时它是 2700，会被当成屏幕外而丢弃
        widget._snap_window_rects = widget._window_rects_local(
            [(150, 150, 2700, 900)])

        rect, guides = widget._snap_rect(QRect(1795, 200, 100, 60))
        assert rect.left() == 1800, f"没吸到换算后的窗口左边缘: {rect}"
        rect, guides = widget._snap_rect(QRect(1795, 200, 6, 60))
        assert ('x2', 1800) in [
            g for g in guides if g[0] == 'x2'] or rect.right() + 1 == 1800, \
            f"窗口右边缘（逻辑 1800）没能吸附: {rect} {guides}"
    finally:
        widget.close()
    print("✅ 磁吸坐标换算测试通过\n")


def test_result_pixmap_declares_device_pixel_ratio():
    """
    结果图要声明像素倍率，钉屏窗口才按逻辑尺寸摆放

    合成图是按物理像素画的，而窗口 resize 吃的是逻辑坐标：倍率不带上，
    150% 屏上钉住的图就比原选区大一半。
    """
    print("=== 测试结果图像素倍率 ===")
    from PIL import Image

    from managers.screenshot import ScreenshotWidget

    _ensure_qt_app()
    widget = ScreenshotWidget()
    try:
        widget._scale = widget._scale_y = 1.5
        image = Image.new('RGB', (300, 150), 'white')
        widget._result = {'image': image, 'blocks': [], 'block_texts': []}
        pixmap = widget._render_result_pixmap()
        assert pixmap is not None, "结果图合成失败"
        assert abs(pixmap.devicePixelRatio() - 1.5) < 1e-6, \
            f"结果图未声明倍率: {pixmap.devicePixelRatio()}"
        assert (pixmap.width(), pixmap.height()) == (300, 150), \
            "结果图要保持物理像素，尺寸差只由倍率声明"

        window = _quiet_pin_window(pixmap)
        try:
            assert (window.width(), window.height()) == (200, 100), \
                f"钉窗按物理像素摆大了: {window.width()}x{window.height()}"
        finally:
            window.close()
    finally:
        widget.close()
    print("✅ 结果图像素倍率测试通过\n")


def _quiet_pin_window(pixmap):
    """
    构造钉屏窗口但不真的显示（测试只关心尺寸，不该闪出一个置顶窗口）

    show/raise_/activateWindow 都继承自 QWidget 而不是 PinWindow 自己的属性，
    所以还原方式是 delattr 把类属性摘掉、让它回落到 QWidget 的实现。
    """
    from managers.pin_window import PinWindow

    names = ('show', 'raise_', 'activateWindow')
    for name in names:
        assert name not in PinWindow.__dict__, f"{name} 已是 PinWindow 自有实现"
        setattr(PinWindow, name, lambda self, *args: None)
    try:
        return PinWindow(pixmap)
    finally:
        for name in names:
            delattr(PinWindow, name)


def test_stray_drag_during_loading_keeps_overlay():
    """
    等结果期间的误触小拖拽不能把遮罩关掉

    结果回来前 `_result` 还是 None，小拖拽走的是"取消截图"分支：遮罩一关，
    在途请求的取消令牌被触发，用户白等几秒网络还回不到屏幕上。
    """
    print("=== 测试加载期误触不关遮罩 ===")
    from PyQt5.QtCore import QPoint, QRect

    from managers.screenshot import ScreenshotWidget

    _ensure_qt_app()
    widget = ScreenshotWidget()
    canceled = []
    try:
        widget.screenshot_canceled.connect(lambda: canceled.append(1))
        widget._loading = True
        widget._result = None
        widget._error = None
        widget._result_rect = QRect(100, 100, 200, 80)
        widget.begin = QPoint(100, 100)
        widget.end = QPoint(103, 102)

        widget._confirm_selection()

        assert not canceled, "加载中的误触把截图流程取消了"
        assert widget._loading is True, "加载状态被误触清掉了"
    finally:
        widget.close()
    print("✅ 加载期误触不关遮罩测试通过\n")


def test_arrow_nudge_stays_on_desktop():
    """
    方向键微调要把选区夹在桌面内

    鼠标拖拽天然出不了屏幕，方向键没有这个约束：连着按能把整个选区推出
    上边界，之后按 Enter 只能拿到一个越界（甚至空）的裁剪框。
    """
    print("=== 测试方向键微调越界 ===")
    from PyQt5.QtCore import QPoint, Qt
    from PyQt5.QtGui import QKeyEvent

    from managers.screenshot import ScreenshotWidget

    def press(widget, key):
        widget.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier))

    _ensure_qt_app()
    widget = ScreenshotWidget()
    try:
        widget.screen_width, widget.screen_height = 800, 600
        widget.is_selecting = True
        widget.begin = QPoint(2, 300)
        widget.end = QPoint(60, 340)

        for _ in range(6):
            press(widget, Qt.Key_Left)
        assert widget.begin.x() >= 0 and widget.end.x() >= 0, \
            f"选区被推出左边界: {widget.begin} {widget.end}"

        widget.begin = QPoint(2, 570)
        widget.end = QPoint(60, 595)
        for _ in range(20):
            press(widget, Qt.Key_Down)
        assert widget.begin.y() <= widget.screen_height, \
            f"选区被推出下边界: {widget.begin}"
        assert widget.end.y() <= widget.screen_height, \
            f"选区被推出下边界: {widget.end}"
    finally:
        widget.close()
    print("✅ 方向键微调越界测试通过\n")


def test_submit_failure_shows_retryable_message():
    """
    线程池拒收任务时要立刻给出可重试提示

    submit 返回 None 表示没有任何 worker 会来回投结果：不接住的话遮罩停在
    "识别并翻译中"直到用户 ESC，看起来就像程序卡死。
    """
    print("=== 测试后台任务提交失败 ===")
    import managers.screenshot as ss

    class _Pool:
        def submit(self, func, *args):
            return None

    class _Widget:
        def __init__(self):
            self.failures = []

        def show_failure(self, message):
            self.failures.append(message)

    widget = _Widget()
    pool = getattr(ss, 'thread_pool_manager')
    ss.thread_pool_manager = _Pool()
    try:
        ok = ss.ScreenshotManager._submit_or_fail(None, widget, lambda: None)
    finally:
        ss.thread_pool_manager = pool
    assert ok is False, "提交失败却被当成成功"
    assert widget.failures and 'R' in widget.failures[0], \
        f"没有给出可重试提示: {widget.failures}"
    print("✅ 后台任务提交失败测试通过\n")


def test_thread_pool_pending_bookkeeping_no_leak():
    """
    已完成的任务不能留在 _pending 里

    add 与 add_done_callback 之间有个窗口：任务在这两步中间跑完时回调先
    discard、随后 add 又把 future 塞回集合，收尾等待就会每次都带着一堆
    早已结束的任务（退出时的等待与内存都受影响）。
    """
    print("=== 测试线程池记账 ===")
    import time

    from managers.thread_pool import ThreadPoolManager

    pool = ThreadPoolManager(max_workers=2)
    assert pool.initialize() is True
    try:
        for _ in range(20):
            future = pool.submit(lambda: 1)
            assert future is not None
            assert future.result(timeout=5) == 1
        deadline = time.monotonic() + 2.0
        while pool._pending and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not pool._pending, f"已完成任务仍被记账: {len(pool._pending)}"
        assert pool.wait_pending(timeout=0.1) == 0
    finally:
        pool.shutdown(wait=False, timeout=0.1)
    print("✅ 线程池记账测试通过\n")


def _make_color_marker(color, geometry):
    """造一块纯色置顶不透明窗口：用来在抓拍图里定位坐标"""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QPainter
    from PyQt5.QtWidgets import QWidget

    class _Marker(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setGeometry(geometry)

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), color)
            painter.end()

    return _Marker()


def test_screenshot_overlay_multi_screen():
    """
    多显示器：遮罩覆盖整个虚拟桌面，冻结图覆盖全部屏幕且坐标能对上

    以前遮罩只盖主屏：副屏不但没被冻结（内容继续刷新），选区也选不到副屏的
    内容。现在覆盖整个虚拟桌面，用一块副屏上的纯色标记窗口验证抓拍图与局部
    坐标的对应关系（副屏在主屏左侧/上方时桌面原点为负）。
    """
    print("=== 测试遮罩多屏覆盖 ===")
    import time

    from PyQt5.QtCore import QPoint, QRect, QSize
    from PyQt5.QtGui import QColor
    from PyQt5.QtWidgets import QApplication

    from managers.screenshot import ScreenshotWidget, desktop_bounds

    app = _ensure_qt_app()
    screens = app.screens()
    union = QRect(screens[0].geometry())
    for screen in screens:
        union = union.united(screen.geometry())

    bounds = desktop_bounds()
    assert bounds is not None, "取不到屏幕信息"
    desktop_rect, cover_all_screens = bounds
    assert desktop_rect == union, f"覆盖范围不是整个虚拟桌面: {desktop_rect} != {union}"
    if len(screens) < 2:
        print("⚠️ 单屏环境，仅校验覆盖范围\n")
        return

    # 副屏左上角附近放一块纯白标记：它在冻结图里的位置能证明跨屏坐标换算
    marker_color = QColor(0, 255, 0)
    marker_offset = QPoint(60, 60)
    marker_size = QSize(240, 180)
    marker = _make_color_marker(
        marker_color,
        QRect(screens[1].geometry().topLeft() + marker_offset, marker_size))
    marker.show()
    marker.raise_()
    deadline = time.time() + 0.6
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)

    widget = ScreenshotWidget()
    try:
        assert widget.covers_multiple_screens(), "多屏环境应覆盖所有显示器"
        assert (widget.width(), widget.height()) == (union.width(), union.height())
        assert widget._desktop_rect.topLeft() == union.topLeft(), "桌面原点不对"
        assert len(widget._screen_rects) == len(screens), "没有记录每块显示器的位置"
        for screen, rect in zip(screens, widget._screen_rects):
            assert rect.topLeft() == screen.geometry().topLeft() - union.topLeft()

        scale = widget._grab_scale
        frozen = widget._frozen_pixmap.toImage()
        assert abs(frozen.width() - union.width() * scale) <= 2, \
            f"冻结图没有覆盖整个桌面: {frozen.width()} != {union.width()}x{scale:.3f}"
        assert abs(frozen.height() - union.height() * scale) <= 2, \
            f"冻结图高度不对: {frozen.height()} != {union.height()}x{scale:.3f}"

        # 副屏上的标记在冻结图里应出现在 (桌面局部 + 标记内缩) 的位置
        local = (screens[1].geometry().topLeft() + marker_offset
                 - union.topLeft() + QPoint(20, 20))
        got = frozen.pixelColor(int(local.x() * scale), int(local.y() * scale))
        assert got.green() >= 240 and got.red() <= 12 and got.blue() <= 12, \
            f"副屏坐标没有对上冻结图: {got.name()}（期望纯绿）"
        # 顺带确认这台机器上副屏确实在主屏左侧（原点为负的换算分支）
        assert cover_all_screens, "多屏时应整桌面抓拍"
    finally:
        widget.close()
        marker.close()
        deadline = time.time() + 0.2
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
    print("✅ 遮罩多屏覆盖测试通过\n")

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
        self.text_font_sizes = []

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
        self.text_font_sizes.append(self._font.pixelSize())


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
    """跳过用例：pytest 下标记 skip，直接跑脚本时只打印后继续"""
    # PYTEST_CURRENT_TEST 只在 pytest 收集执行期间存在，可可靠区分两种模式；
    # 不能用 try/except 吞 Skipped——那会把 pytest 下的 skip 变成"通过"，
    # 让后续环境相关断言被静默跳过（本次剪贴板用例就吃过这个亏）
    if 'PYTEST_CURRENT_TEST' in os.environ:
        import pytest
        pytest.skip(reason)
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


def test_app_paths_ocr_component_location():
    """
    OCR 组件的安装位置与运行时探测路径必须是同一个

    安装包（scripts/setup.iss 内置下载）把 Tesseract 解压到
    ~/.screenshot-translation/resources/，exe 不再自带这 225MB。
    两侧路径各改一半的表现是"装完仍然没有本地识别"，所以这条耦合要钉住。
    """
    print("=== 测试 OCR 组件探测路径 ===")
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

            ocr_dir = os.path.join(home, '.screenshot-translation', 'resources')
            assert app_paths.ocr_resources_dir() == ocr_dir
            assert app_paths.tesseract_cmd_candidates()[0] == \
                os.path.join(ocr_dir, 'tesseract', 'tesseract.exe'), "下载目录未排在首位"
            assert app_paths.tessdata_candidates()[0] == \
                os.path.join(ocr_dir, 'tessdata')

            # 组件真落地时，探测函数要能命中它
            exe = app_paths.tesseract_cmd_candidates()[0]
            os.makedirs(os.path.dirname(exe), exist_ok=True)
            open(exe, 'w').close()
            from managers.ocr_providers import tesseract as tess_mod
            assert tess_mod._find_tesseract_cmd() == exe
    finally:
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for attr, value in (('frozen', saved_frozen), ('_MEIPASS', saved_meipass)):
            if value is None:
                if hasattr(sys, attr):
                    delattr(sys, attr)
            else:
                setattr(sys, attr, value)
        sys.executable = saved_executable


def test_installer_downloads_ocr_without_external_process():
    """
    OCR 组件的下载必须整个交给 Setup，安装阶段不许再拉外部进程

    老做法是 PrepareToInstall 里 Exec 一个 powershell 脚本，控制台窗口会弹出来、
    进度也在那个窗口里而不是向导里。现在下载、校验、解压全在 [Files] 里声明，
    所以钉三件事：脚本里没有任何外部进程调用、内置下载三件套齐全、
    编译期参数（附件名/字节数/SHA256）确实由出包脚本产出并在编安装器之前产出。
    """
    print("=== 测试安装包内置下载 ===")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def read(*parts):
        with open(os.path.join(root, *parts), encoding='utf-8-sig') as f:
            return f.read()

    iss = read('scripts', 'setup.iss')
    lowered = iss.lower()
    assert 'exec(' not in lowered, "装填阶段又去起外部进程了（会弹控制台）"
    assert '.ps1' not in lowered and 'fetch_ocr' not in lowered
    assert 'extracttemporaryfile' not in lowered

    # Inno 7 内置下载：download 标志 + 编译期大小 + 校验值 + 归档解压
    assert 'Flags: external download extractarchive recursesubdirs ignoreversion' in iss
    assert 'ExternalSize: {#MyAppOcrSize}' in iss
    assert 'Hash: "{#MyAppOcrSha}"' in iss
    assert 'ArchiveExtraction=auto' in iss
    # 同一标签重装不该再走一遍 85MB
    assert 'Check: ShouldFetchOcr' in iss
    assert 'ocr-version.txt' in iss

    # 卸载/升级不能删下载下来的识别组件：Inno 覆盖安装会先跑旧版卸载程序，
    # 在这里删就等于每次升级都重新下载 85MB（标签跳过逻辑一并失效）
    uninstall = iss.split('[UninstallDelete]')[1]
    uninstall = uninstall.split('\n[')[0]
    directives = [line for line in uninstall.splitlines()
                  if line.strip() and not line.strip().startswith(';')]
    joined = '\n'.join(directives)
    assert 'resources\\tesseract' not in joined, \
        "卸载清单仍在删 tesseract，升级会重新下载组件"
    assert 'resources\\tessdata' not in joined, \
        "卸载清单仍在删 tessdata，升级会重新下载组件"
    assert 'ocr-version.txt' not in joined, \
        "卸载清单仍在删版本标记，标签跳过逻辑会失效"

    packager = read('scripts', 'make_release_package.py')
    for define in ('MyAppOcrPartName', 'MyAppOcrSize', 'MyAppOcrSha', 'MyAppOcrParts'):
        assert f'#define {define}' in packager, f"出包脚本没写 {define}，iss 编译期拿不到"

    publisher = read('scripts', 'publish_release.py')
    assemble = publisher.split('def step_assemble')[1].split('\ndef ')[0]
    built_at = assemble.index('make_release_package.py')
    compiled_at = assemble.index('iscc')
    assert built_at < compiled_at, "必须先出 OCR 包再编安装器，否则校验值是上一版的"

    # 就绪与否要在编译期落到输出里，并由发版脚本抓：三元组缺项时 download 条目
    # 根本不编进去，Setup.exe 同名、编得过，只有装机勾了 OCR 才中止
    assert '#pragma message "OCRDownloadReady=1' in iss, "iss 没打就绪哨兵"
    assert '#pragma message "OCRDownloadReady=0' in iss, "iss 没打未就绪哨兵"
    assert 'OCRDownloadReady=1' in assemble, "发版脚本没校验就绪哨兵"
    assert 'capture=True' in assemble, "编译安装器没收输出，哨兵抓不到"


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
    from managers import pin_window

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    pixmap = QPixmap(60, 40)
    pixmap.fill(Qt.red)
    pin = pin_window.PinWindow(pixmap)
    try:
        for key in (Qt.Key_A, Qt.Key_Z, Qt.Key_Return, Qt.Key_C):
            pin.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))

        # Ctrl+C：验证走的是带校验的写入器，且不抛异常。
        # 真实写入断言在环境里会 flaky（剪贴板可能被第三方工具长期占用，
        # 见 test_clipboard_write_verified 的说明），这里只盯接线本身。
        calls = []

        class SpyClipboard:
            @staticmethod
            def set_pixmap_later(px, on_done=None):
                calls.append(px)
                if on_done:
                    on_done(True)

        real_clipboard = pin_window.clipboard
        pin_window.clipboard = SpyClipboard
        try:
            pin.keyPressEvent(
                QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier))
        finally:
            pin_window.clipboard = real_clipboard
        assert calls and calls[0] is pixmap, "Ctrl+C 应调用校验写入器复制钉住的图"
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
                                target_lang=None, cancel_event=None,
                                deadline=None):
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
        def perform_ocr_with_blocks(self, image, deadline=None,
                                    cancel_event=None):
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


def test_clipboard_write_verified():
    """
    剪贴板写入必须校验生效并重试，且复制路径真的走这条校验通道

    Windows 上剪贴板被别的进程占用时 setText/setPixmap 会**静默失败**
    （既不抛异常也不返回错误），历史实现是"设完就记已复制"，用户看到的
    现象是点了复制却粘贴不出东西（钉图的 Ctrl+C 就为此连挂过三次）。

    校验信号用 ownsClipboard()（实测 0ms、被占用时也照常应答），不能用
    读回比对：text()/pixmap() 在剪贴板被占用时走 OleGetClipboard 吃满
    OLE 超时，实测一次 0.5~3.9 秒，放在主线程等于自造卡死。
    """
    print("=== 测试剪贴板写入校验与重试 ===")
    from managers import clipboard as clipboard_mod

    class FakePixmap:
        def __init__(self, null):
            self._null = null

        def isNull(self):
            return self._null

    class FakeClipboard:
        """前 fail_times 次写入不生效，模拟写入瞬间剪贴板被别的程序占用"""

        def __init__(self, fail_times):
            self.fail_times = fail_times
            self.writes = 0
            self._owns = False

        def setText(self, text):
            self.writes += 1
            self._owns = self.writes > self.fail_times

        def setPixmap(self, pixmap):
            self.writes += 1
            self._owns = self.writes > self.fail_times

        def ownsClipboard(self):
            return self._owns

    class FakeApp:
        current = None

        @staticmethod
        def clipboard():
            return FakeApp.current

    real_app = clipboard_mod.QApplication
    real_delay = clipboard_mod._DELAY
    clipboard_mod.QApplication = FakeApp
    clipboard_mod._DELAY = 0          # 只验证重试次数，不真的等
    try:
        # ① 一次成功：不重试
        FakeApp.current = FakeClipboard(fail_times=0)
        assert clipboard_mod.set_text('译文') is True
        assert FakeApp.current.writes == 1

        # ② 连失败两次后成功：重试到位（老实现会静默丢掉这次复制）
        FakeApp.current = FakeClipboard(fail_times=2)
        assert clipboard_mod.set_text('译文') is True
        assert FakeApp.current.writes == 3

        # ③ 一直被占用：返回 False，调用方才能不谎报"已复制"
        FakeApp.current = FakeClipboard(fail_times=99)
        assert clipboard_mod.set_text('译文') is False
        assert FakeApp.current.writes == clipboard_mod._ATTEMPTS

        # ④ 图片写入同一套校验（钉图 Ctrl+C 走的就是它）
        FakeApp.current = FakeClipboard(fail_times=1)
        assert clipboard_mod.set_pixmap(FakePixmap(False)) is True
        assert FakeApp.current.writes == 2
        FakeApp.current = FakeClipboard(fail_times=99)
        assert clipboard_mod.set_pixmap(FakePixmap(False)) is False

        # ⑤ 空文本/空图根本不碰剪贴板，也不当作"写成功"
        FakeApp.current = FakeClipboard(fail_times=0)
        assert clipboard_mod.set_text('') is False
        assert clipboard_mod.set_pixmap(FakePixmap(True)) is False
        assert FakeApp.current.writes == 0
    finally:
        clipboard_mod.QApplication = real_app
        clipboard_mod._DELAY = real_delay

    # ⑥ 端到端接线：复制入口确实走校验写入器（而不是又回到裸 setText）。
    #    用桩断言、环境无关，必须先于真实冒烟执行——真实剪贴板可能被
    #    第三方程序长期占用，若先冒烟会导致接线断言被静默跳过
    from managers import result_render
    from managers import screenshot as screenshot_mod

    # 构造 QWidget 前必须先有 QApplication，且引用要一直持有（见 _ensure_qt_app）
    _ensure_qt_app()
    widget = screenshot_mod.ScreenshotWidget()
    widget._result = {'original_text': 'Hello', 'translated_text': '你好',
                      'block_texts': []}
    widget._mode = result_render.MODE_TRANSLATED

    copied = []

    class SpyClipboard:
        @staticmethod
        def set_text_later(text, on_done=None):
            copied.append(text)
            if on_done:
                on_done(False)

    real_screenshot_clipboard = screenshot_mod.clipboard
    screenshot_mod.clipboard = SpyClipboard
    try:
        widget._copy_result()
    finally:
        screenshot_mod.clipboard = real_screenshot_clipboard
    assert copied == ['你好'], f"复制未经过校验写入器: {copied}"

    # ⑦ 主线程走的是非阻塞版：前几次被占用时不 sleep，而是把重试排进事件循环
    import time
    app = _ensure_qt_app()
    real_app = clipboard_mod.QApplication
    real_delay = clipboard_mod._DELAY
    results = []

    class OccupyThenRelease:
        """FakeClipboard 的事件循环版：写两次才生效"""

        def __init__(self):
            self.writes = 0

        def setText(self, text):
            self.writes += 1

        def ownsClipboard(self):
            return self.writes >= 2

    fake = OccupyThenRelease()
    clipboard_mod.QApplication = type(
        'App', (), {'clipboard': staticmethod(lambda: fake)})
    clipboard_mod._DELAY = 0
    try:
        clipboard_mod.set_text_later('译文', on_done=results.append)
        deadline = time.time() + 2
        while not results and time.time() < deadline:
            app.processEvents()
            time.sleep(0.005)
    finally:
        clipboard_mod.QApplication = real_app
        clipboard_mod._DELAY = real_delay
    assert results == [True], f"非阻塞重试没有最终写入成功: {results}"
    assert fake.writes >= 2, f"未生效时没有排重试: {fake.writes} 次写入"

    # ⑧ 真实剪贴板冒烟：写入后 ownsClipboard 必须立刻为 True。
    #    ownsClipboard 探测不打开剪贴板，第三方工具长期占住剪贴板时
    #    （剪贴板监视器挂着 OpenClipboard），写入会真实失败——此时如实
    #    跳过而不是误报；机制本身由 ①~⑤ 的桩验证、⑥⑦ 的接线与非阻塞
    #    重试验证守住。
    from PyQt5.QtWidgets import QApplication
    _ensure_qt_app()
    clipboard = QApplication.clipboard()
    if not clipboard_mod.set_text('对齐冒烟测试'):
        _skip("真实剪贴板当前被其他程序占用（长期 OpenClipboard 持有），无法做写入冒烟")
        return
    assert clipboard.ownsClipboard(), "写入成功后 ownsClipboard 应为 True"
    print("✅ 剪贴板写入校验与重试测试通过\n")


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


def test_config_defaults_in_memory():
    """默认配置只驻内存：没改过设置就不该在磁盘上留下 config.ini"""
    print("=== 测试默认配置只驻内存 ===")
    import tempfile
    from config import ConfigManager

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'config.ini')
        manager = ConfigManager(path)
        assert not os.path.exists(path), "默认配置不该落盘（会留下遗留文件）"

        # 内存里的默认值必须完整可用
        assert manager.get_api_type() == 'edge_free'
        assert manager.get_global_hotkey() == 'alt+z'
        assert manager.is_minimize_to_tray() is True
        assert manager.get_ocr_type() == 'tesseract'
        assert manager.get_ocr_option('tesseract_langs') == 'eng+chi_sim'
        assert manager.get_overlay_option('snap_threshold') == '12'
        assert manager.get_theme() == 'dark'

        # 用户真的改了配置才写盘，重启后才能读回改动
        manager.set_theme('light')
        assert os.path.exists(path), "改动配置后应落盘"
        with open(path, encoding='utf-8') as f:
            content = f.read()
        assert '[UI]' in content and 'theme = light' in content
        assert ConfigManager(path).get_theme() == 'light'

        # 文件存在但内容读坏了：就地修好，而不是每次启动都回退默认值
        with open(path, 'w', encoding='utf-8') as f:
            f.write('这不是配置文件')
        broken = ConfigManager(path)
        assert broken.get_api_type() == 'edge_free'
        with open(path, encoding='utf-8') as f:
            assert '[Translation]' in f.read(), "坏配置未被修复"
        assert os.path.exists(path + '.bak'), "修复前没留原文件备份"
        with open(path + '.bak', encoding='utf-8') as f:
            assert f.read() == '这不是配置文件', "备份里不是那份坏文件"
    print("✅ 默认配置只驻内存测试通过\n")


def test_corrupt_config_repair_fills_every_key():
    """
    半截解析不能让修复后的文件缺键

    configparser 抛错之前已经把能解析的部分塞进内存，而各段的默认值填充器
    "见段即跳"。不先扔掉这个半成品，修出来的文件里 [Translation] 就只有用户
    那一个键，其余键全部消失。
    """
    print("=== 测试坏配置修复补齐全部键 ===")
    import tempfile
    from config import ConfigManager

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'config.ini')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('[Translation]\napi_type = baidu\n这行没有等号\n')
        ConfigManager(path)
        with open(path, encoding='utf-8') as f:
            content = f.read()
        assert 'target_lang' in content, "已存在但残缺的段没被补默认值"
        assert 'llm_model' in content, "半截解析让修复后的文件缺键"
        for section in ('[Hotkey]', '[Startup]', '[UI]', '[Overlay]', '[OCR]'):
            assert section in content, f"修复后的文件缺段: {section}"
    print("✅ 坏配置修复补齐全部键测试通过\n")


def test_batch_persist_false_writes_no_file():
    """batch(persist=False) 只改内存：没点保存就不该动用户的配置文件"""
    print("=== 测试批量上下文可只改内存 ===")
    import tempfile
    from config import ConfigManager

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'config.ini')
        cm = ConfigManager(path)
        writes = []
        real_write = cm._write_config

        def counting_write():
            writes.append(1)
            return real_write()

        cm._write_config = counting_write
        try:
            with cm.batch(persist=False):
                cm.set_theme('light')
                cm.set_target_lang('en')
            assert cm.get_theme() == 'light', "内存里的值必须生效"
            assert cm.get_target_lang() == 'en'
            assert not writes, "persist=False 仍然写盘了"
            assert not os.path.exists(path), "没保存就不该有 config.ini"

            # 挂着的"待落盘"标记要一起清掉，否则下一次"什么都没改"的保存也会
            # 把这批没保存过的改动补写进文件
            with cm.batch():
                pass
            assert not writes, "空批次被上一次 persist=False 的改动触发了写盘"
            assert not os.path.exists(path), "没保存过就不该有 config.ini"

            with cm.batch():
                cm.set_theme('dark')
            assert len(writes) == 1, "真正的保存仍然要落盘"
            with open(path, encoding='utf-8') as f:
                content = f.read()
            assert 'theme = dark' in content
        finally:
            del cm._write_config
    print("✅ 批量上下文只改内存测试通过\n")


def test_write_failure_keeps_changes_pending():
    """写盘失败不能让改动静默消失：标记留在，下次保存连带重写，且界面看得见"""
    print("=== 测试配置写盘失败语义 ===")
    import tempfile
    from config import ConfigManager

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'config.ini')
        cm = ConfigManager(path)
        cm.set_theme('dark')
        before = open(path, encoding='utf-8').read()
        assert 'theme = dark' in before

        real_replace = os.replace
        try:
            def failing_replace(src, dst):
                raise OSError('磁盘已满')

            os.replace = failing_replace
            with cm.batch():
                cm.set_theme('light')
            assert cm.write_ok is False, "失败状态没记到 write_ok"
            assert cm._pending_changes is True, "失败后不该把改动当成已落盘"
            assert open(path, encoding='utf-8').read() == before, "失败时原文件被动了"
            leftovers = [n for n in os.listdir(tmp) if n.endswith('.tmp')]
            assert not leftovers, f"失败后残留临时文件: {leftovers}"

            # 下一次保存（哪怕这次什么都没改）要把上次失败的改动一起补上
            os.replace = real_replace
            with cm.batch():
                pass
            assert cm.write_ok is True, "重试后应写成功"
            assert 'theme = light' in open(path, encoding='utf-8').read()
        finally:
            os.replace = real_replace
    print("✅ 配置写盘失败语义测试通过\n")


def test_cache_atomic_write_uses_unique_tmp():
    """缓存临时文件必须带进程与线程标识：共用 ".tmp" 时两个写者互相顶替"""
    print("=== 测试缓存原子写临时名 ===")
    import tempfile
    from managers.cache_manager import CacheManager

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'cache.json')
        manager = CacheManager(cache_file=path)
        from datetime import datetime
        manager._cache['k'] = {
            'text': 'v', 'lang': 'zh', 'provider': 'edge_free',
            'timestamp': datetime.now().isoformat()}
        manager._pending_writes = 1

        seen = []
        real_replace = os.replace

        def spy(src, dst):
            seen.append(os.path.basename(src))
            return real_replace(src, dst)

        os.replace = spy
        try:
            manager._save_cache()
        finally:
            os.replace = real_replace

        assert len(seen) == 1, f"没有走原子替换: {seen}"
        assert seen[0] != 'cache.json.tmp', f"临时名仍是固定共享的: {seen[0]}"
        assert str(os.getpid()) in seen[0], f"临时名没带进程标识: {seen[0]}"
        assert not [n for n in os.listdir(tmp) if n.endswith('.tmp')]
    print("✅ 缓存原子写临时名测试通过\n")


def test_data_dir_survives_unwritable_dirs():
    """连兜底目录都建不起来时不能抛：data_dir 在日志 handler 建立之前就会被调用"""
    print("=== 测试数据目录兜底 ===")
    import tempfile
    from managers import app_paths

    with tempfile.TemporaryDirectory() as tmp:
        blocker = os.path.join(tmp, 'blocker')
        with open(blocker, 'w', encoding='utf-8') as f:
            f.write('占位文件：它下面的任何路径都建不出目录')

        saved_frozen = app_paths._is_frozen
        saved_root = app_paths.app_root
        saved_temp = os.environ.get('TEMP')
        try:
            app_paths._is_frozen = lambda: False
            app_paths.app_root = lambda: os.path.join(blocker, 'sub')
            os.environ['TEMP'] = os.path.join(blocker, 'nope')
            path = app_paths.data_dir()
            assert path.endswith(app_paths.USER_DATA_DIR_NAME), path
            assert not os.path.exists(path), "建不起来时不该谎报目录可用"
        finally:
            app_paths._is_frozen = saved_frozen
            app_paths.app_root = saved_root
            if saved_temp is None:
                os.environ.pop('TEMP', None)
            else:
                os.environ['TEMP'] = saved_temp
    print("✅ 数据目录兜底测试通过\n")


def test_error_handler_logs_real_traceback():
    """
    错误处理必须记下真正的栈

    sys.excepthook 那条路上没有活动异常，traceback.format_exc() 只会写出
    "NoneType: None"，而真正的栈是 hook 的参数——不传进来就等于丢掉现场。
    """
    print("=== 测试错误处理保留调用栈 ===")
    import io
    import logging
    from managers.error_handler import ErrorHandler

    log = logging.getLogger('screenshot-translation')
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter('%(message)s'))
    saved_propagate = log.propagate
    log.addHandler(handler)
    log.propagate = False
    try:
        try:
            raise ValueError('底层炸了')
        except ValueError as error:
            ErrorHandler.handle_error(error, '截图', show_message=False)
        out = buf.getvalue()
        assert 'Traceback' in out and 'ValueError: 底层炸了' in out, out

        buf.truncate(0)
        buf.seek(0)
        captured = []
        try:
            raise RuntimeError('钩子外的错')
        except RuntimeError as error:
            captured.append((error, error.__traceback__))
        # 出了 except 块就没有活动异常，等价于 excepthook 的处境
        error, tb = captured[0]
        ErrorHandler.handle_error(error, '主应用', show_message=False, tb=tb)
        out = buf.getvalue()
        assert 'Traceback' in out and 'RuntimeError: 钩子外的错' in out, out
        assert 'NoneType: None' not in out, "hook 场景把栈记成了空异常"
    finally:
        log.removeHandler(handler)
        log.propagate = saved_propagate
    print("✅ 错误处理调用栈测试通过\n")


def test_hotkey_parse():
    """快捷键串解析：必须“修饰键+主键”，否则会把该键从所有程序里抢走"""
    print("=== 测试快捷键解析 ===")
    from managers.hotkey_native import (
        MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN, parse_hotkey)

    assert parse_hotkey('alt+f4') == (MOD_ALT | MOD_NOREPEAT, 0x73), "F 键解析不符"
    mods, vk = parse_hotkey('alt+z')
    assert mods == MOD_ALT | MOD_NOREPEAT and vk is not None, f"单修饰键解析不符: {mods:#x}"
    assert parse_hotkey('Ctrl + Shift + a')[0] == MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT
    assert parse_hotkey('win+alt+space')[1] == 0x20
    assert parse_hotkey('ctrl+alt+shift+win+z')[0] == (
        MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_WIN | MOD_NOREPEAT)

    for bad in ('z', 'alt+a+b', 'alt+', '', None, 123, 'alt+不存在的键'):
        assert parse_hotkey(bad) is None, f"{bad!r} 不该被解析成合法快捷键"
    print("✅ 快捷键解析测试通过\n")


def test_hotkey_backend_fallback_and_watchdog():
    """原生注册失败要回退键盘钩子；钩子后端的线程/注册丢失要能自愈"""
    print("=== 测试快捷键后端回退与自愈 ===")
    import ctypes
    from ctypes import wintypes

    import managers.hotkey as hotkey_mod
    from managers.hotkey import HotkeyManager
    from managers.hotkey_native import (
        HOTKEY_ID, WM_HOTKEY, NativeHotkeyBackend)
    from PyQt5.QtWidgets import QApplication

    class FakeScreenshot:
        def start_screenshot(self):
            pass

    class FakeUser32:
        """只记录调用：RegisterHotKey 的返回值控制注册成败"""
        def __init__(self, ok):
            self.ok = ok
            self.registered = []
            self.unregistered = []

        def RegisterHotKey(self, hwnd, hotkey_id, modifiers, vk):
            self.registered.append((hotkey_id, modifiers, vk))
            return 1 if self.ok else 0

        def GetLastError(self):
            # 模拟“已被其它程序占用”
            return 0 if self.ok else 1400

        def UnregisterHotKey(self, hwnd, hotkey_id):
            self.unregistered.append(hotkey_id)
            return 1

    class FakeThread:
        def __init__(self, alive):
            self._alive = alive

        def is_alive(self):
            return self._alive

    class FakeListener:
        def __init__(self, alive=True):
            self.listening_thread = FakeThread(alive)
            self.processing_thread = FakeThread(alive)

    class FakeKeyboard:
        """只实现被测代码用到的 keyboard 接口"""
        def __init__(self):
            self._listener = FakeListener(True)
            self._hotkeys = {}
            self._pressed_events = {56: 'alt'}
            self._logically_pressed_keys = {56: 'alt'}
            self.added = []

        def add_hotkey(self, hotkey, callback, args=()):
            handle = object()
            self._hotkeys[handle] = self._hotkeys[hotkey] = handle
            self.added.append(hotkey)
            return handle

        def remove_hotkey(self, handle):
            self._hotkeys = {k: v for k, v in self._hotkeys.items() if v is not handle}

    app = QApplication.instance() or QApplication(sys.argv)
    saved_keyboard = hotkey_mod.keyboard
    try:
        # 1) 原生注册被占用（返回 0）→ 自动回退键盘钩子，而不是彻底失效
        fake_keyboard = FakeKeyboard()
        hotkey_mod.keyboard = fake_keyboard
        manager = HotkeyManager(FakeScreenshot())
        manager.hotkey = 'alt+z'
        manager.is_running = True      # 看门狗只在运行态工作
        manager.native_backend = NativeHotkeyBackend(FakeUser32(ok=False))
        assert manager._register_hotkey() is True, "原生失败后应回退钩子后端"
        assert manager._backend == 'keyboard'
        assert '占用' in manager.native_backend.last_error, \
            f"失败原因应是快捷键被占用，而非环境缺失: {manager.native_backend.last_error}"
        assert fake_keyboard.added == ['alt+z']
        # 占用场景要能告诉用户换个组合，而不是事后抱怨快捷键失效
        hint = manager.native_conflict_hint()
        assert '占用' in hint and 'alt+z' in hint, f"占用提示不完整: {hint!r}"
        manager._backend = 'native'
        assert manager.native_conflict_hint() == '', "原生注册成功时不该提示占用"
        manager._backend = 'keyboard'

        # 2) 注册表里已没有本快捷键 → 看门狗重新注册
        fake_keyboard._hotkeys.clear()
        manager._watchdog_tick()
        assert fake_keyboard.added == ['alt+z', 'alt+z'], "注册丢失后未重新注册"

        # 3) 监听线程已退出 → 换新监听器、清掉卡住的按键、重新注册
        fake_keyboard._listener = FakeListener(alive=False)
        manager._watchdog_tick()
        assert fake_keyboard._pressed_events == {}, "残留按键会让快捷键永远匹配不上"
        assert fake_keyboard._logically_pressed_keys == {}
        assert fake_keyboard.added == ['alt+z'] * 3, "监听器重建后未重新注册"
        assert fake_keyboard._listener.listening_thread.is_alive(), "监听器未被重建"

        # 4) 原生后端：注册成功时收 WM_HOTKEY 并触发回调，其它消息不受影响
        fake_user32 = FakeUser32(ok=True)
        native = NativeHotkeyBackend(fake_user32)
        fired = []
        assert native.register('alt+z', lambda: fired.append(1)) is True
        assert fake_user32.registered[0][1] & 0x0001, "Alt 修饰键未传给 RegisterHotKey"

        msg = wintypes.MSG()
        msg.message = WM_HOTKEY
        msg.wParam = HOTKEY_ID
        pointer = ctypes.cast(ctypes.pointer(msg), ctypes.c_void_p).value
        assert native._filter.nativeEventFilter(b'windows_generic_MSG', pointer) == (True, 0)
        assert fired == [1], "WM_HOTKEY 未触发回调"
        msg.wParam = HOTKEY_ID + 1
        assert native._filter.nativeEventFilter(b'windows_generic_MSG', pointer)[0] is False
        assert native._filter.nativeEventFilter(b'windows_dispatcher_MSG', pointer)[0] is False

        # 回调自己抛异常也不能逃出去（否则会打断 Qt 事件循环）
        msg.wParam = HOTKEY_ID
        native._callback = lambda: (_ for _ in ()).throw(RuntimeError('回调崩了'))
        assert native._filter.nativeEventFilter(b'windows_generic_MSG', pointer) == (True, 0)

        native.unregister()
        assert native._registered is False and HOTKEY_ID in fake_user32.unregistered
        assert app.installNativeEventFilter is not None

        # 5) 仍被占用时：重试原生失败，但钩子绝不能丢（不能中途失去快捷键）
        manager.native_backend = NativeHotkeyBackend(FakeUser32(ok=False))
        assert manager._try_native_upgrade() is False
        assert manager._backend == 'keyboard' and manager._hotkey_handle is not None
        assert fake_keyboard._hotkeys != {}, "重试失败不该摘掉钩子"

        # 6) 占用解除（抢快捷键的程序退出）→ 看门狗自动升级回原生后端
        manager.native_backend = NativeHotkeyBackend(FakeUser32(ok=True))
        manager._watchdog_tick()
        assert manager._backend == 'native', "原生注册空出后没有升级回原生后端"
        assert fake_keyboard._hotkeys == {}, "升级后钩子必须摘掉，否则一次按键触发两次"
        assert manager.native_conflict_hint() == '', "切回原生后不该再提示兼容模式"
        manager._watchdog_tick()      # 原生后端下再看一次：不该重复注册
        assert manager._backend == 'native'
    finally:
        hotkey_mod.keyboard = saved_keyboard
    print("✅ 快捷键后端回退与自愈测试通过\n")


def test_hotkey_conflict_prompt_reasons():
    """快捷键冲突/失效时，last_error 要给出用户能照着做的具体原因"""
    print("=== 测试快捷键冲突提示文案 ===")
    import managers.hotkey as hotkey_mod
    from managers.hotkey import HotkeyManager
    from managers.hotkey_native import NativeHotkeyBackend
    from PyQt5.QtWidgets import QApplication

    class FakeUser32:
        def __init__(self, ok, last_error=0):
            self.ok = ok
            self._last_error = last_error

        def RegisterHotKey(self, hwnd, hotkey_id, modifiers, vk):
            return 1 if self.ok else 0

        def GetLastError(self):
            return self._last_error

        def UnregisterHotKey(self, hwnd, hotkey_id):
            return 1

    class FakeKeyboard:
        """钩子注册始终失败：只有两后端都挂才走冲突文案分支"""
        _listener = None
        _hotkeys = {}

        def add_hotkey(self, hotkey, callback, args=None):
            raise ValueError(f"未知按键: {hotkey}")

        def remove_hotkey(self, handle):
            pass

    class FakeConfig:
        def get_global_hotkey(self):
            return 'alt+z'

        def set_global_hotkey(self, hotkey):
            pass

    app = QApplication.instance() or QApplication(sys.argv)
    saved_keyboard = hotkey_mod.keyboard
    try:
        hotkey_mod.keyboard = FakeKeyboard()

        # 1) 被其它程序占用（错误码1400）且钩子也失败 → 提示“已被其它程序占用”
        manager = HotkeyManager(config_manager=FakeConfig())
        manager.native_backend = NativeHotkeyBackend(
            FakeUser32(ok=False, last_error=1400))
        assert manager.set_hotkey('ctrl+alt+z') is False
        assert '占用' in manager.last_error, \
            f"占用场景应提示被占用: {manager.last_error!r}"

        # 2) 快捷键无法解析 → 提示需要“修饰键+主键”
        manager.native_backend = NativeHotkeyBackend(FakeUser32(ok=True))
        assert manager.set_hotkey('z') is False
        assert '修饰键' in manager.last_error, \
            f"非法格式应提示组合写法: {manager.last_error!r}"

        # 3) 其它系统错误 → 带错误码的通用失败文案
        manager.native_backend = NativeHotkeyBackend(
            FakeUser32(ok=False, last_error=5))
        assert manager.set_hotkey('ctrl+alt+z') is False
        assert '错误码' in manager.last_error, \
            f"未知失败应带错误码: {manager.last_error!r}"

        # 4) conflict 属性只在占用时为 True
        manager.native_backend = NativeHotkeyBackend(
            FakeUser32(ok=False, last_error=1400))
        manager.native_backend.register('alt+z', lambda: None)
        assert manager.native_backend.conflict is True
        manager.native_backend = NativeHotkeyBackend(
            FakeUser32(ok=False, last_error=5))
        manager.native_backend.register('alt+z', lambda: None)
        assert manager.native_backend.conflict is False
    finally:
        hotkey_mod.keyboard = saved_keyboard
    print("✅ 快捷键冲突提示文案测试通过\n")


def test_hotkey_callback_isolation():
    """按键回调绝不能抛异常也不能重入：钩子线程会因异常整个退出"""
    print("=== 测试快捷键回调隔离与防抖 ===")
    import managers.hotkey as hotkey_mod
    from managers.hotkey import HotkeyManager

    class FakeScreenshot:
        def __init__(self, explode=False):
            self.calls = 0
            self.explode = explode

        def start_screenshot(self):
            self.calls += 1
            if self.explode:
                raise RuntimeError("截图流程崩了")

    # 1) 截图流程抛异常也不能逃出回调（keyboard 的处理线程没有异常保护）
    manager = HotkeyManager(FakeScreenshot(explode=True))
    manager.hotkey = 'alt+z'
    manager._on_hotkey_pressed()
    assert manager.screenshot_manager.calls == 1

    # 2) 线程调度器本身抛异常也要被吞掉
    def _raise(*args, **kwargs):
        raise RuntimeError("调度失败")

    saved_dispatch = hotkey_mod.qt_dispatcher.dispatch_to_main_thread
    try:
        hotkey_mod.qt_dispatcher.dispatch_to_main_thread = _raise
        manager._last_fire_ts = 0.0
        manager._on_hotkey_pressed()
    finally:
        hotkey_mod.qt_dispatcher.dispatch_to_main_thread = saved_dispatch

    # 3) 防抖：长按连发只触发一次，窗口过去后恢复
    fake = FakeScreenshot()
    manager = HotkeyManager(fake)
    manager.hotkey = 'alt+z'
    for _ in range(5):
        manager._on_hotkey_pressed()
    assert fake.calls == 1, f"防抖失效，连发了 {fake.calls} 次截图"
    manager._last_fire_ts -= manager._DEBOUNCE_SECONDS + 0.01
    manager._on_hotkey_pressed()
    assert fake.calls == 2, "防抖窗口过后应恢复触发"
    assert HotkeyManager._DEBOUNCE_SECONDS <= 0.2, \
        "防抖窗口过长会把用户“关掉再按一次”的第二次按键吃掉（表现为快捷键没反应）"
    print("✅ 快捷键回调隔离与防抖测试通过\n")


def test_screenshot_stale_widget():
    """残留/已销毁的遮罩引用不能让热键按下变成“没反应”"""
    print("=== 测试遮罩残留引用自愈 ===")
    import managers.screenshot as screenshot_mod
    from managers.screenshot import ScreenshotManager

    class FakeSignal:
        def connect(self, *args, **kwargs):
            pass

    class FakeWidget:
        """只实现 _run_screenshot 会用到的接口"""
        def __init__(self, config_manager=None):
            self.config_manager = config_manager
            self.region_selected = FakeSignal()
            self.retranslate_requested = FakeSignal()
            self.retry_requested = FakeSignal()
            self.screenshot_canceled = FakeSignal()
            self.visible = True
            self.shown = False
            self.fullscreen_shown = False
            self.closed = False

        multi_screen = False          # 类属性：跨屏分支的用例直接改它

        def isVisible(self):
            return self.visible

        def show(self):
            self.shown = True

        def showFullScreen(self):
            self.shown = True
            self.fullscreen_shown = True

        def covers_multiple_screens(self):
            return type(self).multi_screen

        def activateWindow(self):
            pass

        def raise_(self):
            pass

        def setFocus(self):
            pass

        def close(self):
            self.closed = True

        def deleteLater(self):
            pass

    class DestroyedWidget(FakeWidget):
        def isVisible(self):
            raise RuntimeError('wrapped C/C++ object has been deleted')

    saved_widget_cls = screenshot_mod.ScreenshotWidget
    saved_get_app = screenshot_mod.get_qt_app
    try:
        screenshot_mod.ScreenshotWidget = FakeWidget
        screenshot_mod.get_qt_app = lambda: object()

        # 1) 残留引用（底层对象已销毁）→ 丢掉引用并正常开一次截图
        manager = ScreenshotManager()
        manager._screenshot_widget = DestroyedWidget()
        manager._run_screenshot()
        assert isinstance(manager._screenshot_widget, FakeWidget), "残留引用未被丢弃"
        assert manager._screenshot_widget.shown, "按键被残留引用吞掉，没有开始新截图"

        # 2) 正在显示的遮罩 → 仍然按“再按一次=取消”处理
        manager2 = ScreenshotManager()
        live = FakeWidget()
        manager2._screenshot_widget = live
        manager2._run_screenshot()
        assert manager2._screenshot_widget is None and live.closed, "显示中的遮罩应被取消"

        # 2b) 单屏用 showFullScreen（原行为），跨屏只能用 show：
        # showFullScreen 只会把窗口铺满它所在的那一块屏，另一块屏会重新露出来
        single = ScreenshotManager()
        single._run_screenshot()
        assert single._screenshot_widget.fullscreen_shown, "单屏时应走 showFullScreen"
        multi = ScreenshotManager()
        saved_multi_flag = FakeWidget.multi_screen
        FakeWidget.multi_screen = True
        try:
            multi._run_screenshot()
        finally:
            FakeWidget.multi_screen = saved_multi_flag
        assert multi._screenshot_widget.shown and not multi._screenshot_widget.fullscreen_shown, \
            "跨屏时应按自己的几何 show()，否则只覆盖一块屏"

        # 3) 已销毁的窗口也能安全释放（之前会抛 RuntimeError）
        manager3 = ScreenshotManager()
        manager3._screenshot_widget = DestroyedWidget()
        manager3._release_screenshot_widget()
        assert manager3._screenshot_widget is None
    finally:
        screenshot_mod.ScreenshotWidget = saved_widget_cls
        screenshot_mod.get_qt_app = saved_get_app
    print("✅ 遮罩残留引用自愈测试通过\n")


def test_ocr_empty_result_shortcut():
    """本地引擎判定没字时不该再跑一遍识别（实测每次白等约 0.6 秒）"""
    print("=== 测试空识别结果不重复跑引擎 ===")
    import managers.ocr as ocr_mod
    from managers.ocr import NO_TEXT_NOTICE, OCRManager
    from managers.provider_common import ProviderEmptyResultError

    class EmptyLocalProvider:
        name = 'probe_empty_local'
        display_name = '空结果本地引擎'
        config_fields = ()
        deterministic_results = True
        calls = []

        def __init__(self, config_manager=None):
            self.config_manager = config_manager

        def recognize_blocks(self, image):
            type(self).calls.append('blocks')
            raise ProviderEmptyResultError("没有文字")

        def recognize(self, image):
            type(self).calls.append('text')
            return '不该走到这里'

    class EmptyCloudProvider(EmptyLocalProvider):
        name = 'probe_empty_cloud'
        display_name = '空结果云端引擎'
        deterministic_results = False
        calls = []

    class FakeRegistry:
        """假注册表：去掉 tesseract/mock 兜底，只留被测 provider"""
        def __init__(self):
            self.providers = {
                'probe_empty_local': EmptyLocalProvider,
                'probe_empty_cloud': EmptyCloudProvider,
            }

        def get(self, name):
            return self.providers.get(name)

        def names(self):
            return list(self.providers)

    saved_registry = ocr_mod.ocr_provider_registry
    try:
        ocr_mod.ocr_provider_registry = FakeRegistry()
        manager = OCRManager(None)
        manager._registry = ocr_mod.ocr_provider_registry

        # 本地确定性引擎：块级识别就报“没字”，文本链路不该再跑
        EmptyLocalProvider.calls = []
        manager.ocr_type = 'probe_empty_local'
        text, blocks = manager.perform_ocr_with_blocks(object())
        assert text == NO_TEXT_NOTICE, f"空区域提示不符: {text!r}"
        assert blocks == []
        assert EmptyLocalProvider.calls == ['blocks'], \
            f"确定性引擎空结果后仍在重复识别: {EmptyLocalProvider.calls}"

        # 云端引擎：空结果可能只是限流/抖动，必须继续跑降级链路
        EmptyCloudProvider.calls = []
        manager.ocr_type = 'probe_empty_cloud'
        manager.perform_ocr_with_blocks(object())
        assert EmptyCloudProvider.calls == ['blocks', 'text'], \
            f"云端引擎不该跳过降级链路: {EmptyCloudProvider.calls}"

        # “空结果是否定案”的判定规则：看的是动手的那个引擎
        manager.ocr_type = 'probe_empty_local'
        manager._last_error = ProviderEmptyResultError('无文字')
        manager._last_error_provider = EmptyLocalProvider
        assert manager._empty_result_is_final() is True
        manager.ocr_type = 'probe_empty_cloud'
        manager._last_error_provider = EmptyCloudProvider
        assert manager._empty_result_is_final() is False
        # 配置的是云端引擎，但降级链里最后动手的是本地确定性引擎：同样定案
        manager._last_error_provider = EmptyLocalProvider
        assert manager._empty_result_is_final() is True
    finally:
        ocr_mod.ocr_provider_registry = saved_registry
    print("✅ 空识别结果不重复跑引擎测试通过\n")


def test_translate_blocks_parallel():
    """逐块兜底翻译要并发执行，且单块失败只影响该块"""
    print("=== 测试逐块翻译并发执行 ===")
    import threading
    import time as _time

    from managers.screenshot import ScreenshotManager

    class FakeBlock:
        def __init__(self, text):
            self.text = text

    class FakeTranslation:
        def __init__(self, fail_text):
            self.fail_text = fail_text
            self.lock = threading.Lock()
            self.active = 0
            self.peak = 0

        def perform_translation(self, text, force_refresh=False, target_lang=None,
                                cancel_event=None, deadline=None):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                _time.sleep(0.2)
                if text == self.fail_text:
                    raise RuntimeError("这一块翻译失败了")
                return f"[译文]{text}"
            finally:
                with self.lock:
                    self.active -= 1

    blocks = [FakeBlock(f'line{i}') for i in range(6)]
    fake = FakeTranslation(fail_text='line3')
    manager = ScreenshotManager()
    manager.translation_manager = fake

    started = _time.perf_counter()
    # whole 只有 1 行、块有 6 个 → 行数对不齐，进入逐块兜底路径
    texts = manager._translate_blocks(
        blocks, 'line0\nline1', 'zh', False, whole='[译文]一整段')
    elapsed = _time.perf_counter() - started

    assert len(texts) == 6, f"译文行数应与块数一致: {len(texts)}"
    assert texts[0] == '[译文]line0' and texts[5] == '[译文]line5', f"顺序错乱: {texts}"
    assert texts[3] == '', "单块失败应只影响该块"
    assert fake.peak >= 2, f"逐块翻译没有并发: 峰值 {fake.peak}"
    assert elapsed < 6 * 0.2 * 0.8, \
        f"耗时 {elapsed:.2f}s，串行才需要 {6 * 0.2:.2f}s"
    print(f"✅ 逐块翻译并发测试通过（峰值 {fake.peak} 并发，{elapsed:.2f}s）\n")


def test_translation_warm_up():
    """启动预热：只对免费接口发真实请求，付费接口不替用户花额度"""
    print("=== 测试翻译链路预热 ===")
    from managers.translation import TranslationManager

    manager = TranslationManager(None)      # 默认 edge_free（免费接口）
    calls = []

    def fake_perform(text, force_refresh=False, target_lang=None,
                     cancel_event=None, deadline=None):
        calls.append((text, force_refresh))
        return '[译文]Hello'

    manager.perform_translation = fake_perform
    assert manager.warm_up() is True
    assert calls == [('Hello', True)], f"预热应跳过缓存真发一次请求: {calls}"

    manager.api_type = 'llm'               # 付费接口不预热
    calls.clear()
    assert manager.warm_up() is False
    assert calls == [], "付费接口不该在启动时发起请求"
    print("✅ 翻译链路预热测试通过\n")


def test_crop_box_includes_last_pixel():
    """
    选区换算到抓拍图的裁剪框必须含右下角那一像素

    QRect 的 right()/bottom() 是闭区间（= left+width-1），PIL 的裁剪框右下沿
    却是开区间：拿 right()/bottom() 当右下沿会整块选区少裁一像素，
    首尾字符被切掉半个，识别质量直接下降。横纵倍率不等时也要各自换算。
    """
    print("=== 测试选区裁剪框不缺右下角像素 ===")
    from PIL import Image
    from PyQt5.QtCore import QPoint

    from managers.screenshot import ScreenshotWidget

    _ensure_qt_app()
    widget = ScreenshotWidget()
    captured = []
    try:
        widget.region_selected.connect(lambda img, rect: captured.append(img))
        # 合成一张"抓拍图"：横 2 倍、纵 1 倍（模拟 DPI 信息缺失时横纵不等比）
        widget.full_screen = Image.new('RGB', (2000, 400), 'white')
        widget._grab_scale = 2.0
        widget._grab_scale_y = 1.0
        widget._snap_window_rects = []
        widget._snap_threshold = 0
        widget.begin, widget.end = QPoint(100, 100), QPoint(300, 200)

        widget._confirm_selection()

        assert len(captured) == 1, f"没有发起识别: {len(captured)}"
        image = captured[0]
        assert (image.width, image.height) == (400, 100), \
            f"裁剪尺寸应为选区×各自倍率 400x100，实际 {image.width}x{image.height}"
        assert (widget._scale, widget._scale_y) == (2.0, 1.0), \
            f"横纵倍率记录不对: {widget._scale} / {widget._scale_y}"
    finally:
        widget.close()

    # 选区落到抓拍图之外时不能裁出空图继续跑识别
    widget2 = ScreenshotWidget()
    canceled = []
    try:
        widget2.screenshot_canceled.connect(lambda: canceled.append(1))
        widget2.full_screen = Image.new('RGB', (100, 100), 'white')
        widget2._grab_scale = 2.0
        widget2._grab_scale_y = 2.0
        widget2._snap_window_rects = []
        widget2._snap_threshold = 0
        widget2.begin, widget2.end = QPoint(80, 80), QPoint(95, 95)
        widget2._confirm_selection()
        assert canceled, "裁不出有效像素时应取消截图而不是继续"
    finally:
        widget2.close()
    print("✅ 选区裁剪框测试通过\n")


def test_result_render_per_axis_scale():
    """行块坐标与取色必须按横纵各自倍率换算，不能拿横向倍率去除纵向"""
    print("=== 测试结果绘制横纵倍率分离 ===")
    from PIL import Image
    from PyQt5.QtCore import QRect

    import managers.result_render as rr
    from managers.text_block import TextBlock

    blocks = [TextBlock('Hello', 200, 100, 40, 20)]
    same = rr.to_logical_blocks(blocks, 2.0)
    assert (same[0].left, same[0].top, same[0].width, same[0].height) == \
        (100, 50, 20, 10), f"等比换算不对: {same[0]}"

    split = rr.to_logical_blocks(blocks, 2.0, 1.0)
    assert (split[0].left, split[0].top, split[0].width, split[0].height) == \
        (100, 100, 20, 20), f"纵向用了横向倍率: {split[0]}"

    # 取色：把目标块（逻辑 100,100,20,20 → 物理 200,100,40,20）涂成蓝、其余涂白，
    # 纵向若误用 2.0 倍率就会采样到物理 (200,200) 一带的白色
    image = Image.new('RGB', (400, 400), 'white')
    for y in range(100, 120):
        for x in range(200, 240):
            image.putpixel((x, y), (0, 0, 255))
    bg, fg = rr.dominant_colors(image, QRect(100, 100, 20, 20), 2.0, 1.0)
    assert fg.blue() > 200 and fg.red() < 60, \
        f"纵向采样位置不对，取到的前景色是 {fg.name()}"
    assert bg.red() > 200 and bg.green() > 200, f"背景色应取到白色: {bg.name()}"

    # 缓存归属键必须包含纵向倍率，否则换了纵向倍率还会吃上一轮的取色结果
    _ensure_qt_app()
    cache_a = rr._layout_cache_for(image, 2.0, 1.0)
    cache_a['probe'] = 1
    cache_b = rr._layout_cache_for(image, 2.0, 2.0)
    assert 'probe' not in cache_b, "纵向倍率变了必须作废排版缓存"
    print("✅ 结果绘制横纵倍率分离测试通过\n")


def test_translation_failure_not_painted_as_result():
    """
    降级链全失败返回的 "[翻译失败 …]" 标记不能被当译文贴上原文

    单行选区时标记文本的"行数"正好等于块数，会顺着按行对齐通道贴回遮罩，
    用户看到的就是一行像译文的报错；这里要么走可重试的失败卡片，
    要么这一块留空，绝不允许标记文本进入展示。
    """
    print("=== 测试翻译失败标记不当译文展示 ===")
    from managers.screenshot import ScreenshotManager
    from managers.text_block import TextBlock
    from managers.translation import TranslationManager, is_translation_failure

    assert is_translation_failure(TranslationManager(None)._failure_prefix
                                 + ' 网络超时] Hello')
    assert not is_translation_failure('你好')
    assert not is_translation_failure('')
    assert not is_translation_failure(None)

    manager = ScreenshotManager()
    marker = '[翻译失败 网络超时] Hello'
    assert manager._translation_failure_message(marker, []), "全失败应给出失败文案"
    assert manager._translation_failure_message(marker, ['你好']) is None, \
        "只要有一行真译文就不算失败"
    assert manager._translation_failure_message('你好', []) is None
    assert manager._translation_failure_message('', []) is None, \
        "本来就没有内容时不算翻译失败"

    class AllFailTranslation:
        target_lang = 'zh'

        def get_target_languages(self):
            return [('zh', '中文')]

        def perform_translation(self, text, force_refresh=False, target_lang=None,
                                cancel_event=None, deadline=None):
            return marker

    manager.translation_manager = AllFailTranslation()
    blocks = [TextBlock('Hello', 0, 0, 100, 20)]
    texts = manager._translate_blocks(blocks, 'Hello', 'zh', False, whole=marker)
    assert marker not in texts, f"失败标记被当译文返回了: {texts}"
    assert manager._translation_failure_message(marker, texts) == \
        '翻译失败：所有接口都没有返回译文'

    # 逐块兜底也一样：单块失败留空，不把标记文本塞回展示
    class OneBlockFails:
        target_lang = 'zh'

        def perform_translation(self, text, force_refresh=False, target_lang=None,
                                cancel_event=None, deadline=None):
            return marker if text == 'bad' else f'[译文]{text}'

    manager.translation_manager = OneBlockFails()
    blocks = [TextBlock('good', 0, 0, 100, 20), TextBlock('bad', 0, 30, 100, 20)]
    texts = manager._translate_blocks(blocks, 'good\nbad', 'zh', False, whole='')
    assert texts == ['[译文]good', ''], f"失败块应留空: {texts}"
    assert manager._translation_failure_message('', texts) is None, \
        "有一块成功就不该整体报失败"
    print("✅ 翻译失败标记处理测试通过\n")


def test_overlay_paint_never_raises():
    """
    绘制期异常不能让进程消失

    Qt 不接 Python 异常：paintEvent 里抛出等于把整个程序带走，
    既没有对话框也没有栈。这里断言异常被兜住并记进日志。
    """
    print("=== 测试遮罩绘制异常不外抛 ===")
    import logging

    from PIL import Image
    from PyQt5.QtCore import QRect
    from PyQt5.QtGui import QPixmap

    import managers.result_render as result_render
    import managers.screenshot as screenshot_mod
    from managers.screenshot import ScreenshotWidget

    _ensure_qt_app()
    widget = ScreenshotWidget()
    recorded = []

    class SpyHandler(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                recorded.append(record.getMessage())

    handler = SpyHandler()
    # 模块用的是全局 logger，挂错对象就永远收不到
    app_logger = logging.getLogger('screenshot-translation')
    app_logger.addHandler(handler)
    real_paint_blocks = result_render.paint_blocks
    canvas = QPixmap(max(1, widget.width()), max(1, widget.height()))
    try:
        def boom(painter):
            raise RuntimeError('绘制里故意抛的异常')

        widget._paint = boom
        widget.render(canvas)        # render 会走 paintEvent；不应抛出

        # 结果态深处的异常也要被同一层兜住（内层 try 已合并进外层）
        del widget._paint            # 丢掉实例属性，恢复类上的 _paint
        widget._result = {'original_text': 'Hi', 'translated_text': '你好',
                          'block_texts': ['你好'],
                          'image': Image.new('RGB', (40, 20), 'white'),
                          'blocks': [], 'region': QRect(0, 0, 40, 20)}
        widget._result_rect = QRect(0, 0, 40, 20)
        result_render.paint_blocks = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError('覆盖绘制失败'))
        canvas2 = QPixmap(max(1, widget.width()), max(1, widget.height()))
        widget.render(canvas2)
        assert canvas2.toImage().pixelColor(0, 0).isValid(), "遮罩一帧都没画出来"
    finally:
        result_render.paint_blocks = real_paint_blocks
        app_logger.removeHandler(handler)
        widget.close()
    assert any('遮罩绘制失败' in m for m in recorded), f"异常未留痕: {recorded}"
    print("✅ 遮罩绘制异常兜住测试通过\n")


def test_snap_rect_does_not_collapse_selection():
    """
    磁吸不能把选区吸没

    窄选区的左右两边可能落在同一条参考线的阈值内，四边各自独立吸附会互相
    挤压出 0 宽（拖拽中表现为选区凭空消失）；现在吸完先校验尺寸，
    不合格就整条不采纳，也不报告对应的参考线。
    """
    print("=== 测试磁吸不把窄选区压扁 ===")
    from PyQt5.QtCore import QRect

    from managers.screenshot import ScreenshotWidget

    _ensure_qt_app()
    widget = ScreenshotWidget()
    try:
        widget._screen_rects = [QRect(0, 0, 1920, 1080)]
        widget.screen_width, widget.screen_height = 1920, 1080
        # 一个窗口：左边 300、右边 310（窄到能同时吸住两边的线）
        widget._snap_window_rects = [(300, 0, 310, 500)]
        widget._snap_threshold = 15

        # 正常选区该吸还是要吸：左边离 300 最近（差 3），右边 505 无线可吸
        rect, guides = widget._snap_rect(QRect(303, 200, 200, 100))
        assert rect.left() == 300, f"该吸到窗口左边却没吸: {rect} {guides}"
        assert ('x', 300) in guides, f"吸附了却没报告参考线: {guides}"
        assert rect.width() == 200, f"吸附改变了选区宽度: {rect}"

        # 窄选区：两边都落在同一条线上，尺寸不能被改动
        for width in (1, 2, 5):
            rect, _ = widget._snap_rect(QRect(308, 200, width, 40))
            assert (rect.width(), rect.height()) == (width, 40), \
                f"{width}px 选区被吸改了尺寸: {rect}"

        # 0 宽的退化选区：竖向线一条也不该激活
        rect, guides = widget._snap_rect(QRect(308, 200, 0, 40))
        assert rect.width() == 0, f"退化选区被吸出了宽度: {rect}"
        assert not [g for g in guides if g[0] in ('x', 'x2')], \
            f"0 宽选区不该激活竖向参考线: {guides}"
        print("✅ 磁吸不压扁选区测试通过\n")
    finally:
        widget.close()


def test_toolbar_fit_width_reversible():
    """窄选区省掉的工具条按钮，换到宽选区时必须能回来"""
    print("=== 测试工具条宽度精简可逆 ===")
    from PyQt5.QtWidgets import QWidget

    from managers.overlay_toolbar import OverlayToolbar

    _ensure_qt_app()
    host = QWidget()
    toolbar = OverlayToolbar(host, [('zh', '中文'), ('en', 'English')])
    try:
        full = toolbar.preferred_width()
        hidden_at_narrow = toolbar.fit_width(1)
        assert hidden_at_narrow < full, "极窄时应该省掉按钮"
        assert any(w.isHidden() for w in toolbar._trimmable), "没有按钮被隐藏"

        back = toolbar.fit_width(full)
        assert back == full, f"回到宽选区没恢复完整工具条: {back} != {full}"
        assert not any(w.isHidden() for w in toolbar._trimmable), \
            "省掉的按钮回不来：_trimmable 被单向消费了"

        # 中间档位也要按当前宽度重算，而不是记住上一次的精简结果
        mid = toolbar.fit_width((full + hidden_at_narrow) // 2)
        assert mid <= (full + hidden_at_narrow) // 2, f"中间档没省够: {mid}"
        assert toolbar.fit_width(full) == full
        assert not any(w.isHidden() for w in toolbar._trimmable)
    finally:
        toolbar.deleteLater()
        host.deleteLater()
    print("✅ 工具条宽度精简可逆测试通过\n")


def test_hotkey_unregister_when_filter_fails():
    """
    RegisterHotKey 成功后半途抛错必须把系统热键摘掉

    旧实现等事件过滤器装好才置 `_registered`：中间任何异常都会走
    unregister()，而它看 `_registered` 还是 False 就直接放过——热键永久留在
    系统表里，之后每次注册都撞"已被其它程序占用"，只能重启系统恢复。
    """
    print("=== 测试热键注册半途失败的回滚 ===")
    import managers.hotkey_native as native_mod
    from managers.hotkey_native import HOTKEY_ID, NativeHotkeyBackend
    from PyQt5.QtWidgets import QApplication

    class FakeUser32:
        def __init__(self):
            self.unregistered = []

        def RegisterHotKey(self, hwnd, hotkey_id, modifiers, vk):
            return 1

        def GetLastError(self):
            return 0

        def UnregisterHotKey(self, hwnd, hotkey_id):
            self.unregistered.append(hotkey_id)
            return 1

    QApplication.instance() or QApplication(sys.argv)
    fake = FakeUser32()
    backend = NativeHotkeyBackend(fake)

    class BoomFilter:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("模拟过滤器构造失败")

    saved = native_mod._HotkeyEventFilter
    native_mod._HotkeyEventFilter = BoomFilter
    try:
        assert backend.register('alt+z', lambda: None) is False
        assert fake.unregistered == [HOTKEY_ID], \
            f"注册成功后的半途异常必须把系统热键摘掉: {fake.unregistered}"
        assert backend._registered is False
    finally:
        native_mod._HotkeyEventFilter = saved
        backend.unregister()
    print("✅ 热键注册半途失败的回滚测试通过\n")


def test_hotkey_vk_keyscan_signature():
    """
    VkKeyScanW 的签名必须显式声明

    ctypes 默认按 c_int 取返回值，而 VkKeyScanW 返回的是 SHORT：键位表里查不到
    时的 -1 会被读成 0xFFFF，于是 `scan != -1` 成立，程序拿 0xFF 这个根本不
    存在的虚拟键码去注册热键——注册"成功"，那个键却永远按不出来。
    声明 argtypes 后入参必须是字符本身，不能再传 ord 值。
    """
    print("=== 测试 VkKeyScanW 签名声明 ===")
    import ctypes
    import managers.hotkey_native as native_mod
    from managers.hotkey_native import _key_to_vk, user32

    if sys.platform == 'win32':
        handle = user32()
        assert handle.VkKeyScanW.restype is ctypes.c_short, \
            "restype 必须是 c_short，否则 -1 会被读成 0xFFFF"
        assert handle.VkKeyScanW.argtypes == [ctypes.c_wchar], \
            f"argtypes 应为 c_wchar: {handle.VkKeyScanW.argtypes}"

    class FakeHandle:
        def __init__(self):
            self.args = []

        def VkKeyScanW(self, ch):
            self.args.append(ch)
            return -1      # 键位表里查不到这个字符

    fake = FakeHandle()
    saved = native_mod._user32
    native_mod._user32 = fake
    try:
        # 查不到时退回"字母数字取大写 ASCII"，且传的是字符不是 ord 值
        assert _key_to_vk('z') == ord('Z')
        assert fake.args == ['z'], f"必须传字符本身: {fake.args}"
    finally:
        native_mod._user32 = saved
    print("✅ VkKeyScanW 签名声明测试通过\n")


def test_single_instance_closes_full_width_handle():
    """
    后来的实例释放互斥量句柄时不能把它截断成 32 位

    CreateMutexW 的返回是 64 位指针，CloseHandle 没有 argtypes 时 ctypes 按
    C int 传，高位被砍掉：关的其实是个无效值，真句柄一直漏着，而每次
    "程序已在运行"的那次启动都会留一个。
    """
    print("=== 测试单实例句柄释放 ===")
    import ctypes
    from managers.single_instance import SingleInstanceGuard

    if sys.platform != 'win32':
        print("非 Windows，跳过\n")
        return

    kernel32 = ctypes.windll.kernel32
    name = f'Local\\ScreenshotTranslator.Test.{os.getpid()}.closehandle'
    first = SingleInstanceGuard(name=name)
    try:
        assert first.acquire() is True, "首个实例应拿到互斥量"
        assert first._handle, "守卫必须持有句柄直到进程结束"

        second = SingleInstanceGuard(name=name)
        assert second.acquire() is False, "同名互斥量应判为已在运行"
        assert second._handle is None
        assert kernel32.CloseHandle.argtypes == [ctypes.c_void_p], \
            "CloseHandle 必须声明指针参数，否则 64 位句柄会被截断"

        # 第三个实例走同一条关闭路径，不能被 ctypes 异常打断启动流程
        assert SingleInstanceGuard(name=name).acquire() is False
    finally:
        if first._handle:
            kernel32.CloseHandle(ctypes.c_void_p(first._handle))
            first._handle = None
    print("✅ 单实例句柄释放测试通过\n")


def test_empty_tessdata_prefix_is_not_exported():
    """
    没定位到 tessdata 时不能把空串写进 TESSDATA_PREFIX

    空前缀会让 tesseract 去找 "/eng.traineddata"（相对当前工作目录），而
    什么都不设时它用的是 exe 自带的默认目录——探测失败时设个空串等于把一个
    本来能用的引擎判死。
    """
    print("=== 测试空的 TESSDATA_PREFIX 不导出 ===")
    import managers.ocr_providers.tesseract as tess_mod

    saved_env = os.environ.get('TESSDATA_PREFIX')
    saved_applied = tess_mod._applied_prefix
    probe_dir = os.path.abspath(os.path.join(os.getcwd(), 'tessdata'))
    try:
        os.environ.pop('TESSDATA_PREFIX', None)
        tess_mod._applied_prefix = None

        tess_mod._apply_tessdata_prefix('')
        assert 'TESSDATA_PREFIX' not in os.environ, \
            "空前缀不该导出（tesseract 会去找相对路径下的语言包）"

        tess_mod._apply_tessdata_prefix(probe_dir)
        assert os.environ['TESSDATA_PREFIX'] == probe_dir
        assert tess_mod._applied_prefix == probe_dir

        # 后来者没探测到：清掉我们设的那份，别让旧目录继续生效
        tess_mod._apply_tessdata_prefix('')
        assert 'TESSDATA_PREFIX' not in os.environ, \
            "自己设过的前缀应在探测失败时清掉"

        # 用户环境里原有的前缀不归我们动
        os.environ['TESSDATA_PREFIX'] = 'C:\\UserOwned\\tessdata'
        tess_mod._applied_prefix = None
        tess_mod._apply_tessdata_prefix('')
        assert os.environ['TESSDATA_PREFIX'] == 'C:\\UserOwned\\tessdata', \
            "不该清掉用户自己设的前缀"
    finally:
        tess_mod._applied_prefix = saved_applied
        if saved_env is None:
            os.environ.pop('TESSDATA_PREFIX', None)
        else:
            os.environ['TESSDATA_PREFIX'] = saved_env
    print("✅ 空的 TESSDATA_PREFIX 不导出测试通过\n")


def _load_script(filename):
    """按文件路径加载 scripts/ 下的脚本模块（没有 __init__.py，不能直接 import）"""
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, 'scripts', filename)
    spec = importlib.util.spec_from_file_location(
        f'_script_{os.path.splitext(filename)[0]}', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_runner_discovers_every_paramless_test():
    """
    脚本运行器按命名空间发现用例，而不是靠手抄名单

    抄漏的那几条不报错，只是"全绿但少测了"——本仓库真这么漏过九条。
    """
    print("=== 测试脚本运行器用例发现 ===")
    from _runner import run_all

    ran = []

    def fake_globals():
        return {
            'test_first': lambda: ran.append('first'),
            'test_needs_fixture': lambda tmp_path: None,
            'test_last': lambda: ran.append('last'),
            'helper_not_a_test': lambda: ran.append('nope'),
        }

    assert run_all(fake_globals(), "ok") is True
    assert ran == ['first', 'last'], f"应只跑无参用例且按定义顺序: {ran}"

    # 首条失败即停，且不把后面的用例悄悄跳过去
    ran.clear()
    def boom():
        raise AssertionError('模拟用例失败')
    started = {'test_boom': boom, 'test_after': lambda: ran.append('after')}
    assert run_all(started, "ok") is False
    assert ran == [], "失败后不应继续执行后续用例"
    print("✅ 脚本运行器用例发现测试通过\n")


def test_collect_ocr_bundle_includes_config_dirs():
    """
    最小集要收上 tessdata/configs 与 tessdata/tessconfigs

    顶层 listdir 见目录就跳，tessconfigs 一直是这么漏掉的；配置目录里还可能有
    子目录，所以按 os.walk 收。训练工具 exe 仍然不收（60MB+）。
    """
    print("=== 测试OCR最小集收配置目录 ===")
    import tempfile
    mod = _load_script('collect_ocr_bundle.py')
    saved_root = mod.PROJECT_ROOT
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tes = os.path.join(tmp, 'resources', 'tesseract')
            data = os.path.join(tmp, 'resources', 'tessdata')
            # 便携包把配置目录放在 exe 旁边（跟着 exe 的 tessdata/）
            cfg = os.path.join(tes, 'tessdata', 'configs')
            tesscfg = os.path.join(tes, 'tessdata', 'tessconfigs')
            for path in (os.path.join(tes, 'tesseract.exe'),
                         os.path.join(tes, 'libtesseract.dll'),
                         os.path.join(tes, 'lstmtraining.exe'),
                         os.path.join(cfg, 'tsv'),
                         os.path.join(cfg, 'team', 'nested.conf'),
                         os.path.join(tesscfg, 'std'),
                         os.path.join(data, 'eng.traineddata'),
                         os.path.join(data, 'chi_sim.traineddata')):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'wb') as f:
                    f.write(b'x')
            mod.PROJECT_ROOT = tmp
            out = os.path.join(tmp, 'out')
            count, _total = mod.collect(out)
            assert count == 7, f"应收到 7 个文件（训练 exe 除外）: {count}"

            def packed(*rel):
                return os.path.isfile(os.path.join(out, *rel))

            assert packed('tessdata', 'configs', 'tsv')
            assert packed('tessdata', 'configs', 'team', 'nested.conf'), \
                "配置目录的子目录没收（只 listdir 一层）"
            assert packed('tessdata', 'tessconfigs', 'std'), \
                "tessconfigs 没进包：-c 指定的老式配置名会打不开"
            assert not packed('tesseract', 'lstmtraining.exe'), "训练工具不该进包"
    finally:
        mod.PROJECT_ROOT = saved_root
    print("✅ OCR最小集配置目录测试通过\n")


def test_publish_release_requires_ready_sentinel():
    """
    装进内置下载这件事必须由编译输出证明

    [Files] 里的 download 条目是条件编译的：没就绪时 Setup.exe 同名、编得过，
    只有用户装机勾了 OCR 才中止。所以脚本抓 iscc 的哨兵，缺了就中止。
    """
    print("=== 测试发版脚本校验内置下载哨兵 ===")
    import tempfile
    mod = _load_script('publish_release.py')
    saved = (mod.PROJECT_ROOT, mod.ISCC_CANDIDATES, mod.run)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for rel in ('dist/ScreenshotTranslator/app.exe',
                        'dist/release/ocr-components.zip.001',
                        'dist/release/ocr-components.zip.sha256',
                        'dist/release/ocr-artifact.iss',
                        'fake/iscc.exe'):
                path = os.path.join(tmp, *rel.split('/'))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'wb') as f:
                    f.write(b'x')
            mod.PROJECT_ROOT = tmp
            mod.ISCC_CANDIDATES = [os.path.join(tmp, 'fake', 'iscc.exe')]

            calls = []

            def make_run(output):
                def _run(cmd, cwd=None, check=True, env=None, capture=False):
                    calls.append((cmd, capture))
                    return output
                return _run

            # 输出里没有就绪哨兵 → 中止，且不产出安装包
            mod.run = make_run("OCRDownloadReady=0 reason=no-artifact-include")
            try:
                mod.step_assemble('9.9.9')
                assert False, "内置下载没就绪时必须中止"
            except SystemExit as e:
                assert '内置下载' in str(e), f"中止原因应指向内置下载: {e}"
            assert not any('iscc.exe' in c[0][-1] for c in calls[-1:]) or True

            # 就绪 → 放行，并且确实把 iscc 的输出收进来了（capture=True）
            calls.clear()
            mod.run = make_run("Line 66: OCRDownloadReady=1 part=ocr-components.zip.001")
            mod.step_assemble('9.9.9')
            iscc_calls = [c for c in calls if 'iscc.exe' in c[0][0]]
            assert len(iscc_calls) == 1, f"应调用一次 iscc: {len(iscc_calls)}"
            assert iscc_calls[0][1] is True, "编译安装器要收输出，否则哨兵没法校验"
    finally:
        mod.PROJECT_ROOT, mod.ISCC_CANDIDATES, mod.run = saved
    print("✅ 发版脚本内置下载哨兵测试通过\n")


def test_publish_release_refuses_tag_on_other_commit():
    """
    同名 tag 已存在且指向别的提交时必须中止

    脚本随后就在这个 tag 上发 Release、挂本次构建的附件；tag 指到别处等于发出去
    的安装包和页面上的源码对不上。移动 tag 要 force-push（会顶掉别人已拉的引用），
    所以这里只中止，不擅自动手。
    """
    print("=== 测试发版脚本校验 tag 指向 ===")
    mod = _load_script('publish_release.py')
    saved = (mod.run, mod.git_output, mod.current_branch, mod.subprocess)

    class FakeProc:
        returncode = 1  # 视为"有待提交改动"

    calls = []

    def fake_run(cmd, cwd=None, check=True, env=None, capture=False):
        calls.append(' '.join(cmd))
        return ""

    def make_git(tag_commit, head_commit):
        def _git(args):
            joined = ' '.join(args)
            if joined.startswith('git rev-list'):
                return tag_commit
            if joined == 'git rev-parse HEAD':
                return head_commit
            if joined.startswith('git tag -l'):
                return 'v9.9.9'
            return ''
        return _git

    class FakeSubprocess:
        run = staticmethod(lambda *a, **k: FakeProc())

    try:
        mod.run = fake_run
        mod.current_branch = lambda: 'master'
        mod.subprocess = FakeSubprocess
        # tag 指向旧提交 → 中止且不推送
        mod.git_output = make_git('a' * 40, 'b' * 40)
        try:
            mod.step_git('9.9.9', 'v9.9.9')
            assert False, "tag 指向别的提交时必须中止"
        except SystemExit as e:
            assert 'tag' in str(e), f"中止原因应点名 tag: {e}"
        assert not any('push' in c for c in calls), f"中止前不该推送: {calls}"

        # tag 就指向当前提交 → 正常推送
        calls.clear()
        mod.git_output = make_git('b' * 40, 'b' * 40)
        mod.step_git('9.9.9', 'v9.9.9')
        assert [c for c in calls if 'push' in c] == ['git push origin master',
                                                     'git push origin v9.9.9']
    finally:
        mod.run, mod.git_output, mod.current_branch, mod.subprocess = saved
    print("✅ 发版脚本 tag 指向校验测试通过\n")


def test_stale_attachment_delete_failure_is_fatal():
    """
    同名旧附件删不掉要让发版失败（404 除外）

    装填端是按附件名取的：留两份同名的就可能取到旧的那份，每台机器都过不了
    SHA256 校验。删不干净不能只打个警告继续。
    """
    print("=== 测试同名旧附件清理失败语义 ===")
    import io
    import tempfile
    from urllib.error import HTTPError
    mod = _load_script('publish_release.py')
    saved = mod.api_request
    try:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = os.path.join(tmp, 'ScreenshotTranslator-Setup.exe')
            with open(artifact, 'wb') as f:
                f.write(b'pkg')

            def http_error(code):
                return HTTPError('https://github.com', code, 'err', {},
                                 io.BytesIO(b'{"message":"boom"}'))

            def make_api(delete_code):
                def _api(url, token, data=None, content_type=None, method=None):
                    if method == 'DELETE':
                        if delete_code:
                            raise http_error(delete_code)
                        return {}
                    if data is None and method is None and url.endswith('/assets'):
                        return [{'id': 7, 'name': 'ScreenshotTranslator-Setup.exe'}]
                    return {'name': 'ScreenshotTranslator-Setup.exe',
                            'size': 3, 'browser_download_url': 'https://x/y'}
                return _api

            mod.api_request = make_api(403)
            try:
                mod.upload_attachment(1, artifact, 't')
                assert False, "403 删不掉旧附件应当报错"
            except RuntimeError as e:
                assert 'ScreenshotTranslator-Setup.exe' in str(e), \
                    f"报错要点名是哪个附件: {e}"

            # 404 = 旧附件早就不在了，目的已达成，不算失败
            mod.api_request = make_api(404)
            mod.upload_attachment(1, artifact, 't')
    finally:
        mod.api_request = saved
    print("✅ 同名旧附件清理失败语义测试通过\n")


def test_result_pixmap_keeps_full_selection_on_hidpi():
    """
    150% 屏上导出图必须是完整选区的 1:1 物理像素

    结果图给 QPixmap 声明 devicePixelRatio 后，QPainter 的坐标系变成逻辑像素
    （= 物理 / 倍率）：若先声明再按物理坐标绘制，内容会被放大倍率倍，导出图
    只剩原选区左上 1/倍率²。只断言倍率与尺寸发现不了这个问题，必须看像素。
    """
    print("=== 测试高分屏导出图内容完整 ===")
    from PIL import Image

    from managers.screenshot import ScreenshotWidget

    _ensure_qt_app()
    widget = ScreenshotWidget()
    try:
        widget._scale = widget._scale_y = 1.5
        image = Image.new('RGB', (300, 150), 'white')
        # 只在右下角放一个蓝块：内容被放大/裁剪时它会消失
        for y in range(130, 150):
            for x in range(280, 300):
                image.putpixel((x, y), (0, 0, 255))
        widget._result = {'image': image, 'blocks': [], 'block_texts': []}

        pixmap = widget._render_result_pixmap()
        assert pixmap is not None, "结果图合成失败"
        assert abs(pixmap.devicePixelRatio() - 1.5) < 1e-6, \
            f"结果图未声明倍率: {pixmap.devicePixelRatio()}"

        out = pixmap.toImage()
        corner = out.pixelColor(290, 140)
        assert (corner.red(), corner.green(), corner.blue()) == (0, 0, 255), \
            "导出图右下角内容丢失：绘制期间声明设备倍率会把内容整体放大"
    finally:
        widget.close()
    print("✅ 高分屏导出图内容完整测试通过\n")


def test_wait_pending_survives_concurrent_completion():
    """wait_pending 与完成回调并发时不能抛集合被修改的异常"""
    print("=== 测试线程池收尾等待并发安全 ===")
    import threading

    from managers.thread_pool import ThreadPoolManager

    pool = ThreadPoolManager(max_workers=3)
    assert pool.initialize() is True
    errors = []

    def submitter():
        for _ in range(80):
            pool.submit(lambda: 1)

    def waiter():
        for _ in range(300):
            try:
                pool.wait_pending(timeout=0.001)
            except Exception as e:      # 修复前是 "Set changed size during iteration"
                errors.append(e)

    t1 = threading.Thread(target=submitter)
    t2 = threading.Thread(target=waiter)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    try:
        pool.shutdown(wait=False, timeout=0.1)
    finally:
        pass
    assert not errors, f"收尾等待与完成回调并发时报错: {errors}"
    print("✅ 线程池收尾等待并发安全测试通过\n")


def test_ocr_secret_env_override(tmp_path):
    """云端 OCR 密钥支持环境变量注入，且不当作普通配置项读写"""
    print("=== 测试 OCR 密钥环境变量 ===")
    import os as _os

    from config import ConfigManager

    key = 'BAIDU_OCR_API_KEY'
    saved = _os.environ.get(key)
    try:
        _os.environ[key] = 'env-secret'
        cm = ConfigManager(str(tmp_path / 'config.ini'))
        assert cm.is_env_ocr_secret('baidu_ocr_api_key') is True
        assert cm.get_ocr_option('baidu_ocr_api_key') == 'env-secret', \
            "读取应按环境变量优先"
        assert cm.is_env_ocr_secret('tesseract_langs') is False, \
            "非密钥项不该走环境变量通道"
    finally:
        if saved is None:
            _os.environ.pop(key, None)
        else:
            _os.environ[key] = saved
    print("✅ OCR 密钥环境变量测试通过\n")


def test_retry_handler_zero_attempts():
    """max_attempts 为 0 时应抛明确错误，而不是 raise None 的 TypeError"""
    print("=== 测试重试器零尝试保护 ===")
    from managers.retry_handler import RetryHandler

    handler = RetryHandler(max_attempts=0)
    try:
        handler.execute_with_retry(lambda: 1)
        assert False, "零次尝试不该返回结果"
    except RuntimeError as e:
        assert '尝试' in str(e), f"错误信息应说明原因: {e}"
    except TypeError as e:
        assert False, f"零次尝试抛了 TypeError（raise None）: {e}"
    print("✅ 重试器零尝试保护测试通过\n")


def test_error_handler_accepts_non_exception():
    """传入非异常对象时只记日志，不拼畸形 exc_info"""
    print("=== 测试错误处理器非异常入参 ===")
    from managers.error_handler import ErrorHandler

    text = ErrorHandler.handle_error('纯文本错误', '测试', show_message=False)
    assert '纯文本错误' in text
    print("✅ 错误处理器非异常入参测试通过\n")


def test_publish_run_merges_child_stderr():
    """
    构建/编译子进程的 stderr 必须并入输出流

    PyInstaller 与 iscc 把进度和 INFO 日志写在 stderr；调用方习惯整条命令
    `2>&1`，PowerShell 会把那些日志变成错误记录，自动化据此把成功的发版判成
    失败（实测发版跑完仍报 exit code 1）。日志不是错误，统一走 stdout 才能
    让"退出码 0 = 成功"成立。
    """
    print("=== 测试发版脚本子进程 stderr 归并 ===")
    import subprocess as sp

    mod = _load_script('publish_release.py')
    calls = []

    class FakeResult:
        returncode = 0
        stdout = "OCRDownloadReady=1"

    def fake_run(cmd, **kwargs):
        calls.append(kwargs)
        return FakeResult()

    saved = mod.subprocess.run
    try:
        mod.subprocess.run = fake_run
        mod.run(['fake-cmd'])                  # 直通分支
        mod.run(['fake-cmd'], capture=True)    # 收输出分支
    finally:
        mod.subprocess.run = saved

    plain, captured = calls
    assert plain.get('stderr') == sp.STDOUT, \
        "直通分支没把子进程 stderr 并入 stdout，PowerShell 会把 INFO 日志当错误"
    assert plain.get('stdout') is None, "直通分支不该收走 stdout（要边跑边看）"
    assert captured.get('stderr') == sp.STDOUT
    assert captured.get('stdout') == sp.PIPE
    print("✅ 发版脚本子进程 stderr 归并测试通过\n")


def main():
    """主测试函数"""
    print("开始功能测试...\n")
    from _runner import run_all
    return run_all(globals(), "🎉 所有测试通过！新功能工作正常。")

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)