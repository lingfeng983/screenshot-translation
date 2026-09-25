#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qt线程调度器
负责确保所有UI操作都在主线程执行，解决线程安全问题
"""

import threading
import functools
import sys
from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication
import logging

logger = logging.getLogger('screenshot-translation')


def get_qt_app():
    """
    获取QApplication单例实例

    只允许在主线程中创建实例：在非主线程创建QApplication会被Qt判定为致命错误。

    Returns:
        QApplication: 已有或新建的实例；在非主线程且实例不存在时返回None
    """
    app = QApplication.instance()
    if app is not None:
        return app

    if threading.current_thread() is not threading.main_thread():
        logger.error("QApplication实例不存在，且当前不在主线程，无法创建")
        return None

    logger.debug("创建新的QApplication实例")
    return QApplication(sys.argv)


class _MainThreadInvoker(QObject):
    """
    驻留主线程的调用中转对象

    通过跨线程信号（Qt.QueuedConnection）把可调用对象投递到主线程事件循环执行。
    """

    invoke_requested = pyqtSignal(object)

    def __init__(self):
        """初始化中转对象并建立队列连接"""
        super().__init__()
        self.invoke_requested.connect(self._on_invoke_requested, Qt.QueuedConnection)

    def _on_invoke_requested(self, wrapped_func):
        """
        在主线程执行被投递的可调用对象

        Args:
            wrapped_func: 无参可调用对象
        """
        try:
            wrapped_func()
        except Exception as e:
            logger.error(f"主线程执行调度任务失败: {e}")


class QtDispatcher(QObject):
    """
    Qt线程调度器类
    确保所有UI操作都在主线程执行，避免Qt线程安全问题
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """实现单例模式"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(QtDispatcher, cls).__new__(cls)
                    # 初始化实例变量
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """初始化调度器（在主线程完成，_invoker 的线程亲和性即为主线程）"""
        with QtDispatcher._lock:
            if self._initialized:
                return

            # 调用父类初始化
            super(QtDispatcher, self).__init__()
            if threading.current_thread() is not threading.main_thread():
                # 中转对象必须驻留主线程，否则队列信号会投递到无事件循环的线程被静默丢弃
                logger.warning("QtDispatcher 首次实例化不在主线程，主线程调度可能失效")
            self._invoker = _MainThreadInvoker()
            self._initialized = True
            logger.info("Qt线程调度器初始化完成")

    def dispatch_to_main_thread(self, func, *args, **kwargs):
        """
        调度函数到主线程执行

        子线程中不使用QTimer.singleShot：工作线程没有Qt事件循环，定时器不会触发，
        回调会被静默丢弃。改为通过驻留主线程的QObject发跨线程队列信号。

        Args:
            func: 要执行的函数
            *args: 函数参数
            **kwargs: 函数关键字参数

        Returns:
            函数执行结果（如果是主线程调用），否则返回None
        """
        if threading.current_thread() is threading.main_thread():
            # 已经在主线程，直接执行
            return func(*args, **kwargs)
        else:
            # 在子线程中，通过队列信号调度到主线程执行
            wrapped_func = functools.partial(func, *args, **kwargs)
            self._invoker.invoke_requested.emit(wrapped_func)
            logger.debug(f"函数 {getattr(func, '__name__', repr(func))} 已调度到主线程执行")
            return None

    @staticmethod
    def main_thread_only(func):
        """
        装饰器：确保函数只在主线程执行

        Args:
            func: 要装饰的函数

        Returns:
            装饰后的函数
        """
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            """在必要时把被装饰方法重新调度到主线程执行"""
            if threading.current_thread() is not threading.main_thread():
                # 不在主线程，重新调度到主线程
                dispatcher = QtDispatcher()
                return dispatcher.dispatch_to_main_thread(
                    lambda: func(self, *args, **kwargs)
                )
            return func(self, *args, **kwargs)
        return wrapper

# 全局调度器实例
qt_dispatcher = QtDispatcher()