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

from logging.handlers import RotatingFileHandler

from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QLineEdit, QPushButton,
    QComboBox, QCheckBox, QGroupBox, QFormLayout, QMessageBox,
    QHBoxLayout, QLabel, QScrollArea, QFrame, QWidget
)

# 配置日志（全局唯一入口，其他模块只使用 logging.getLogger）
# 日志路径按运行形态定位：源码→项目根，exe→~/.screenshot-translation（可写）
from managers import app_paths, result_render, single_instance, styles
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
        self.app = None
        self.config_window = None

        # 应用状态
        self.is_running = False
        # 资源已清理标记，保证 cleanup 幂等
        self._cleaned_up = False

        # 资源锁
        self._resource_lock = threading.Lock()





    def _setup_exception_handler(self):
        """安装全局未捕获异常钩子"""
        sys.excepthook = self._exception_handler

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

            # 配置管理器先就位：主题存在配置里，样式表要按它生成
            self.config_manager = ConfigManager()
            styles.set_current_theme(self.config_manager.get_theme())
            styles.apply_app(self.app)
            self._apply_app_icon()

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

            # 初始化全局快捷键管理器
            self.hotkey_manager = HotkeyManager(self.screenshot_manager, self.config_manager)
            self.hotkey_manager.initialize()
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
                                    "截图翻译已就绪", "Alt+Z 开始截图翻译")
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
        try:
            hint = self.hotkey_manager.native_conflict_hint() if self.hotkey_manager else ''
        except Exception as e:
            logger.debug(f"读取快捷键冲突提示失败: {e}")
            return
        if not hint:
            return
        logger.warning(hint)

        def _show_conflict_notice():
            """托盘通知：告诉用户当前是兼容模式，换个组合就稳了"""
            try:
                if self.tray_manager:
                    self.tray_manager.show_notification("快捷键已降级为兼容模式", hint)
            except Exception as e:
                logger.warning(f"快捷键兼容模式通知失败: {e}")

        # 比首次运行提示（2.0s）晚一点：两者同时发出会互相顶掉
        notice_timer = threading.Timer(3.5, _show_conflict_notice)
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

        托盘左键双击、菜单「设置」与启动行为都会走这里：每次都新建窗口会让
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
                # 按顺序清理各组件
                if self.hotkey_manager:
                    logger.debug("清理全局快捷键")
                    self.hotkey_manager.cleanup()

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

        self.setWindowTitle("截图翻译工具 - 设置")
        # 设置项较多，内容用滚动区承载，小屏也能完整操作
        self.resize(640, 620)
        self.setMinimumSize(520, 420)

        # 应用当前主题（深色/浅色由 [UI] theme 决定，与全局样式一致）
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

        # 外观配置（界面主题）
        appearance_group = QGroupBox("外观")
        appearance_layout = _new_form()

        self.theme_combo = QComboBox()
        for theme_key, theme_label in styles.available_themes():
            self.theme_combo.addItem(theme_label, theme_key)
        current_theme = (self.config_manager.get_theme()
                         if self.config_manager else styles.DEFAULT_THEME)
        theme_index = self.theme_combo.findData(current_theme)
        if theme_index >= 0:
            self.theme_combo.setCurrentIndex(theme_index)
        # 切换即预览：先看效果，点保存后才长期生效
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        appearance_layout.addRow("界面主题:", self.theme_combo)

        theme_hint = QLabel("切换后立即预览，点“保存”后长期生效")
        theme_hint.setObjectName("hintLabel")
        appearance_layout.addRow("", theme_hint)

        appearance_group.setLayout(appearance_layout)
        layout.addWidget(appearance_group)

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

        api_group.setLayout(api_layout)
        layout.addWidget(api_group)

        # 按初始选择生成动态字段
        self._on_api_type_changed(self.api_type_combo.currentIndex())

        # OCR引擎配置
        ocr_group = QGroupBox("OCR引擎配置")
        ocr_layout = _new_form()

        # 从OCR provider注册表获取可用引擎
        from managers.ocr_providers import ocr_provider_registry
        ocr_names = ocr_provider_registry.names()

        self.ocr_type_combo = QComboBox()
        for name in ocr_names:
            self.ocr_type_combo.addItem(
                ocr_provider_registry.display_name_of(name), name)
        if self.config_manager:
            try:
                current_ocr = self.config_manager.get_ocr_type()
                ocr_index = self.ocr_type_combo.findData(current_ocr)
                if ocr_index >= 0:
                    self.ocr_type_combo.setCurrentIndex(ocr_index)
            except Exception as e:
                logger.error(f"获取OCR引擎配置失败: {e}")
        self.ocr_type_combo.currentIndexChanged.connect(self._on_ocr_type_changed)
        ocr_layout.addRow("OCR引擎:", self.ocr_type_combo)

        # 当前OCR引擎的动态字段区域
        self.ocr_fields_layout = _new_form()
        ocr_layout.addRow(self.ocr_fields_layout)
        self._ocr_field_edits = {}

        ocr_group.setLayout(ocr_layout)
        layout.addWidget(ocr_group)

        # 按初始选择生成OCR动态字段
        self._on_ocr_type_changed(self.ocr_type_combo.currentIndex())

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

        # 快捷键配置
        hotkey_group = QGroupBox("快捷键配置")
        hotkey_layout = _new_form()

        self.hotkey_edit = QLineEdit()
        if self.config_manager:
            try:
                self.hotkey_edit.setText(self.config_manager.get_global_hotkey())
            except Exception as e:
                logger.error(f"获取全局快捷键失败: {e}")
        hotkey_layout.addRow("全局快捷键:", self.hotkey_edit)

        # 快捷键的真实注册状态摆在眼前：被别的程序占用时只能退到键盘钩子，
        # 而钩子在提权窗口/锁屏切换后收不到按键——用户只觉得"偶尔失灵"。
        # 只在保存时才说就太晚了，打开设置就得看见。
        self.hotkey_status_label = QLabel("")
        # 整行宽度都要用上：占用提示是两行长句，缩在字段列里会被裁掉
        self.hotkey_status_label.setWordWrap(True)
        hotkey_layout.addRow(self.hotkey_status_label)

        hotkey_group.setLayout(hotkey_layout)
        layout.addWidget(hotkey_group)

        # 启动行为配置
        startup_group = QGroupBox("启动行为")
        startup_layout = _new_form()

        self.minimize_to_tray_check = QCheckBox()
        if self.config_manager:
            try:
                self.minimize_to_tray_check.setChecked(self.config_manager.is_minimize_to_tray())
            except Exception as e:
                logger.error(f"获取启动行为配置失败: {e}")
        startup_layout.addRow("启动后最小化到托盘:", self.minimize_to_tray_check)

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
        把快捷键的注册状态写进快捷键分组

        每次显示窗口都重算：占用是动态的——看门狗会在占用方退出后把后端升回
        系统原生注册，用户再次打开设置时应该看到恢复后的状态而不是旧文案。
        """
        label = getattr(self, 'hotkey_status_label', None)
        if label is None:
            return
        manager = getattr(self.main_app, 'hotkey_manager', None)
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

    def _on_theme_changed(self, index):
        """主题下拉切换：立即应用进程级样式做预览，保存后才落盘"""
        theme = self.theme_combo.currentData()
        if not theme:
            return
        styles.set_current_theme(theme)
        app = QApplication.instance()
        if app is not None:
            styles.apply_app(app)
        styles.apply_to(self)
        logger.info(f"主题预览: {theme}（点保存后长期生效）")

    def _current_provider(self):
        """获取当前选中的provider类，未找到时返回None"""
        from managers.providers import provider_registry
        return provider_registry.get(self.api_type_combo.currentData())

    def _on_api_type_changed(self, index):
        """API类型切换时，重新生成该API的配置字段"""
        try:
            # 清空旧字段
            while self.api_fields_layout.rowCount():
                self.api_fields_layout.removeRow(0)
            self._api_field_edits = {}
            self._env_secret_values = {}

            provider_cls = self._current_provider()
            if provider_cls is None:
                return

            # 按provider声明动态生成输入框
            for field_name, label, is_password in provider_cls.config_fields:
                edit = QLineEdit()
                if is_password:
                    edit.setEchoMode(QLineEdit.Password)
                value = self._load_field_value(field_name)
                edit.setText(value)
                # 环境变量提供的密钥不回填进明文 config.ini：界面标出来源，
                # 用户没动它就跳过写入（显式重打过的值仍按普通字段保存）
                if value and self.config_manager.is_env_secret(field_name):
                    self._env_secret_values[field_name] = value
                    edit.setPlaceholderText('值来自环境变量，保持不变则不写入配置文件')
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
                'bing_api_key': self.config_manager.get_bing_api_key,
                'baidu_app_id': lambda: self.config_manager.get_baidu_api_info()[0],
                'baidu_api_key': lambda: self.config_manager.get_baidu_api_info()[1],
                'youdao_app_id': lambda: self.config_manager.get_youdao_api_info()[0],
                'youdao_api_key': lambda: self.config_manager.get_youdao_api_info()[1],
                'llm_base_url': self.config_manager.get_llm_base_url,
                'llm_api_key': self.config_manager.get_llm_api_key,
                'llm_model': self.config_manager.get_llm_model,
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
            'llm_base_url': self.config_manager.set_llm_base_url,
            'llm_api_key': self.config_manager.set_llm_api_key,
            'llm_model': self.config_manager.set_llm_model,
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
            thread_pool_manager.submit(
                self._run_test_translation, api_type, target_lang)
        except Exception as e:
            logger.error(f"测试翻译失败: {e}")
            self.test_result_label.setText(f"❌ {str(e)[:60]}")

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
        qt_dispatcher.dispatch_to_main_thread(self._finish_test_translation, text)

    def _finish_test_translation(self, text):
        """主线程回填测试结果并恢复按钮"""
        self.test_button.setEnabled(True)
        self.test_result_label.setText(text)

    def _save_form_fields(self, persist=True):
        """
        保存当前表单的动态字段值到配置（翻译API + OCR引擎 + 截图遮罩）

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
            for field_name, edit in self._ocr_field_edits.items():
                self.config_manager.set_ocr_option(field_name, edit.text().strip())
            # 截图遮罩滑条
            self.config_manager.set_overlay_option(
                'snap_threshold', str(self.snap_slider.value()))
            self.config_manager.set_overlay_option(
                'dim_opacity', str(self.dim_slider.value()))

    def _current_ocr_provider(self):
        """获取当前选中的OCR provider类，未找到时返回None"""
        from managers.ocr_providers import ocr_provider_registry
        return ocr_provider_registry.get(self.ocr_type_combo.currentData())

    def _on_ocr_type_changed(self, index):
        """OCR引擎切换时，重新生成该引擎的配置字段"""
        try:
            # 清空旧字段
            while self.ocr_fields_layout.rowCount():
                self.ocr_fields_layout.removeRow(0)
            self._ocr_field_edits = {}

            provider_cls = self._current_ocr_provider()
            if provider_cls is None:
                return

            # 按provider声明动态生成输入框，值从 [OCR] 配置段读取
            for field_name, label, is_password in provider_cls.config_fields:
                edit = QLineEdit()
                if is_password:
                    edit.setEchoMode(QLineEdit.Password)
                if self.config_manager:
                    edit.setText(self.config_manager.get_ocr_option(field_name))
                self._ocr_field_edits[field_name] = edit
                self.ocr_fields_layout.addRow(f"{label}:", edit)
        except Exception as e:
            logger.error(f"生成OCR配置字段失败: {e}")

    def _on_save(self):
        """保存表单配置并同步到翻译与快捷键管理器"""
        try:
            if not self.config_manager:
                return

            # save 前把值取出来：后面要在批量写盘之外用
            api_type = self.api_type_combo.currentData()
            ocr_type = self.ocr_type_combo.currentData()
            target_lang = self.target_lang_combo.currentData() or 'zh'
            hotkey = self.hotkey_edit.text()
            hotkey_ok = True

            # 所有配置写入放进批量上下文：十几次 setter 只落盘一次
            with self.config_manager.batch():
                # API类型与OCR引擎类型（userData 存的是provider名称）
                self.config_manager.set_api_type(api_type)
                self.config_manager.set_ocr_type(ocr_type)
                # 目标语言与降级API
                self.config_manager.set_target_lang(target_lang)
                self.config_manager.set_fallback_api_type(
                    self.fallback_api_combo.currentData() or '')
                # 界面主题（切换时已预览生效，这里只落盘）
                theme = self.theme_combo.currentData()
                if theme:
                    self.config_manager.set_theme(theme)
                # 当前API/OCR的动态字段（密钥/URL等）与遮罩滑条
                self._save_form_fields()
                # 启动行为
                self.config_manager.set_minimize_to_tray(
                    self.minimize_to_tray_check.isChecked())
                # 全局快捷键：注册成功后由 set_hotkey 负责写配置，失败则不落盘
                if self.main_app.hotkey_manager:
                    hotkey_ok = self.main_app.hotkey_manager.set_hotkey(hotkey)
                else:
                    # 快捷键管理器不可用（启动降级场景），无法校验注册结果，仅记录
                    logger.warning(f"快捷键管理器不可用，未校验直接保存快捷键: {hotkey}")
                    self.config_manager.set_global_hotkey(hotkey)

            # 同步翻译管理器
            if self.main_app.translation_manager:
                self.main_app.translation_manager.set_api_type(api_type)
                self.main_app.translation_manager.set_target_lang(target_lang)
                self.main_app.translation_manager.reload_config()

            # 同步OCR管理器
            if self.main_app.ocr_manager:
                self.main_app.ocr_manager.set_ocr_type(ocr_type)
                self.main_app.ocr_manager.reload_config()

            if not hotkey_ok:
                # 配置已全部落盘、管理器已同步，只有热键保持原值
                fail_reason = getattr(
                    self.main_app.hotkey_manager, 'last_error', '') or \
                    f"快捷键 {hotkey} 注册失败，已保留原快捷键。"
                # 输入框要跟着回滚：留着刚输入的组合就等于告诉用户"改成了"，
                # 而实际生效的还是配置里那个
                self.hotkey_edit.setText(self.config_manager.get_global_hotkey())
                # 回滚之后窗口还开着，状态行必须跟上，不然显示的仍是刚输入的值
                self._refresh_hotkey_status()
                QMessageBox.warning(
                    self,
                    "快捷键无效",
                    f"{fail_reason}其他配置已保存。"
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
            conflict_hint = ''
            if self.main_app.hotkey_manager:
                conflict_hint = self.main_app.hotkey_manager.native_conflict_hint()
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
        self._revert_unsaved_theme()
        # 接受关闭事件，让窗口关闭
        event.accept()

    def _revert_unsaved_theme(self):
        """
        没保存就关窗时撤掉主题预览

        下拉框的"切换即预览"改的是进程级样式（app stylesheet），不落盘就关掉
        窗口，界面会一直顶着预览主题跑，和配置里的值对不上——下次别的窗口
        构造时用的也是这个错的主题。
        """
        try:
            persisted = styles.normalize_theme(
                self.config_manager.get_theme()
                if self.config_manager else styles.DEFAULT_THEME)
            if styles.current_theme() == persisted:
                return
            styles.set_current_theme(persisted)
            app = QApplication.instance()
            if app is not None:
                styles.apply_app(app)
            logger.info(f"主题预览未保存，已恢复为配置中的 {persisted}")
        except Exception as e:
            logger.warning(f"恢复主题失败: {e}")



if __name__ == '__main__':
    # 启动应用
    app = MainApp()
    app.start()
