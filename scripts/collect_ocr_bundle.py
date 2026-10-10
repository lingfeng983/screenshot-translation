#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收集 Tesseract 运行最小集

从 resources/tesseract/ 与 resources/tessdata/ 抽取 OCR 运行必需的文件，
输出到 dist/ocr-bundle/（供 scripts/make_release_package.py 切成 Release 附件，
安装阶段再由 scripts/fetch_ocr.ps1 下载解压）：

- tesseract.exe + 全部 .dll（运行必需）
- tessdata：eng.traineddata + chi_sim.traineddata + tessdata/configs 配置
- 排除：除 tesseract.exe 以外的所有 exe（训练工具 lstmtraining 等 60MB+）

用法：python scripts/collect_ocr_bundle.py [--out dist/ocr-bundle]
"""

import argparse
import os
import shutil
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def collect(out_dir):
    """
    收集最小集到 out_dir

    Returns:
        tuple[int, float]: (文件数, 总MB)
    """
    src_tes = os.path.join(PROJECT_ROOT, 'resources', 'tesseract')
    src_data = os.path.join(PROJECT_ROOT, 'resources', 'tessdata')
    if not os.path.isdir(src_tes):
        print(f"❌ 缺少 {src_tes}（先运行 scripts/setup_ocr.py）")
        return 0, 0.0
    if not os.path.isdir(src_data):
        print(f"❌ 缺少 {src_data}（先运行 scripts/setup_ocr.py）")
        return 0, 0.0

    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)

    count, total = 0, 0.0

    def _copy(src, dest):
        nonlocal count, total
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
        count += 1
        total += os.path.getsize(src) / 1048576

    # 1) tesseract.exe + 依赖 DLL 与数据文件；其余 exe（训练/开发工具）一律不收
    for name in os.listdir(src_tes):
        src = os.path.join(src_tes, name)
        if not os.path.isfile(src):
            continue
        lower = name.lower()
        if lower.endswith('.exe') and lower != 'tesseract.exe':
            continue
        _copy(src, os.path.join(out_dir, 'tesseract', name))

    # 2) tessdata：eng/chi_sim 语言数据 + configs 目录（tesseract 运行需要）
    for name in ('eng.traineddata', 'chi_sim.traineddata'):
        src = os.path.join(src_data, name)
        if os.path.isfile(src):
            _copy(src, os.path.join(out_dir, 'tessdata', name))
        else:
            print(f"❌ 缺少语言数据 {src}")
            return 0, 0.0

    # configs 是 tesseract 运行时可选配置目录，一并带上。
    # 便携包的 configs 可能落在 resources/tessdata/configs，
    # 也可能跟着 tesseract.exe 在 resources/tesseract/tessdata/configs
    cfg_dir = next((d for d in (os.path.join(src_data, 'configs'),
                                os.path.join(src_tes, 'tessdata', 'configs'))
                   if os.path.isdir(d)), None)
    if cfg_dir:
        for name in os.listdir(cfg_dir):
            src = os.path.join(cfg_dir, name)
            if os.path.isfile(src):
                _copy(src, os.path.join(out_dir, 'tessdata', 'configs', name))
    else:
        print("⚠ 未找到 tessdata/configs，识别时若指定 --psm 等参数可能失败")

    if not os.path.isfile(os.path.join(out_dir, 'tesseract', 'tesseract.exe')):
        print("❌ 没收到 tesseract.exe（resources/tesseract 里没有便携版主程序）")
        return 0, 0.0

    print(f"✅ 收集 {count} 个文件，共 {total:.1f} MB → {out_dir}")
    return count, total


def main():
    parser = argparse.ArgumentParser(description="收集Tesseract运行最小集")
    parser.add_argument('--out', default=os.path.join(
        PROJECT_ROOT, 'dist', 'ocr-bundle'))
    args = parser.parse_args()

    count, total = collect(args.out)
    if count == 0:
        return 1
    print("下一步：python scripts/make_release_package.py 把它切成 Release 附件分卷")
    return 0


if __name__ == '__main__':
    sys.exit(main())
