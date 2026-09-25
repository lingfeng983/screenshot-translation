#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
线程池管理器
统一管理后台任务的线程池生命周期与任务提交
"""

import concurrent.futures
import threading

import logging
logger = logging.getLogger('screenshot-translation')

class ThreadPoolManager:
    """
    线程池管理器类
    负责线程池的初始化、任务提交与关闭
    """

    def __init__(self, max_workers=4):
        """
        初始化线程池管理器

        Args:
            max_workers (int): 最大工作线程数
        """
        self.max_workers = max_workers
        self.executor = None
        self.lock = threading.Lock()
        self.is_initialized = False
        # 终态标志：一旦 shutdown 就不再允许重建线程池
        self._is_shutdown = False

        logger.info(f"线程池管理器初始化，最大工作线程数: {max_workers}")

    def initialize(self):
        """
        创建底层线程池（已初始化时直接返回成功）

        Returns:
            bool: 初始化是否成功；已 shutdown 后返回 False
        """
        with self.lock:
            if self._is_shutdown:
                logger.warning("线程池已关闭，拒绝重新初始化")
                return False
            if not self.is_initialized:
                try:
                    self.executor = concurrent.futures.ThreadPoolExecutor(
                        max_workers=self.max_workers,
                        thread_name_prefix="screenshot-translation-"
                    )
                    self.is_initialized = True
                    logger.info("线程池初始化成功")
                    return True
                except Exception as e:
                    logger.error(f"线程池初始化失败: {e}")
                    import traceback
                    traceback.print_exc()
                    return False
            return True

    def submit(self, func, *args, **kwargs):
        """
        提交任务到线程池执行

        Args:
            func (Callable): 要执行的函数
            *args: 函数参数
            **kwargs: 函数关键字参数

        Returns:
            concurrent.futures.Future: 任务句柄；线程池不可用或提交失败时返回None
        """
        with self.lock:
            if self._is_shutdown:
                logger.warning("线程池已关闭，拒绝提交新任务")
                return None
            executor = self.executor if self.is_initialized else None

        if executor is None:
            if not self.initialize():
                logger.error("线程池未初始化，无法提交任务")
                return None
            with self.lock:
                executor = self.executor
                if executor is None:
                    return None

        try:
            future = executor.submit(func, *args, **kwargs)
            logger.debug(f"任务已提交到线程池: {func.__name__}")
            return future
        except Exception as e:
            logger.error(f"提交任务到线程池失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def shutdown(self, wait=True, cancel_futures=False):
        """
        关闭线程池（终态操作，之后不再允许重建）

        Args:
            wait (bool): 是否等待已提交任务执行完成
            cancel_futures (bool): 是否取消尚未开始执行的任务（Python 3.9+）
        """
        with self.lock:
            self._is_shutdown = True
            if self.is_initialized:
                try:
                    try:
                        self.executor.shutdown(wait=wait, cancel_futures=cancel_futures)
                    except TypeError:
                        # Python < 3.9 不支持 cancel_futures
                        self.executor.shutdown(wait=wait)
                    self.is_initialized = False
                    self.executor = None
                    logger.info(f"线程池已关闭，等待任务完成: {wait}，取消排队任务: {cancel_futures}")
                except Exception as e:
                    logger.error(f"关闭线程池失败: {e}")
                    import traceback
                    traceback.print_exc()

    def is_running(self):
        """
        查询线程池是否处于可用状态

        Returns:
            bool: 线程池是否已初始化且未关闭
        """
        return self.is_initialized

# 创建全局线程池管理器实例
thread_pool_manager = ThreadPoolManager()