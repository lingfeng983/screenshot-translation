#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一条命令出包 + 发版到 Gitee

流程：
    1. pytest 全量测试（--skip-tests 可跳过）
    2. PyInstaller 构建 onedir（OCR 组件不进 exe，见第 3 步）
    3. 出包：onedir 同步到 release/onedir/ → Inno Setup 编译唯一的交付物
       release/ScreenshotTranslator-Setup.exe；再抽 OCR 最小集切成
       dist/release/ocr-components.zip.00N，作为安装包安装时要下载的 Release 附件
    4. CHANGELOG 的 [Unreleased] 段落就地改名为 [v版本] — 日期
    5. git commit + tag + push（含 tag）
    6. Gitee API 创建/更新 Release（取 [v版本] 段作为说明），并把安装包与
       OCR 组件分卷作为附件上传（releases/{id}/attach_files，multipart）：
       同名旧附件先删再传，所以整条命令可以重复执行。
       Setup.exe 与 ocr-components 分卷必须落在同一个 tag 下——安装包按
       v版本 去取 OCR 附件，取不到就按设计中止安装。

用法：
    python scripts/publish_release.py --version 1.2.0
    python scripts/publish_release.py --version 1.2.0 --skip-tests --no-build
    python scripts/publish_release.py --version 1.2.0 --only-release   # 只更新发行版+重传附件
    python scripts/publish_release.py --version 1.2.0 --no-api   # 只推送，Release 手动建

令牌：环境变量 GITEE_ACCESS_TOKEN，或项目根 .gitee_token 文件（已 gitignore）
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from datetime import date
from urllib.parse import quote

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GITEE_REPO = "lingfeng983/screenshot-translation"
GITEE_API = "https://gitee.com/api/v5"

ISCC_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\iscc.exe",
    r"C:\Program Files\Inno Setup 6\iscc.exe",
    r"C:\Program Files (x86)\Inno Setup 7\iscc.exe",
    r"C:\Program Files\Inno Setup 7\iscc.exe",
]


def run(cmd, cwd=PROJECT_ROOT, check=True):
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    if check and result.returncode != 0:
        sys.exit(f"命令失败，退出码 {result.returncode}")
    return result.returncode


def step_tests(skip):
    print("\n=== 1/6 测试 ===")
    if skip:
        print("跳过（--skip-tests）")
        return
    run([sys.executable, "-m", "pytest", "-q"])


def step_build(no_build):
    print("\n=== 2/6 PyInstaller 构建 ===")
    if no_build:
        print("跳过（--no-build）")
        return
    run([sys.executable, "-m", "PyInstaller", "screenshot-translator.spec", "--noconfirm"])


def step_assemble(version):
    print("\n=== 3/6 出包：Setup.exe + OCR 组件分卷 ===")
    src = os.path.join(PROJECT_ROOT, "dist", "ScreenshotTranslator")
    dst = os.path.join(PROJECT_ROOT, "release", "onedir")
    if not os.path.isdir(src):
        sys.exit(f"缺少 onedir 产物：{src}（先构建或去掉 --no-build）")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    iscc = next((p for p in ISCC_CANDIDATES if os.path.isfile(p)), None)
    if not iscc:
        sys.exit("未找到 Inno Setup 编译器 iscc.exe，请安装后重试")
    iss = os.path.join(PROJECT_ROOT, "scripts", "setup.iss")
    run([iscc, f"/DMyAppVersion={version}", f"/DMyAppOcrTag=v{version}", iss])

    # OCR 组件不进 exe，改成安装时从本次 Release 的附件拉取，
    # 所以分卷必须跟安装包一起产出、一起上传（包名固定，装填器按名字找）
    run([sys.executable, "scripts/collect_ocr_bundle.py"])
    run([sys.executable, "scripts/make_release_package.py"])
    parts_dir = os.path.join(PROJECT_ROOT, "dist", "release")
    for name in ("ocr-components.zip.001", "ocr-components.zip.sha256"):
        path = os.path.join(parts_dir, name)
        if not os.path.isfile(path):
            sys.exit(f"缺少 OCR 附件：{path}（安装包里勾了下载的都会失败）")


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
    tags = subprocess.run(["git", "tag", "-l", tag], cwd=PROJECT_ROOT,
                          capture_output=True, text=True).stdout.strip()
    if not tags:
        run(["git", "tag", "-a", tag, "-m", notes_title or f"v{version}"])
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            cwd=PROJECT_ROOT, capture_output=True, text=True).stdout.strip()
    run(["git", "push", "origin", branch])
    run(["git", "push", "origin", tag])


