#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一条命令出包 + 发版到 Gitee

流程：
    1. pytest 全量测试（--skip-tests 可跳过）
    2. PyInstaller 构建 onedir + onefile
    3. onedir 产物同步到 release/onedir/，Inno Setup 编译安装器（版本号经 /D 注入）
    4. CHANGELOG 的 [Unreleased] 段落就地改名为 [v版本] — 日期
    5. git commit + tag + push（含 tag）
    6. Gitee API 创建 Release（正式），取 [v版本] 段作为说明
       注意：Gitee 开放 API 的 attachments 只收 URL、不支持本地文件上传，
       所以 Setup.exe / Onefile.exe 附件由脚本打开 Release 编辑页人工拖入
       （附件不进 git，仅 Release 分发）

用法：
    python scripts/publish_release.py --version 1.2.0
    python scripts/publish_release.py --version 1.2.0 --skip-tests --no-build
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
    run([sys.executable, "-m", "PyInstaller", "screenshot-translator-onefile.spec", "--noconfirm"])


def step_assemble(version):
    print("\n=== 3/6 同步 release/ 并编译安装器 ===")
    src = os.path.join(PROJECT_ROOT, "dist", "ScreenshotTranslator")
    dst = os.path.join(PROJECT_ROOT, "release", "onedir")
    if not os.path.isdir(src):
        sys.exit(f"缺少 onedir 产物：{src}（先构建或去掉 --no-build）")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    one_src = os.path.join(PROJECT_ROOT, "dist", "ScreenshotTranslator-Onefile.exe")
    if os.path.isfile(one_src):
        shutil.copy2(one_src, os.path.join(PROJECT_ROOT, "release"))
    iscc = next((p for p in ISCC_CANDIDATES if os.path.isfile(p)), None)
    if not iscc:
        sys.exit("未找到 Inno Setup 编译器 iscc.exe，请安装后重试")
    iss = os.path.join(PROJECT_ROOT, "scripts", "setup.iss")
    run([iscc, f"/DMyAppVersion={version}", iss])


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


def step_release(version, notes, token, no_api):
    print("\n=== 6/6 Gitee Release ===")
    tag = f"v{version}"
    if no_api or not token:
        if not no_api:
            print("⚠ 未找到 GITEE_ACCESS_TOKEN（环境变量或 .gitee_token 文件），转手动模式")
        url = f"https://gitee.com/{GITEE_REPO}/releases/new?tag={tag}&title={tag}"
        print(f" Release 说明已输出到 release-notes-{tag}.md，可直接粘贴")
        notes_path = os.path.join(PROJECT_ROOT, f"release-notes-{tag}.md")
        with open(notes_path, "w", encoding="utf-8") as f:
            f.write(notes + "\n")
        webbrowser.open(url)
        return
    import json
    from urllib.request import Request, urlopen
    body = {
        "access_token": token,
        "tag_name": tag,
        "title": tag,
        "body": notes,
        "target_commitish": "master",
    }
    req = Request(f"{GITEE_API}/repos/{GITEE_REPO}/releases",
                  data=json.dumps(body).encode("utf-8"),
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        print(f"✅ Release 已创建: {info.get('html_url')}")
    except Exception as e:  # 已存在等情况
        print(f"⚠ API 创建失败（可能 Release 已存在）: {e}")
    # 附件无法走开放 API 上传（只收 URL），打开编辑页人工拖入
    edit_url = f"https://gitee.com/{GITEE_REPO}/releases/edit/{tag}"
    print(f"附件请拖入（Setup.exe / Onefile.exe / OCR 分卷），正在打开编辑页: {edit_url}")
    webbrowser.open(edit_url)


def main():
    parser = argparse.ArgumentParser(description="一条命令出包+发版到 Gitee")
    parser.add_argument("--version", required=True, help="版本号，如 1.2.0（tag 自动加 v 前缀）")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--no-build", action="store_true", help="复用现有 dist/ 产物")
    parser.add_argument("--no-api", action="store_true", help="不调 Gitee API，仅推送并打开手动发布页")
    args = parser.parse_args()

    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        sys.exit("版本号格式应为 x.y.z")

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
