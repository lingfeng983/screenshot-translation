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

logger = logging.getLogger('screenshot-translation')

# 防抖窗口：固定下限/上限与系统值之间的夹取范围（秒）
_DEBOUNCE_MIN = 0.30
_DEBOUNCE_MAX = 0.50
# 读取不到系统双击速度时的回退值
_DEBOUNCE_FALLBACK = 0.40


def _get_double_click_window():
    """
    计算防抖窗口：优先跟随系统双击速度，夹取到 [0.30, 0.50] 秒

    用户在 Windows「鼠标设置」里调过的双击间隔就是他们的肌肉记忆，
    跟随它可以让单击响应与双击识别都贴合个人习惯。

    Returns:
        float: 防抖窗口（秒）
    """
    try:
        import ctypes
        # GetDoubleClickTime 返回毫秒（Windows 默认 500ms，可调 200~900）
        sys_ms = ctypes.windll.user32.GetDoubleClickTime()
        if sys_ms and sys_ms > 0:
            window = sys_ms / 1000.0
            return min(max(window, _DEBOUNCE_MIN), _DEBOUNCE_MAX)
    except Exception:
        pass
    # 非 Windows 或读取失败：用回退值
    return _DEBOUNCE_FALLBACK


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

        # 左键单击/双击防抖状态
        self._click_timer = None
        self._click_lock = threading.Lock()
        # 防抖窗口：启动时读一次系统双击速度，运行期不变
        self._click_window = _get_double_click_window()
        logger.info(f"托盘防抖窗口: {self._click_window:.2f}秒")

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
                "截图翻译工具（左键截图 · 双击设置 · 右键菜单）",
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
        # 隐藏的 default 项捕获左键激活，进入防抖处理区分单击/双击
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
        左键激活回调（单击与双击的第一下都走这里）

        防抖区分：
        - 单击：等待 _DOUBLE_CLICK_WINDOW 后无第二次激活 → 开始截图
        - 双击：窗口内第二次激活 → 取消单击，打开设置
        """
        with self._click_lock:
            if self._click_timer is not None:
                # 这是双击的第二下：取消待执行的单击动作
                self._click_timer.cancel()
                self._click_timer = None
                logger.info("托盘双击：打开设置")
                # open_config_window 内部负责调度到主线程，回调线程直接调用安全
                self._on_open_config()
                return

            # 可能是单击：延迟执行，等待是否出现第二次激活
            self._click_timer = threading.Timer(
                self._click_window, self._fire_single_click)
            self._click_timer.daemon = True
            self._click_timer.start()

    def _fire_single_click(self):
        """防抖窗口结束后确认是单击：开始截图"""
        with self._click_lock:
            self._click_timer = None
        logger.info("托盘单击：开始截图")
        self._on_start_screenshot()

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

    def _on_open_config(self, icon=None, item=None):
        """
        "设置"菜单项/双击图标回调：打开设置页面
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
        """
        logger.info("托盘菜单：退出应用")
        try:
            # 先清理托盘资源
            self.cleanup()

            if self.main_app:
                # 通知主应用退出
                self.main_app.quit()
            else:
                # 直接退出
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

            # 取消待触发的单击动作，避免退出后误发截图
            with self._click_lock:
                if self._click_timer is not None:
                    self._click_timer.cancel()
                    self._click_timer = None

            if self.tray_icon:
                self.tray_icon.stop()
                self.tray_icon = None

            logger.debug("系统托盘已清理")

        except Exception as e:
            logger.error(f"清理系统托盘失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
