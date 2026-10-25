#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
制作 OCR 组件发行包

把 dist/ocr-bundle/（scripts/collect_ocr_bundle.py 抽出的 Tesseract 运行最小集：
便携版主程序 + 依赖 DLL + eng/chi_sim 语言数据）打成分卷文件（默认 ≤90MB/卷），
供上传到 GitHub Release 附件（GitHub 单附件上限 2GB）。

安装程序（scripts/setup.iss）在装填阶段按固定包名 ocr-components 从这个仓库的
Release 附件下载并解压到 ~/.screenshot-translation/resources/，所以包名不要带日期：
换了标签装填器就找不到文件了。

分卷格式：单个 zip 按字节顺序切分；恢复端按序合并后即为完整 zip。
sha256 文件记录完整 zip 的校验值。

除了分卷和校验文件，还会写出 dist/release/ocr-artifact.iss：安装脚本编译期 include
它的三元组（第一卷附件名、该附件字节数、整包 SHA256）。Inno 的 download 标志要求
编译期就给出 ExternalSize 与 Hash，装填端没法运行时现算，只能由这里传过去。

用法：
    python scripts/collect_ocr_bundle.py
    python scripts/make_release_package.py
    python scripts/make_release_package.py --out dist/release --part-size 80

输出：dist/release/ocr-components.zip.001 / .002 / ...
      dist/release/ocr-components.zip.sha256
      dist/release/ocr-artifact.iss
"""

import argparse
import hashlib
import os
import sys
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 打包源目录（相对项目根）：collect_ocr_bundle.py 的最小集产物，
# 内部已是 tesseract/ 与 tessdata/ 两个前缀，直接按原样入包
BUNDLE_DIR = os.path.join(PROJECT_ROOT, 'dist', 'ocr-bundle')

# 装填器按这个名字找附件，改动要同步 scripts/setup.iss 与 setup_ocr.py
DEFAULT_LABEL = 'ocr-components'

# 安装脚本编译期 include 的参数文件（路径也写在 setup.iss 的 MyAppOcrArtifact 里）
ARTIFACT_FILENAME = 'ocr-artifact.iss'

_CHUNK = 1024 * 1024  # 1MB


def collect_sources():
    """
    收集待打包文件

    Returns:
        list[tuple[str, str]]: (磁盘路径, zip内相对路径)；缺目录或语言包时为 []
    """
    if not os.path.isdir(BUNDLE_DIR):
        print(f"❌ 缺少 {BUNDLE_DIR}（先运行 python scripts/collect_ocr_bundle.py）")
        return []
    sources = []
    for root, _dirs, files in os.walk(BUNDLE_DIR):
        for name in files:
            path = os.path.join(root, name)
            arcname = os.path.relpath(path, BUNDLE_DIR).replace(os.sep, '/')
            sources.append((path, arcname))
    if not sources:
        print(f"❌ {BUNDLE_DIR} 是空的（collect_ocr_bundle.py 没收到任何文件）")
        return []
    # 语言包缺失时打出来的包能下载能解压，却跑不了识别——在这里拦掉，
    # 比让装填端在解压后探测关键文件失败更早、原因也更清楚
    missing = [rel for rel in ('tessdata/eng.traineddata', 'tessdata/chi_sim.traineddata')
               if not os.path.isfile(os.path.join(BUNDLE_DIR, *rel.split('/')))]
    if missing:
        print(f"❌ 最小集缺少语言数据: {missing}（先运行 python scripts/setup_ocr.py）")
        return []
    return sources


def clear_stale_parts(out_dir, label):
    """
    删掉同名包的历史产物

    分卷数会随包大小变（上次 3 卷、这次 2 卷），旧的 .003 留在目录里会被
    发布脚本一并上传，恢复端按序合并就多出一段脏字节。
    """
    if not os.path.isdir(out_dir):
        return
    prefix = f"{label}.zip"
    for name in os.listdir(out_dir):
        if name.startswith(prefix):
            path = os.path.join(out_dir, name)
            if os.path.isfile(path):
                os.remove(path)
                print(f"  - 清理旧产物 {name}")


def write_artifact_include(out_dir, part_paths, zip_sha):
    """
    写安装脚本编译期要用的下载参数

    Inno 的 download 标志要求在编译期就给出 ExternalSize（远端文件字节数）和
    Hash（下载文件的 SHA256），装填端运行时算不出来，只能由出包这一步传过去。
    校验值取整包 zip 的：只有一卷时 .001 就是 zip 本身，逐字节相同。

    Args:
        out_dir (str): 输出目录
        part_paths (list[str]): 分卷路径（有序）
        zip_sha (str): 完整 zip 的 sha256（十六进制小写）

    Returns:
        str: 生成的 ocr-artifact.iss 路径
    """
    path = os.path.join(out_dir, ARTIFACT_FILENAME)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('; 由 scripts/make_release_package.py 生成，scripts/setup.iss 编译期 include，不要手改\n')
        f.write(f'#define MyAppOcrPartName "{os.path.basename(part_paths[0])}"\n')
        f.write(f'#define MyAppOcrSize {os.path.getsize(part_paths[0])}\n')
        f.write(f'#define MyAppOcrSha "{zip_sha}"\n')
        f.write(f'#define MyAppOcrParts {len(part_paths)}\n')
    print(f"已生成 {ARTIFACT_FILENAME}（{len(part_paths)} 卷，内置下载{'可用' if len(part_paths) == 1 else '不可用：只支持单附件'}）")
    return path


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
        print("❌ 没有可打包的文件（先运行 python scripts/collect_ocr_bundle.py）")
        return []

    os.makedirs(out_dir, exist_ok=True)
    clear_stale_parts(out_dir, label)
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
    zip_sha = sha.hexdigest()
    with open(sha_path, 'w', encoding='utf-8') as f:
        f.write(f"{zip_sha}  {label}.zip\n")
    print(f"SHA256: {zip_sha[:16]}...")

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

    # 4) 安装脚本编译期要用的下载三元组
    artifact_path = write_artifact_include(out_dir, part_paths, zip_sha)

    total_size = sum(os.path.getsize(p) for p in part_paths)
    print(f"\n✅ 已生成 {len(part_paths)} 个分卷 + 1 个校验文件，共 {total_size / 1024 / 1024:.1f} MB")
    for p in part_paths + [sha_path]:
        print(f"   {os.path.basename(p)}  ({os.path.getsize(p) / 1024 / 1024:.1f} MB)")
    print(f"\n下一步：全部上传到 GitHub Release 附件（tag=v版本），")
    print(f"克隆端运行 python scripts/setup_ocr.py --release v版本 自动下载恢复。")
    return part_paths + [sha_path, artifact_path]


def main():
    parser = argparse.ArgumentParser(description="打包OCR组件为GitHub Release分卷附件")
    parser.add_argument('--out', default=os.path.join(PROJECT_ROOT, 'dist', 'release'),
                        help='输出目录（默认 dist/release）')
    parser.add_argument('--part-size', type=int, default=90,
                        help='单卷上限MB（默认90；整包单卷时安装包才能内置下载）')
    parser.add_argument('--label', default=DEFAULT_LABEL,
                        help=f'包名（默认 {DEFAULT_LABEL}，装填器按此名找附件，勿加日期）')
    args = parser.parse_args()

    # 返回 [] 表示什么都没产出：发版脚本据此中止，不能报成功
    return 0 if make_package(args.out, args.part_size, args.label) else 1


if __name__ == '__main__':
    sys.exit(main())
