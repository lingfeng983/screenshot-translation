#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收集 Tesseract 运行最小集

从 resources/tesseract/ 与 resources/tessdata/ 抽取 OCR 运行必需的文件，
输出到 dist/ocr-bundle/（供 PyInstaller add-data 打进 exe）：

- tesseract.exe + 全部 .dll（运行必需）
- tessdata：eng.traineddata + chi_sim.traineddata + tessdata/configs 配置
- 排除：训练工具（lstmtraining 等 60MB+）、语言包之外的杂项

用法：python scripts/collect_ocr_bundle.py [--out dist/ocr-bundle]
"""

import argparse
import os
import shutil
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 训练/开发工具，运行识别用不到，全部排除（约60MB）
_EXCLUDED_EXES = {
    'ambiguous_words.exe', 'classifier_tester.exe', 'cntraining.exe',
    'combine_lang_model.exe', 'combine_tessdata.exe', 'dawg2wordlist.exe',
    'lstmeval.exe', 'lstmtraining.exe', 'merge_unicharsets.exe',
    'mftraining.exe', 'set_unicharset_properties.exe', 'shapeclustering.exe',
    'text2image.exe', 'unicharset_extractor.exe', 'wordlist2dawg.exe',
    'winpath.exe',
}


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

    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)

    count, total = 0, 0.0

    def _copy(src, dest):
        nonlocal count, total
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
        count += 1
        total += os.path.getsize(src) / 1048576

    # 1) tesseract.exe + 全部 dll（不复制被排除的训练工具）
    for name in os.listdir(src_tes):
        src = os.path.join(src_tes, name)
        if not os.path.isfile(src):
            continue
        lower = name.lower()
        if lower in _EXCLUDED_EXES:
            continue
        if lower.endswith('.exe') and lower != 'tesseract.exe':
            continue
        _copy(src, os.path.join(out_dir, 'tesseract', name))

    # 2) tessdata：eng/chi_sim 语言数据 + configs 目录（tesseract 运行需要）
    if os.path.isdir(src_data):
        for name in ('eng.traineddata', 'chi_sim.traineddata'):
            src = os.path.join(src_data, name)
            if os.path.isfile(src):
                _copy(src, os.path.join(out_dir, 'tessdata', name))
        # configs 是 tesseract 运行时可选配置目录，一并带上
        cfg_dir = os.path.join(src_tes, 'tessdata', 'configs')
        if os.path.isdir(cfg_dir):
            for name in os.listdir(cfg_dir):
                _copy(os.path.join(cfg_dir, name),
                      os.path.join(out_dir, 'tessdata', 'configs', name))

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
    print(f"下一步：pyinstaller spec 会把 {args.out} 整体打进 exe")
    return 0


if __name__ == '__main__':
    sys.exit(main())
