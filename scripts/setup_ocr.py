#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载并恢复 OCR 组件（Tesseract 便携包 + tessdata 语言数据）

从本仓库 Gitee Release 附件下载分卷 zip，合并校验后解压到 resources/。
大文件不入 git 仓库，克隆后运行本脚本一次即可。

用法：
    python scripts/setup_ocr.py
    python scripts/setup_ocr.py --release v2.0.0
    python scripts/setup_ocr.py --local dist/release   # 用本地已打包的文件恢复（离线）
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_URL = 'https://gitee.com/lingfeng983/screenshot-translation.git'
DEFAULT_LABEL = 'ocr-components'


def _download(url, dest):
    """下载文件（带进度显示），requests 不可用时回退 curl"""
    try:
        import requests
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            done = 0
            with open(dest, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100 // total
                        print(f"\r  下载 {pct}% ({done / 1048576:.1f}/{total / 1048576:.1f} MB)",
                              end='', flush=True)
            print()
        return True
    except ImportError:
        pass
    # 回退 curl（Windows 10+ 自带）
    return subprocess.call(['curl', '-L', '--fail', '-o', dest, url]) == 0


def _sha256_of(paths):
    """计算多个文件按序合并后的 sha256"""
    sha = hashlib.sha256()
    for path in paths:
        with open(path, 'rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                sha.update(block)
    return sha.hexdigest()


def _find_parts(source_dir, label):
    """在目录中定位 label 的分卷与校验文件"""
    parts = []
    index = 1
    while True:
        part = os.path.join(source_dir, f"{label}.zip.{index:03d}")
        if not os.path.exists(part):
            break
        parts.append(part)
        index += 1
    sha_file = os.path.join(source_dir, f"{label}.zip.sha256")
    return parts, sha_file


def restore_from_local(source_dir, label):
    """从本地目录恢复"""
    parts, sha_file = _find_parts(source_dir, label)
    if not parts:
        print(f"❌ {source_dir} 中未找到 {label}.zip.001 分卷")
        return False

    print(f"找到 {len(parts)} 个分卷，校验完整性...")
    actual = _sha256_of(parts)
    if os.path.exists(sha_file):
        expected = open(sha_file, encoding='utf-8').read().split()[0]
        if actual != expected:
            print(f"❌ 校验失败\n  期望: {expected}\n  实际: {actual}")
            return False
        print("SHA256 校验通过")
    else:
        print("⚠ 无校验文件，跳过校验")

    return _merge_and_extract(parts)


def restore_from_release(release_tag, label):
    """从 Gitee Release 附件恢复"""
    base = f"{REPO_URL.rstrip('.git')}/releases/download/{release_tag}"
    print(f"从 Release {release_tag} 下载: {base}")

    tmp_dir = tempfile.mkdtemp(prefix='ocr-setup-')
    try:
        # 先探测分卷数量（下载 .001，逐步尝试）
        parts = []
        index = 1
        while True:
            name = f"{label}.zip.{index:03d}"
            dest = os.path.join(tmp_dir, name)
            url = f"{base}/{name}"
            print(f"下载 {name} ...")
            if not _download(url, dest):
                os.remove(dest) if os.path.exists(dest) else None
                if index == 1:
                    print(f"❌ 下载失败：{url}\n"
                          f"   请确认 Release {release_tag} 已上传附件，或检查网络。")
                    return False
                break  # 该序号不存在 = 分卷结束
            parts.append(dest)
            index += 1
            if index > 20:  # 安全上限
                break

        # 下载校验文件（可选）
        sha_dest = os.path.join(tmp_dir, f"{label}.zip.sha256")
        if _download(f"{base}/{label}.zip.sha256", sha_dest):
            parts_sha = parts
            actual = _sha256_of(parts_sha)
            expected = open(sha_dest, encoding='utf-8').read().split()[0]
            if actual != expected:
                print(f"❌ 校验失败\n  期望: {expected}\n  实际: {actual}")
                return False
            print("SHA256 校验通过")

        return _merge_and_extract(parts)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _merge_and_extract(parts):
    """合并分卷并解压到 resources/"""
    print("合并分卷并解压...")
    merged = os.path.join(tempfile.gettempdir(), 'ocr-components-merged.zip')
    with open(merged, 'wb') as out:
        for part in parts:
            with open(part, 'rb') as f:
                shutil.copyfileobj(f, out)

    resources = os.path.join(PROJECT_ROOT, 'resources')
    os.makedirs(resources, exist_ok=True)
    with zipfile.ZipFile(merged) as zf:
        # zip 内路径为 tesseract/... 与 tessdata/...，直接解到 resources/
        zf.extractall(resources)

    os.remove(merged)

    # 验证关键文件
    checks = [
        os.path.join(resources, 'tesseract', 'tesseract.exe'),
        os.path.join(resources, 'tessdata', 'eng.traineddata'),
        os.path.join(resources, 'tessdata', 'chi_sim.traineddata'),
    ]
    missing = [p for p in checks if not os.path.exists(p)]
    if missing:
        print(f"❌ 解压后缺少关键文件: {missing}")
        return False

    print(f"✅ OCR 组件已恢复到 {resources}")
    print("   - resources/tesseract/  （便携版主程序）")
    print("   - resources/tessdata/   （eng + chi_sim 语言数据）")
    return True


def main():
    parser = argparse.ArgumentParser(description="下载/恢复 OCR 组件")
    parser.add_argument('--release', default=None,
                        help='Release 标签（默认自动探测最新，如 v2.0.0）')
    parser.add_argument('--label', default=DEFAULT_LABEL,
                        help=f'附件包名前缀（默认 {DEFAULT_LABEL}）')
    parser.add_argument('--local', default=None,
                        help='从本地目录恢复（离线模式），如 dist/release')
    args = parser.parse_args()

    if getattr(sys, 'frozen', False):
        pass

    if args.local:
        ok = restore_from_local(args.local, args.label)
    else:
        tag = args.release or 'latest'
        ok = restore_from_release(tag, args.label)

    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
