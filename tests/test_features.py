#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
功能测试脚本
测试新添加的功能是否正常工作
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

    # 横向重叠的下一行决定下沿；不相干的行不挡
    assert rr._block_bottom_limit(upper, [lower, aside], bounds) == lower.top - rr.COVER_PAD
    assert rr._block_bottom_limit(upper, [aside], bounds) == bounds.bottom()

    font = QFont()
    assert rr.bilingual_source_layout(font, 18, 12) == (0, 0), \
        "空间不足时应放弃原文行，保住译文"
    pixel_size, source_height = rr.bilingual_source_layout(font, 18, 40)
    assert source_height > 0 and pixel_size >= rr.FONT_MIN_PX
    print("✅ 译文就地覆盖排版测试通过\n")


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

def main():
    """主测试函数"""
    print("开始功能测试...\n")

    try:
        test_retry_handler()
        test_cache_manager()
        test_screenshot_widget()
        test_result_render_layout()
        test_result_render_cache()
        test_ocr_provider_reuse()
        test_app_paths_user_data()
        test_tesseract_no_console_window()
        test_theme_system()
        test_config_batch_and_sections()
        test_non_retryable_error()
        test_pin_window_keys()
        test_qt_dispatcher()

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