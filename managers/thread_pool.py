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
        # 已提交且未完成的 future：收尾时按超时等它们，而不是碰私有属性
        self._pending = set()

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
            if self.is_initialized:
                return True
            try:
                self.executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=self.max_workers,
                    thread_name_prefix="screenshot-translation-"
                )
                self.is_initialized = True
                logger.info("线程池初始化成功")
                return True
            except Exception as e:
                logger.exception(f"线程池初始化失败: {e}")
                return False

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
            # 记账与提交放在同一把锁里：任务可能在 add_done_callback 之前就跑完，
            # 那样回调先 discard、随后 add 会把一个已完成的 future 永久留在集合里
            with self.lock:
                future = executor.submit(func, *args, **kwargs)
                self._pending.add(future)
        except Exception as e:
            logger.exception(f"提交任务到线程池失败: {e}")
            return None

        # 回调在 add 之后注册：future 已结束时会立即执行，等于当场摘掉记账。
        # 回调必须同样持锁：wait_pending 在锁内复制集合，若 worker 线程直接
        # discard，复制过程中集合被改动会抛 "Set changed size during iteration"
        future.add_done_callback(self._forget)
        logger.debug(f"任务已提交到线程池: {getattr(func, '__name__', repr(func))}")
        return future

    def _forget(self, future):
        """
        任务结束后从记账集合里摘除（在 worker 线程执行，必须持锁）

        Args:
            future (concurrent.futures.Future): 已结束的任务
        """
        with self.lock:
            self._pending.discard(future)

    def wait_pending(self, timeout=None):
        """
        等待已提交的任务收尾

        Args:
            timeout (float 或 None): 最多等这么多秒；None 表示等到全部结束

        Returns:
            int: 超时仍未完成的任务数
        """
        with self.lock:
            pending = set(self._pending)
        if not pending:
            return 0
        concurrent.futures.wait(pending, timeout=timeout)
        return sum(1 for future in pending if not future.done())

    def shutdown(self, wait=True, cancel_futures=False, timeout=None):
        """
        关闭线程池（终态操作，之后不再允许重建）

        Args:
            wait (bool): 是否等待已提交任务执行完成
            cancel_futures (bool): 是否取消尚未开始执行的任务（Python 3.9+）
            timeout (float 或 None): wait=True 时最多等这么多秒；None 表示
                等到收尾为止。这里的等待只是尽力而为：工作线程可能正卡在
                网络读上，调用方要按“可能等满 timeout”来安排退出流程
        """
        with self.lock:
            if self._is_shutdown:
                return
            self._is_shutdown = True
            executor = self.executor
            self.executor = None
            self.is_initialized = False

        if executor is None:
            return

        # 等待放在锁外做：持着锁 join 会把这期间任何 submit() 一起卡住
        try:
            executor.shutdown(wait=False, cancel_futures=cancel_futures)
        except TypeError:
            # Python < 3.9 不支持 cancel_futures
            executor.shutdown(wait=False)

        left = self.wait_pending(timeout) if wait else 0
        logger.info(f"线程池已关闭（取消排队任务: {cancel_futures}，"
                    f"仍在运行的任务: {left}）")
        if left:
            logger.warning(f"{left}个任务在超时后仍未结束，退出不再等它们")

    def is_running(self):
        """
        查询线程池是否处于可用状态

        Returns:
            bool: 线程池是否已初始化且未关闭
        """
        return self.is_initialized

# 创建全局线程池管理器实例
thread_pool_manager = ThreadPoolManager()