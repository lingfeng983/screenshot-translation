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
import re
import ssl
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

from . import app_paths

logger = logging.getLogger('screenshot-translation')

DEFAULT_INSTALLER_NAME = 'ScreenshotTranslator-Setup.exe'
_CHUNK_SIZE = 256 * 1024
_DOWNLOAD_TIMEOUT = (5, 30)
# 安装程序参数：静默但保留进度窗口；关掉运行中的本体再覆盖文件；不重启系统
INSTALLER_ARGS = ('/SILENT', '/CLOSEAPPLICATIONS', '/SUPPRESSMSGBOXES', '/NORESTART')
_USER_AGENT = 'ScreenshotTranslator-Updater'

# 目录名与附件名都来自远端响应：只接受不含路径分隔符的短标识，
# 且目录名不得出现连续的点（避免任何形式的相对路径写法）。
_SAFE_COMPONENT_RE = re.compile(r'^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$')
_SAFE_ASSET_RE = re.compile(r'^[0-9A-Za-z][0-9A-Za-z._-]{0,127}\.exe$', re.I)
_ALLOWED_ASSET_HOSTS = ('github.com', 'objects.githubusercontent.com',
                        'release-assets.githubusercontent.com',
                        'github-releases.githubusercontent.com')
# 发布者指纹的配置来源：环境变量优先，其次随程序分发的文本文件。
PUBLISHER_THUMBPRINT_ENV = 'ST_PUBLISHER_THUMBPRINT'
PUBLISHER_THUMBPRINT_FILE = 'publisher_thumbprint.txt'


class UpdateError(Exception):
    """更新过程中的可读错误（下载失败、包不完整等）"""


class UpdateCancelled(UpdateError):
    """用户取消了下载"""


def _safe_dir_component(version):
    """
    把远端版本号收敛成安全的单层目录名

    ``version`` 来自 GitHub 的 tag_name，属于不可信输入：形如
    ``v1.9.0/../../../Public/pwned`` 的字符串在版本比较时仍会被解析成
    ``(1, 9, 0)``，直接拼进 ``os.path.join`` 就能把下载目录逃逸到临时目录外。

    Returns:
        str: 可安全拼进路径的单层目录名

    Raises:
        UpdateError: 版本号含路径分隔符或其它非法字符
    """
    component = str(version or 'latest').strip()
    if not _SAFE_COMPONENT_RE.fullmatch(component) or '..' in component:
        raise UpdateError(f'更新版本号不合法，已拒绝下载: {component!r}')
    return component


def download_dir(version):
    """
    本次更新专用的临时目录

    按版本分目录，重复下载不会撞上上次残留的半截文件。目录名经过白名单
    净化，最后再用 commonpath 兜一次底，确保任何输入都逃不出临时根目录。

    Args:
        version (str): 目标版本号

    Returns:
        str: 目录绝对路径（已创建）

    Raises:
        UpdateError: 版本号不合法，或最终路径逃出了临时根目录
    """
    base = os.path.abspath(os.path.join(
        tempfile.gettempdir(), 'screenshot-translator-update'))
    path = os.path.abspath(os.path.join(base, _safe_dir_component(version)))
    if os.path.commonpath((base, path)) != base or path == base:
        raise UpdateError('更新目录越界，已拒绝下载')
    os.makedirs(path, exist_ok=True)
    return path


def _safe_asset_name(name):
    """
    远端附件名 → 安全的本地文件名

    取 basename 之外还要整串校验：``..\\evil.exe``、空名、带控制字符的名字
    都可能让下载写到目标目录之外或覆盖别的文件。

    Returns:
        str: 可用的文件名

    Raises:
        UpdateError: 附件名不是合法的 exe 文件名
    """
    candidate = str(name or DEFAULT_INSTALLER_NAME)
    # 出现路径分隔符或 '..' 直接拒绝，不要静默取 basename：远端给了
    # '..\evil.exe' 说明响应本身可疑，取个 basename 继续下载等于替它洗白。
    if '/' in candidate or '\\' in candidate or '..' in candidate \
            or '\x00' in candidate:
        raise UpdateError(f'安装包文件名不合法，已拒绝下载: {candidate!r}')
    if not _SAFE_ASSET_RE.fullmatch(candidate):
        raise UpdateError(f'安装包文件名不合法，已拒绝下载: {candidate!r}')
    return candidate


