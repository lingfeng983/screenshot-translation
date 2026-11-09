#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新检查

向 GitHub Releases 取最新正式版标签，与本地版本比较；只负责"有没有新版"，
下载、安装仍交给用户通过发布页完成。
"""

import logging
import re

from .app_version import current_version

logger = logging.getLogger('screenshot-translation')

GITHUB_REPO = 'lingfeng983/screenshot-translation'
API_URL = f'https://api.github.com/repos/{GITHUB_REPO}/releases/latest'
RELEASE_PAGE = f'https://github.com/{GITHUB_REPO}/releases/latest'

_VERSION_RE = re.compile(r'(\d+(?:\.\d+)*)')


class ReleaseInfo:
    """一条最新发行版的精简信息"""

    def __init__(self, version, url='', notes=''):
        """
        Args:
            version (str): 去掉 v 前缀的版本号，如 '1.9.0'
            url (str): 发布页地址
            notes (str): 发行说明（Markdown 原文）
        """
        self.version = version
        self.url = url or RELEASE_PAGE
        self.notes = notes or ''

    def __repr__(self):
        return f"ReleaseInfo(version={self.version!r})"


def parse_version(value):
    """
    把版本字符串转成可比较的数字元组

    'v1.9.0' / '1.9.0-beta.1' 都取第一段数字；完全无法解析时返回空元组，
    调用方据此判定"无法比较"，不会误报更新。

    Args:
        value: 版本字符串

    Returns:
        tuple[int, ...]: 如 (1, 9, 0)；无法解析时 ()
    """
    match = _VERSION_RE.search(str(value or ''))
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split('.'))


def is_newer(latest, current):
    """
    最新版本是否比当前版本新

    Returns:
        bool: latest > current；任一版本无法解析时 False
    """
    latest_tuple = parse_version(latest)
    current_tuple = parse_version(current)
    if not latest_tuple or not current_tuple:
        return False
    width = max(len(latest_tuple), len(current_tuple))
    latest_tuple += (0,) * (width - len(latest_tuple))
    current_tuple += (0,) * (width - len(current_tuple))
    return latest_tuple > current_tuple


def fetch_latest_release(timeout=(3, 6)):
    """
    查询 GitHub 最新正式版

    requests 延迟导入：本模块在启动时就会 import，不能把 requests 及其
    依赖拉进冷启动路径。

    Args:
        timeout: (连接超时, 读取超时)，秒

    Returns:
        ReleaseInfo

    Raises:
        Exception: 网络、HTTP 或响应格式错误，由调用方决定如何提示
    """
    import requests

    response = requests.get(
        API_URL,
        timeout=timeout,
        headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': f'ScreenshotTranslator/{current_version()}',
        })
    response.raise_for_status()
    payload = response.json()

    tag = str(payload.get('tag_name') or '').strip()
    version = tag[1:] if tag[:1] in ('v', 'V') else tag
    if not parse_version(version):
        raise ValueError(f"发行版标签无法解析: {tag!r}")
    return ReleaseInfo(
        version=version,
        url=payload.get('html_url') or RELEASE_PAGE,
        notes=payload.get('body') or '')


def check_for_update(current=None):
    """
    检查是否有比当前版本更新的正式版

    Args:
        current (str): 当前版本，默认取 app_version.APP_VERSION

    Returns:
        ReleaseInfo 或 None: 有新版时返回发行版信息，已是最新时 None

    Raises:
        Exception: 查询失败（网络/HTTP/响应格式），由调用方决定提示方式
    """
    current = current or current_version()
    info = fetch_latest_release()
    if is_newer(info.version, current):
        logger.info(f"发现新版本: v{info.version}（当前 v{current}）")
        return info
    logger.debug(f"已是最新版本: v{current}")
    return None
