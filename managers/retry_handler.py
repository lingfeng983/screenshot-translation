#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重试处理器
实现指数退避和随机抖动的重试机制
"""

import logging
import random
import threading
import time
from typing import Callable, Any, Optional

logger = logging.getLogger('screenshot-translation')


class NonRetryableError(Exception):
    """
    重试无意义的错误（配置缺失、能力不支持等）

    鉴权未填、密钥为空、引擎不支持某种能力：重试多少次结果都一样，
    只会白等退避时间（默认两次退避合计约 3 秒），因此直接放弃。
    """


class ChainExpiredError(Exception):
    """降级链的整体时限用尽：不再发起任何请求，也不再把退避时间睡完"""


class TranslationCancelledError(Exception):
    """用户已经关掉遮罩/重画选区：在飞的请求没有意义，立刻收手"""


class RetryHandler:
    """
    重试处理器类
    支持指数退避和随机抖动的重试机制
    """

    def __init__(self, max_attempts: int = 3, base_delay: float = 1.0,
                 max_delay: float = 10.0, jitter: bool = True):
        """
        初始化重试处理器

        Args:
            max_attempts (int): 最大重试次数
            base_delay (float): 基础延迟时间（秒）
            max_delay (float): 最大延迟时间（秒）
            jitter (bool): 是否启用随机抖动
        """
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
        logger.debug(f"重试处理器初始化: 最大尝试{max_attempts}次，基础延迟{base_delay}秒")

    def execute_with_retry(self, func: Callable, *args,
                           fallback_result: Any = None,
                           deadline: Optional[float] = None,
                           cancel_event: Optional[threading.Event] = None,
                           **kwargs) -> Any:
        """
        执行带重试的操作

        Args:
            func (Callable): 要执行的函数
            *args: 函数参数
            fallback_result (Any): 重试失败后的降级结果；
                传入可调用对象时按惰性求值处理，仅在所有重试失败后才调用
            deadline (float 或 None): time.monotonic() 口径的截止时间戳；
                到点后立即放弃剩余尝试与退避等待
            cancel_event (threading.Event 或 None): 置位后同样立即放弃
            **kwargs: 函数关键字参数

        Returns:
            函数执行结果或降级结果

        Raises:
            Exception: 如果没有降级结果且所有重试都失败
        """
        last_exception = None

        for attempt in range(self.max_attempts):
            stopped = self._stop_reason(deadline, cancel_event)
            if stopped is not None:
                logger.warning(f"第{attempt + 1}次尝试前中止重试: {stopped}")
                last_exception = stopped
                break

            try:
                logger.debug(f"第{attempt + 1}次尝试执行 {func.__name__}")
                result = func(*args, **kwargs)
                if attempt > 0:
                    logger.info(f"第{attempt + 1}次尝试成功")
                return result

            except NonRetryableError as e:
                # 配置类/能力类错误：立刻放弃，不浪费退避时间
                last_exception = e
                logger.warning(f"遇到不可重试错误，不进行重试: {e}")
                break

            except Exception as e:
                last_exception = e
                logger.warning(f"第{attempt + 1}次尝试失败: {e}")

                if attempt >= self.max_attempts - 1:
                    logger.error(f"所有{self.max_attempts}次重试都失败了")
                    break

                delay = self._calculate_delay(attempt)
                logger.info(f"等待{delay:.2f}秒后进行第{attempt + 2}次重试...")
                if not self._sleep(delay, deadline, cancel_event):
                    logger.warning("退避等待被时限/取消打断，放弃剩余重试")
                    break

        # 所有重试都失败，返回降级结果
        if fallback_result is not None:
            logger.info("使用降级结果")
            # 惰性降级：只有真正需要降级时才执行，避免调用方在传参时被提前求值
            return fallback_result() if callable(fallback_result) else fallback_result
        else:
            # 重新抛出最后一个异常
            raise last_exception

    @staticmethod
    def _stop_reason(deadline, cancel_event):
        """
        当前是否已经不该再发起请求

        Returns:
            异常对象（该停下）或 None（可以继续）
        """
        if cancel_event is not None and cancel_event.is_set():
            return TranslationCancelledError("请求已被取消")
        if deadline is not None and time.monotonic() >= deadline:
            return ChainExpiredError("降级链整体时限已用尽")
        return None

    @staticmethod
    def _sleep(delay, deadline, cancel_event):
        """
        可中断的退避等待：时限用尽或取消置位时提前返回

        Returns:
            bool: 是否完整睡完（False 表示被提前打断）
        """
        if deadline is not None:
            delay = min(delay, max(deadline - time.monotonic(), 0.0))
        if delay <= 0:
            return False
        if cancel_event is not None:
            # Event.wait 返回“是否被置位”：置位说明用户已经不等了
            return not cancel_event.wait(delay)
        time.sleep(delay)
        return True

    def _calculate_delay(self, attempt: int) -> float:
        """
        计算延迟时间（指数退避 + 随机抖动）

        Args:
            attempt (int): 当前尝试次数（从0开始）

        Returns:
            float: 延迟时间（秒）
        """
        # 指数退避：delay = base_delay * 2^attempt
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)

        if self.jitter:
            # 添加±25%的随机抖动，避免惊群效应
            jitter_range = delay * 0.25
            delay += random.uniform(-jitter_range, jitter_range)

        # 确保延迟时间不为负数
        return max(delay, 0)

# 默认重试处理器实例
default_retry_handler = RetryHandler(max_attempts=3, base_delay=1.0)
