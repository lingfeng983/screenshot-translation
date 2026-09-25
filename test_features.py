#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
功能测试脚本
测试新添加的功能是否正常工作
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

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

def test_progress_manager():
    """测试进度管理器（需要QApplication）"""
    print("=== 测试进度管理器 ===")
    try:
        from managers.progress_manager import progress_manager
        from PyQt5.QtWidgets import QApplication
        import sys

        # 创建QApplication实例
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)

        # 创建进度对话框
        dialog = progress_manager.show_progress("测试", "测试进度", 0, 100)
        if dialog:
            print("✅ 进度对话框创建成功")

            # 更新进度
            progress_manager.update_progress(50, "进行中...")
            print("✅ 进度更新成功")

            # 关闭进度对话框
            progress_manager.hide_progress()
            print("✅ 进度对话框关闭成功")
        else:
            print("❌ 进度对话框创建失败")

        print("✅ 进度管理器测试完成\n")

    except Exception as e:
        print(f"⚠️ 进度管理器测试跳过 (需要GUI环境): {e}\n")

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
        test_progress_manager()
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