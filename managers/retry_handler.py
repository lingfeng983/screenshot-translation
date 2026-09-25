#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重试处理器
实现指数退避和随机抖动的重试机制
"""

import logging
import random
import time
from typing import Callable, Any

logger = logging.getLogger('screenshot-translation')

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
                          **kwargs) -> Any:
        """
        执行带重试的操作

        Args:
            func (Callable): 要执行的函数
            *args: 函数参数
            fallback_result (Any): 重试失败后的降级结果；
                传入可调用对象时按惰性求值处理，仅在所有重试失败后才调用
            **kwargs: 函数关键字参数

        Returns:
            函数执行结果或降级结果

        Raises:
            Exception: 如果没有降级结果且所有重试都失败
        """
        last_exception = None

        for attempt in range(self.max_attempts):
            try:
                logger.debug(f"第{attempt + 1}次尝试执行 {func.__name__}")
                result = func(*args, **kwargs)
                if attempt > 0:
                    logger.info(f"第{attempt + 1}次尝试成功")
                return result

            except Exception as e:
                last_exception = e
                logger.warning(f"第{attempt + 1}次尝试失败: {e}")

                if attempt < self.max_attempts - 1:
                    delay = self._calculate_delay(attempt)
                    logger.info(f"等待{delay:.2f}秒后进行第{attempt + 2}次重试...")
                    time.sleep(delay)
                else:
                    logger.error(f"所有{self.max_attempts}次重试都失败了")

        # 所有重试都失败，返回降级结果
        if fallback_result is not None:
            logger.info("使用降级结果")
            # 惰性降级：只有真正需要降级时才执行，避免调用方在传参时被提前求值
            return fallback_result() if callable(fallback_result) else fallback_result
        else:
            # 重新抛出最后一个异常
            raise last_exception

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
