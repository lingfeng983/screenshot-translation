#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一条命令出包 + 发版到 GitHub

流程：
    0. 预检（全只读）：干净工作树、CHANGELOG、tag 与 HEAD 一致、版本号可写、
       复用产物是否齐备、origin 是否为发布目标仓库
    1. pytest 全量测试（--skip-tests 可跳过）
    2. CHANGELOG 的 [Unreleased] 段落就地改名为 [v版本] — 日期
    3. 写 APP_VERSION → PyInstaller 构建 onedir（模型随 exe 分发）
    4. 出包：onedir 同步到 release/onedir/ → Inno Setup 编译唯一的交付物
       release/ScreenshotTranslator-Setup.exe
    5. 可选代码签名（ST_SIGNTOOL_COMMAND），成功则固化发布者指纹
    6. git commit（只提交显式清单里的文件）+ tag + push（gitee 存在则镜像）
    7. GitHub API 创建/更新 Release（取 [v版本] 段作为说明），流式上传安装包：
       同名旧附件要等新附件传成功才删，所以整条命令可以重复执行。

任何"必然会失败"的条件都在第 0 步验完；预检之后才允许改文件。

用法：
    python scripts/publish_release.py --version 1.2.0
    python scripts/publish_release.py --version 1.2.0 --skip-tests --no-build
    python scripts/publish_release.py --version 1.2.0 --only-release   # 只更新发行版+重传附件
    python scripts/publish_release.py --version 1.2.0 --no-api   # 只推送，Release 手动建

令牌：环境变量 GITHUB_TOKEN（PAT），否则从 git credential manager 取
   github.com 的凭证（Windows 凭证管理器；git credential fill）。
