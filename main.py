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

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QLineEdit, QPushButton,
    QComboBox, QCheckBox, QGroupBox, QFormLayout, QMessageBox,
    QHBoxLayout, QLabel
)

# 配置日志（全局唯一入口，其他模块只使用 logging.getLogger）
# 日志路径按运行形态定位：源码→项目根，exe→~/.screenshot-translation（可写）
from managers import app_paths, result_render
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        # 常驻托盘的应用日志只增不减，必须轮转，否则长期占盘
        RotatingFileHandler(app_paths.log_file(), maxBytes=2 * 1024 * 1024,
                            backupCount=3, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
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


class MainApp:
    """
    主应用类
    负责各管理器的初始化、生命周期管理与资源清理
    """

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
            finally:
                sys.exit(0)

        # 使用统一的错误处理
        error_handler.handle_error(exc_value, "主应用")

    def start(self):
        """启动应用：初始化各管理器并进入Qt事件循环"""
        logger.info("启动应用")

        try:
            # 初始化QApplication（使用单例模式）
            self.app = get_qt_app()
            # 托盘常驻，关闭配置窗口不应结束事件循环
            self.app.setQuitOnLastWindowClosed(False)

            # 全局深色蓝主题与程序图标
            from managers import styles
            self.app.setStyleSheet(styles.STYLE_SHEET)
            self._apply_app_icon()

            # 初始化配置管理器
            self.config_manager = ConfigManager()

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

            # 初始化线程池管理器
            thread_pool_manager.initialize()

            # 设置应用状态
            self.is_running = True

            logger.info("应用启动成功")

            # 第一次往画布上画中文时 Qt 会现加载字体并整形，实测约 290ms；
            # 趁启动后的空闲把它预热掉，截图结果态首帧就不卡这一下
            QTimer.singleShot(1500, result_render.warm_up_fonts)

            # 启动时处理启动行为
            self._handle_startup_behavior()

            # 运行应用事件循环
            logger.info("应用事件循环开始运行")
            exit_code = self.app.exec_()
            logger.info(f"应用事件循环结束，退出代码: {exit_code}")
            # 事件循环正常结束也要释放热键钩子、托盘与线程池
            self.cleanup()
            sys.exit(exit_code)

        except Exception as e:
            error_handler.handle_error(e, "应用启动")
            # 启动失败也要释放已创建的托盘/线程池等资源，否则进程可能无法退出
            try:
                self.cleanup()
            except Exception as cleanup_error:
                logger.error(f"启动失败后清理资源出错: {cleanup_error}")
            sys.exit(1)

    def _apply_app_icon(self):
        """设置应用全局图标（窗口与任务栏）"""
        try:
            from managers.icon import generate_icon
            from PyQt5.QtGui import QIcon, QImage, QPixmap
            pil_img = generate_icon(64)
            data = pil_img.tobytes('raw', 'RGBA')
            qimage = QImage(data, pil_img.width, pil_img.height,
                            pil_img.width * 4, QImage.Format_RGBA8888)
            self.app.setWindowIcon(QIcon(QPixmap.fromImage(qimage)))
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

    def open_config_window(self):
        """打开配置窗口，非主线程调用时自动调度到主线程执行"""
        logger.info("打开配置窗口")
        try:
            qt_dispatcher.dispatch_to_main_thread(self._open_config_window_main_thread)
        except Exception as e:
            logger.error(f"打开配置窗口失败: {e}")
            import traceback
            traceback.print_exc()

    def _open_config_window_main_thread(self):
        """在主线程创建并显示配置窗口"""
        try:
            logger.debug("在主线程中打开配置窗口")
            self.config_window = ConfigWindow(self)
            self.config_window.show()
            logger.debug("配置窗口已在主线程中打开")
        except Exception as e:
            logger.error(f"在主线程中打开配置窗口失败: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(None, "错误", f"打开配置窗口失败: {e}")



    def quit(self):
        """退出应用：清理资源并结束事件循环"""
        logger.info("退出应用")

        # 清理资源
        self.cleanup()

        # 退出应用
        if self.app:
            self.app.quit()
        else:
            sys.exit(0)

    def cleanup(self):
        """按序清理快捷键、托盘、线程池与Qt应用资源"""
        logger.info("清理资源")

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

                # 关闭线程池：取消尚未开始的任务，避免退出时被在飞的翻译重试阻塞数分钟
                logger.debug("关闭线程池")
                thread_pool_manager.shutdown(wait=False, cancel_futures=True)

                # 清理Qt应用
                if self.app:
                    logger.debug("退出Qt应用")
                    self.app.quit()
                    self.app = None

                # 更新应用状态
                self.is_running = False
                logger.info("资源清理完成")

            except Exception as e:
                logger.error(f"清理资源失败: {e}")
                import traceback
                traceback.print_exc()
                # 即使清理失败也要尽力释放资源
                if self.app:
                    try:
                        self.app.quit()
                    except:
                        pass

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
        self.resize(640, 560)

        # 应用统一深色蓝主题
        from managers import styles
        styles.apply_to(self)

        # 任务栏/窗口图标（PIL Image → QPixmap）
        try:
            from managers.icon import generate_icon
            from PyQt5.QtGui import QIcon, QImage, QPixmap
            from PyQt5.QtCore import Qt
            pil_img = generate_icon(64)
            data = pil_img.tobytes('raw', 'RGBA')
            qimage = QImage(data, pil_img.width, pil_img.height,
                            pil_img.width * 4, QImage.Format_RGBA8888)
            self.setWindowIcon(QIcon(QPixmap.fromImage(qimage)))
        except Exception as e:
            logger.debug(f"设置窗口图标失败: {e}")

        try:
            self._init_ui()
        except Exception as e:
            logger.error(f"初始化配置窗口失败: {e}")
            import traceback
            traceback.print_exc()

    def _init_ui(self):
        """构建配置窗口的表单控件并载入当前配置"""
        try:
            layout = QVBoxLayout()

            # 翻译API配置
            api_group = QGroupBox("翻译API配置")
            api_layout = QFormLayout()

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
            self.api_fields_layout = QFormLayout()
            api_layout.addRow(self.api_fields_layout)
            self._api_field_edits = {}

            api_group.setLayout(api_layout)
            layout.addWidget(api_group)

            # 按初始选择生成动态字段
            self._on_api_type_changed(self.api_type_combo.currentIndex())

            # OCR引擎配置
            ocr_group = QGroupBox("OCR引擎配置")
            ocr_layout = QFormLayout()

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
            self.ocr_fields_layout = QFormLayout()
            ocr_layout.addRow(self.ocr_fields_layout)
            self._ocr_field_edits = {}

            ocr_group.setLayout(ocr_layout)
            layout.addWidget(ocr_group)

            # 按初始选择生成OCR动态字段
            self._on_ocr_type_changed(self.ocr_type_combo.currentIndex())

            # 截图遮罩配置（磁吸阈值/暗角透明度滑条）
            overlay_group = QGroupBox("截图遮罩")
            overlay_layout = QFormLayout()

            def _make_slider(lo, hi, current, suffix):
                """构造带数值标签的滑条行"""
                from PyQt5.QtWidgets import QSlider, QLabel
                from PyQt5.QtCore import Qt as QtCore
                slider = QSlider(QtCore.Horizontal)
                slider.setRange(lo, hi)
                slider.setValue(current)
                value_label = QLabel(f"{current}{suffix}")
                slider.valueChanged.connect(
                    lambda v: value_label.setText(f"{v}{suffix}"))
                return slider, value_label

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
            overlay_layout.addRow("磁吸阈值:", self.snap_slider)
            overlay_layout.addRow("", self.snap_value_label)

            self.dim_slider, self.dim_value_label = _make_slider(
                0, 180, max(0, min(180, dim_current)), "")
            overlay_layout.addRow("暗角透明度:", self.dim_slider)
            overlay_layout.addRow("", self.dim_value_label)

            overlay_group.setLayout(overlay_layout)
            layout.addWidget(overlay_group)

            # 测试按钮行
            test_layout = QHBoxLayout()
            self.test_button = QPushButton("测试翻译")
            self.test_button.clicked.connect(self._on_test_translation)
            test_layout.addWidget(self.test_button)
            self.test_result_label = QLabel("")
            test_layout.addWidget(self.test_result_label)
            test_layout.addStretch()
            layout.addLayout(test_layout)

            # 快捷键配置
            hotkey_group = QGroupBox("快捷键配置")
            hotkey_layout = QFormLayout()

            self.hotkey_edit = QLineEdit()
            if self.config_manager:
                try:
                    self.hotkey_edit.setText(self.config_manager.get_global_hotkey())
                except Exception as e:
                    logger.error(f"获取全局快捷键失败: {e}")
            hotkey_layout.addRow("全局快捷键:", self.hotkey_edit)

            hotkey_group.setLayout(hotkey_layout)
            layout.addWidget(hotkey_group)

            # 启动行为配置
            startup_group = QGroupBox("启动行为")
            startup_layout = QFormLayout()

            self.minimize_to_tray_check = QCheckBox()
            if self.config_manager:
                try:
                    self.minimize_to_tray_check.setChecked(self.config_manager.is_minimize_to_tray())
                except Exception as e:
                    logger.error(f"获取启动行为配置失败: {e}")
            startup_layout.addRow("启动后最小化到托盘:", self.minimize_to_tray_check)

            startup_group.setLayout(startup_layout)
            layout.addWidget(startup_group)

            # 按钮
            button_layout = QHBoxLayout()
            button_layout.addStretch()
            self.save_button = QPushButton("保存")
            self.save_button.setObjectName("primary")
            self.save_button.clicked.connect(self._on_save)
            button_layout.addWidget(self.save_button)

            layout.addLayout(button_layout)

            self.setLayout(layout)
            logger.info("配置窗口UI初始化成功")
        except Exception as e:
            logger.error(f"初始化配置窗口UI失败: {e}")
            import traceback
            traceback.print_exc()

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
            # 清空旧字段
            while self.api_fields_layout.rowCount():
                self.api_fields_layout.removeRow(0)
            self._api_field_edits = {}

            provider_cls = self._current_provider()
            if provider_cls is None:
                return

            # 按provider声明动态生成输入框
            for field_name, label, is_password in provider_cls.config_fields:
                edit = QLineEdit()
                if is_password:
                    edit.setEchoMode(QLineEdit.Password)
                edit.setText(self._load_field_value(field_name))
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
            return getter() if getter else ""
        except Exception as e:
            logger.error(f"读取字段 {field_name} 失败: {e}")
            return ""

    def _save_field_value(self, field_name, value):
        """保存动态字段到配置"""
        if not self.config_manager:
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
        if setter:
            setter(value)

    def _on_test_translation(self):
        """用当前表单配置（未保存也生效）测试翻译API"""
        try:
            self.test_result_label.setText("测试中...")
            QApplication.processEvents()

            api_type = self.api_type_combo.currentData()
            target_lang = self.target_lang_combo.currentData() or 'zh'

            if self.main_app.translation_manager:
                # 直接构造临时provider测试，不影响已保存配置
                from managers.providers import provider_registry
                provider_cls = provider_registry.get(api_type)
                if provider_cls is None:
                    self.test_result_label.setText("❌ 未知API类型")
                    return

                # 先把表单值写入配置，确保测试读到最新值
                self._save_form_fields()

                provider = provider_cls(
                    config_manager=self.config_manager,
                    session=self.main_app.translation_manager.session,
                )
                result = provider.translate("Hello, world!", source='auto', target=target_lang)
                if result and not result.startswith('[翻译失败'):
                    self.test_result_label.setText(f"✅ {result[:50]}")
                else:
                    self.test_result_label.setText(f"❌ {result[:60]}")
            else:
                self.test_result_label.setText("❌ 翻译管理器未初始化")
        except Exception as e:
            logger.error(f"测试翻译失败: {e}")
            self.test_result_label.setText(f"❌ {str(e)[:60]}")

    def _save_form_fields(self):
        """保存当前表单的动态字段值到配置（翻译API + OCR引擎 + 截图遮罩）"""
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
            if self.config_manager:
                # 保存API类型与OCR引擎类型（userData 存的是provider名称）
                api_type = self.api_type_combo.currentData()
                self.config_manager.set_api_type(api_type)
                self.config_manager.set_ocr_type(self.ocr_type_combo.currentData())

                # 保存目标语言与降级API
                self.config_manager.set_target_lang(self.target_lang_combo.currentData() or 'zh')
                self.config_manager.set_fallback_api_type(self.fallback_api_combo.currentData() or '')

                # 保存当前API的动态字段（密钥/URL等）
                self._save_form_fields()

                # 保存启动行为
                minimize_to_tray = self.minimize_to_tray_check.isChecked()
                self.config_manager.set_minimize_to_tray(minimize_to_tray)

                # 同步翻译管理器
                if self.main_app.translation_manager:
                    self.main_app.translation_manager.set_api_type(api_type)
                    self.main_app.translation_manager.set_target_lang(
                        self.target_lang_combo.currentData() or 'zh')
                    self.main_app.translation_manager.reload_config()

                # 同步OCR管理器
                ocr_type = self.ocr_type_combo.currentData()
                if self.main_app.ocr_manager:
                    self.main_app.ocr_manager.set_ocr_type(ocr_type)
                    self.main_app.ocr_manager.reload_config()

                # 更新全局快捷键：注册成功后由 set_hotkey 负责写配置，失败则不落盘
                hotkey = self.hotkey_edit.text()
                if self.main_app.hotkey_manager:
                    if not self.main_app.hotkey_manager.set_hotkey(hotkey):
                        QMessageBox.warning(
                            self,
                            "快捷键无效",
                            f"快捷键 {hotkey} 注册失败，已保留原快捷键，其他配置已保存。"
                        )
                        return
                else:
                    # 快捷键管理器不可用（启动降级场景），无法校验注册结果，仅记录并落盘
                    logger.warning(f"快捷键管理器不可用，未校验直接保存快捷键: {hotkey}")
                    self.config_manager.set_global_hotkey(hotkey)

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
