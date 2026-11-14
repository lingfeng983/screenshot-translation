#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新检查

向 GitHub Releases 取最新正式版标签，与本地版本比较，并把发行版里的安装包
附件（名称、大小、下载地址）一并带出来——下载与安装见 update_installer。
"""

import logging
import re

from .app_version import current_version

logger = logging.getLogger('screenshot-translation')

GITHUB_REPO = 'lingfeng983/screenshot-translation'
API_URL = f'https://api.github.com/repos/{GITHUB_REPO}/releases/latest'
RELEASE_PAGE = f'https://github.com/{GITHUB_REPO}/releases/latest'

_VERSION_RE = re.compile(r'(\d+(?:\.\d+)*)')
# 远端 tag_name 会原样进入下载目录名与界面文案，必须整串严格匹配。
# 只允许 v?x.y.z，外加最多 32 字符的预发布后缀（如 -beta.2 / +build.7），
# 不允许路径分隔符、".." 或任意其它文本。
_RELEASE_TAG_RE = re.compile(
    r'^[vV]?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]{1,32})?)\Z')


class ReleaseInfo:
    """一条最新发行版的精简信息"""

    def __init__(self, version, url='', notes='', asset_url='',
                 asset_name='', asset_size=0, asset_digest=''):
        """
        Args:
            version (str): 去掉 v 前缀的版本号，如 '1.9.0'
            url (str): 发布页地址
            notes (str): 发行说明（Markdown 原文）
            asset_url (str): 安装包直链；发行版没挂 exe 时为空
            asset_name (str): 安装包文件名
            asset_size (int): 安装包字节数；未知为 0
            asset_digest (str): 附件摘要，形如 'sha256:...'；未知为空
        """
        self.version = version
        self.url = url or RELEASE_PAGE
        self.notes = notes or ''
        self.asset_url = asset_url or ''
        self.asset_name = asset_name or ''
        self.asset_size = int(asset_size or 0)
        self.asset_digest = asset_digest or ''

    def __repr__(self):
        return f"ReleaseInfo(version={self.version!r})"


def pick_installer_asset(assets):
    """
    从发行版附件里挑安装包

    优先名字带 setup 的 exe（本项目产物固定是 ScreenshotTranslator-Setup.exe），
    没有时退回第一个 exe；一个 exe 都没有则返回 None（该版本无法自动更新）。

    Args:
        assets (list): GitHub API 的 assets 数组

    Returns:
        dict 或 None: 选中的附件条目
    """
    exe_assets = [asset for asset in assets or []
                  if str(asset.get('name') or '').lower().endswith('.exe')]
    if not exe_assets:
        return None
    for asset in exe_assets:
        if 'setup' in str(asset.get('name') or '').lower():
            return asset
    return exe_assets[0]


def normalize_release_tag(tag):
    """
    校验并规范化远端发行版标签

    ``parse_version`` 只取第一串数字，用于版本比较时很方便，但拿它当目录名
    就危险了：``v1.9.0/../../../Public/pwned`` 照样解析成 ``(1, 9, 0)``。
    所有来自 GitHub 的标签必须先过这里，才能进入文件路径、日志和界面。

    Args:
        tag: ``tag_name`` 原值

    Returns:
        str 或 None: 去掉 v 前缀的规范版本串；不合法时 None
    """
    match = _RELEASE_TAG_RE.fullmatch(str(tag or '').strip())
    return match.group(1) if match else None


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
    version = normalize_release_tag(tag)
    if version is None:
        raise ValueError(f"发行版标签格式不合法: {tag!r}")
    asset = pick_installer_asset(payload.get('assets')) or {}
    return ReleaseInfo(
        version=version,
        url=payload.get('html_url') or RELEASE_PAGE,
        notes=payload.get('body') or '',
        asset_url=asset.get('browser_download_url') or '',
        asset_name=asset.get('name') or '',
        asset_size=asset.get('size') or 0,
        asset_digest=asset.get('digest') or '')


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
