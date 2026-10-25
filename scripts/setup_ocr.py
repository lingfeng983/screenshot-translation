#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
下载并恢复 OCR 组件（Tesseract 便携包 + tessdata 语言数据）

从本仓库 GitHub Release 附件下载分卷 zip，合并校验后解压到项目 resources/。
大文件不入 git 仓库，克隆后运行本脚本一次即可让源码态跑离线识别。
打包安装态不走这里：安装包（scripts/setup.iss）由 Setup 自己从同一批附件下载
并解压到 ~/.screenshot-translation/resources/。本脚本是给源码态/手工补装用的。

GitHub 提供 latest/download 跳转地址，默认取最新 Release；需要固定版本时给
--release v标签。

用法：
    python scripts/setup_ocr.py                 # 取最新 Release 的附件
    python scripts/setup_ocr.py --release v1.5.0
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
REPO_URL = 'https://github.com/lingfeng983/screenshot-translation.git'
DEFAULT_LABEL = 'ocr-components'

# 分卷数上限，防止探测循环失控
_MAX_PARTS = 20


def repo_base(url):
    """去掉仓库地址的 .git 尾巴

    不能用 rstrip('.git')：那是按字符集剥的，仓库名以 t/i/g/. 收尾就会被多啃。
    """
    return url[:-len('.git')] if url.endswith('.git') else url


def _download(url, dest):
    """
    下载文件，返回三态：'ok' / 'missing'（远端 404）/ 'error'（网络或服务端问题）

    分卷是一个个试出来的，只有 404 能当成"分卷到此为止"；把超时和 5xx 也
    当成结尾，就会拿半个组件包去合并解压。
    """
    try:
        import requests
    except ImportError:
        requests = None

    if requests is None:
        # 回退 curl（Windows 10+ 自带）；--fail 下 404 返回退出码 22
        code = subprocess.call(['curl', '-sL', '--fail', '-o', dest, url])
        if code == 0:
            return 'ok'
        print(f"  curl 退出码 {code}：{url}")
        if os.path.exists(dest):
            os.remove(dest)
        return 'missing' if code == 22 else 'error'

    try:
        with requests.get(url, stream=True, timeout=(10, 60)) as r:
            if r.status_code == 404:
                return 'missing'
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
            if total:
                print()
        return 'ok'
    except requests.exceptions.RequestException as e:
        print(f"  下载失败：{url}\n  {e}")
        if os.path.exists(dest):
            os.remove(dest)
        return 'error'


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
    """从本地目录恢复（离线；输入是自己刚打的包，缺校验文件只提示不拦）"""
    parts, sha_file = _find_parts(source_dir, label)
    if not parts:
        print(f"❌ {source_dir} 中未找到 {label}.zip.001 分卷")
        return False

    print(f"找到 {len(parts)} 个分卷，校验完整性...")
    if os.path.exists(sha_file):
        actual = _sha256_of(parts)
        expected = open(sha_file, encoding='utf-8').read().split()[0].lower()
        if actual != expected:
            print(f"❌ 校验失败\n  期望: {expected}\n  实际: {actual}")
            return False
        print("SHA256 校验通过")
    else:
        print("⚠ 无校验文件，跳过校验")

    return _merge_and_extract(parts)


def restore_from_release(release_tag, label):
    """从 GitHub Release 附件恢复（release_tag 为 None 时取 latest 跳转地址）"""
    if release_tag:
        base = f"{repo_base(REPO_URL)}/releases/download/{release_tag}"
    else:
        base = f"{repo_base(REPO_URL)}/releases/latest/download"
    print(f"从 Release {'latest' if not release_tag else release_tag} 下载: {base}")

    tmp_dir = tempfile.mkdtemp(prefix='ocr-setup-')
    try:
        parts = []
        index = 1
        while index <= _MAX_PARTS:
            name = f"{label}.zip.{index:03d}"
            dest = os.path.join(tmp_dir, name)
            print(f"下载 {name} ...")
            status = _download(f"{base}/{name}", dest)
            if status == 'missing':
                break           # 404 才算分卷到此为止
            if status != 'ok':
                print(f"❌ 取 {name} 时网络或服务端出错，无法判断组件是否完整")
                return False
            parts.append(dest)
            index += 1
        else:
            print(f"❌ 连续取到 {_MAX_PARTS} 卷还没遇到结尾，判定为异常")
            return False

        if not parts:
            print(f"❌ 首卷都取不到：Release {release_tag} 可能没上传 {label}.zip.001 附件")
            return False

        # 校验文件必需：网络来的字节拼错了只会表现为"识别不出字"，太难查
        sha_dest = os.path.join(tmp_dir, f"{label}.zip.sha256")
        if _download(f"{base}/{label}.zip.sha256", sha_dest) != 'ok':
            print(f"❌ 取不到校验文件 {base}/{label}.zip.sha256，放弃恢复")
            return False
        actual = _sha256_of(parts)
        expected = open(sha_dest, encoding='utf-8').read().split()[0].lower()
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
    fd, merged = tempfile.mkstemp(prefix='ocr-components-', suffix='.zip')
    os.close(fd)
    try:
        with open(merged, 'wb') as out:
            for part in parts:
                with open(part, 'rb') as f:
                    shutil.copyfileobj(f, out)

        resources = os.path.join(PROJECT_ROOT, 'resources')
        os.makedirs(resources, exist_ok=True)
        with zipfile.ZipFile(merged) as zf:
            # zip 内路径为 tesseract/... 与 tessdata/...，直接解到 resources/
            zf.extractall(resources)
    except (OSError, zipfile.BadZipFile) as e:
        print(f"❌ 合并或解压失败: {e}")
        return False
    finally:
        if os.path.exists(merged):
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
                        help='Release 标签，如 v1.5.0（不给则取 latest 最新版）')
    parser.add_argument('--label', default=DEFAULT_LABEL,
                        help=f'附件包名前缀（默认 {DEFAULT_LABEL}）')
    parser.add_argument('--local', default=None,
                        help='从本地目录恢复（离线模式），如 dist/release')
    args = parser.parse_args()

    if args.local and args.release:
        parser.error('--local 与 --release 只能给一个（--local 用于离线目录）')
    if args.local:
        ok = restore_from_local(args.local, args.label)
    else:
        # 不给 --release 就取 latest（GitHub 跳转地址）
        ok = restore_from_release(args.release, args.label)

    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