def _validate_asset_url(url):
    """
    附件直链必须是 GitHub 系的 HTTPS 地址

    ``requests`` 会拒绝 file://，但证书回退走的是 urllib，而 urllib 支持
    file:// 等协议；远端响应一旦被篡改就能让程序读本地文件并当成安装包。
    """
    parsed = urlparse(str(url or ''))
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not any(
            host == allowed or host.endswith('.' + allowed)
            for allowed in _ALLOWED_ASSET_HOSTS):
        raise UpdateError(f'安装包地址不合法，已拒绝下载: {str(url)[:120]!r}')


def _expected_digest(digest):
    """
    取发行版附件的 sha256 摘要

    摘要缺失或算法不是 sha256 时直接判失败：自动执行一个几十 MB 的 EXE，
    不能只靠文件大小和 MZ 头，更不能因为 API 没给 digest 就静默跳过校验。

    Returns:
        str: 小写十六进制摘要

    Raises:
        UpdateError: 摘要缺失、算法不支持或格式不对
    """
    raw = str(digest or '').strip()
    algorithm, separator, expected = raw.partition(':')
    if not separator:
        # 调用方已经把校验值规范化成裸 hex 时也认，仍只按 sha256 比较
        algorithm, expected = 'sha256', raw
    expected = expected.strip().lower()
    if algorithm.lower() != 'sha256' or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise UpdateError('发行版未提供有效的 sha256 摘要，已拒绝自动更新')
    return expected


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
    _validate_asset_url(url)
    # 远端字段全部先净化再落盘；摘要缺失时连下载都不发起，省掉几十 MB 流量
    name = _safe_asset_name(getattr(info, 'asset_name', ''))
    digest = _expected_digest(getattr(info, 'asset_digest', ''))
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
        _verify(path, expected, digest)
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

    # urllib 的 timeout 是"每次 socket 操作"的总限时，不像 requests 那样能拆成
    # (连接, 读取)。这里取较长的读超时：沿用 5 秒连接值会让慢网下每读一块都
    # 只有 5 秒，反而比 requests 路径更严、更容易把正常下载误判成失败。
    socket_timeout = timeout[1] if isinstance(timeout, (tuple, list)) else timeout
    request = urllib.request.Request(
        url, headers={'User-Agent': _USER_AGENT, 'Accept': 'application/octet-stream'})
    with urllib.request.urlopen(request, timeout=socket_timeout or 30) as response:
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
        digest (str): 附件摘要，形如 'sha256:...' 或裸 sha256 hex；
            必须提供，缺失/算法不支持/格式错误一律拒绝安装

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
    """按 GitHub 给的 digest 校验内容（强制 sha256，缺失或其它算法直接拒绝）"""
    expected = _expected_digest(digest)
    hasher = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b''):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual != expected:
        raise UpdateError('安装包校验值不匹配，已丢弃')


def _remove_quietly(path):
    """删掉半截文件；失败只记日志（临时目录由系统回收）"""
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as e:
        logger.debug(f"清理临时安装包失败: {e}")


def expected_publisher_thumbprint():
    """
    读取随程序分发的发布者证书指纹

    优先级：环境变量 ``ST_PUBLISHER_THUMBPRINT`` > 打包资源
    ``publisher_thumbprint.txt``。发版脚本对安装包完成 Authenticode 签名后
    把证书指纹写进该资源文件，客户端就会强制校验签名者身份。

    Returns:
        str: 规范化后的大写十六进制指纹；未配置时为空串
    """
    raw = os.environ.get(PUBLISHER_THUMBPRINT_ENV, '').strip()
    if not raw:
        # 源码运行：发版脚本把文件写在 resources/ 下；
        # 打包运行：spec 把它放到 _internal 根（resource_path 的第一候选）。
        candidates = (
            app_paths.resource_path(PUBLISHER_THUMBPRINT_FILE),
            app_paths.resource_path('resources', PUBLISHER_THUMBPRINT_FILE),
        )
        for path in candidates:
            try:
                if os.path.isfile(path):
                    with open(path, encoding='utf-8-sig') as handle:
                        raw = handle.read().strip()
                    break
            except OSError as e:
                logger.debug(f"读取发布者指纹失败 {path}: {e}")
    return _normalize_thumbprint(raw)


