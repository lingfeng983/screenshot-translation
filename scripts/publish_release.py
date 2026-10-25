#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一条命令出包 + 发版到 GitHub

流程：
    1. pytest 全量测试（--skip-tests 可跳过）
    2. PyInstaller 构建 onedir（OCR 组件不进 exe，见第 3 步）
    3. 出包：onedir 同步到 release/onedir/ → Inno Setup 编译唯一的交付物
       release/ScreenshotTranslator-Setup.exe；再抽 OCR 最小集切成
       dist/release/ocr-components.zip.00N，作为安装包安装时要下载的 Release 附件
    4. CHANGELOG 的 [Unreleased] 段落就地改名为 [v版本] — 日期
    5. git commit + tag + push（含 tag；origin 推 GitHub，gitee 远程存在则镜像）
    6. GitHub API 创建/更新 Release（取 [v版本] 段作为说明），并把安装包与
       OCR 组件分卷作为附件上传（uploads.github.com，二进制 body）：
       同名旧附件要等新附件传成功才删，所以整条命令可以重复执行。
       origin 与发布目标仓库不一致时在第 1 步之前就中止。
       Setup.exe 与 ocr-components 分卷必须落在同一个 tag 下——安装包按
       v版本 去取 OCR 附件，取不到就按设计中止安装。

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
    # 安装脚本用了 Inno 7 的内置下载（download 标志 + ExternalSize + Hash +
    # extractarchive），6.x 编不过，所以候选只列 7
    r"C:\Program Files\Inno Setup 7\iscc.exe",
    r"C:\Program Files (x86)\Inno Setup 7\iscc.exe",
]


def run(cmd, cwd=PROJECT_ROOT, check=True, env=None, capture=False):
    """
    执行子命令

    Args:
        capture (bool): 收走输出并原样回显，同时把文本返回给调用方判定
        （iscc 的输出是控制台码页，哨兵本身是 ASCII，解码失败也不影响判定）

    Returns:
        str: capture 时为合并后的输出，否则为空串
    """
    print(f"$ {' '.join(cmd)}")
    if capture:
        result = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace")
        output = result.stdout or ""
        print(output, end="")
    else:
        result = subprocess.run(cmd, cwd=cwd, env=env)
        output = ""
    if check and result.returncode != 0:
        sys.exit(f"命令失败，退出码 {result.returncode}")
    return output


def step_tests(skip):
    print("\n=== 1/6 测试 ===")
    if skip:
        print("跳过（--skip-tests）")
        return
    run([sys.executable, "-m", "pytest", "-q"])


def step_build(version, no_build):
    print("\n=== 2/6 PyInstaller 构建 ===")
    if no_build:
        print("跳过（--no-build）")
        return
    # 版本号经环境变量交给 spec，写进 exe 的版本资源（右键属性能看到）
    run([sys.executable, "-m", "PyInstaller", "screenshot-translator.spec", "--noconfirm"],
        env=dict(os.environ, ST_VERSION=version))