def read_token():
    token = os.environ.get("GITEE_ACCESS_TOKEN", "").strip()
    if token:
        return token
    path = os.path.join(PROJECT_ROOT, ".gitee_token")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def api_request(url, token, data=None, content_type=None, method=None):
    """Gitee v5 调用：表单参数走 urlencode，附件走 multipart，返回 JSON"""
    import json
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
    body = None
    if isinstance(data, dict):
        body = urlencode(dict(data, access_token=token)).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    elif isinstance(data, (bytes, bytearray)):
        # 附件上传的 multipart 报文由调用方拼好，原样发出
        body = bytes(data)
    elif data is not None:
        body = json.dumps(data).encode("utf-8")
        content_type = "application/json"
    req = Request(url, data=body, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    with urlopen(req) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def get_release(tag, token):
    """按标签查发行版，不存在返回 None"""
    try:
        d = api_request(f"{GITEE_API}/repos/{GITEE_REPO}/releases/tags/{quote(tag)}",
                        token)
        return d if d.get("id") else None
    except Exception:
        return None


def upload_attachment(rid, path, token):
    """上传单个附件；同名旧附件先删掉，所以重复执行不会产生两份"""
    import uuid
    from urllib.parse import quote as _q
    name = os.path.basename(path)
    url_base = f"{GITEE_API}/repos/{GITEE_REPO}/releases/{rid}/attach_files"
    for att in api_request(f"{url_base}?access_token={_q(token)}", token):
        if att.get("name") == name:
            api_request(f"{url_base}/{att['id']}?access_token={_q(token)}",
                        token, method="DELETE")
            print(f"  覆盖旧附件 {name} (id={att['id']})")
    bid = uuid.uuid4().hex

    def part(field, value, filename=None):
        line = f'Content-Disposition: form-data; name="{field}"'
        if filename:
            line += f'; filename="{filename}"'
        raw = value if isinstance(value, bytes) else value.encode("utf-8")
        return (f"--{bid}\r\n{line}\r\nContent-Type: application/octet-stream\r\n\r\n"
                ).encode("utf-8") + raw + b"\r\n"

    with open(path, "rb") as f:
        payload = (part("access_token", token) + part("release_id", str(rid))
                   + part("file", f.read(), name) + f"--{bid}--\r\n".encode())
    resp = api_request(url_base, token, data=payload,
                       content_type=f"multipart/form-data; boundary={bid}")
    print(f"  已上传 {resp['name']}（{resp['size'] / 1048576:.1f} MB）")
    print(f"  {resp['browser_download_url']}")


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
    print("\n=== 6/6 Gitee Release ===")
    tag = f"v{version}"
    if no_api or not token:
        if not no_api:
            print("⚠ 未找到 GITEE_ACCESS_TOKEN（环境变量或 .gitee_token 文件），转手动模式")
        url = f"https://gitee.com/{GITEE_REPO}/releases/new?tag={tag}&title={tag}"
        notes_path = os.path.join(PROJECT_ROOT, f"release-notes-{tag}.md")
        with open(notes_path, "w", encoding="utf-8") as f:
            f.write(notes + "\n")
        print(f"Release 说明已输出到 {notes_path}，可直接粘贴")
        webbrowser.open(url)
        return

    rel = get_release(tag, token)
    if rel is None:
        # Gitee 建发行版必填的是 name（传 title 会 400 "name is missing"）
        rel = api_request(f"{GITEE_API}/repos/{GITEE_REPO}/releases", token, {
            "tag_name": tag, "name": tag, "body": notes,
            "target_commitish": "master",
        })
        print(f"Release 已创建 id={rel.get('id')}")
    else:
        # 更新时 Gitee 要求 tag_name 与 body 一起重传
        api_request(f"{GITEE_API}/repos/{GITEE_REPO}/releases/{rel['id']}", token, {
            "tag_name": tag, "name": tag, "body": notes,
            "target_commitish": "master",
        }, method="PATCH")
        print(f"Release 已更新 id={rel.get('id')}")

    rid = rel.get("id")
    print("上传附件：")
    for path in collect_attachments():
        upload_attachment(rid, path, token)
    print(f"页面: https://gitee.com/{GITEE_REPO}/releases/tag/{tag}")


def main():
    parser = argparse.ArgumentParser(description="一条命令出包+发版到 Gitee")
    parser.add_argument("--version", required=True, help="版本号，如 1.2.0（tag 自动加 v 前缀）")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--no-build", action="store_true", help="复用现有 dist/ 产物")
    parser.add_argument("--no-api", action="store_true", help="不调 Gitee API，仅推送并打开手动发布页")
    parser.add_argument("--only-release", action="store_true",
                        help="只做第 6 步：按 CHANGELOG 现有 [v版本] 段更新发行版并重传附件")
    args = parser.parse_args()

    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        sys.exit("版本号格式应为 x.y.z")

    if args.only_release:
        with open(os.path.join(PROJECT_ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
            notes = extract_notes(f.read(), args.version)
        step_release(args.version, notes, read_token(), args.no_api)
        return 0

    step_tests(args.skip_tests)
    step_build(args.no_build)
    step_assemble(args.version)
    notes = step_changelog(args.version)
    step_git(args.version, f"v{args.version}")
    step_release(args.version, notes, read_token(), args.no_api)
    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