def _normalize_thumbprint(value):
    """去掉 Windows 证书指纹里常见的空格/冒号，统一成大写"""
    return str(value or '').replace(' ', '').replace(':', '').strip().upper()


def _authenticode_status(path):
    """
    查询 Windows Authenticode 签名状态与签名者证书指纹

    非 Windows 或查询失败时返回 None，由调用方按"是否配置了期望指纹"决定
    放行还是拒绝——绝不把查询失败当成签名有效。

    Returns:
        dict 或 None: {'status': ..., 'thumbprint': ...}
    """
    if sys.platform != 'win32':
        return None
    script = (
        "$sig = Get-AuthenticodeSignature -LiteralPath $env:ST_VERIFY_PATH; "
        "$thumb = ''; "
        "if ($sig.SignerCertificate) { "
        "$thumb = [string]$sig.SignerCertificate.Thumbprint }; "
        "@{ status = [string]$sig.Status; thumbprint = $thumb } | "
        "ConvertTo-Json -Compress"
    )
    env = dict(os.environ, ST_VERIFY_PATH=os.path.abspath(path))
    try:
        import json
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
            env=env, capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            logger.warning(f"读取安装包签名失败: {(result.stderr or '').strip()[:200]}")
            return None
        data = json.loads((result.stdout or '').strip() or '{}')
        return {'status': str(data.get('status') or ''),
                'thumbprint': _normalize_thumbprint(data.get('thumbprint'))}
    except Exception as e:
        logger.warning(f"读取安装包签名异常: {e}")
        return None


def verify_publisher_signature(path, expected=None):
    """
    执行前的发布者真实性检查

    Digest 只能证明"下载到的就是 Release 附件那一个"，而附件与 digest 来自
    同一次 API 响应：仓库、发布凭证或 CDN 任一环节失守都不足以拦住恶意包。
    因此发布包一旦配置了签名证书指纹，这里会强制要求签名有效且指纹一致。
    尚未启用代码签名的发行版仍按历史行为放行（只靠强制 sha256），但会明确
    记一条警告，方便发版时发现问题。

    Raises:
        UpdateError: 签名无效、签名者不匹配，或配置了指纹却无法完成校验
    """
    expected = (_normalize_thumbprint(expected) if expected is not None
                else expected_publisher_thumbprint())
    if expected and not re.fullmatch(r'[0-9A-F]{40}', expected):
        raise UpdateError('发布者指纹配置格式错误，已拒绝自动更新')
    info = _authenticode_status(path)
    if info is None:
        if expected:
            raise UpdateError('无法验证安装包签名，已拒绝自动更新')
        logger.warning('当前环境无法检查安装包 Authenticode 签名，仅按 sha256 摘要校验')
        return False

    status = (info.get('status') or '').strip()
    thumbprint = info.get('thumbprint') or ''
    if status == 'Valid':
        if expected and thumbprint != expected:
            raise UpdateError('安装包签名者不是受信任的发布者，已拒绝自动更新')
        logger.info('安装包 Authenticode 签名有效'
                    + ('（发布者指纹已核对）' if expected else '（未固定发布者指纹）'))
        return True
    if status in ('', 'NotSigned'):
        if expected:
            raise UpdateError('发行版要求签名安装包，但下载到的文件未签名')
        logger.warning('安装包未做 Authenticode 签名；建议发版时启用代码签名并固定发布者指纹')
        return False
    raise UpdateError(f'安装包签名校验不通过（{status}），已拒绝自动更新')


def launch(path):
    """
    启动安装程序（静默安装并关闭本程序，装完由 Setup 重新拉起）

    执行前会再核一次发布者签名：下载与启动之间安装包一直躺在临时目录里，
    校验放在最后一步才不会被这段窗口绕过。

    Args:
        path (str): 安装包路径

    Raises:
        UpdateError: 安装包不存在、签名不受信任或无法启动
    """
    if not os.path.isfile(path):
        raise UpdateError('安装包不存在，请重新更新')
    verify_publisher_signature(path)
    try:
        process = subprocess.Popen(
            [path, *INSTALLER_ARGS], cwd=os.path.dirname(path) or None,
            close_fds=True)
    except OSError as e:
        raise UpdateError(f'无法启动安装程序：{e}') from e
    logger.info(f"安装程序已启动（pid={process.pid}）")
    return process
