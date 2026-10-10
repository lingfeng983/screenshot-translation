#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
错误处理工具类
负责统一处理错误和提供友好的错误提示
"""

import traceback
import logging

from PyQt5.QtWidgets import QMessageBox

from .provider_common import describe_error
from .qt_dispatcher import qt_dispatcher

# 日志配置统一由入口 main.py 完成，此处只获取 logger
logger = logging.getLogger('screenshot-translation')

class ErrorHandler:
    """
    错误处理工具类
    负责统一处理错误和提供友好的错误提示
    """

    # 是否允许弹模态框：截图遮罩是全屏置顶窗口，此时弹框会把用户卡在
    # “遮罩点不掉、框也看不见”的位置
    _modal_allowed = True
    # 退出清理期间置位且不再复位：收尾阶段冒出来的框会挡住退出流程
    _modal_disabled_for_shutdown = False

    @staticmethod
    def set_modal_allowed(allowed):
        """
        开关模态错误框（遮罩显示期间关闭，遮罩释放后恢复）

        Args:
            allowed (bool): 是否允许弹出模态错误框
        """
        if allowed and ErrorHandler._modal_disabled_for_shutdown:
            return
        ErrorHandler._modal_allowed = bool(allowed)

    @staticmethod
    def disable_modals_for_shutdown():
        """退出清理期间永久禁框：此后遮罩释放也不恢复"""
        ErrorHandler._modal_disabled_for_shutdown = True
        ErrorHandler._modal_allowed = False

    @staticmethod
    def handle_error(error, module_name, show_message=True):
        """
        处理错误

        Args:
            error (Exception): 错误对象
            module_name (str): 模块名称
            show_message (bool): 是否显示错误提示

        Returns:
            str: 错误信息
        """
        # 记录错误日志
        logger.error(f"{module_name}模块发生错误: {str(error)}")
        logger.error(traceback.format_exc())

        # 显示错误提示（可能被工作线程调用，QMessageBox 必须在主线程创建）
        if show_message:
            if not ErrorHandler._modal_allowed:
                # 遮罩在场：失败文案由遮罩内的结果区展示，弹窗只会挡住交互
                logger.info(f"{module_name}的错误提示已改为遮罩内展示/仅记日志")
                return f"{module_name}模块发生错误: {str(error)}"
            qt_dispatcher.dispatch_to_main_thread(
                ErrorHandler._show_critical_box, module_name, error
            )

        return f"{module_name}模块发生错误: {str(error)}"

    @staticmethod
    def _show_critical_box(module_name, error):
        """
        在主线程弹出错误提示框

        Args:
            module_name (str): 模块名称
            error (Exception): 错误对象
        """
        try:
            QMessageBox.critical(
                None,
                "错误",
                f"{module_name}模块发生错误:\n{str(error)}",
                QMessageBox.Ok
            )
        except Exception as e:
            logger.error(f"显示错误提示失败: {e}")

    @staticmethod
    def handle_ocr_error(error, show_message=False):
        """
        处理OCR错误

        Args:
            error (Exception): 错误对象
            show_message (bool): 是否显示错误提示

        Returns:
            str: 默认的OCR结果
        """
        ErrorHandler.handle_error(error, "OCR", show_message)

        # OCR失败时返回默认文本
        return "[OCR识别失败，请确保已安装Tesseract OCR]"

    @staticmethod
    def handle_translation_error(error, show_message=False):
        """
        处理翻译错误

        Args:
            error (Exception): 错误对象
            show_message (bool): 是否显示错误提示

        Returns:
            str: 错误信息
        """
        ErrorHandler.handle_error(error, "翻译", show_message)

        # 翻译失败时返回错误信息（异常里可能带签名参数的 URL，必须脱敏）
        return f"[翻译失败: {describe_error(error)}]"

    @staticmethod
    def handle_screenshot_error(error, show_message=True):
        """
        处理截图错误

        Args:
            error (Exception): 错误对象
            show_message (bool): 是否显示错误提示
        """
        ErrorHandler.handle_error(error, "截图", show_message)

    @staticmethod
    def handle_hotkey_error(error, show_message=True):
        """
        处理快捷键错误

        Args:
            error (Exception): 错误对象
            show_message (bool): 是否显示错误提示
        """
        ErrorHandler.handle_error(error, "快捷键", show_message)

    @staticmethod
    def handle_tray_error(error, show_message=False):
        """
        处理系统托盘错误

        Args:
            error (Exception): 错误对象
            show_message (bool): 是否显示错误提示
        """
        ErrorHandler.handle_error(error, "系统托盘", show_message)

# 创建错误处理器实例
error_handler = ErrorHandler()