#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进度管理器
负责显示和管理操作进度对话框
"""

import threading
from PyQt5.QtWidgets import QProgressDialog
from PyQt5.QtCore import Qt
import logging

from .qt_dispatcher import qt_dispatcher

logger = logging.getLogger('screenshot-translation')

class ProgressManager:
    """
    进度管理器类
    负责显示和管理操作进度对话框

    所有QWidget的创建与更新都会被调度到主线程执行，
    调用方可以在任意线程安全地使用本类的公开方法。
    """

    def __init__(self):
        """初始化进度管理器"""
        self._dialogs = {}  # 调用方线程ID -> 对话框映射
        self._lock = threading.Lock()
        logger.debug("进度管理器初始化完成")

    def show_progress(self, title: str, message: str,
                     min_value: int = 0, max_value: int = 100):
        """
        显示进度对话框

        Args:
            title (str): 对话框标题
            message (str): 进度消息
            min_value (int): 最小值
            max_value (int): 最大值

        Returns:
            QProgressDialog: 主线程调用时返回进度对话框实例；
                子线程调用时对话框异步创建，返回None
        """
        thread_id = threading.get_ident()
        return qt_dispatcher.dispatch_to_main_thread(
            self._show_progress_impl, thread_id, title, message, min_value, max_value
        )

    def _show_progress_impl(self, thread_id, title, message, min_value, max_value):
        """
        在主线程创建进度对话框

        Args:
            thread_id (int): 发起请求的线程ID，用作对话框索引
            title (str): 对话框标题
            message (str): 进度消息
            min_value (int): 最小值
            max_value (int): 最大值

        Returns:
            QProgressDialog: 进度对话框实例，创建失败时返回None
        """
        with self._lock:
            stale = self._dialogs.pop(thread_id, None)
            if stale is not None:
                # 线程池线程 id 会被复用，先清掉上一次未正常关闭的残留对话框
                try:
                    stale.close()
                    stale.deleteLater()
                except Exception as e:
                    logger.error(f"清理残留进度对话框失败: {e}")

            try:
                dialog = QProgressDialog(message, "取消", min_value, max_value)
                dialog.setWindowTitle(title)
                dialog.setWindowModality(Qt.WindowModal)
                dialog.setMinimumDuration(0)  # 立即显示
                dialog.setValue(0)
                dialog.setAutoReset(False)
                dialog.setAutoClose(False)

                self._dialogs[thread_id] = dialog
                logger.debug(f"进度对话框已创建: {title}")

            except Exception as e:
                logger.error(f"创建进度对话框失败: {e}")
                return None

            return self._dialogs[thread_id]

    def update_progress(self, value: int, message: str = None):
        """
        更新进度

        Args:
            value (int): 进度值
            message (str): 可选的新消息
        """
        thread_id = threading.get_ident()
        qt_dispatcher.dispatch_to_main_thread(
            self._update_progress_impl, thread_id, value, message
        )

    def _update_progress_impl(self, thread_id, value, message):
        """
        在主线程更新进度对话框

        Args:
            thread_id (int): 发起请求的线程ID
            value (int): 进度值
            message (str): 可选的新消息
        """
        with self._lock:
            if thread_id in self._dialogs:
                try:
                    dialog = self._dialogs[thread_id]
                    dialog.setValue(value)
                    if message:
                        dialog.setLabelText(message)
                    logger.debug(f"进度更新: {value}%")

                except Exception as e:
                    logger.error(f"更新进度失败: {e}")

    def hide_progress(self):
        """隐藏并清理进度对话框"""
        thread_id = threading.get_ident()
        qt_dispatcher.dispatch_to_main_thread(self._hide_progress_impl, thread_id)

    def _hide_progress_impl(self, thread_id):
        """
        在主线程关闭并释放进度对话框

        Args:
            thread_id (int): 发起请求的线程ID
        """
        with self._lock:
            if thread_id in self._dialogs:
                try:
                    dialog = self._dialogs[thread_id]
                    dialog.close()
                    dialog.deleteLater()
                    del self._dialogs[thread_id]
                    logger.debug("进度对话框已关闭")

                except Exception as e:
                    logger.error(f"关闭进度对话框失败: {e}")

# 全局进度管理器实例
progress_manager = ProgressManager()