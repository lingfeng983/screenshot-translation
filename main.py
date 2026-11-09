#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图翻译工具主入口
负责初始化各管理器、配置日志与运行Qt事件循环
"""

import logging
import os
import sys
import threading
import webbrowser

# ONNX Runtime 必须先于 PyQt5 导入：在本机组合（Python 3.14 + PyQt5 5.15.10 +
# onnxruntime 1.31）上，先加载 QtCore 再导入 onnxruntime 会在模块初始化阶段
# 访问冲突直接终止进程，反序则正常。RapidOCR 只在截图识别时才用到，这里花
# 约 0.2s 把运行时带起来；缺依赖时留给 provider 在真正识别时报可读错误。
try:
    import onnxruntime  # noqa: F401
except ImportError:
    pass

from logging.handlers import RotatingFileHandler

from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QLineEdit, QPushButton,
    QComboBox, QCheckBox, QGroupBox, QFormLayout, QMessageBox,
    QHBoxLayout, QLabel, QScrollArea, QFrame, QWidget
)

# 配置日志（全局唯一入口，其他模块只使用 logging.getLogger）
# 日志路径按运行形态定位：源码→项目根，exe→~/.screenshot-translation（可写）
from managers import app_paths, autostart, result_render, single_instance, styles, update_checker
from managers.hotkey_native import parse_hotkey
from managers.provider_common import redact_url_query


_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


class _RedactingFormatter(logging.Formatter):
    """
    把日志里 URL 的查询串抹掉

    requests 与各家 provider 的异常字符串带着完整请求 URL，而签名参数、
    access_token 这类凭证就写在 query 里；靠每个打印点自觉脱敏迟早会漏。
    脱敏只能做在 Formatter 里而不是 handler Filter：Filter 跑在格式化之前，
    既看不见 logger.exception 那写由 exc_text 现渲染出来的调用栈，也只能靠
    改写 record.msg 生效（改完 args 就废了）。
    """

    def format(self, record):
        try:
            return redact_url_query(super().format(record))
        except Exception:
            # 兜底：脱敏本身出错时宁可写原文也不要丢日志
            return super().format(record)


def _setup_logging():
    """
    配置全局日志（其他模块只用 logging.getLogger，不再各自 basicConfig）

    日志路径按运行形态定位：源码→项目根，exe→~/.screenshot-translation（可写）。
    本函数在模块导入期就跑，数据目录连兜底都建不出来时必须降级成纯控制台而不是
    抛出去——那等于让整个程序连启动都做不到。
    """
    formatter = _RedactingFormatter(_LOG_FORMAT)
    handlers = []
    try:
        # 常驻托盘的应用日志只增不减，必须轮转，否则长期占盘
        file_handler = RotatingFileHandler(
            app_paths.log_file(), maxBytes=2 * 1024 * 1024,
            backupCount=3, encoding='utf-8')
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except OSError as e:
        print(f"日志文件不可写，本次只输出到控制台: {e}", file=sys.stderr)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)
    logging.basicConfig(level=logging.INFO, handlers=handlers)


_setup_logging()
logger = logging.getLogger('screenshot-translation')

# 导入管理器
from config import ConfigManager
from managers.screenshot import ScreenshotManager
from managers.ocr import OCRManager
from managers.translation import TranslationManager
from managers.tray import SystemTrayManager
from managers.hotkey import HotkeyManager
from managers.thread_pool import thread_pool_manager
from managers.error_handler import error_handler
from managers.qt_dispatcher import get_qt_app, qt_dispatcher

_APP_QICON = None


def _app_qicon():
    """
    程序全局窗口图标：16~256 全阶梯多尺寸 QIcon

    托盘图标同样来自 managers.icon.generate_icon，保证托盘/任务栏/
    窗口/Alt-Tab 看到的是同一套图标；多尺寸让 Windows 在任何 DPI 下
    都能取到原生清晰的那档（以前只给 64px 一张，系统拉大就糊）。
    """
    global _APP_QICON
    if _APP_QICON is not None:
        return _APP_QICON
    from PyQt5.QtGui import QIcon, QImage, QPixmap
    from managers.icon import generate_icon
    qicon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pil_img = generate_icon(size)
        data = pil_img.tobytes('raw', 'RGBA')
        qimage = QImage(data, pil_img.width, pil_img.height,
                        pil_img.width * 4, QImage.Format_RGBA8888)
        qicon.addPixmap(QPixmap.fromImage(qimage))
    _APP_QICON = qicon
    return qicon


def _hotkey_identity(value):
    """
    快捷键的唯一标识（供设置页查重）

    按解析结果归一化，而不是比字面串：ctrl/control、win/windows 别名，以及
    修饰键顺序不同（ctrl+alt+z / alt+ctrl+z）都是同一个组合。无法解析的
    组合退化为字面条目，至少能挡住完全相同的输入。

    Args:
        value (str): 用户输入的快捷键组合

    Returns:
        tuple 或 str 或 None: 可哈希的标识；空输入返回 None
    """
    raw = (value or '').replace(' ', '').lower()
    if not raw:
        return None
    parsed = parse_hotkey(raw)
    return parsed if parsed is not None else raw


class MainApp:
    """
    主应用类
    负责各管理器的初始化、生命周期管理与资源清理
    """

    # 退出时给在飞的后台任务留这么点收尾时间，到点就不再陪它们
    _EXIT_DRAIN_SECONDS = 1.5

    def __init__(self):
        """初始化主应用并注册全局异常处理"""
        logger.info("初始化主应用")

        # 旧版把可写文件放在 exe 旁边，先搬进用户目录再判断是否首次运行
        app_paths.migrate_legacy_data()
        self.first_run = not os.path.exists(app_paths.config_file())

        # 设置全局异常处理
        self._setup_exception_handler()

        # 初始化组件
        self.config_manager = None
        self.screenshot_manager = None
        self.ocr_manager = None
        self.translation_manager = None
        self.tray_manager = None
        self.hotkey_manager = None
        self.capture_hotkey_manager = None
        self.text_hotkey_manager = None
        self.fullscreen_hotkey_manager = None
        self.window_hotkey_manager = None
        self.repeat_hotkey_manager = None
        self.app = None
        self.config_window = None
        self.translate_window = None

        # 应用状态
        self.is_running = False
        # 资源已清理标记，保证 cleanup 幂等
        self._cleaned_up = False

        # 资源锁
        self._resource_lock = threading.Lock()

        # 更新检查状态（同一时间只跑一个请求，手动检查在自动检查进行中排队）
        self._update_checking = False
        self._interactive_update_pending = False
        self._update_timer = None





    def _setup_exception_handler(self):
        """安装全局未捕获异常钩子（主线程 + 工作线程）"""
        sys.excepthook = self._exception_handler

        def handle_thread_exception(args):
            # 线程池等子线程的未捕获异常默认打到 stderr；console=False 的打包
            # 程序没有控制台，整条栈都会丢，必须转进日志。
            if issubclass(args.exc_type, SystemExit):
                return
            # 只记日志：从工作线程弹模态框既会打断用户，也可能卡住退出流程
            error_handler.handle_error(
                args.exc_value, "后台线程未捕获异常",
                show_message=False, tb=args.exc_traceback)

        threading.excepthook = handle_thread_exception

    def _exception_handler(self, exc_type, exc_value, exc_traceback):
        """
        全局未捕获异常处理

        Args:
            exc_type: 异常类型
            exc_value: 异常实例
            exc_traceback: 异常调用栈
        """
        # 特殊处理键盘中断
        if issubclass(exc_type, KeyboardInterrupt):
            logger.info("收到键盘中断信号，正在优雅退出...")
            try:
                self.cleanup()
            except Exception as cleanup_error:
                logger.error(f"清理过程中出错: {cleanup_error}")
            self._force_exit(0)
            return

        # 使用统一的错误处理（栈由 hook 传进去：这里没有活动异常）
        error_handler.handle_error(exc_value, "主应用", tb=exc_traceback)

    def start(self):
        """启动应用：初始化各管理器并进入Qt事件循环"""
        logger.info("启动应用")

        # 多开会让两个实例抢同一个热键（后起的只能退到键盘钩子）、托盘上叠出
        # 两个图标，用户侧就是"快捷键时灵时不灵"。必须在注册热键之前拦住。
        if not single_instance.acquire():
            single_instance.notify_already_running()
            # 不走 _force_exit：本进程什么都没读过，落盘翻译缓存只会把已经在跑
            # 的那个实例写好的 translation_cache.json 覆盖成空表
            logging.shutdown()
            os._exit(0)
            return

        try:
            # 初始化QApplication（使用单例模式）
            self.app = get_qt_app()
            # 托盘常驻，关闭配置窗口不应结束事件循环
            self.app.setQuitOnLastWindowClosed(False)

            # 配置管理器先就位，界面固定使用浅色主题
            self.config_manager = ConfigManager()
            styles.apply_app(self.app)
            self._apply_app_icon()

            # 配置里开着自启动时，顺手把注册表命令校正到当前 exe 路径
            # （整目录覆盖升级后 exe 可能换了位置）
            self._sync_autostart()

            # 初始化OCR管理器（必须传入配置，否则 [OCR] 段的引擎与语言配置不生效）
            self.ocr_manager = OCRManager(self.config_manager)

            # 初始化翻译管理器
            self.translation_manager = TranslationManager(self.config_manager)

            # 初始化截图管理器
            self.screenshot_manager = ScreenshotManager()
            self.screenshot_manager.set_managers(self.ocr_manager, self.translation_manager)
            self.screenshot_manager.set_config_manager(self.config_manager)

            # 初始化系统托盘管理器
            self.tray_manager = SystemTrayManager(self, self.screenshot_manager)
            self.tray_manager.initialize()

            # 初始化全局快捷键管理器：翻译 / 区域截图 / 文本翻译页 /
            # 全屏截图 / 活动窗口截图 / 重复上次区域，各一条且 ID 互不相同
            self.hotkey_manager = HotkeyManager(self.screenshot_manager, self.config_manager)
            self.hotkey_manager.initialize()
            self.capture_hotkey_manager = HotkeyManager(
                self.screenshot_manager, self.config_manager, action='capture')
            self.capture_hotkey_manager.initialize()
            self.text_hotkey_manager = HotkeyManager(
                self.screenshot_manager, self.config_manager, action='text',
                on_trigger=self.open_translate_window)
            self.text_hotkey_manager.initialize()
            self.fullscreen_hotkey_manager = HotkeyManager(
                self.screenshot_manager, self.config_manager, action='fullscreen')
            self.fullscreen_hotkey_manager.initialize()
            self.window_hotkey_manager = HotkeyManager(
                self.screenshot_manager, self.config_manager, action='window')
            self.window_hotkey_manager.initialize()
            self.repeat_hotkey_manager = HotkeyManager(
                self.screenshot_manager, self.config_manager, action='repeat')
            self.repeat_hotkey_manager.initialize()
            # 原生注册被别的程序占用时只能退到键盘钩子，而钩子在提权窗口/锁屏切换后
            # 收不到按键——用户侧的感受就是“快捷键时灵时不灵”。启动时就明确提示一次，
            # 能换组合早换，而不是等他事后抱怨。
            self._notify_hotkey_conflict()

            # 初始化线程池管理器
            thread_pool_manager.initialize()

            # 设置应用状态
            self.is_running = True

            logger.info("应用启动成功")

            # 第一次往画布上画中文时 Qt 会现加载字体并整形，实测约 290ms；
            # 趁启动后的空闲把它预热掉，截图结果态首帧就不卡这一下
            QTimer.singleShot(1500, result_render.warm_up_fonts)

            # 翻译链路预热：首个请求要付 DNS+TLS 握手（约 0.3 秒），
            # 趁启动空闲提前付掉，第一次截图就少等这一截
            QTimer.singleShot(800, self._warm_up_translation)

            # 启动时处理启动行为
            self._handle_startup_behavior()

            # 更新推送：启动几秒后查一次，之后每 6 小时再查一次——托盘常驻
            # 期间也能收到新版本提醒，不用等下次重启
            self._update_timer = QTimer(self.app)
            self._update_timer.setInterval(6 * 60 * 60 * 1000)
            self._update_timer.timeout.connect(self.check_updates)
            self._update_timer.start()
            QTimer.singleShot(6000, self.check_updates)

            # 运行应用事件循环
            logger.info("应用事件循环开始运行")
            exit_code = self.app.exec_()
            logger.info(f"应用事件循环结束，退出代码: {exit_code}")
            # 事件循环正常结束也要释放热键钩子、托盘与线程池
            self.cleanup()
            self._force_exit(exit_code)

        except Exception as e:
            error_handler.handle_error(e, "应用启动")
            # 启动失败也要释放已创建的托盘/线程池等资源，否则进程可能无法退出
            try:
                self.cleanup()
            except Exception as cleanup_error:
                logger.error(f"启动失败后清理资源出错: {cleanup_error}")
            self._force_exit(1)

    def _apply_app_icon(self):
        """设置应用全局图标（窗口与任务栏）"""
        try:
            self.app.setWindowIcon(_app_qicon())
        except Exception as e:
            logger.warning(f"设置应用图标失败: {e}")

    def _sync_autostart(self):
        """配置开了开机自启动时，确保注册表命令指向当前运行路径"""
        try:
            if not self.config_manager.is_auto_start():
                return
            if not autostart.apply(True):
                logger.warning(f"开机自启动同步失败: {autostart.last_error}")
        except Exception as e:
            logger.warning(f"同步开机自启动失败: {e}")

    # ---- 更新检查 ----

    def check_updates(self, interactive=False):
        """
        检查 GitHub 上有没有新版本

        网络请求丢到线程池，结果回主线程处理；启动时的自动检查只在
        "启动时检查更新"打开时执行，托盘菜单的手动检查不受该开关限制。

        Args:
            interactive (bool): True 为用户主动点"检查更新"，会弹窗给出结果
        """
        try:
            if not interactive and not self.config_manager.is_auto_check_update():
                return
            if self._update_checking:
                # 自动检查还没回来时用户又点了手动检查：等这次结果出来
                # 按交互式处理，避免用户点了却什么都没看到
                if interactive:
                    self._interactive_update_pending = True
                return

            self._update_checking = True
            submitted = thread_pool_manager.submit(self._check_update_worker, interactive)
            if submitted is None:
                self._update_checking = False
                if interactive:
                    QMessageBox.warning(None, "检查更新", "后台任务无法启动，请稍后重试。")
        except Exception as e:
            self._update_checking = False
            logger.warning(f"启动更新检查失败: {e}")
            if interactive:
                QMessageBox.warning(None, "检查更新", f"检查更新失败：{e}")

    def _check_update_worker(self, interactive):
        """池线程：查询最新版本，结果回主线程"""
        info = None
        error = ''
        try:
            info = update_checker.check_for_update()
        except Exception as e:
            error = str(e) or e.__class__.__name__
            logger.warning(f"检查更新失败: {error}")
        try:
            qt_dispatcher.dispatch_to_main_thread(
                self._apply_update_result, info, error, interactive)
        except Exception:
            logger.exception("更新检查结果回投失败")
            try:
                # 优先让主线程走正常的复位路径（含 _interactive_update_pending）
                qt_dispatcher.dispatch_to_main_thread(
                    self._apply_update_result, info, error, interactive)
            except Exception:
                logger.exception("更新检查结果重投失败")
                # 两次都投不出去才由本线程兜底复位，否则之后每次检查
                # 都会被当成「还在检查」而跳过
                self._update_checking = False
                self._interactive_update_pending = False

    def _apply_update_result(self, info, error, interactive):
        """主线程：按检查结果推送通知或给出交互提示"""
        interactive = interactive or self._interactive_update_pending
        self._interactive_update_pending = False
        self._update_checking = False
        current = update_checker.current_version()

        if error:
            if interactive:
                QMessageBox.warning(
                    None, "检查更新",
                    f"检查更新失败：{error}\n请检查网络连接后重试。")
            return

        if info is None:
            if interactive:
                QMessageBox.information(
                    None, "检查更新", f"当前已是最新版本（v{current}）。")
            return

        logger.info(f"发现新版本 v{info.version}（当前 v{current}）")
        if interactive:
            if self._prompt_open_release_page(info):
                self._open_release_page(info.url)
            return

        # 自动检查：同一个版本只推一次，避免每次开机都弹同一条通知；
        # 想再次查看可随时从托盘菜单手动检查
        if self.config_manager.get_last_notified_version() == info.version:
            logger.debug(f"新版本 v{info.version} 已提醒过，本次不重复推送")
            return
        if self.tray_manager:
            self.tray_manager.show_notification(
                f"发现新版本 v{info.version}",
                f"当前 v{current}，右键托盘图标选择「检查更新」查看")
        self.config_manager.set_last_notified_version(info.version)

    def _prompt_open_release_page(self, info):
        """
        询问是否打开发布页

        Args:
            info (update_checker.ReleaseInfo): 新版本信息

        Returns:
            bool: 用户选择打开发布页时为 True
        """
        box = QMessageBox()
        box.setWindowTitle("检查更新")
        box.setIcon(QMessageBox.Information)
        box.setText(f"发现新版本 v{info.version}（当前 v{update_checker.current_version()}）。")
        box.setInformativeText("是否打开发布页面下载？")
        open_button = box.addButton("打开发布页", QMessageBox.AcceptRole)
        box.addButton("稍后", QMessageBox.RejectRole)
        box.exec_()
        return box.clickedButton() is open_button

    @staticmethod
    def _open_release_page(url):
        """用系统浏览器打开发布页（失败只记日志，不影响程序）"""
        try:
            webbrowser.open(url or update_checker.RELEASE_PAGE)
        except Exception as e:
            logger.warning(f"打开发布页失败: {e}")

    def _handle_startup_behavior(self):
        """根据配置决定启动后是否直接打开配置窗口"""
        if self.config_manager:
            try:
                minimize_to_tray = self.config_manager.is_minimize_to_tray()
                logger.info(f"启动行为: 最小化到托盘 = {minimize_to_tray}")

                if not minimize_to_tray:
                    # 显示配置窗口
                    self.open_config_window()
                elif self.first_run:
                    # 只在首次运行提示一次：图标常驻后每次都弹通知是噪音，
                    # 交互方式托盘 tooltip 已经写了。通知要延迟发出：
                    # 1) 避开 pystray Icon.run 未就绪的窗口期
                    # 2) QTimer 在 exec_ 之前创建不触发（Qt对象尚未关联事件循环），
                    #    threading.Timer 不依赖 Qt，最可靠
                    def _startup_notice():
                        try:
                            if self.tray_manager:
                                self.tray_manager.show_notification(
                                    "截图翻译已就绪",
                                    "Alt+Z 截图翻译 / Ctrl+Alt+T 文本翻译")
                        except Exception as e:
                            logger.warning(f"启动通知失败: {e}")

                    notice_timer = threading.Timer(2.0, _startup_notice)
                    notice_timer.daemon = True
                    notice_timer.start()
                else:
                    logger.info("非首次启动，跳过托盘提示通知")
            except Exception as e:
                logger.error(f"处理启动行为失败: {e}")

    def _notify_hotkey_conflict(self):
        """
        快捷键处于兼容模式（原生注册被占用）时，托盘就绪后提示一次

        提示要延迟发出：pystray 的 Icon.run 未就绪时 notify 会被丢掉，
        而 QTimer 在 exec_ 之前创建不触发（Qt 对象尚未关联事件循环）。
        """
        self._notify_single_hotkey_conflict(self.hotkey_manager, '截图翻译快捷键', 3.5)
        self._notify_single_hotkey_conflict(self.capture_hotkey_manager, '纯截图快捷键', 4.0)
        self._notify_single_hotkey_conflict(self.text_hotkey_manager, '文本翻译快捷键', 4.5)
        self._notify_single_hotkey_conflict(self.fullscreen_hotkey_manager, '全屏截图快捷键', 5.0)
        self._notify_single_hotkey_conflict(self.window_hotkey_manager, '活动窗口截图快捷键', 5.5)
        self._notify_single_hotkey_conflict(self.repeat_hotkey_manager, '重复上次区域快捷键', 6.0)

    def _notify_single_hotkey_conflict(self, manager, label, delay):
        """一条快捷键的兼容模式提示（没有冲突时什么都不做）"""
        try:
            hint = manager.native_conflict_hint() if manager else ''
        except Exception as e:
            logger.debug(f"读取{label}冲突提示失败: {e}")
            return
        if not hint:
            return
        logger.warning(hint)

        def _show_conflict_notice():
            """托盘通知：告诉用户当前是兼容模式，换个组合就稳了"""
            try:
                if self.tray_manager:
                    self.tray_manager.show_notification(f"{label}已降级为兼容模式", hint)
            except Exception as e:
                logger.warning(f"{label}兼容模式通知失败: {e}")

        # 比首次运行提示（2.0s）晚一点：两者同时发出会互相顶掉
        notice_timer = threading.Timer(delay, _show_conflict_notice)
        notice_timer.daemon = True
        notice_timer.start()

    def _warm_up_translation(self):
        """启动后在后台预热翻译链路（失败只记日志，不影响使用）"""
        try:
            if self.translation_manager:
                thread_pool_manager.submit(self.translation_manager.warm_up)
        except Exception as e:
            logger.warning(f"翻译链路预热调度失败: {e}")

    def open_config_window(self):
        """打开配置窗口，非主线程调用时自动调度到主线程执行"""
        logger.info("打开配置窗口")
        try:
            qt_dispatcher.dispatch_to_main_thread(self._open_config_window_main_thread)
        except Exception:
            logger.exception("打开配置窗口失败")

    def _open_config_window_main_thread(self):
        """
        在主线程显示配置窗口，同一时间只保留一个

        托盘菜单「设置」与启动行为都会走这里：每次都新建窗口会让
        设置页叠成一堆（关掉最上面那个，下面的还在），所以已存在时只置前。
        """
        try:
            logger.debug("在主线程中打开配置窗口")
            window = self._live_config_window()
            if window is not None:
                # 已打开（或关掉后仍留着的实例）：置前并抢焦点，不叠新窗口
                window.show()
                window.raise_()
                window.activateWindow()
                logger.debug("配置窗口已存在，置前显示")
                return
            # 先建再存：构造半途抛错时不能留下一个"半个窗口"的实例，
            # 否则 _live_config_window 之后每次都会复用这个缺控件的壳
            window = ConfigWindow(self)
            window.show()
            self.config_window = window
            logger.debug("配置窗口已在主线程中打开")
        except Exception as e:
            # 栈要进日志：打包后没有控制台，print_exc 那路等于什么都没留
            logger.exception("在主线程中打开配置窗口失败")
            QMessageBox.critical(None, "错误", f"打开配置窗口失败: {e}")

    def _live_config_window(self):
        """
        返回当前可复用的配置窗口实例

        Returns:
            ConfigWindow 或 None: 实例已被销毁（底层 C++ 对象没了）时返回 None
        """
        window = self.config_window
        if window is None:
            return None
        try:
            window.isVisible()   # 已销毁的包装对象在这里抛 RuntimeError
        except RuntimeError:
            self.config_window = None
            return None
        return window

    def open_translate_window(self):
        """打开文本翻译页，非主线程调用时自动调度到主线程执行"""
        logger.info("打开文本翻译页")
        try:
            qt_dispatcher.dispatch_to_main_thread(self._open_translate_window_main_thread)
        except Exception:
            logger.exception("打开文本翻译页失败")

    def _open_translate_window_main_thread(self):
        """在主线程显示文本翻译页，同一时间只保留一个"""
        try:
            window = self._live_translate_window()
            if window is not None:
                window.show()
                window.raise_()
                window.activateWindow()
                logger.debug("文本翻译页已存在，置前显示")
                return
            from managers.translate_window import TranslateWindow
            window = TranslateWindow(self)
            window.show()
            self.translate_window = window
            logger.debug("文本翻译页已在主线程中打开")
        except Exception as e:
            logger.exception("在主线程中打开文本翻译页失败")
            QMessageBox.critical(None, "错误", f"打开文本翻译页失败: {e}")

    def _live_translate_window(self):
        """
        返回当前可复用的文本翻译页实例

        Returns:
            TranslateWindow 或 None: 实例已被销毁（底层 C++ 对象没了）时返回 None
        """
        window = self.translate_window
        if window is None:
            return None
        try:
            window.isVisible()   # 已销毁的包装对象在这里抛 RuntimeError
        except RuntimeError:
            self.translate_window = None
            return None
        return window

    def quit(self):
        """退出应用：清理资源并结束事件循环"""
        logger.info("退出应用")

        # 清理资源
        self.cleanup()

        # 退出应用
        if self.app:
            self.app.quit()
        else:
            # 事件循环已经不在了：不会再有下一段代码替我们收尾，直接结束进程
            self._force_exit(0)

    def cleanup(self):
        """按序清理快捷键、托盘、线程池与Qt应用资源"""
        logger.info("清理资源")

        # 清理阶段再弹模态框等于把退出流程卡住（要用户点掉才能继续）
        error_handler.disable_modals_for_shutdown()

        with self._resource_lock:
            if self._cleaned_up:
                return
            self._cleaned_up = True
            try:
                # 先停周期任务，避免退出过程中又发起网络请求
                if self._update_timer is not None:
                    self._update_timer.stop()
                    self._update_timer = None

                # 按顺序清理各组件
                if self.hotkey_manager:
                    logger.debug("清理全局快捷键")
                    self.hotkey_manager.cleanup()

                if self.capture_hotkey_manager:
                    logger.debug("清理纯截图快捷键")
                    self.capture_hotkey_manager.cleanup()

                if self.text_hotkey_manager:
                    logger.debug("清理文本翻译快捷键")
                    self.text_hotkey_manager.cleanup()

                for manager in (self.fullscreen_hotkey_manager,
                                self.window_hotkey_manager,
                                self.repeat_hotkey_manager):
                    if manager:
                        manager.cleanup()

                if self.tray_manager:
                    logger.debug("清理系统托盘")
                    self.tray_manager.cleanup()

                # 关闭线程池：排队任务直接取消，在飞的最多再等这么点时间。
                # 真等它们收尾不划算——worker 可能正卡在一次网络读上
                logger.debug("关闭线程池")
                thread_pool_manager.shutdown(
                    wait=True, cancel_futures=True,
                    timeout=self._EXIT_DRAIN_SECONDS)

                # 释放翻译链路的连接池（复用同一个 Session，退出时不留 socket）
                if self.translation_manager:
                    logger.debug("关闭翻译连接池")
                    self.translation_manager.close()

                # OCR 侧同理：云端识别引擎自己持有 Session
                if self.ocr_manager:
                    logger.debug("关闭OCR连接池")
                    self.ocr_manager.close_providers()

                # 清理Qt应用
                if self.app:
                    logger.debug("退出Qt应用")
                    self.app.quit()
                    self.app = None

                # 更新应用状态
                self.is_running = False
                logger.info("资源清理完成")

            except Exception as e:
                logger.exception(f"清理资源失败: {e}")
                # 即使清理失败也要尽力释放资源
                if self.app:
                    try:
                        self.app.quit()
                    except Exception as e:
                        logger.error(f"退出Qt应用失败: {e}")

    def _force_exit(self, exit_code=0):
        """
        落盘缓存与日志后立即结束进程

        解释器收尾阶段会无条件 join 线程池的工作线程（`concurrent.futures` 在
        3.9 起把 worker 设成非 daemon 并注册 atexit 等待），卡在单次网络读上的
        worker 能让进程迟迟不退——实测最久约 90 秒。清理与落盘都已经在这之前
        做完，剩下的只有“别人替我们等”，所以直接结束进程。

        Args:
            exit_code (int): 进程退出码
        """
        try:
            from managers.cache_manager import cache_manager
            # atexit 不会再跑，翻译缓存必须在这里落盘
            cache_manager.flush()
        except Exception as e:
            logger.warning(f"退出前落盘缓存失败: {e}")
        try:
            logging.shutdown()
        except Exception as e:
            logger.warning(f"退出前刷新日志失败: {e}")
        os._exit(exit_code)

class ConfigWindow(QDialog):
    """
    配置窗口
    提供翻译API、快捷键与启动行为的设置界面
    """

    def __init__(self, main_app):
        """
        初始化配置窗口

        Args:
            main_app (MainApp): 主应用实例，用于读取配置与联动更新
        """
        super().__init__()
        self.main_app = main_app
        self.config_manager = main_app.config_manager
        # 测试翻译结果没回投时的兜底复位（子 QTimer，随窗口一起销毁）
        self._test_watchdog = None

        self.setWindowTitle("截图翻译工具 - 设置")
        # 去掉标题栏右上角的“？”（QDialog 默认带上下文帮助按钮）
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        # 设置项较多，内容用滚动区承载，小屏也能完整操作
        self.resize(640, 620)
        self.setMinimumSize(520, 420)

        # 应用界面样式（固定浅色主题）
        styles.apply_to(self)

        # 任务栏/窗口图标（PIL Image → QPixmap）
        try:
            self.setWindowIcon(_app_qicon())
        except Exception as e:
            logger.debug(f"设置窗口图标失败: {e}")

        # 构造失败必须冒出去：这里吞掉的话半个窗口会被主程序当成可用的
        # 设置窗口缓存起来，之后每次"打开设置"都只是 show 一个缺控件的壳
        self._init_ui()

    def _init_ui(self):
        """构建配置窗口的表单控件并载入当前配置"""
        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # 内容区放进滚动视图：设置项变多后小屏也能看到全部配置
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 10, 16, 10)
        # 分组之间紧凑排布（原 14px 偏松）
        layout.setSpacing(10)
        scroll_area.setWidget(content)

        def _new_form():
            """统一表单规格：标签右对齐、行距紧凑、字段列随窗口伸展"""
            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setVerticalSpacing(6)
            form.setHorizontalSpacing(10)
            return form

        # 翻译API配置
        api_group = QGroupBox("翻译API配置")
        api_layout = _new_form()

        # 从provider注册表获取可用的API（支持插件式扩展）
        from managers.providers import provider_registry
        provider_names = provider_registry.names()

        # API类型选择（默认Edge免费翻译，国内可直连）
        self.api_type_combo = QComboBox()
        for name in provider_names:
            self.api_type_combo.addItem(provider_registry.display_name_of(name), name)
        if self.config_manager:
            try:
                current_api = self.config_manager.get_api_type()
                index = self.api_type_combo.findData(current_api)
                if index >= 0:
                    self.api_type_combo.setCurrentIndex(index)
            except Exception as e:
                logger.error(f"获取API类型失败: {e}")
        self.api_type_combo.currentIndexChanged.connect(self._on_api_type_changed)
        api_layout.addRow("API类型:", self.api_type_combo)

        # 目标语言选择
        self.target_lang_combo = QComboBox()
        for lang_code, lang_name in self._target_languages():
            self.target_lang_combo.addItem(f"{lang_name} ({lang_code})", lang_code)
        if self.config_manager:
            try:
                lang_index = self.target_lang_combo.findData(
                    self.config_manager.get_target_lang())
                if lang_index >= 0:
                    self.target_lang_combo.setCurrentIndex(lang_index)
            except Exception as e:
                logger.error(f"获取目标语言失败: {e}")
        api_layout.addRow("目标语言:", self.target_lang_combo)

        # 降级API：主API失败后自动切换（可选）
        self.fallback_api_combo = QComboBox()
        self.fallback_api_combo.addItem("不降级", "")
        for name in provider_names:
            self.fallback_api_combo.addItem(
                provider_registry.display_name_of(name), name)
        if self.config_manager:
            try:
                fallback_index = self.fallback_api_combo.findData(
                    self.config_manager.get_fallback_api_type())
                if fallback_index >= 0:
                    self.fallback_api_combo.setCurrentIndex(fallback_index)
            except Exception as e:
                logger.error(f"获取降级API配置失败: {e}")
        api_layout.addRow("降级API:", self.fallback_api_combo)

        # 当前API的动态字段区域（根据所选provider动态生成）
        self.api_fields_layout = _new_form()
        api_layout.addRow(self.api_fields_layout)
        self._api_field_edits = {}
        # 当前显示的环境变量密钥（字段名 -> 值），保存时据此跳过回写
        self._env_secret_values = {}
        # 切换 API 类型时暂存尚未保存的输入：切走再切回来不能变回旧值
        self._api_drafts = {}
        self._last_api_type = None

        api_group.setLayout(api_layout)
        layout.addWidget(api_group)

        # 按初始选择生成动态字段
        self._on_api_type_changed(self.api_type_combo.currentIndex())

        # 截图遮罩配置（磁吸阈值/暗角透明度滑条）
        overlay_group = QGroupBox("截图遮罩")
        overlay_layout = _new_form()

        def _make_slider(lo, hi, current, suffix):
            """构造带数值标签的滑条行"""
            from PyQt5.QtWidgets import QSlider
            slider = QSlider(Qt.Horizontal)
            slider.setRange(lo, hi)
            slider.setValue(current)
            slider.setMinimumWidth(180)
            value_label = QLabel(f"{current}{suffix}")
            value_label.setMinimumWidth(46)
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            slider.valueChanged.connect(
                lambda v: value_label.setText(f"{v}{suffix}"))
            return slider, value_label

        def _slider_row(slider, value_label):
            """滑条与数值标签并排成一行（避免标签单独占一行）"""
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(slider, 1)
            row.addWidget(value_label)
            return row

        cm = self.config_manager

        def _read_int(raw, fallback):
            """读取整数配置，非法值回退默认"""
            try:
                return int(float(str(raw).strip()))
            except Exception:
                return fallback

        snap_current = _read_int(
            cm.get_overlay_option('snap_threshold', fallback='12') if cm else '12', 12)
        dim_current = _read_int(
            cm.get_overlay_option('dim_opacity', fallback='90') if cm else '90', 90)

        self.snap_slider, self.snap_value_label = _make_slider(
            0, 48, max(0, min(48, snap_current)), " px")
        overlay_layout.addRow("磁吸阈值:", _slider_row(
            self.snap_slider, self.snap_value_label))

        self.dim_slider, self.dim_value_label = _make_slider(
            0, 180, max(0, min(180, dim_current)), "")
        overlay_layout.addRow("暗角透明度:", _slider_row(
            self.dim_slider, self.dim_value_label))

        overlay_group.setLayout(overlay_layout)
        layout.addWidget(overlay_group)

        # 纯截图：输出格式/保存行为/框选辅助/滚动截图参数
        capture_group = QGroupBox("纯截图（不翻译）")
        capture_layout = _new_form()
        from PyQt5.QtWidgets import QSpinBox

        def _cap(option, fallback):
            return (cm.get_capture_option(option, fallback=fallback)
                    if cm else fallback)

        def _cap_bool(option, default=False):
            raw = str(_cap(option, 'true' if default else 'false'))
            return raw.strip().lower() in ('true', '1', 'yes', 'on')

        self.capture_format_combo = QComboBox()
        for key, label in (('png', 'PNG'), ('jpg', 'JPG'), ('webp', 'WebP')):
            self.capture_format_combo.addItem(label, key)
        self.capture_format_combo.setCurrentIndex(
            max(0, self.capture_format_combo.findData(_cap('format', 'png'))))
        capture_layout.addRow("保存格式:", self.capture_format_combo)

        self.capture_quality_spin = QSpinBox()
        self.capture_quality_spin.setRange(1, 100)
        self.capture_quality_spin.setValue(
            max(1, min(100, _read_int(_cap('jpg_quality', '90'), 90))))
        capture_layout.addRow("JPG/WebP 质量:", self.capture_quality_spin)

        self.capture_dir_edit = QLineEdit(_cap('save_dir', ''))
        self.capture_dir_edit.setPlaceholderText('留空 = 系统「图片」目录')
        capture_layout.addRow("保存目录:", self.capture_dir_edit)

        self.capture_template_edit = QLineEdit(
            _cap('filename_template', '截图_%Y%m%d_%H%M%S'))
        capture_layout.addRow("文件名模板:", self.capture_template_edit)

        self.capture_quick_check = QCheckBox("保存时不弹文件对话框")
        self.capture_quick_check.setChecked(_cap_bool('quick_save'))
        capture_layout.addRow(self.capture_quick_check)

        self.capture_after_combo = QComboBox()
        for key, label in (('none', '仅保存'), ('copy', '保存并复制'),
                           ('close', '保存并关闭'),
                           ('copy_close', '保存、复制并关闭')):
            self.capture_after_combo.addItem(label, key)
        self.capture_after_combo.setCurrentIndex(
            max(0, self.capture_after_combo.findData(_cap('after_save', 'none'))))
        capture_layout.addRow("保存后动作:", self.capture_after_combo)

        self.capture_magnifier_check = QCheckBox("框选时显示放大镜与坐标/RGB")
        self.capture_magnifier_check.setChecked(_cap_bool('magnifier', True))
        capture_layout.addRow(self.capture_magnifier_check)

        for attr, label, option, default, lo, hi, suffix in (
                ('capture_scroll_delay_spin', '滚动等待:',
                 'scroll_delay_ms', 180, 30, 2000, ' ms'),
                ('capture_scroll_step_spin', '每次滚轮格数:',
                 'scroll_step', 3, 1, 10, ' 格'),
                ('capture_scroll_max_spin', '长图最大高度:',
                 'scroll_max_height', 12000, 1000, 40000, ' px')):
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setSuffix(suffix)
            spin.setValue(max(lo, min(hi, _read_int(
                _cap(option, str(default)), default))))
            setattr(self, attr, spin)
            capture_layout.addRow(label, spin)

        capture_group.setLayout(capture_layout)
        layout.addWidget(capture_group)

        # 快捷键配置：六条各自独立注册，任意两条重复都会让一次按键触发多个动作。
        # 每条下面跟一个状态标签：被别的程序占用时只能退到键盘钩子，而钩子在
        # 提权窗口/锁屏切换后收不到按键，只等到保存时才说就太晚了。
        hotkey_group = QGroupBox("快捷键配置")
        hotkey_layout = _new_form()
        hotkey_fields = (
            ('hotkey', '截图翻译快捷键:', 'get_global_hotkey', 'hotkey_status'),
            ('capture_hotkey', '纯截图快捷键:', 'get_capture_hotkey',
             'capture_hotkey_status'),
            ('text_hotkey', '文本翻译快捷键:', 'get_translate_hotkey',
             'text_hotkey_status'),
            ('fullscreen_hotkey', '全屏截图快捷键:', 'get_fullscreen_hotkey',
             'fullscreen_hotkey_status'),
            ('window_hotkey', '活动窗口截图快捷键:', 'get_window_hotkey',
             'window_hotkey_status'),
            ('repeat_hotkey', '重复上次区域快捷键:', 'get_repeat_hotkey',
             'repeat_hotkey_status'),
        )
        for key, label_text, getter_name, status_key in hotkey_fields:
            edit = QLineEdit()
            if self.config_manager:
                try:
                    edit.setText(getattr(self.config_manager, getter_name)())
                except Exception as e:
                    logger.error(f"获取快捷键失败: {e}")
            setattr(self, f'{key}_edit', edit)
            hotkey_layout.addRow(label_text, edit)
            status = QLabel("")
            # 整行宽度都要用上：占用提示是两行长句，缩在字段列里会被裁掉
            status.setWordWrap(True)
            setattr(self, f'{status_key}_label', status)
            hotkey_layout.addRow(status)

        hotkey_group.setLayout(hotkey_layout)
        layout.addWidget(hotkey_group)

        # 启动行为配置
        startup_group = QGroupBox("启动行为")
        startup_layout = _new_form()

        self.minimize_to_tray_check = QCheckBox()
        self.auto_start_check = QCheckBox()
        self.auto_check_update_check = QCheckBox()
        if self.config_manager:
            try:
                self.minimize_to_tray_check.setChecked(self.config_manager.is_minimize_to_tray())
                self.auto_start_check.setChecked(self.config_manager.is_auto_start())
                self.auto_check_update_check.setChecked(
                    self.config_manager.is_auto_check_update())
            except Exception as e:
                logger.error(f"获取启动行为配置失败: {e}")
        startup_layout.addRow("启动后最小化到托盘:", self.minimize_to_tray_check)
        startup_layout.addRow("开机自启动:", self.auto_start_check)
        startup_layout.addRow("启动时检查更新:", self.auto_check_update_check)

        startup_group.setLayout(startup_layout)
        layout.addWidget(startup_group)

        # 操作行固定在滚动区下方：测试与保存在任何滚动位置都可点
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(16, 2, 16, 12)
        button_layout.setSpacing(8)
        self.test_button = QPushButton("测试翻译")
        self.test_button.clicked.connect(self._on_test_translation)
        button_layout.addWidget(self.test_button)
        self.test_result_label = QLabel("")
        self.test_result_label.setObjectName("testResultLabel")
        button_layout.addWidget(self.test_result_label)
        button_layout.addStretch()
        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self._on_save)
        button_layout.addWidget(self.save_button)

        outer_layout.addWidget(scroll_area, 1)
        outer_layout.addLayout(button_layout)
        self.setLayout(outer_layout)

        # 下拉框不跟随鼠标滚轮：滚轮只滚页面，改选项必须点击或键盘，
        # 避免滚动浏览设置时误改当前选中项（弹出列表自身的滚动不受影响）
        for combo in content.findChildren(QComboBox):
            combo.installEventFilter(self)

        self._refresh_hotkey_status()
        logger.info("配置窗口UI初始化成功")

    def eventFilter(self, obj, event):
        """下拉框不吃滚轮：转交给滚动区滚页面，选项只靠点击/键盘改"""
        if event.type() == QEvent.Wheel and isinstance(obj, QComboBox):
            scroll = self.findChild(QScrollArea)
            if scroll is not None:
                # 与事件冒泡等效：页面照常滚动，下拉值不变
                QApplication.sendEvent(scroll.viewport(), event)
            return True
        return super().eventFilter(obj, event)

    def _refresh_hotkey_status(self):
        """
        把六条快捷键的注册状态写进快捷键分组

        每次显示窗口都重算：占用是动态的——看门狗会在占用方退出后把后端升回
        系统原生注册，用户再次打开设置时应该看到恢复后的状态而不是旧文案。
        """
        pairs = (
            ('hotkey_status_label', 'hotkey_manager'),
            ('capture_hotkey_status_label', 'capture_hotkey_manager'),
            ('text_hotkey_status_label', 'text_hotkey_manager'),
            ('fullscreen_hotkey_status_label', 'fullscreen_hotkey_manager'),
            ('window_hotkey_status_label', 'window_hotkey_manager'),
            ('repeat_hotkey_status_label', 'repeat_hotkey_manager'),
        )
        for label_attr, manager_attr in pairs:
            self._refresh_single_hotkey_status(
                getattr(self, label_attr, None),
                getattr(self.main_app, manager_attr, None))

    def _refresh_single_hotkey_status(self, label, manager):
        """写一条快捷键的状态文案（label/manager 缺失时安全返回）"""
        if label is None:
            return
        if manager is None:
            text, warning = ("快捷键管理器不可用：本次启动未能注册全局快捷键", True)
        else:
            try:
                text, warning = manager.status_hint()
            except Exception as e:
                logger.warning(f"读取快捷键状态失败: {e}")
                return

        label.setText(text)
        # objectName 决定颜色，改完必须重新 polish，否则 QSS 还是旧的那套
        label.setObjectName("hotkeyWarning" if warning else "hintLabel")
        style = label.style()
        style.unpolish(label)
        style.polish(label)

    def showEvent(self, event):
        """每次显示设置窗口都刷新快捷键状态（占用情况会在运行期间变化）"""
        super().showEvent(event)
        self._refresh_hotkey_status()

    def _target_languages(self):
        """获取目标语言列表（翻译管理器可用时从其读取，否则用默认值）"""
        try:
            if self.main_app.translation_manager:
                return self.main_app.translation_manager.get_target_languages()
        except Exception:
            pass
        from managers.providers.base import TARGET_LANGUAGES
        return TARGET_LANGUAGES

    def _current_provider(self):
        """获取当前选中的provider类，未找到时返回None"""
        from managers.providers import provider_registry
        return provider_registry.get(self.api_type_combo.currentData())

    def _on_api_type_changed(self, index):
        """API类型切换时，重新生成该API的配置字段"""
        try:
            new_type = self.api_type_combo.currentData()
            # 切走前把当前类型的未保存输入留成草稿：用户填了一半的密钥/地址
            # 不会因为点开下拉看看别的类型就消失
            if self._last_api_type and self._api_field_edits:
                self._api_drafts[self._last_api_type] = {
                    name: edit.text() for name, edit in self._api_field_edits.items()}

            # 清空旧字段
            while self.api_fields_layout.rowCount():
                self.api_fields_layout.removeRow(0)
            self._api_field_edits = {}
            self._env_secret_values = {}

            provider_cls = self._current_provider()
            self._last_api_type = new_type
            if provider_cls is None:
                return

            draft = self._api_drafts.get(new_type) or {}
            # 按provider声明动态生成输入框
            for field_name, label, is_password in provider_cls.config_fields:
                edit = QLineEdit()
                if is_password:
                    edit.setEchoMode(QLineEdit.Password)
                value = (draft[field_name] if field_name in draft
                         else self._load_field_value(field_name))
                edit.setText(value)
                # 环境变量提供的值不回填进明文 config.ini：界面标出来源，
                # 用户没动它就跳过写入（显式重打过的值仍按普通字段保存）
                if value and self.config_manager.is_env_secret(field_name):
                    self._env_secret_values[field_name] = value
                    edit.setPlaceholderText('值来自环境变量，保持不变则不写入配置文件')
                elif field_name == 'bing_region' and value \
                        and os.environ.get('BING_REGION'):
                    self._env_secret_values[field_name] = value
                    edit.setPlaceholderText(
                        '值来自环境变量 BING_REGION，保持不变则不写入配置文件')
                self._api_field_edits[field_name] = edit
                self.api_fields_layout.addRow(f"{label}:", edit)
        except Exception as e:
            logger.error(f"生成API配置字段失败: {e}")

    def _load_field_value(self, field_name):
        """从配置读取动态字段的当前值"""
        if not self.config_manager:
            return ""
        try:
            getters = {
                'libretranslate_url': self.config_manager.get_libretranslate_url,
                # 非密钥，但 provider 侧 `_get_secret` 也是环境变量优先：
                # 界面必须显示实际生效的值，否则设了 BING_REGION 却看到空
                'bing_region': lambda: (
                    os.environ.get('BING_REGION')
                    or self.config_manager.get_translation_option('bing_region')),
                'bing_api_key': self.config_manager.get_bing_api_key,
                'baidu_app_id': lambda: self.config_manager.get_baidu_api_info()[0],
                'baidu_api_key': lambda: self.config_manager.get_baidu_api_info()[1],
                'youdao_app_id': lambda: self.config_manager.get_youdao_api_info()[0],
                'youdao_api_key': lambda: self.config_manager.get_youdao_api_info()[1],
            }
            getter = getters.get(field_name)
            if getter is not None:
                return getter()
            # 扩展provider的新字段：走通用通道读 [Translation] 段，
            # 否则插件声明的字段在界面上永远是空的
            return self.config_manager.get_translation_option(field_name)
        except Exception as e:
            logger.error(f"读取字段 {field_name} 失败: {e}")
            return ""

    def _save_field_value(self, field_name, value):
        """保存动态字段到配置"""
        if not self.config_manager:
            return
        env_value = self._env_secret_values.get(field_name)
        if env_value is not None and value == env_value:
            # 环境变量给的值本来就不在 config.ini 里，原样保存等于把明文密钥
            # 写进随包分发/可被读到的文件
            return
        setters = {
            'libretranslate_url': self.config_manager.set_libretranslate_url,
            'bing_api_key': self.config_manager.set_bing_api_key,
            'baidu_app_id': lambda v: self.config_manager.set_baidu_api_info(
                v, self.config_manager.get_baidu_api_info()[1]),
            'baidu_api_key': lambda v: self.config_manager.set_baidu_api_info(
                self.config_manager.get_baidu_api_info()[0], v),
            'youdao_app_id': lambda v: self.config_manager.set_youdao_api_info(
                v, self.config_manager.get_youdao_api_info()[1]),
            'youdao_api_key': lambda v: self.config_manager.set_youdao_api_info(
                self.config_manager.get_youdao_api_info()[0], v),
        }
        setter = setters.get(field_name)
        if setter is not None:
            setter(value)
        else:
            # 扩展provider的新字段：走通用通道写 [Translation] 段
            self.config_manager.set_translation_option(field_name, value)

    def _on_test_translation(self):
        """
        用当前表单配置（未保存也生效）测试翻译API

        真实请求放线程池：同步发起会让界面白屏到超时（最长百秒级）。
        """
        if not self.main_app.translation_manager:
            self.test_result_label.setText("❌ 翻译管理器未初始化")
            return
        try:
            from managers.providers import provider_registry
            api_type = self.api_type_combo.currentData()
            if provider_registry.get(api_type) is None:
                self.test_result_label.setText("❌ 未知API类型")
                return

            # 表单值先写入内存配置，线程里构造的 provider 才能读到最新值；
            # 用户没点保存，不该因此改他的 config.ini
            self._save_form_fields(persist=False)
            target_lang = self.target_lang_combo.currentData() or 'zh'

            self.test_button.setEnabled(False)
            self.test_result_label.setText("测试中…")
            self._start_test_watchdog()
            if thread_pool_manager.submit(
                    self._run_test_translation, api_type, target_lang) is None:
                # 线程池已关闭或拒收：没有任何线程会回填结果，按钮必须当场复原
                self._finish_test_translation("❌ 后台任务无法启动，请稍后重试")
        except Exception as e:
            logger.error(f"测试翻译失败: {e}")
            # 按钮在提交前已禁用，异常路径同样要复原，否则测试页从此点不动
            self._finish_test_translation(f"❌ {str(e)[:60]}")

    def _start_test_watchdog(self):
        """结果迟迟不回投（线程池卡死/调度失败）时兜底复原测试按钮"""
        if self._test_watchdog is None:
            self._test_watchdog = QTimer(self)
            self._test_watchdog.setSingleShot(True)
            self._test_watchdog.timeout.connect(self._on_test_timeout)
        # 翻译链最长 25s + 抖动，45s 足够；正常回投会主动停表
        self._test_watchdog.start(45000)

    def _on_test_timeout(self):
        self.test_button.setEnabled(True)
        self.test_result_label.setText("❌ 测试超时，请重试")

    def _run_test_translation(self, api_type, target_lang):
        """在池线程真实调用一次翻译，结果回主线程展示"""
        try:
            from managers.providers import provider_registry
            provider_cls = provider_registry.get(api_type)
            provider = provider_cls(
                config_manager=self.config_manager,
                session=self.main_app.translation_manager.session,
            )
            result = provider.translate(
                "Hello, world!", source='auto', target=target_lang)
            if result and not result.startswith('[翻译失败'):
                text = f"✅ {result[:50]}"
            else:
                text = f"❌ {result[:60]}"
        except Exception as e:
            logger.error(f"测试翻译失败: {e}")
            text = f"❌ {str(e)[:60]}"
        try:
            qt_dispatcher.dispatch_to_main_thread(
                self._finish_test_translation, text)
        except Exception:
            logger.exception("测试翻译结果回投失败")
            try:
                qt_dispatcher.dispatch_to_main_thread(
                    self._finish_test_translation, "❌ 结果回投失败，请重试")
            except Exception:
                # 两次都投不出去（事件循环已退出），按钮由 _test_watchdog
                # 在 45s 后兜底复原
                logger.exception("测试翻译结果重投失败")

    def _finish_test_translation(self, text):
        """主线程回填测试结果并恢复按钮"""
        self.test_button.setEnabled(True)
        self.test_result_label.setText(text)
        if self._test_watchdog is not None:
            self._test_watchdog.stop()

    def _save_form_fields(self, persist=True):
        """
        保存当前表单的动态字段值到配置（翻译API + 截图遮罩）

        Args:
            persist (bool): 是否落盘。“测试翻译”只要让未保存的表单值当场生效，
                替用户写 config.ini 是不该有的副作用
        """
        if not self.config_manager:
            return
        # 一次“保存/测试”会写十几个选项，批量上下文里只落盘一次
        with self.config_manager.batch(persist=persist):
            for field_name, edit in self._api_field_edits.items():
                self._save_field_value(field_name, edit.text().strip())
            if persist:
                # 遮罩参数只在真正"保存"时写入：点"测试翻译"不该顺手改掉
                # 本次会话后续截图的遮罩观感（内存配置会立刻影响下一次截图）
                self.config_manager.set_overlay_option(
                    'snap_threshold', str(self.snap_slider.value()))
                self.config_manager.set_overlay_option(
                    'dim_opacity', str(self.dim_slider.value()))
                self._save_capture_fields()

    def _save_capture_fields(self):
        """把「纯截图」分组写回 [Capture] 段（只在真正保存时调用）"""
        cm = self.config_manager
        if cm is None:
            return
        cm.set_capture_option(
            'format', self.capture_format_combo.currentData() or 'png')
        cm.set_capture_option('jpg_quality',
                              str(self.capture_quality_spin.value()))
        cm.set_capture_option('save_dir', self.capture_dir_edit.text().strip())
        cm.set_capture_option(
            'filename_template',
            self.capture_template_edit.text().strip() or '截图_%Y%m%d_%H%M%S')
        cm.set_capture_option(
            'quick_save',
            'true' if self.capture_quick_check.isChecked() else 'false')
        cm.set_capture_option(
            'after_save', self.capture_after_combo.currentData() or 'none')
        cm.set_capture_option(
            'magnifier',
            'true' if self.capture_magnifier_check.isChecked() else 'false')
        cm.set_capture_option('scroll_delay_ms',
                              str(self.capture_scroll_delay_spin.value()))
        cm.set_capture_option('scroll_step',
                              str(self.capture_scroll_step_spin.value()))
        cm.set_capture_option('scroll_max_height',
                              str(self.capture_scroll_max_spin.value()))

    def _apply_hotkey(self, manager, hotkey, label, save_direct):
        """
        注册一条全局快捷键（成功后由管理器自己写配置）

        Args:
            manager: HotkeyManager 或 None（管理器不可用时直接写配置）
            hotkey (str): 快捷键组合
            label (str): 出错文案里的动作名
            save_direct: 管理器不可用时直接写配置的回调

        Returns:
            tuple[bool, str]: (是否成功, 失败原因)
        """
        if manager is None:
            # 快捷键管理器不可用（启动降级场景），无法校验注册结果，仅记录
            logger.warning(f"{label}管理器不可用，未校验直接保存: {hotkey}")
            save_direct(hotkey)
            return True, ''
        try:
            if manager.set_hotkey(hotkey):
                return True, ''
        except Exception as e:
            logger.error(f"{label} {hotkey} 注册异常: {e}")
            return False, f"{label} {hotkey} 注册异常：{e}"
        return False, (getattr(manager, 'last_error', '')
                       or f"{label} {hotkey} 注册失败，已保留原快捷键。")

    def _on_save(self):
        """保存表单配置并同步到翻译与快捷键管理器"""
        try:
            if not self.config_manager:
                return

            # save 前把值取出来：后面要在批量写盘之外用
            api_type = self.api_type_combo.currentData()
            target_lang = self.target_lang_combo.currentData() or 'zh'
            hotkey = self.hotkey_edit.text()
            capture_hotkey = self.capture_hotkey_edit.text()
            text_hotkey = self.text_hotkey_edit.text()
            fullscreen_hotkey = self.fullscreen_hotkey_edit.text()
            window_hotkey = self.window_hotkey_edit.text()
            repeat_hotkey = self.repeat_hotkey_edit.text()
            auto_start = self.auto_start_check.isChecked()

            # 任意两条热键用同一个组合：键盘钩子后端会把一次按键投给多个回调，
            # 结果是一次按键同时触发多个动作。原生后端则后注册的会失败。
            entries = (
                ('截图翻译快捷键', hotkey, self.hotkey_edit,
                 self.config_manager.get_global_hotkey),
                ('纯截图快捷键', capture_hotkey, self.capture_hotkey_edit,
                 self.config_manager.get_capture_hotkey),
                ('文本翻译快捷键', text_hotkey, self.text_hotkey_edit,
                 self.config_manager.get_translate_hotkey),
                ('全屏截图快捷键', fullscreen_hotkey,
                 self.fullscreen_hotkey_edit,
                 self.config_manager.get_fullscreen_hotkey),
                ('活动窗口截图快捷键', window_hotkey,
                 self.window_hotkey_edit,
                 self.config_manager.get_window_hotkey),
                ('重复上次区域快捷键', repeat_hotkey,
                 self.repeat_hotkey_edit,
                 self.config_manager.get_repeat_hotkey),
            )
            # 按解析结果归一化查重：别名字体/修饰键顺序不同都是同一个组合，
            # 只比字面会漏判，键盘钩子后端会一次按键触发多个动作。
            identities = [identity for identity in
                          (_hotkey_identity(value)
                           for _label, value, _edit, _getter in entries)
                          if identity is not None]
            if len(set(identities)) != len(identities):
                QMessageBox.warning(
                    self, "快捷键重复",
                    "六条快捷键不能有任意两条相同，"
                    "否则一次按键会同时触发多个动作。请换一个组合。")
                return

            # 所有配置写入放进批量上下文：十几次 setter 只落盘一次
            with self.config_manager.batch():
                # API类型（userData 存的是provider名称）
                self.config_manager.set_api_type(api_type)
                # 目标语言与降级API
                self.config_manager.set_target_lang(target_lang)
                self.config_manager.set_fallback_api_type(
                    self.fallback_api_combo.currentData() or '')
                # 当前API的动态字段（密钥/URL等）与遮罩滑条
                self._save_form_fields()
                # 启动行为
                self.config_manager.set_minimize_to_tray(
                    self.minimize_to_tray_check.isChecked())
                self.config_manager.set_auto_start(auto_start)
                self.config_manager.set_auto_check_update(
                    self.auto_check_update_check.isChecked())
                # 全局快捷键：注册成功后由 set_hotkey 负责写配置，失败则不落盘。
                # 这里必须自己接住异常：让异常穿出 with 块会跳过"快捷键无效"的
                # 提示路径，用户看到的是笼统的"保存配置失败"，而其他配置其实
                # 已经随批量上下文落盘了——提示与实际状态对不上。
                hotkey_results = (
                    ('截图翻译快捷键',
                     self._apply_hotkey(
                         getattr(self.main_app, 'hotkey_manager', None),
                         hotkey, '截图翻译快捷键',
                         self.config_manager.set_global_hotkey),
                     self.hotkey_edit,
                     self.config_manager.get_global_hotkey),
                    ('纯截图快捷键',
                     self._apply_hotkey(
                         getattr(self.main_app, 'capture_hotkey_manager', None),
                         capture_hotkey, '纯截图快捷键',
                         self.config_manager.set_capture_hotkey),
                     self.capture_hotkey_edit,
                     self.config_manager.get_capture_hotkey),
                    ('文本翻译快捷键',
                     self._apply_hotkey(
                         getattr(self.main_app, 'text_hotkey_manager', None),
                         text_hotkey, '文本翻译快捷键',
                         self.config_manager.set_translate_hotkey),
                     self.text_hotkey_edit,
                     self.config_manager.get_translate_hotkey),
                    ('全屏截图快捷键',
                     self._apply_hotkey(
                         getattr(self.main_app, 'fullscreen_hotkey_manager', None),
                         fullscreen_hotkey, '全屏截图快捷键',
                         self.config_manager.set_fullscreen_hotkey),
                     self.fullscreen_hotkey_edit,
                     self.config_manager.get_fullscreen_hotkey),
                    ('活动窗口截图快捷键',
                     self._apply_hotkey(
                         getattr(self.main_app, 'window_hotkey_manager', None),
                         window_hotkey, '活动窗口截图快捷键',
                         self.config_manager.set_window_hotkey),
                     self.window_hotkey_edit,
                     self.config_manager.get_window_hotkey),
                    ('重复上次区域快捷键',
                     self._apply_hotkey(
                         getattr(self.main_app, 'repeat_hotkey_manager', None),
                         repeat_hotkey, '重复上次区域快捷键',
                         self.config_manager.set_repeat_hotkey),
                     self.repeat_hotkey_edit,
                     self.config_manager.get_repeat_hotkey),
                )

            # 保存成功：草稿里的值已经写进配置，清掉避免下次切回来又盖回旧输入
            self._api_drafts.clear()

            # 同步翻译管理器
            if self.main_app.translation_manager:
                self.main_app.translation_manager.set_api_type(api_type)
                self.main_app.translation_manager.set_target_lang(target_lang)
                self.main_app.translation_manager.reload_config()

            # 自启动落在注册表而不是 config.ini：写盘成功但注册表失败时，
            # 配置要回滚成注册表的真实状态，否则界面与开机行为对不上
            if not autostart.apply(auto_start):
                actual = autostart.is_enabled()
                self.config_manager.set_auto_start(actual)
                self.auto_start_check.setChecked(actual)
                QMessageBox.warning(
                    self, "开机自启动设置失败",
                    f"{autostart.last_error}。其他配置已保存。")
                return

            failures = [(label, result, edit, getter)
                        for label, result, edit, getter in hotkey_results
                        if not result[0]]
            if failures:
                # 配置已全部落盘、管理器已同步，只有失败的那条热键保持原值
                problems = []
                for label, (_ok, error), edit, getter in failures:
                    problems.append(error)
                    # 输入框要跟着回滚：留着刚输入的组合就等于告诉用户"改成了"，
                    # 而实际生效的还是配置里那个
                    edit.setText(getter())
                # 回滚之后窗口还开着，状态行必须跟上，不然显示的仍是刚输入的值
                self._refresh_hotkey_status()
                QMessageBox.warning(
                    self,
                    "快捷键无效",
                    f"{'；'.join(problems)}其他配置已保存。"
                )
                return

            if not self.config_manager.write_ok:
                # 写盘失败被 ConfigManager 兜成了日志（不然一处磁盘问题就能让
                # 设置窗口崩掉），用户侧必须看得见"这次没存上"
                QMessageBox.warning(
                    self, "配置未能写入",
                    "设置已在本次运行中生效，但没能写入配置文件，重启后会回到旧值。"
                    "请检查磁盘空间与配置文件权限。"
                )
                return

            # 被其他程序占用时只能退到键盘钩子：提前告知比事后抱怨"快捷键失效"好
            hints = []
            for manager in (self.main_app.hotkey_manager,
                            self.main_app.capture_hotkey_manager,
                            getattr(self.main_app, 'text_hotkey_manager', None),
                            getattr(self.main_app, 'fullscreen_hotkey_manager', None),
                            getattr(self.main_app, 'window_hotkey_manager', None),
                            getattr(self.main_app, 'repeat_hotkey_manager', None)):
                if manager:
                    hints.append(manager.native_conflict_hint())
            conflict_hint = '\n'.join(hint for hint in hints if hint)
            if conflict_hint:
                QMessageBox.information(self, "快捷键已保存（兼容模式）", conflict_hint)
                self.close()
                return

            QMessageBox.information(self, "保存成功", "配置已保存")
            self.close()

        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            QMessageBox.critical(self, "保存错误", f"保存配置失败: {e}")

    def closeEvent(self, event):
        """
        窗口关闭事件处理

        Args:
            event (QCloseEvent): Qt关闭事件对象
        """
        logger.debug("配置窗口关闭事件触发")
        # 接受关闭事件，让窗口关闭
        event.accept()



if __name__ == '__main__':
    # 启动应用
    app = MainApp()
    app.start()
