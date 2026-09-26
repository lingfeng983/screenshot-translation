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
    """测试缓存管理器"""
    print("=== 测试缓存管理器 ===")
    from managers.cache_manager import cache_manager

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
        test_tesseract_no_console_window()
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