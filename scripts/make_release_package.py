#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
制作 OCR 组件发行包

把 resources/tesseract/（便携版主程序）与 resources/tessdata/（语言数据）
打成分卷文件（默认 ≤90MB/卷），供上传到 Gitee Release 附件
（Gitee 单附件上限 100MB）。

分卷格式：单个 zip 按字节顺序切分；恢复端按序合并后即为完整 zip。
sha256 文件记录完整 zip 的校验值。

用法：
    python scripts/make_release_package.py
    python scripts/make_release_package.py --out dist/release --part-size 80

输出：dist/release/ocr-components-<日期>.zip.001 / .002 / ...
      dist/release/ocr-components-<日期>.zip.sha256
"""

import argparse
import hashlib
import os
import sys
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 打包的目录（相对项目根）→ zip 内前缀
INCLUDED_DIRS = [
    ('resources/tesseract', 'tesseract'),
    ('resources/tessdata', 'tessdata'),
]

_CHUNK = 1024 * 1024  # 1MB


def collect_sources():
    """
    收集待打包文件

    Returns:
        list[tuple[str, str]]: (磁盘路径, zip内相对路径)
    """
    sources = []
    for src_rel, arc_prefix in INCLUDED_DIRS:
        src_dir = os.path.join(PROJECT_ROOT, src_rel)
        if not os.path.isdir(src_dir):
            print(f"⚠ 目录不存在，跳过: {src_rel}")
            continue
        for root, _dirs, files in os.walk(src_dir):
            for name in files:
                path = os.path.join(root, name)
                arcname = f"{arc_prefix}/{os.path.relpath(path, src_dir).replace(os.sep, '/')}"
                sources.append((path, arcname))
    return sources


def make_package(out_dir, part_size_mb, label):
    """
    打包为分卷

    Args:
        out_dir (str): 输出目录
        part_size_mb (int): 单卷上限（MB）
        label (str): 包名

    Returns:
        list[str]: 生成的文件路径列表
    """
    sources = collect_sources()
    if not sources:
        print("❌ 没有可打包的文件（resources/tesseract 与 resources/tessdata 均不存在）")
        return []

    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, f"{label}.zip")

    # 1) 打完整 zip
    print(f"打包 {len(sources)} 个文件 → {os.path.basename(zip_path)}")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path, arcname in sources:
            print(f"  + {arcname}")
            zf.write(path, arcname)

    zip_size = os.path.getsize(zip_path)
    print(f"zip 完成: {zip_size / 1024 / 1024:.1f} MB")

    # 2) 记录完整 zip 的 sha256
    sha = hashlib.sha256()
    with open(zip_path, 'rb') as f:
        for block in iter(lambda: f.read(_CHUNK), b''):
            sha.update(block)
    sha_path = f"{zip_path}.sha256"
    with open(sha_path, 'w', encoding='utf-8') as f:
        f.write(f"{sha.hexdigest()}  {label}.zip\n")
    print(f"SHA256: {sha.hexdigest()[:16]}...")

    # 3) 按字节切分卷
    part_size = part_size_mb * _CHUNK
    part_paths = []
    with open(zip_path, 'rb') as f:
        index = 1
        while True:
            block = f.read(part_size)
            if not block:
                break
            part_path = f"{zip_path}.{index:03d}"
            with open(part_path, 'wb') as pf:
                pf.write(block)
            part_paths.append(part_path)
            index += 1

    # 切完即删完整 zip（恢复端合并生成）
    os.remove(zip_path)

    total_size = sum(os.path.getsize(p) for p in part_paths)
    print(f"\n✅ 已生成 {len(part_paths)} 个分卷 + 1 个校验文件，共 {total_size / 1024 / 1024:.1f} MB")
    for p in part_paths + [sha_path]:
        print(f"   {os.path.basename(p)}  ({os.path.getsize(p) / 1024 / 1024:.1f} MB)")
    print(f"\n下一步：全部上传到 Gitee Release 附件，")
    print(f"克隆端运行 python scripts/setup_ocr.py 自动下载恢复。")
    return part_paths + [sha_path]


def main():
    parser = argparse.ArgumentParser(description="打包OCR组件为Gitee Release分卷附件")
    parser.add_argument('--out', default=os.path.join(PROJECT_ROOT, 'dist', 'release'),
                        help='输出目录（默认 dist/release）')
    parser.add_argument('--part-size', type=int, default=90,
                        help='单卷上限MB（默认90，Gitee附件上限100）')
    parser.add_argument('--label', default=None, help='包名（默认 ocr-components-日期）')
    args = parser.parse_args()

    from datetime import date
    label = args.label or f"ocr-components-{date.today():%Y%m%d}"
    make_package(args.out, args.part_size, label)
    return 0


if __name__ == '__main__':
    sys.exit(main())
