#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统托盘管理器
负责处理系统托盘相关功能
使用pystray库实现跨平台系统托盘支持
"""

import logging
import threading

import pystray

from .icon import generate_icon
from .qt_dispatcher import qt_dispatcher

logger = logging.getLogger('screenshot-translation')


class SystemTrayManager:
    """
    系统托盘管理器类
    负责处理系统托盘相关功能
    """

    def __init__(self, main_app=None, screenshot_manager=None):
        """
        初始化系统托盘管理器

        Args:
            main_app: 主应用实例
            screenshot_manager: 截图管理器实例
        """
        self.main_app = main_app
        self.screenshot_manager = screenshot_manager
        self.tray_icon = None
        self.is_running = False

    def initialize(self):
        """
        初始化系统托盘
        """
        try:
            # 创建托盘图标
            image = self._create_tray_icon()

            # 创建菜单
            menu = self._create_menu()

            # 创建托盘图标（悬停提示说明交互方式）
            self.tray_icon = pystray.Icon(
                "screenshot-translation",
                image,
                "截图翻译工具（左键文本翻译 · 右键菜单）",
                menu
            )

            # 启动托盘图标
            self.is_running = True

            def run_tray():
                """在独立线程中运行pystray事件循环"""
                try:
                    logger.debug("系统托盘线程开始运行")
                    self.tray_icon.run()
                    logger.debug("系统托盘线程结束运行")
                except Exception as e:
                    logger.error(f"系统托盘线程错误: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())

            # 守护线程：主程序退出时不会被托盘线程阻塞，避免残留进程
            tray_thread = threading.Thread(target=run_tray, daemon=True)
            tray_thread.start()

            logger.info("系统托盘初始化成功")
            return True

        except Exception as e:
            logger.error(f"系统托盘初始化失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False

    def _create_tray_icon(self):
        """
        创建托盘图标

        Returns:
            PIL Image: 托盘图标
        """
        # 使用与主窗口一致的程序图标
        return generate_icon(64)

    def _create_menu(self):
        """
        创建托盘菜单

        Returns:
            pystray.Menu: 托盘菜单
        """
        # 创建菜单项：
        # 隐藏的 default 项捕获左键激活，单击和双击都打开文本翻译页
        menu_items = [
            pystray.MenuItem(
                "激活",
                self._on_tray_activate,
                default=True,
                visible=False
            ),
            pystray.MenuItem(
                "开始截图",
                self._on_start_screenshot
            ),
            pystray.MenuItem(
                "仅截图（不翻译）",
                self._on_start_capture
            ),
            pystray.MenuItem(
                "文本翻译",
                self._on_open_translate
            ),
            pystray.MenuItem(
                "检查更新",
                self._on_check_update
            ),
            pystray.MenuItem(
                "设置",
                self._on_open_config
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "退出",
                self._on_exit
            )
        ]

        return pystray.Menu(*menu_items)

    def _on_tray_activate(self, icon=None, item=None):
        """
        左键激活回调

        Windows 上单击与双击都会走到这里：两种操作统一打开文本翻译页。
        双击时第二击再调用一次也只是把已打开的窗口置前，不会叠出新窗口。
        """
        logger.info("托盘左键激活：打开文本翻译页")
        self._on_open_translate()

    def _on_start_screenshot(self, icon=None, item=None):
        """
        开始截图菜单项回调
        """
        logger.info("托盘菜单：开始截图")
        if self.screenshot_manager:
            try:
                # 直接调用截图功能，避免线程池可能的问题
                self.screenshot_manager.start_screenshot()
            except Exception as e:
                logger.error(f"启动截图失败: {e}")
                import traceback
                logger.debug(traceback.format_exc())
        else:
            logger.error("截图管理器未初始化")

    def _on_start_capture(self, icon=None, item=None):
        """仅截图菜单项回调：框选后不识别、不翻译"""
        logger.info("托盘菜单：仅截图")
        if self.screenshot_manager:
            try:
                self.screenshot_manager.start_capture()
            except Exception as e:
                logger.error(f"启动仅截图失败: {e}")
                import traceback
                logger.debug(traceback.format_exc())
        else:
            logger.error("截图管理器未初始化")

    def _on_open_translate(self, icon=None, item=None):
        """
        "文本翻译"菜单项回调：打开输入翻译页面
        """
        logger.info("托盘菜单：文本翻译")
        if self.main_app:
            try:
                # open_translate_window 内部已负责调度到主线程
                self.main_app.open_translate_window()
            except Exception as e:
                logger.error(f"打开文本翻译页失败: {e}")
                import traceback
                logger.debug(traceback.format_exc())
        else:
            logger.error("主应用未初始化")

    def _on_check_update(self, icon=None, item=None):
        """
        "检查更新"菜单项回调：手动检查并弹出结果

        检查本身在后台线程跑，结果会由主应用投回主线程再弹窗。
        """
        logger.info("托盘菜单：检查更新")
        if self.main_app:
            try:
                self.main_app.check_updates(interactive=True)
            except Exception as e:
                logger.error(f"检查更新失败: {e}")
                import traceback
                logger.debug(traceback.format_exc())
        else:
            logger.error("主应用未初始化")

    def _on_open_config(self, icon=None, item=None):
        """
        "设置"菜单项回调：打开设置页面
        """
        logger.info("打开设置")
        if self.main_app:
            try:
                # open_config_window 内部已负责调度到主线程
                self.main_app.open_config_window()
            except Exception as e:
                logger.error(f"打开设置窗口失败: {e}")
                import traceback
                logger.debug(traceback.format_exc())
        else:
            logger.error("主应用未初始化")

    def _on_exit(self, icon=None, item=None):
        """
        退出菜单项回调

        回调跑在 pystray 自己的线程里：直接在这调用 `QApplication.quit()`
        只是给主线程置个退出标志，事件循环还在睡，用户看到的就是
        “点了退出没反应”，要等下一个事件才被带走。所以把退出动作投回主线程。
        """
        logger.info("托盘菜单：退出应用")
        try:
            # 先清理托盘资源（只碰 pystray 侧，不涉及 Qt 对象）
            self.cleanup()
        except Exception as e:
            logger.error(f"清理托盘资源失败: {e}")

        try:
            if self.main_app:
                # 通知主应用退出：必须在 Qt 主线程执行
                qt_dispatcher.dispatch_to_main_thread(self.main_app.quit)
            else:
                # 没有主应用可通知，直接结束进程
                import sys
                sys.exit(0)
        except Exception as e:
            logger.error(f"退出应用时发生错误: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            # 强制退出
            import sys
            sys.exit(1)

    def show_notification(self, title, message):
        """
        显示托盘通知

        Args:
            title (str): 通知标题
            message (str): 通知内容
        """
        try:
            if self.tray_icon:
                # pystray 在 Windows 上支持 notify 接口
                self.tray_icon.notify(message, title)
                # 部分平台通知常驻，几秒后移除
                timer = threading.Timer(5.0, self._remove_notification)
                timer.daemon = True
                timer.start()
                logger.info(f"托盘通知: {title} - {message}")
            else:
                logger.warning("托盘图标未就绪，通知未显示")
        except Exception as e:
            logger.warning(f"显示通知失败: {e}")

    def _remove_notification(self):
        """移除托盘通知（平台不支持时静默忽略）"""
        try:
            if self.tray_icon:
                self.tray_icon.remove_notification()
        except Exception:
            pass

    def cleanup(self):
        """停止托盘图标并释放相关资源"""
        try:
            self.is_running = False

            if self.tray_icon:
                self.tray_icon.stop()
                self.tray_icon = None

            logger.debug("系统托盘已清理")

        except Exception as e:
            logger.error(f"清理系统托盘失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
