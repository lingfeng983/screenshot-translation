#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缓存管理器
负责管理和持久化翻译结果缓存
"""

import atexit
import hashlib
import json
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional
import logging

logger = logging.getLogger('screenshot-translation')

class CacheManager:
    """
    缓存管理器类
    负责管理和持久化翻译结果缓存

    缓存条数有上限（LRU 淘汰），写盘按次数节流，进程退出时兜底落盘。
    """

    def __init__(self, cache_file: str = None,
                 expire_hours: int = 24, max_entries: int = 1000,
                 flush_every: int = 10):
        """
        初始化缓存管理器

        Args:
            cache_file (str): 缓存文件路径；None时按运行形态自动定位
            expire_hours (int): 缓存过期时间（小时）
            max_entries (int): 缓存最大条数，超出按最旧条目淘汰
            flush_every (int): 每累积多少次写入才落盘一次
        """
        if cache_file is None:
            from . import app_paths
            cache_file = app_paths.cache_file()
        self.cache_file = cache_file
        self.expire_hours = expire_hours
        self.max_entries = max_entries
        self.flush_every = max(flush_every, 1)
        self._cache: Dict[str, dict] = {}
        self._pending_writes = 0
        self._lock = threading.RLock()
        self._load_cache()
        atexit.register(self.flush)
        logger.debug(f"缓存管理器初始化完成，缓存文件: {cache_file}")

    def _is_expired(self, entry: dict, margin_hours: float = 0) -> bool:
        """
        判断缓存条目是否过期

        Args:
            entry (dict): 缓存条目
            margin_hours (float): 提前判定的小时数，用于"即将过期"统计

        Returns:
            bool: 过期或 timestamp 缺失/格式错误时返回True
        """
        try:
            cached_time = datetime.fromisoformat(entry['timestamp'])
        except (ValueError, KeyError, TypeError):
            return True
        threshold = timedelta(hours=self.expire_hours - margin_hours)
        return datetime.now() - cached_time >= threshold

    def _get_cache_key(self, text: str, api_type: str) -> str:
        """
        生成缓存键

        Args:
            text (str): 要翻译的文本
            api_type (str): API类型

        Returns:
            str: MD5哈希值作为缓存键
        """
        content = f"{api_type}:{text}"
        return hashlib.md5(content.encode()).hexdigest()

    def get_cached_translation(self, text: str, api_type: str) -> Optional[str]:
        """
        获取缓存的翻译结果

        Args:
            text (str): 原始文本
            api_type (str): API类型

        Returns:
            Optional[str]: 缓存的翻译结果，如果没有则返回None
        """
        cache_key = self._get_cache_key(text, api_type)

        with self._lock:
            cache_entry = self._cache.get(cache_key)
            if cache_entry is None:
                return None

            if self._is_expired(cache_entry):
                logger.debug(f"缓存已过期或损坏，删除: {text[:50]}...")
                del self._cache[cache_key]
                self._mark_dirty()
                return None

            # LRU：命中后移到末尾，淘汰时优先保留近期使用的条目
            self._cache[cache_key] = self._cache.pop(cache_key)
            logger.debug(f"命中缓存: {text[:50]}...")
            return cache_entry.get('translation')

    def cache_translation(self, text: str, api_type: str, translation: str):
        """
        缓存翻译结果

        Args:
            text (str): 原始文本
            api_type (str): API类型
            translation (str): 翻译结果
        """
        cache_key = self._get_cache_key(text, api_type)

        with self._lock:
            self._cache.pop(cache_key, None)
            self._cache[cache_key] = {
                'text': text,
                'api_type': api_type,
                'translation': translation,
                'timestamp': datetime.now().isoformat()
            }
            self._evict_overflow()
            self._mark_dirty()
            logger.debug(f"翻译结果已缓存: {text[:50]}...")

    def _evict_overflow(self):
        """按插入/访问顺序淘汰超出 max_entries 的最旧条目"""
        overflow = len(self._cache) - self.max_entries
        if overflow <= 0:
            return
        for key in list(self._cache.keys())[:overflow]:
            del self._cache[key]
        logger.debug(f"缓存超出上限，淘汰{overflow}条最旧记录")

    def _mark_dirty(self):
        """累计一次待落盘写入，达到阈值时才真正写文件"""
        self._pending_writes += 1
        if self._pending_writes >= self.flush_every:
            self._save_cache()

    def flush(self):
        """立即把待落盘的缓存写入文件（进程退出时由 atexit 兜底调用）"""
        with self._lock:
            if self._pending_writes:
                self._save_cache()

    def _load_cache(self):
        """从文件加载缓存"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    loaded_cache = json.load(f)
                    # 验证缓存格式
                    if isinstance(loaded_cache, dict):
                        self._cache = loaded_cache
                        self._cleanup_expired()
                        self._evict_overflow()
                        logger.info(f"缓存加载成功，共{len(self._cache)}条记录")
                    else:
                        logger.warning("缓存文件格式错误，使用空缓存")
                        self._cache = {}
            else:
                logger.info("缓存文件不存在，创建新缓存")
                self._cache = {}

        except Exception as e:
            logger.error(f"加载缓存失败: {e}")
            self._cache = {}

    def _save_cache(self):
        """保存缓存到文件（原子写入，避免中断产生损坏文件）"""
        tmp_file = f"{self.cache_file}.tmp"
        try:
            # 清理过期条目
            self._cleanup_expired()

            # 确保目录存在
            cache_dir = os.path.dirname(self.cache_file)
            if cache_dir and not os.path.exists(cache_dir):
                os.makedirs(cache_dir)

            # 先写入同目录下的临时文件，再原子替换，防止写入中断损坏原文件
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, self.cache_file)
            self._pending_writes = 0
            logger.debug(f"缓存保存成功，共{len(self._cache)}条记录")

        except Exception as e:
            logger.error(f"保存缓存失败: {e}")
            # 清理残留的临时文件，避免长期堆积
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except OSError:
                pass

    def _cleanup_expired(self):
        """清理过期缓存条目"""
        expired_keys = [key for key, entry in self._cache.items() if self._is_expired(entry)]

        # 删除过期条目
        for key in expired_keys:
            del self._cache[key]

        if expired_keys:
            logger.debug(f"清理了{len(expired_keys)}条过期缓存")

    def clear_cache(self):
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()
            self._save_cache()
            logger.info("缓存已清空")

    def get_cache_stats(self) -> dict:
        """获取缓存统计信息"""
        with self._lock:
            total_entries = len(self._cache)

            # 计算即将过期的条目数量（1小时内过期）
            soon_expire_count = sum(
                1 for entry in self._cache.values()
                if self._is_expired(entry, margin_hours=1)
            )

            return {
                'total_entries': total_entries,
                'soon_expire_entries': soon_expire_count,
                'expire_hours': self.expire_hours
            }

# 全局缓存管理器实例
cache_manager = CacheManager()
