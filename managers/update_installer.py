#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新包的下载与安装

更新走"程序内自己完成"：下载发行版附件里的安装包到临时目录，校验大小与可执行
文件头，然后静默启动安装程序并退出本体，让 Setup 覆盖正在运行的文件、装完自动
把程序拉起来。不再把发布页丢给用户手点。

下载放在线程池里跑，进度通过回调上报（回调由调用方决定怎么切回主线程）。
"""

import hashlib
import logging
import os
import ssl
import subprocess
import tempfile

logger = logging.getLogger('screenshot-translation')

DEFAULT_INSTALLER_NAME = 'ScreenshotTranslator-Setup.exe'
_CHUNK_SIZE = 256 * 1024
_DOWNLOAD_TIMEOUT = (5, 30)
# 安装程序参数：静默但保留进度窗口；关掉运行中的本体再覆盖文件；不重启系统
INSTALLER_ARGS = ('/SILENT', '/CLOSEAPPLICATIONS', '/SUPPRESSMSGBOXES', '/NORESTART')
_USER_AGENT = 'ScreenshotTranslator-Updater'


class UpdateError(Exception):
    """更新过程中的可读错误（下载失败、包不完整等）"""


class UpdateCancelled(UpdateError):
    """用户取消了下载"""


def download_dir(version):
    """
    本次更新专用的临时目录

    按版本分目录，重复下载不会撞上上次残留的半截文件。

    Args:
        version (str): 目标版本号

    Returns:
        str: 目录绝对路径（已创建）
    """
    base = os.path.join(tempfile.gettempdir(), 'screenshot-translator-update')
    path = os.path.join(base, str(version or 'latest'))
    os.makedirs(path, exist_ok=True)
    return path


def download(info, directory=None, progress=None, cancel=None,
             timeout=_DOWNLOAD_TIMEOUT):
    """
    下载安装包

    Args:
        info (update_checker.ReleaseInfo): 带 asset_url 的发行版信息
        directory (str): 保存目录，默认按版本建一个临时目录
        progress (callable): progress(已下载字节, 总字节)；总字节未知时为 0
        cancel (threading.Event): 置位后中止下载
        timeout: (连接超时, 读取超时)，秒

    Returns:
        str: 下载好的安装包路径

    Raises:
        UpdateError: 该版本没有安装包、网络/HTTP 失败、包不完整
        UpdateCancelled: 用户中止
    """
    if not getattr(info, 'asset_url', ''):
        raise UpdateError('该版本没有可下载的安装包')

    url = info.asset_url
    # 附件名来自远端，取 basename 免得带出目录分隔符
    name = os.path.basename(getattr(info, 'asset_name', '') or DEFAULT_INSTALLER_NAME)
    target_dir = directory or download_dir(getattr(info, 'version', ''))
    path = os.path.join(target_dir, name)
    expected = int(getattr(info, 'asset_size', 0) or 0)

    logger.info(f"开始下载 v{getattr(info, 'version', '')}：{url}")
    try:
        _fetch_with_requests(url, path, progress, cancel, timeout, expected)
    except UpdateCancelled:
        _remove_quietly(path)
        raise
    except Exception as e:
        # requests 自带 certifi 证书库；遇到企业代理/系统根证书差异时会报
        # 证书校验失败（本机实测 github.com 就走不通）。这种情况退回 urllib，
        # 它用 Windows 系统信任库，两种都失败才判下载失败——绝不跳过校验。
        if not _is_certificate_error(e):
            _remove_quietly(path)
            raise UpdateError(f'下载失败：{e}') from e
        logger.warning(f"requests 证书链校验失败，改用系统信任库重试：{e}")
        try:
            _fetch_with_urllib(url, path, progress, cancel, timeout, expected)
        except UpdateCancelled:
            _remove_quietly(path)
            raise
        except Exception as retry_error:
            _remove_quietly(path)
            raise UpdateError(f'下载失败：{retry_error}') from retry_error

    try:
        _verify(path, expected, getattr(info, 'asset_digest', ''))
    except UpdateError:
        # 校验不过的包不能留在磁盘上，免得下次误当成品安装
        _remove_quietly(path)
        raise
    logger.info(f"更新包已下载：{path}（{os.path.getsize(path)} 字节）")
    return path


def _is_certificate_error(error):
    """是否属于"证书链校验不过"这一类错误（换信任库值得重试）"""
    if isinstance(error, ssl.SSLError):
        return True
    text = str(error)
    return 'CERTIFICATE_VERIFY_FAILED' in text or 'certificate verify failed' in text


def _fetch_with_requests(url, path, progress, cancel, timeout, fallback_total=0):
    """requests 下载（跟随重定向、读环境代理变量）"""
    import requests

    with requests.get(url, stream=True, timeout=timeout,
                      headers={'Accept': 'application/octet-stream',
                               'User-Agent': _USER_AGENT}) as response:
        response.raise_for_status()
        total = int(response.headers.get('Content-Length') or fallback_total or 0)
        _save_chunks(response.iter_content(_CHUNK_SIZE), path, progress, cancel, total)


def _fetch_with_urllib(url, path, progress, cancel, timeout, fallback_total=0):
    """urllib 下载（走系统信任库与系统代理）

    延迟导入：本模块在启动时就 import，urllib.request 会连带拉进 http.client、
    email 等一堆东西（约 40ms），不该进冷启动路径。
    """
    import urllib.request

    connect_timeout = timeout[0] if isinstance(timeout, (tuple, list)) else timeout
    request = urllib.request.Request(
        url, headers={'User-Agent': _USER_AGENT, 'Accept': 'application/octet-stream'})
    with urllib.request.urlopen(request, timeout=connect_timeout or 30) as response:
        total = int(response.headers.get('Content-Length') or fallback_total or 0)
        _save_chunks(iter(lambda: response.read(_CHUNK_SIZE), b''),
                     path, progress, cancel, total)


def _save_chunks(chunks, path, progress, cancel, total):
    """
    把数据块写进文件并上报进度

    Args:
        chunks (iterable): 字节块迭代器
        path (str): 目标文件
        progress (callable): progress(已下载, 总字节)；可为 None
        cancel (threading.Event): 置位即中止
        total (int): 总字节；未知为 0
    """
    done = 0
    with open(path, 'wb') as handle:
        for chunk in chunks:
            if cancel is not None and cancel.is_set():
                raise UpdateCancelled('下载已取消')
            if not chunk:
                continue
            handle.write(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, total)


def _verify(path, expected_size=0, digest=''):
    """
    校验下载结果

    大小对不上说明传输被截断；EXE 头不是 MZ 说明拿到的是错误页而不是安装包
    （GitHub 的限流/错误响应同样是 200 的情况并不罕见）；发行版带 digest 时再
    比一次 sha256——自动装的东西必须确认是发布者上传的那一个。

    Args:
        path (str): 下载好的文件
        expected_size (int): 附件声明的大小；0 表示不校验
        digest (str): 附件摘要，形如 'sha256:...'；空表示不校验

    Raises:
        UpdateError: 校验不通过
    """
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    if size <= 0:
        raise UpdateError('下载的安装包是空的')
    if expected_size and size != expected_size:
        raise UpdateError(f'安装包不完整（{size}/{expected_size} 字节）')
    with open(path, 'rb') as handle:
        if handle.read(2) != b'MZ':
            raise UpdateError('下载到的不是可执行文件')
    _verify_digest(path, digest)


def _verify_digest(path, digest):
    """按 GitHub 给的 digest 校验内容（只认 sha256，其他算法跳过）"""
    algorithm, _, expected = str(digest or '').partition(':')
    if algorithm.lower() != 'sha256' or not expected:
        return
    hasher = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b''):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual.lower() != expected.strip().lower():
        raise UpdateError('安装包校验值不匹配，已丢弃')


def _remove_quietly(path):
    """删掉半截文件；失败只记日志（临时目录由系统回收）"""
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as e:
        logger.debug(f"清理临时安装包失败: {e}")


def launch(path):
    """
    启动安装程序（静默安装并关闭本程序，装完由 Setup 重新拉起）

    Args:
        path (str): 安装包路径

    Raises:
        UpdateError: 安装包不存在或无法启动
    """
    if not os.path.isfile(path):
        raise UpdateError('安装包不存在，请重新更新')
    try:
        process = subprocess.Popen(
            [path, *INSTALLER_ARGS], cwd=os.path.dirname(path) or None,
            close_fds=True)
    except OSError as e:
        raise UpdateError(f'无法启动安装程序：{e}') from e
    logger.info(f"安装程序已启动（pid={process.pid}）")
    return process
