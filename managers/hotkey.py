#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全局快捷键管理器

两级后端，按可靠性优先选择：
1. **原生 RegisterHotKey**（Windows 首选）：由系统投递 WM_HOTKEY，不存在
   键盘钩子被系统静默摘掉的问题，这是"快捷键经常失效"的主因；
2. **keyboard 库钩子**（非 Windows 或原生注册被占用时回退）：额外带看门狗，
   钩子线程死掉或注册被清空时自动重建。

两条路径共用的约束：回调里只做线程切换，不做日志、不碰 Qt 控件——
钩子处理线程没有异常保护，回调抛异常会让它整个退出，此后所有快捷键都
静默失联（只能重启程序）。
"""

import logging
import threading
import time

import keyboard

from .hotkey_native import NativeHotkeyBackend
from .qt_dispatcher import qt_dispatcher

logger = logging.getLogger('screenshot-translation')


class HotkeyManager:
    """
    全局快捷键管理器类
    负责注册、切换与自愈全局快捷键，把按键事件转成截图动作
    """

    # 两次触发之间的最小间隔：keyboard 后端长按会连发（按住不放时 Windows 每隔
    # 约 30ms 补一个 keydown），防抖避免一次长按叠出一堆遮罩。窗口取 0.15s：
    # 足以吞掉机器速率的连发，又不会把用户“先关掉再按一次”的第二次按键吃掉——
    # 那种情况在用户看来就是“快捷键按了没反应”。
    _DEBOUNCE_SECONDS = 0.15
    # 看门狗检查间隔（秒）
    _WATCHDOG_INTERVAL = 15.0

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
        # keyboard 后端的注销句柄，用于精确注销
        self._hotkey_handle = None
        # 当前生效的后端名：'native' / 'keyboard' / None
        self._backend = None
        # 最近一次按键触发时间（防抖用）
        self._last_fire_ts = 0.0
        self._watchdog = None
        # 最近一次注册失败的原因文案，供设置页给出针对性提示
        self.last_error = ''
        # 原生后端实例（非 Windows 上会自动不可用）
        self.native_backend = NativeHotkeyBackend()

        logger.debug("全局快捷键管理器初始化")

    # ---- 生命周期 ----

    def initialize(self):
        """
        初始化全局快捷键

        Returns:
            bool: 快捷键是否注册成功（注册失败不阻塞程序启动，
                用户可在设置里换一个快捷键）
        """
        try:
            self._load_hotkey_config()

            registered = False
            if self.hotkey:
                registered = self._register_hotkey()

            # 无论注册结果如何都进入运行态：看门狗与清理依赖这个标志
            self.is_running = True

            if registered:
                self._start_watchdog()
                logger.info(f"全局快捷键初始化完成: {self.hotkey}"
                            f"（后端: {self._backend}）")
            else:
                logger.warning(f"全局快捷键注册失败: {self.hotkey}，"
                               f"请在设置中更换快捷键")
            return registered

        except Exception as e:
            logger.error(f"全局快捷键初始化失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False

    def _load_hotkey_config(self):
        """加载快捷键配置"""
        if self.config_manager:
            try:
                self.hotkey = self.config_manager.get_global_hotkey()
                logger.info(f"从配置加载快捷键: {self.hotkey}")
            except Exception as e:
                logger.error(f"加载快捷键配置失败: {e}")
                # 使用默认快捷键
                self.hotkey = 'alt+z'
                logger.info(f"使用默认快捷键: {self.hotkey}")
        else:
            # 使用默认快捷键
            self.hotkey = 'alt+z'
            logger.info(f"配置管理器未初始化，使用默认快捷键: {self.hotkey}")

    # ---- 注册 / 注销 ----

    def _register_hotkey(self):
        """
        注册全局快捷键：原生后端优先，失败回退键盘钩子

        两个后端都失败才算注册失败（原生失败最常见的原因是快捷键已经被
        别的程序注册走了，此时钩子后端仍可用）。

        Returns:
            bool: 注册是否成功
        """
        self._unregister_hotkey()

        if self._register_native():
            return True
        return self._register_keyboard()

    def _register_native(self):
        """用 Windows 原生接口注册；不可用或被占用时返回 False"""
        try:
            if self.native_backend.register(self.hotkey, self._on_hotkey_pressed):
                self._backend = 'native'
                self.last_error = ''
                logger.info(f"快捷键已用系统原生接口注册: {self.hotkey}")
                return True
        except Exception as e:
            logger.warning(f"原生快捷键注册异常，回退键盘钩子: {e}")
        return False

    def _register_keyboard(self):
        """
        用 keyboard 库的键盘钩子注册

        Returns:
            bool: 注册是否成功
        """
        try:
            self._hotkey_handle = keyboard.add_hotkey(
                self.hotkey,
                self._on_hotkey_pressed,
                args=[]
            )
            self._backend = 'keyboard'
            logger.info(f"快捷键已用键盘钩子注册: {self.hotkey}")
            return True

        except Exception as e:
            logger.error(f"快捷键注册失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            self.last_error = f"快捷键 {self.hotkey} 无法注册：{e}"
            self._hotkey_handle = None
            self._backend = None
            return False

    def _remove_keyboard_hook(self):
        """
        只摘掉 keyboard 后端的注册（幂等，不动原生后端）

        不使用remove_all_hotkeys()，避免影响其他组件注册的热键。
        """
        if self._hotkey_handle is None:
            return
        try:
            keyboard.remove_hotkey(self._hotkey_handle)
            logger.debug("已移除键盘钩子注册的快捷键")
        except (KeyError, ValueError) as e:
            # 句柄已失效（如keyboard内部已清理），忽略即可
            logger.debug(f"快捷键已不存在，跳过移除: {e}")
        except Exception as e:
            logger.warning(f"移除快捷键失败: {e}")
        finally:
            self._hotkey_handle = None

    def _unregister_hotkey(self):
        """注销当前后端的注册（幂等）"""
        try:
            self.native_backend.unregister()
        except Exception as e:
            logger.debug(f"注销原生快捷键失败: {e}")

        self._remove_keyboard_hook()
        self._backend = None

    # ---- 按键回调 ----

    def _on_hotkey_pressed(self):
        """
        按键回调（可能运行在键盘钩子处理线程或 Qt 主线程）

        这里任何耗时操作或异常都会造成"快捷键永久失效"：钩子处理线程没有
        try/except，回调抛异常会让该线程退出；钩子回调被拖住超过系统
        LowLevelHooksTimeout（默认 300ms）时 Windows 会把钩子静默摘掉。
        所以只做线程切换，日志与截图调度都留给主线程的 _fire。
        """
        try:
            qt_dispatcher.dispatch_to_main_thread(self._fire)
        except Exception:
            # 连调度都失败（Qt 未就绪/正在退出）也只能忽略，但绝不能外抛
            pass

    def _fire(self):
        """主线程侧：防抖后触发一次截图"""
        now = time.monotonic()
        if now - self._last_fire_ts < self._DEBOUNCE_SECONDS:
            logger.debug("快捷键在防抖窗口内重复触发，已忽略")
            return
        self._last_fire_ts = now

        logger.info(f"快捷键 {self.hotkey} 被按下")
        if not self.screenshot_manager:
            logger.error("截图管理器未初始化")
            return
        try:
            self.screenshot_manager.start_screenshot()
        except Exception as e:
            logger.error(f"启动截图失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())

    # ---- 看门狗：钩子后端的自愈 ----

    def _start_watchdog(self):
        """启动看门狗线程（只有键盘钩子后端需要）"""
        if self._backend != 'keyboard':
            return
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        self._watchdog = threading.Thread(
            target=self._watchdog_loop, name='hotkey-watchdog', daemon=True)
        self._watchdog.start()
        logger.debug("快捷键看门狗已启动")

    def _watchdog_loop(self):
        """周期性检查键盘钩子后端的健康度并自愈"""
        while self.is_running:
            time.sleep(self._WATCHDOG_INTERVAL)
            if not self.is_running:
                return
            try:
                self._watchdog_tick()
            except Exception as e:
                logger.debug(f"快捷键看门狗检查失败: {e}")

    def _watchdog_tick(self):
        """
        一次健康检查

        可检测的三类真实故障：监听/处理线程已退出（此后收不到任何按键）、
        注册表里已经没有本快捷键（收不到按键且不报错）、以及“还在用钩子”
        本身（钩子会被系统静默摘掉，见 _try_native_upgrade）。
        """
        if self._backend != 'keyboard' or not self.hotkey or not self.is_running:
            return
        if not self._keyboard_listener_alive():
            logger.warning("键盘钩子监听线程已退出，重建监听器并重新注册快捷键")
            self._revive_keyboard_listener()
            return
        if not self._keyboard_registration_alive():
            logger.warning("快捷键注册已丢失，重新注册")
            self._register_keyboard()
        # 钩子只是兜底：系统会在回调超时后静默摘掉它，提权窗口/锁屏切换期间也
        # 收不到键。原生注册失败往往只是被别的程序临时占着，所以每次检查都顺手
        # 再试一次，一旦对方松手就切回可靠的系统投递路径。
        self._try_native_upgrade()

    def _try_native_upgrade(self):
        """
        尝试把钩子后端升级回原生后端

        默认快捷键 `alt+z` 常被别的程序（其他截图/剪贴板工具）占着，用户往往
        没换快捷键就一直待在钩子后端，于是“快捷键时灵时不灵”。占用是动态的：
        对方退出后原生注册就能成功，这里抓住那个机会把后端换回来。

        Returns:
            bool: 是否已切换到原生后端
        """
        if self._backend != 'keyboard' or not self.hotkey:
            return False
        if threading.current_thread() is not threading.main_thread():
            # RegisterHotKey(hwnd=None) 把 WM_HOTKEY 投给“调用线程”的消息队列，
            # 而看门狗线程不泵消息：在这儿注册等于注册了个哑巴热键，还会因
            # 下面的摘钩子把唯一可用的路径一起废掉
            qt_dispatcher.dispatch_to_main_thread(self._try_native_upgrade)
            return False
        if not self._register_native():
            return False
        # 原生已接管：钩子必须摘掉，否则同一次按键会被两条路径各触发一次
        self._remove_keyboard_hook()
        logger.info(f"快捷键 {self.hotkey} 已从键盘钩子升级为系统原生注册")
        return True

    @staticmethod
    def _keyboard_listener_alive():
        """
        keyboard 库的监听线程与处理线程是否都还活着

        拿不到内部对象时按"健康"处理：宁可不自愈，也不要误重建
        （重建会重复安装系统钩子）。

        Returns:
            bool: 线程是否都存活
        """
        listener = getattr(keyboard, '_listener', None)
        if listener is None:
            return True
        for attr in ('listening_thread', 'processing_thread'):
            thread = getattr(listener, attr, None)
            if thread is not None and not thread.is_alive():
                return False
        return True

    def _keyboard_registration_alive(self):
        """
        本快捷键是否仍在 keyboard 的注册表里

        Returns:
            bool: 仍注册（或无法判断）时为 True
        """
        hotkeys = getattr(keyboard, '_hotkeys', None)
        if not isinstance(hotkeys, dict) or self._hotkey_handle is None:
            return True
        return self._hotkey_handle in hotkeys

    def _revive_keyboard_listener(self):
        """
        重建 keyboard 的监听器并重新注册

        threading.Thread 只能 start 一次，已退出的监听线程无法重启，只能
        整个换新；同时要清掉按键状态表——钩子中途死掉时"按下未抬起"的键
        会一直留在表里，新监听器也跟着永远匹配不上快捷键组合。
        """
        try:
            listener_cls = type(getattr(keyboard, '_listener', None))
            keyboard._listener = listener_cls()
            self._clear_keyboard_state()

            hotkeys = getattr(keyboard, '_hotkeys', None)
            if isinstance(hotkeys, dict):
                hotkeys.clear()
            self._hotkey_handle = None

            if self._register_keyboard():
                logger.info(f"键盘钩子已重建，快捷键恢复: {self.hotkey}")
        except Exception as e:
            logger.error(f"重建键盘监听器失败: {e}")

    @staticmethod
    def _clear_keyboard_state():
        """清空可能残留的按键状态（卡住的按键会让快捷键永远匹配不上）"""
        for name in ('_pressed_events', '_logically_pressed_keys'):
            state = getattr(keyboard, name, None)
            if isinstance(state, dict):
                try:
                    state.clear()
                except Exception as e:
                    logger.debug(f"清空 {name} 失败: {e}")

    # ---- 配置接口 ----

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
            self.last_error = ''
            if not self._register_hotkey():
                # 注册失败：回滚到旧快捷键并尝试恢复，不写配置。
                # 文案要先定下来——回滚再失败会覆盖 last_error，把真实原因弄丢
                logger.warning(f"快捷键 {hotkey} 注册失败，回滚为: {previous_hotkey}")
                if self.native_backend.conflict:
                    self.last_error = (
                        f"快捷键 {hotkey} 已被其它程序占用，且键盘钩子注册也失败，"
                        "已保留原快捷键。请换一个组合再试。")
                elif '无法解析快捷键' in self.native_backend.last_error:
                    self.last_error = (
                        f"快捷键 {hotkey} 无法识别，需要“修饰键+主键”的组合"
                        "（如 ctrl+alt+z），已保留原快捷键。")
                else:
                    self.last_error = (
                        self.native_backend.last_error or
                        f"快捷键 {hotkey} 注册失败") + "，已保留原快捷键。"
                failure_reason = self.last_error
                self.hotkey = previous_hotkey
                if previous_hotkey:
                    self._register_hotkey()
                self.last_error = failure_reason
                return False

            # 后端可能变过（如原生注册被占用而回退钩子），看门狗要跟着调整
            self._start_watchdog()

            # 保存到配置
            if self.config_manager:
                self.config_manager.set_global_hotkey(hotkey)

            logger.info(f"快捷键已设置为: {hotkey}")
            return True

        except Exception as e:
            logger.error(f"设置快捷键失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            self.hotkey = previous_hotkey
            return False

    def get_hotkey(self):
        """
        获取当前快捷键组合

        Returns:
            str: 当前快捷键，未设置时为None
        """
        return self.hotkey

    def native_conflict_hint(self):
        """
        快捷键被其他程序占用时的建议文案

        这种时候程序退到了键盘钩子：钩子能收到按键，但在 UAC 提权窗口获得
        焦点、锁屏/远程桌面切换等场景收不到，用户看到的就是"快捷键偶尔失效"。
        能提前告知就告知——本机实测默认的 alt+z 确实已被别的程序注册走。

        Returns:
            str: 建议文案；非占用场景返回空字符串
        """
        if self._backend != 'keyboard':
            return ''
        if not self.native_backend.conflict:
            return ''
        return (f"快捷键 {self.hotkey} 已被其他程序占用，已改用兼容方式"
                "（键盘钩子）注册：在提权窗口或锁屏切换后可能收不到按键。\n"
                "建议换一个组合（例如 ctrl+alt+z）以获得更稳定的响应。")

    def status_hint(self):
        """
        当前快捷键的真实注册状态，供设置页直接展示

        只说"注册成功"是不够的：退到键盘钩子时按键照样能触发，直到某次
        提权窗口抢了焦点才失灵，那种状态必须写在界面上。

        Returns:
            tuple[str, bool]: (文案, 是否属于需要提醒用户的异常状态)
        """
        if not self.hotkey:
            return ('未设置快捷键：填一个"修饰键+主键"的组合（如 ctrl+alt+z）后保存',
                    True)
        hint = self.native_conflict_hint()
        if hint:
            return hint, True
        if self.last_error:
            return self.last_error, True
        if self._backend == 'native':
            return f'快捷键 {self.hotkey} 已由系统注册，随时可用', False
        if self._backend == 'keyboard':
            return f'快捷键 {self.hotkey} 通过键盘钩子生效', False
        return f'快捷键 {self.hotkey} 当前未注册，点一次“保存”即可注册', True

    def cleanup(self):
        """注销全局快捷键并复位运行状态"""
        try:
            self.is_running = False

            # 移除快捷键
            self._unregister_hotkey()

            logger.debug("全局快捷键已清理")

        except Exception as e:
            logger.error(f"清理全局快捷键失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