"""

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import quote

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GITHUB_REPO = "lingfeng983/screenshot-translation"
GITHUB_API = "https://api.github.com"
GITHUB_UPLOAD = "https://uploads.github.com"

logger = logging.getLogger("publish-release")

ISCC_CANDIDATES = [
    # lzma2/ultra64 压缩字典在 Inno 7 上才放开，6.x 会退回默认字典
    r"C:\Program Files\Inno Setup 7\iscc.exe",
    r"C:\Program Files (x86)\Inno Setup 7\iscc.exe",
]


def run(cmd, cwd=PROJECT_ROOT, check=True, env=None, capture=False):
    """
    执行子命令

    子进程的 stderr 一律并入输出流（不 capture 时用 stderr=STDOUT，capture 时
    stderr=PIPE）：PyInstaller / iscc 会把进度与 INFO 日志写在 stderr，而调用
    方常把整条命令的 stderr 一起重定向（`python ... 2>&1`）——在 PowerShell
    里那会变成一批错误记录，自动化据此把成功的发版判成失败。日志本身不是
    错误，统一走 stdout 才是"成功=0"的干净语义。

    Args:
        capture (bool): 收走输出并原样回显，同时把文本返回给调用方判定
        （iscc 的输出是控制台码页，哨兵本身是 ASCII，解码失败也不影响判定）

    Returns:
        str: capture 时为合并后的输出，否则为空串
    """
    print(f"$ {' '.join(cmd)}", flush=True)
    if capture:
        result = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace")
        output = result.stdout or ""
        print(output, end="", flush=True)
    else:
        result = subprocess.run(cmd, cwd=cwd, env=env,
                                stdout=None, stderr=subprocess.STDOUT)
        output = ""
    if check and result.returncode != 0:
        sys.exit(f"命令失败，退出码 {result.returncode}")
    return output


def step_tests(skip):
    print("\n=== 1/7 测试 ===")
    if skip:
        print("跳过（--skip-tests）")
        return
    run([sys.executable, "-m", "pytest", "-q"])


def step_app_version(version):
    """把本次发版版本号写进源码常量（随 tag 一起提交）"""
    print("\n=== 写入应用版本号 ===")
    path = os.path.join(PROJECT_ROOT, "managers", "app_version.py")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    pattern = r"(?m)^APP_VERSION = '[^']*'$"
    if not re.search(pattern, text):
        sys.exit("managers/app_version.py 中找不到 APP_VERSION 定义，无法写入版本号")
    new_text = re.sub(pattern, f"APP_VERSION = '{version}'", text, count=1)
    if new_text == text:
        print(f"已是 {version}")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"APP_VERSION -> {version}")


def step_build(version, no_build):
    print("\n=== 3/7 PyInstaller 构建 ===")
    if no_build:
        print("跳过（--no-build）")
        return
    # 版本号经环境变量交给 spec，写进 exe 的版本资源（右键属性能看到）
    run([sys.executable, "-m", "PyInstaller", "screenshot-translator.spec", "--noconfirm"],
        env=dict(os.environ, ST_VERSION=version))


def step_assemble(version):
    print("\n=== 4/7 出包：Setup.exe ===")
    src = os.path.join(PROJECT_ROOT, "dist", "ScreenshotTranslator")
    dst = os.path.join(PROJECT_ROOT, "release", "onedir")
    if not os.path.isdir(src):
        sys.exit(f"缺少 onedir 产物：{src}（先构建或去掉 --no-build）")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    iscc = next((p for p in ISCC_CANDIDATES if os.path.isfile(p)), None)
    if not iscc:
        sys.exit("未找到 Inno Setup 7 编译器 iscc.exe，请安装后重试")
    iss = os.path.join(PROJECT_ROOT, "scripts", "setup.iss")
    run([iscc, f"/DMyAppVersion={version}", iss], capture=True)


def read_changelog():
    """CHANGELOG 全文（预检与版本化共用一个读取入口）"""
    with open(os.path.join(PROJECT_ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        return f.read()


def check_changelog(version, only_release=False):
    """
    发版文案的只读预检

    以前这项检查放在 step_changelog 里，而 step_changelog 排在测试、写版本号、
    PyInstaller 构建、删除重建 release/onedir 与 Inno 编译之后——脚本跑到一半
    才因为「CHANGELOG 没有 [Unreleased] 段」退出，版本源码和产物已经被改过。
    所有这类"必然会失败"的条件都必须在任何写操作之前验完。
    """
    text = read_changelog()
    header = f"## [v{version}]"
    if only_release:
        if header not in text:
            sys.exit(f"CHANGELOG.md 中没有 {header} 段，--only-release 无法取到发行说明")
        return
    if header not in text and "## [Unreleased]" not in text:
        sys.exit("CHANGELOG.md 中既没有 '## [Unreleased]' 段，也没有本次版本的段落，"
                 "无法自动版本化，请手动处理")


def preflight(version, args):
    """
    发版预检：全部只读，且必须排在任何写操作之前

    检查：发布目标仓库、工作树是否干净、CHANGELOG 文案、tag 与 HEAD 是否一致、
    版本常量能否写入、复用产物时文件是否齐备、签名命令是否可用。
    """
    print("\n=== 0/7 发版预检 ===")
    check_origin()
    check_changelog(version, only_release=args.only_release)

    if not args.only_release:
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
            capture_output=True, text=True).stdout.strip()
        if status:
            sys.exit("工作区不干净，请先提交或清理以下文件再发版（未跟踪的生产模块"
                     "尤其容易在 git add -A 时被带出去）：\n" + status[:4000])

    tag = f"v{version}"
    tagged = git_output(["git", "rev-list", "-n", "1", tag])
    head = git_output(["git", "rev-parse", "HEAD"])
    if tagged and head and tagged != head:
        sys.exit(f"tag {tag} 已存在且指向 {tagged[:8]}，当前提交是 {head[:8]}："
                 f"发出去的附件会和该 tag 的源码不一致。请用新版本号重发，"
                 f"或确认无误后自行 git tag -f {tag} 再重跑")

    if not args.only_release:
        app_version_path = os.path.join(PROJECT_ROOT, "managers", "app_version.py")
        with open(app_version_path, encoding="utf-8") as f:
            if not re.search(r"(?m)^APP_VERSION = '[^']*'$", f.read()):
                sys.exit("managers/app_version.py 中找不到 APP_VERSION 定义，无法写入版本号")
        if args.no_build and not os.path.isdir(
                os.path.join(PROJECT_ROOT, "dist", "ScreenshotTranslator")):
            sys.exit("--no-build 需要现成的 dist/ScreenshotTranslator/ 产物")

    if args.only_release:
        # 正常流程会重新编译安装包，只有"只补发行版"时才要求附件已存在
        installer = os.path.join(PROJECT_ROOT, "release", "ScreenshotTranslator-Setup.exe")
        if not os.path.isfile(installer):
            sys.exit(f"缺少待上传附件: {installer}")

    if not args.only_release and os.environ.get("ST_SIGNTOOL_COMMAND", "").strip():
        import shlex
        if "$f" not in shlex.split(os.environ["ST_SIGNTOOL_COMMAND"]):
            sys.exit("ST_SIGNTOOL_COMMAND 必须包含 $f 占位符（待签名的 Setup.exe 路径）")
    print("预检通过")


PUBLISHER_THUMBPRINT_FILE_NAME = "publisher_thumbprint.txt"


def step_sign():
    """
    可选的代码签名与发布者指纹固化

    设置 ``ST_SIGNTOOL_COMMAND``（含 ``$f`` 占位符，指向待签名的 Setup.exe）
    时对安装包签名，验签通过后把证书指纹写进随程序分发的
    ``resources/publisher_thumbprint.txt``——客户端据此强制校验自动更新的
    签名者身份。未配置时只警告，保持历史发布可用（仍强制 sha256 摘要）。
    """
    print("\n=== 5/7 代码签名（可选） ===")
    command = os.environ.get("ST_SIGNTOOL_COMMAND", "").strip()
    if not command:
        logger.warning("未配置 ST_SIGNTOOL_COMMAND：安装包未做 Authenticode 签名，"
                       "自动更新只能依赖 Release 附件的 sha256 摘要。"
                       "配置签名证书后重跑可启用发布者指纹校验")
        return
    if sys.platform != "win32":
        sys.exit("代码签名需要 Windows 上的 signtool / PowerShell")

    import json
    import shlex
    installer = os.path.join(PROJECT_ROOT, "release", "ScreenshotTranslator-Setup.exe")
    argv = [part.replace("$f", installer) for part in shlex.split(command)]
    run(argv)
    script = (
        "$sig = Get-AuthenticodeSignature -LiteralPath $env:ST_SIGN_FILE; "
        "$thumb = ''; "
        "if ($sig.SignerCertificate) { "
        "$thumb = [string]$sig.SignerCertificate.Thumbprint }; "
        "@{ status = [string]$sig.Status; thumbprint = $thumb } | "
        "ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env=dict(os.environ, ST_SIGN_FILE=installer),
        capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        sys.exit(f"验签失败：{(result.stderr or '').strip()[:300]}")
    info = json.loads((result.stdout or '').strip() or '{}')
    status = str(info.get('status') or '')
    thumb = str(info.get('thumbprint') or '').replace(' ', '').upper()
    if status != 'Valid' or len(thumb) != 40:
        sys.exit(f"安装包签名无效（状态 {status or '未知'}，指纹 {thumb or '空'}），"
                 f"已中止发版，避免发布客户端校验不过的包")

    thumb_path = os.path.join(PROJECT_ROOT, "resources", PUBLISHER_THUMBPRINT_FILE_NAME)
    with open(thumb_path, "w", encoding="utf-8") as f:
        f.write(thumb + "\n")
    print(f"安装包签名有效，发布者指纹已写入 {thumb_path}")


def step_changelog(version):
    print("\n=== 2/7 CHANGELOG 版本化 ===")
    text = read_changelog()
    header = f"## [v{version}] — {date.today():%Y-%m-%d}"
    if header in text:
        print(f"已存在 {header}，跳过")
    else:
        if "## [Unreleased]" not in text:
            sys.exit("CHANGELOG.md 中没有 '## [Unreleased]' 段，无法自动版本化，请手动处理")
        text = text.replace("## [Unreleased]", header, 1)
        with open(os.path.join(PROJECT_ROOT, "CHANGELOG.md"), "w",
                  encoding="utf-8") as f:
            f.write(text)
        print(f"[Unreleased] -> {header}")
    return extract_notes(text, version)


def extract_notes(changelog_text, version):
    m = re.search(rf"## \[v{re.escape(version)}\][^\n]*\n(.*?)(?=\n## \[v?|\Z)",
                  changelog_text, re.S)
    return m.group(1).strip() if m else f"v{version}"


def git_output(args):
    return subprocess.run(args, cwd=PROJECT_ROOT, capture_output=True,
                          text=True).stdout.strip()


def current_branch():
    branch = git_output(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if not branch or branch == "HEAD":
        sys.exit("取不到当前分支名（游离 HEAD？），无法确定发版分支")
    return branch


def check_origin():
    """
    确认 origin 就是发布目标仓库

    脚本会直接在 GITHUB_REPO 上建 Release、传附件、推 tag：在 fork 里跑这条
    命令，版本会发到上游去，而附件对应的源码提交在 origin 上根本不存在。
    """
    url = git_output(["git", "remote", "get-url", "origin"])
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?/?$", url)
    if not m:
        sys.exit(f"origin 不是一个 GitHub 仓库，无法发版：{url or '(未配置 origin)'}")
    if m.group(1).lower() != GITHUB_REPO.lower():
        sys.exit(f"origin 是 {m.group(1)}，与发布目标 {GITHUB_REPO} 不一致")


def step_git(version, notes_title):
    print("\n=== 6/7 git 提交 / tag / 推送 ===")
    tag = f"v{version}"
    # 显式清单而不是 git add -A：发版提交只包含本次脚本自己改过的文件。
    # 开发者手边没审查完的改动、未跟踪的在制品都不该被一次发版顺手推上去。
    release_files = ["CHANGELOG.md", "managers/app_version.py"]
    thumbprint_file = "resources/publisher_thumbprint.txt"
    if os.path.isfile(os.path.join(PROJECT_ROOT, thumbprint_file)):
        release_files.append(thumbprint_file)
    run(["git", "add", "--", *release_files])
    status = subprocess.run(["git", "diff", "--cached", "--quiet"],
                            cwd=PROJECT_ROOT).returncode
    if status != 0:
        run(["git", "commit", "-m", f"发布 {tag}"])
    else:
        print("无待提交改动")
    tags = git_output(["git", "tag", "-l", tag])
    if not tags:
        run(["git", "tag", "-a", tag, "-m", notes_title or f"v{version}"])
    else:
        # tag 已存在时不新建，但必须确认它指的就是刚才这一份代码：脚本随后会在这个
        # tag 上发 Release 并挂本次构建的附件，tag 指向别处就等于发出去的安装包
        # 和页面上的源码对不上。改 tag 要 force-push（会顶掉别人已经拉过的引用），
        # 所以这里只中止，由人决定是重版本号还是确认过再手动移动。
        tagged = git_output(["git", "rev-list", "-n", "1", tag])
        head = git_output(["git", "rev-parse", "HEAD"])
        if tagged and tagged != head:
            sys.exit(f"tag {tag} 已存在且指向 {tagged[:8]}，当前提交是 {head[:8]}："
                     f"发出去的附件会和该 tag 的源码不一致。请用新版本号重发，"
                     f"或确认无误后自行 git tag -f {tag} 再重跑（--only-release 只补发行版）")
    branch = current_branch()
    run(["git", "push", "origin", branch])
    run(["git", "push", "origin", tag])
    # gitee 远程存在时镜像推送（老渠道存档；推送失败不拦 GitHub 发版）
    if "gitee" in git_output(["git", "remote"]).split():
        mirrored = True
        for ref in (branch, tag):
            proc = subprocess.run(["git", "push", "gitee", ref],
                                  cwd=PROJECT_ROOT, capture_output=True, text=True)
            if proc.returncode != 0:
                mirrored = False
                logger.warning(f"gitee 镜像推送 {ref} 失败（不影响 GitHub 发版）："
                               f"{(proc.stderr or '').strip()[:200]}")
        if mirrored:
            print("已镜像推送到 gitee 远程")


def read_token():
    """GitHub 令牌：GITHUB_TOKEN 环境变量优先，否则从 credential manager 取"""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    # git credential fill 走已配置的 helper（Windows 上即凭证管理器）；
    # 回显会把令牌带进子进程管道，这里只取 password= 行且不打印
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("password="):
                    return line[len("password="):].strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def api_request(url, token, data=None, content_type=None, method=None,
                timeout=30):
    """GitHub REST 调用：JSON 走请求体，附件走原始二进制，返回 JSON

    令牌只放 Authorization 头：URL 查询串会被代理和访问日志原样落盘，
    等于把私人令牌写到别人机器上。

    Args:
        timeout (float): socket 级超时（连接与每次读），不是整个请求的总
            时长；上传几十 MB 附件时由调用方放宽，避免慢网被误判卡死。
    """
    import json
    from urllib.request import Request, urlopen
    if isinstance(data, (dict, list)):
        import json as _json
        body = _json.dumps(data).encode("utf-8")
        content_type = "application/json"
    elif isinstance(data, (bytes, bytearray)):
        # 附件上传的二进制报文由调用方原样发出
        body = bytes(data)
    elif data is not None:
        body = str(data).encode("utf-8")
    else:
        body = None
    req = Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "publish-release/1.0")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if content_type:
        req.add_header("Content-Type", content_type)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def error_detail(exc):
    """HTTP 错误体里的说明（GitHub 把原因放在 body 的 message 字段）"""
    try:
        import json
        payload = exc.read().decode("utf-8", "replace")[:300]
        try:
            return json.loads(payload).get("message", payload)
        except ValueError:
            return payload
    except Exception:
        return str(exc)


def get_release(tag, token):
    """
    按标签查发行版：不存在返回 None，其它错误照抛

    早先所有异常都吞成 None，令牌失效也会走到"创建"分支，
    最后报一个看不出根因的错。
    """
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/releases/tags/{quote(tag)}"
    try:
        d = api_request(url, token) or {}
    except HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"查询 Release {tag} 失败（HTTP {e.code}）：{error_detail(e)}") from e
    return d if d.get("id") else None


def stream_upload(url, path, token, timeout=120):
    """
    流式上传附件（几十 MB 的 EXE 不整块读进内存）

    urllib 的 Request.data 会把 bytes 一次性塞进请求体；这里直接用
    http.client 分块 send，Content-Length 由本地文件大小给出。

    Raises:
        HTTPError: GitHub 返回非 2xx，错误体交给 error_detail 读出
    """
    import http.client
    import io
    import json
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    target = parts.path + (('?' + parts.query) if parts.query else '')
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'publish-release/1.0',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/octet-stream',
        'Content-Length': str(os.path.getsize(path)),
    }
    conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443,
                                       timeout=timeout)
    try:
        conn.putrequest('POST', target)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(256 * 1024), b''):
                conn.send(chunk)
        response = conn.getresponse()
        raw = response.read()
        status, response_headers = response.status, response.headers
    finally:
        conn.close()
    if not 200 <= status < 300:
        raise HTTPError(url, status, '附件上传失败', response_headers,
                        io.BytesIO(raw))
    return json.loads(raw.decode('utf-8') or '{}')


def upload_attachment(rid, path, token):
    """
    上传单个附件，成功后才删被它顶掉的同名旧附件

    先删后传的话，上传一失败这个附件就没了，装填端只能报"附件不存在"；
    先传后删最坏是同时留两份，重复执行即收敛。
    """
    name = os.path.basename(path)
    url_base = f"{GITHUB_API}/repos/{GITHUB_REPO}/releases/{rid}/assets"
    try:
        listed = api_request(url_base, token) or []
        stale = [a["id"] for a in listed if a.get("name") == name]
    except HTTPError as e:
        # 列不出已有附件不拦上传：别把已经传得动的包堵死
        print(f"  ⚠ 列不出已有附件（HTTP {e.code}），跳过同名旧附件清理")
        stale = []

    upload_url = (f"{GITHUB_UPLOAD}/repos/{GITHUB_REPO}/releases/{rid}/assets"
                  f"?name={quote(name)}")
    # 上传是几十 MB 的二进制，慢网下单次写可能远超普通 API 的超时
    resp = stream_upload(upload_url, path, token, timeout=120)
    print(f"  已上传 {resp['name']}（{resp.get('size', 0) / 1048576:.1f} MB）")
    print(f"  {resp['browser_download_url']}")
    undeleted = []
    for att_id in stale:
        try:
            api_request(f"{url_base}/{att_id}", token, method="DELETE")
            print(f"  已删除被顶掉的旧附件 id={att_id}")
        except HTTPError as e:
            if e.code == 404:
                # 已经被删掉了（并发重跑、或页面上手动删过），目的达到
                print(f"  旧附件 id={att_id} 已不存在")
                continue
            undeleted.append(f"{name} id={att_id}（HTTP {e.code}：{error_detail(e)}）")
    if undeleted:
        # 同名两份不是"页面上多一份"这么轻：装填端是按名字取附件的，命中旧的
        # 那份就永远过不了 SHA256 校验，而且每台机器都这样。传新件成功但清不掉
        # 旧件时必须让这条命令以失败结束，逼人在页面上删掉再重跑。
        raise RuntimeError("以下同名旧附件没删掉，页面上会有两份、装填端可能取到旧的那份，"
                           "请到 Release 页面手动删除后重跑（--only-release 只补这一步）：\n  "
                           + "\n  ".join(undeleted))


def collect_attachments():
    """本次要传的附件：只有安装包（OCR 模型已打进 onedir）"""
    files = [os.path.join(PROJECT_ROOT, "release", "ScreenshotTranslator-Setup.exe")]
    missing = [p for p in files if not os.path.isfile(p)]
    if missing:
        sys.exit(f"缺少待上传附件: {missing}")
    return files


def step_release(version, notes, token, no_api):
    print("\n=== 7/7 GitHub Release ===")
    tag = f"v{version}"
    if no_api or not token:
        if not no_api:
            print("⚠ 未取到 GitHub 令牌（GITHUB_TOKEN 或 git credential manager），转手动模式")
        url = f"https://github.com/{GITHUB_REPO}/releases/new?tag={tag}&title={tag}"
        notes_path = os.path.join(PROJECT_ROOT, f"release-notes-{tag}.md")
        with open(notes_path, "w", encoding="utf-8") as f:
            f.write(notes + "\n")
        print(f"Release 说明已输出到 {notes_path}，可直接粘贴")
        webbrowser.open(url)
        return

    branch = current_branch()
    rel = get_release(tag, token)
    if rel is None:
        rel = api_request(f"{GITHUB_API}/repos/{GITHUB_REPO}/releases", token, {
            "tag_name": tag, "name": tag, "body": notes,
            "target_commitish": branch, "draft": False, "prerelease": False,
        })
        print(f"Release 已创建 id={rel.get('id')}")
    else:
        api_request(f"{GITHUB_API}/repos/{GITHUB_REPO}/releases/{rel['id']}", token, {
            "tag_name": tag, "name": tag, "body": notes,
        }, method="PATCH")
        print(f"Release 已更新 id={rel.get('id')}")

    rid = rel.get("id")
    print("上传附件：")
    for path in collect_attachments():
        upload_attachment(rid, path, token)
    print(f"页面: https://github.com/{GITHUB_REPO}/releases/tag/{tag}")


def run_release_step(version, notes, token, no_api):
    """
    跑第 6 步并把接口失败收成一行原因

    到这一步包已经全建好了，甩一个 traceback 反而看不出是令牌、网络还是字段问题。
    """
    try:
        step_release(version, notes, token, no_api)
    except HTTPError as e:
        sys.exit(f"发版失败（HTTP {e.code}）：{error_detail(e)}")
    except URLError as e:
        sys.exit(f"发版失败（连不上 GitHub）：{e.reason}")
    except RuntimeError as e:
        sys.exit(f"发版失败：{e}")


def main():
    # 输出改成行缓冲：本脚本的进度行与子进程（pytest/PyInstaller/iscc）的
    # 直写输出共用终端，缓冲会让父进程的进度行整段滞后到子进程之后，
    # 日志顺序看起来像"发布完了才开始测试"
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    # 本脚本自己的告警也走 stdout：日志不是错误，stderr 一沾上就可能被调用方
    # 的 `2>&1` 变成错误记录，把成功的发版判成失败
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="[%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="一条命令出包+发版到 GitHub")
    parser.add_argument("--version", required=True, help="版本号，如 1.2.0（tag 自动加 v 前缀）")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--no-build", action="store_true", help="复用现有 dist/ 产物")
    parser.add_argument("--no-api", action="store_true", help="不调 GitHub API，仅推送并打开手动发布页")
    parser.add_argument("--only-release", action="store_true",
                        help="只做第 6 步：按 CHANGELOG 现有 [v版本] 段更新发行版并重传附件")
    args = parser.parse_args()

    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        sys.exit("版本号格式应为 x.y.z")
    # 所有只读预检都排在写操作之前：工作树、CHANGELOG、tag、产物、签名配置
    preflight(args.version, args)

    if args.only_release:
        notes = extract_notes(read_changelog(), args.version)
        run_release_step(args.version, notes, read_token(), args.no_api)
        return 0

    step_tests(args.skip_tests)
    # CHANGELOG 版本化是会改文件的那一步里最容易失败的（缺 [Unreleased]），
    # 放到测试之后、写版本号与构建之前；预检已保证它不会在这里失败
    notes = step_changelog(args.version)
    step_app_version(args.version)
    step_build(args.version, args.no_build)
    step_assemble(args.version)
    step_sign()
    step_git(args.version, f"v{args.version}")
    run_release_step(args.version, notes, read_token(), args.no_api)
    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