def step_assemble(version):
    print("\n=== 3/6 出包：Setup.exe + OCR 组件分卷 ===")
    src = os.path.join(PROJECT_ROOT, "dist", "ScreenshotTranslator")
    dst = os.path.join(PROJECT_ROOT, "release", "onedir")
    if not os.path.isdir(src):
        sys.exit(f"缺少 onedir 产物：{src}（先构建或去掉 --no-build）")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    # OCR 组件不进 exe，改成安装时从本次 Release 的附件拉取，所以分卷必须跟安装包
    # 一起产出、一起上传（包名固定，装填器按名字找）。而且必须**先**打包再编安装脚本：
    # download 标志要在编译期给出附件字节数与 SHA256，这两个值只有出包那步知道
    run([sys.executable, "scripts/collect_ocr_bundle.py"])
    run([sys.executable, "scripts/make_release_package.py"])
    parts_dir = os.path.join(PROJECT_ROOT, "dist", "release")
    for name in ("ocr-components.zip.001", "ocr-components.zip.sha256",
                 "ocr-artifact.iss"):
        path = os.path.join(parts_dir, name)
        if not os.path.isfile(path):
            sys.exit(f"缺少 OCR 附件：{path}（安装包里勾了下载的都会失败）")
    parts = sorted(n for n in os.listdir(parts_dir)
                   if n.startswith("ocr-components.zip.") and not n.endswith(".sha256"))
    if len(parts) > 1:
        # Setup 内置下载只取单个附件，多卷得回到老的合并路线，不能默默出一个
        # 勾了 OCR 就必然中止的安装包
        sys.exit(f"OCR 组件被切成了 {len(parts)} 卷：安装包只会下载第一卷，识别必然失败。"
                 f"用 --part-size 放宽到单卷（GitHub 单附件上限 2GB），"
                 f"或临时用 python scripts/setup_ocr.py --release v{version} 补装组件")

    iscc = next((p for p in ISCC_CANDIDATES if os.path.isfile(p)), None)
    if not iscc:
        sys.exit("未找到 Inno Setup 7 编译器 iscc.exe，请安装后重试")
    iss = os.path.join(PROJECT_ROOT, "scripts", "setup.iss")
    output = run([iscc, f"/DMyAppVersion={version}", f"/DMyAppOcrTag=v{version}", iss],
                 capture=True)
    # 上面查的是文件在不在，这里查的是编译结果真把 download 条目编进去了没有：
    # 三元组缺一项或多卷时 setup.iss 走的是"未就绪"分支，产物同名、编得过，
    # 只有装到用户机器上勾了 OCR 才会中止——那种包不能发
    if "OCRDownloadReady=1" not in output:
        reason = re.search(r"OCRDownloadReady=0 reason=(\S+)", output)
        sys.exit("安装包没编进 OCR 内置下载（"
                 + (reason.group(1) if reason else "编译输出里没有就绪哨兵")
                 + "）：确认 dist/release/ocr-artifact.iss 与单卷附件齐备后重跑")


def step_changelog(version):
    print("\n=== 4/6 CHANGELOG 版本化 ===")
    path = os.path.join(PROJECT_ROOT, "CHANGELOG.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    header = f"## [v{version}] — {date.today():%Y-%m-%d}"
    if header in text:
        print(f"已存在 {header}，跳过")
    else:
        if "## [Unreleased]" not in text:
            sys.exit("CHANGELOG.md 中没有 '## [Unreleased]' 段，无法自动版本化，请手动处理")
        text = text.replace("## [Unreleased]", header, 1)
        with open(path, "w", encoding="utf-8") as f:
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
    print("\n=== 5/6 git 提交 / tag / 推送 ===")
    tag = f"v{version}"
    run(["git", "add", "-A"])
    # release/ 在 .gitignore 内，不会被带入
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


def api_request(url, token, data=None, content_type=None, method=None):
    """GitHub REST 调用：JSON 走请求体，附件走原始二进制，返回 JSON

    令牌只放 Authorization 头：URL 查询串会被代理和访问日志原样落盘，
    等于把私人令牌写到别人机器上。
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
    with urlopen(req) as resp:
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

    with open(path, "rb") as f:
        payload = f.read()
    upload_url = (f"{GITHUB_UPLOAD}/repos/{GITHUB_REPO}/releases/{rid}/assets"
                  f"?name={quote(name)}")
    resp = api_request(upload_url, token, data=payload,
                       content_type="application/octet-stream")
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
    """本次要传的附件：安装包 + OCR 组件分卷与校验文件"""
    files = [os.path.join(PROJECT_ROOT, "release", "ScreenshotTranslator-Setup.exe")]
    parts_dir = os.path.join(PROJECT_ROOT, "dist", "release")
    files += sorted(
        os.path.join(parts_dir, n)
        for n in os.listdir(parts_dir)
        if n.startswith("ocr-components.zip.")
    ) if os.path.isdir(parts_dir) else []
    missing = [p for p in files if not os.path.isfile(p)]
    if missing:
        sys.exit(f"缺少待上传附件: {missing}")
    return files


def step_release(version, notes, token, no_api):
    print("\n=== 6/6 GitHub Release ===")
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
    check_origin()

    if args.only_release:
        with open(os.path.join(PROJECT_ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
            notes = extract_notes(f.read(), args.version)
        run_release_step(args.version, notes, read_token(), args.no_api)
        return 0

    step_tests(args.skip_tests)
    step_build(args.version, args.no_build)
    step_assemble(args.version)
    notes = step_changelog(args.version)
    step_git(args.version, f"v{args.version}")
    run_release_step(args.version, notes, read_token(), args.no_api)
    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
