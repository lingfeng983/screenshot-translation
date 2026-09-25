#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全局快捷键管理器
负责处理全局快捷键相关功能
使用keyboard库实现全局热键监听
"""

import keyboard

class HotkeyManager:
    """
    全局快捷键管理器类
    负责处理全局快捷键相关功能
    """

    def __init__(self, screenshot_manager=None, config_manager=None):
        """
        初始化全局快捷键管理器

        Args:
            screenshot_manager: 截图管理器实例
            config_manager: 配置管理器实例
        """
        self.screenshot_manager = screenshot_manager
        self.config_manager = config_manager
        self.hotkey = None
        self.is_running = False
        # keyboard.add_hotkey 返回的句柄，用于精确注销
        self._hotkey_handle = None

        print("全局快捷键管理器初始化")

    def initialize(self):
        """
        初始化全局快捷键

        Returns:
            bool: 初始化是否成功
        """
        try:
            # 加载快捷键配置
            self._load_hotkey_config()

            # 注册快捷键
            if self.hotkey:
                self._register_hotkey()

            # 设置运行状态
            self.is_running = True

            print("全局快捷键初始化成功")
            return True

        except Exception as e:
            print(f"全局快捷键初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _load_hotkey_config(self):
        """
        加载快捷键配置
        """
        if self.config_manager:
            try:
                self.hotkey = self.config_manager.get_global_hotkey()
                print(f"从配置加载快捷键: {self.hotkey}")
            except Exception as e:
                print(f"加载快捷键配置失败: {e}")
                # 使用默认快捷键
                self.hotkey = 'alt+z'
                print(f"使用默认快捷键: {self.hotkey}")
        else:
            # 使用默认快捷键
            self.hotkey = 'alt+z'
            print(f"配置管理器未初始化，使用默认快捷键: {self.hotkey}")

    def _register_hotkey(self):
        """
        注册全局快捷键

        Returns:
            bool: 注册是否成功
        """
        try:
            # 先移除已注册的快捷键，避免回调重复累积
            self._unregister_hotkey()

            # 注册新的快捷键，保存句柄以便后续精确注销
            self._hotkey_handle = keyboard.add_hotkey(
                self.hotkey,
                self._on_hotkey_pressed,
                args=[]
            )

            print(f"快捷键注册成功: {self.hotkey}")
            return True

        except Exception as e:
            print(f"快捷键注册失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _unregister_hotkey(self):
        """
        移除已注册的快捷键

        只注销本管理器注册的句柄，不使用remove_all_hotkeys()，
        避免影响其他组件注册的热键。
        """
        if self._hotkey_handle is None:
            return

        try:
            keyboard.remove_hotkey(self._hotkey_handle)
            print("已移除此前注册的快捷键")
        except (KeyError, ValueError) as e:
            # 句柄已失效（如keyboard内部已清理），忽略即可
            print(f"快捷键已不存在，跳过移除: {e}")
        except Exception as e:
            print(f"移除快捷键失败: {e}")
        finally:
            self._hotkey_handle = None

    def _on_hotkey_pressed(self):
        """
        快捷键被按下时的回调
        """
        print(f"快捷键 {self.hotkey} 被按下")

        if self.screenshot_manager:
            try:
                # 直接调用截图功能，避免线程池可能的问题
                self.screenshot_manager.start_screenshot()
            except Exception as e:
                print(f"启动截图失败: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("截图管理器未初始化")



    def set_hotkey(self, hotkey):
        """
        设置并重新注册全局快捷键

        注册失败时回滚到旧快捷键，且不把非法值写入配置。

        Args:
            hotkey (str): 新的快捷键组合，如 'ctrl+shift+a'

        Returns:
            bool: 设置是否成功
        """
        previous_hotkey = self.hotkey
        try:
            self.hotkey = hotkey
            if not self._register_hotkey():
                # 注册失败：回滚到旧快捷键并尝试恢复，不写配置
                print(f"快捷键 {hotkey} 注册失败，回滚为: {previous_hotkey}")
                self.hotkey = previous_hotkey
                if previous_hotkey:
                    self._register_hotkey()
                return False

            # 保存到配置
            if self.config_manager:
                self.config_manager.set_global_hotkey(hotkey)

            print(f"快捷键已设置为: {hotkey}")
            return True

        except Exception as e:
            print(f"设置快捷键失败: {e}")
            import traceback
            traceback.print_exc()
            self.hotkey = previous_hotkey
            return False

    def get_hotkey(self):
        """
        获取当前快捷键组合

        Returns:
            str: 当前快捷键，未设置时为None
        """
        return self.hotkey

    def cleanup(self):
        """注销全局快捷键并复位运行状态"""
        try:
            self.is_running = False

            # 移除快捷键
            self._unregister_hotkey()

            print("全局快捷键已清理")

        except Exception as e:
            print(f"清理全局快捷键失败: {e}")
            import traceback
            traceback.print_exc()


