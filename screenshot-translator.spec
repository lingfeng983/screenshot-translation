# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 单文件打包配置

构建：pyinstaller screenshot-translator.spec
产物：dist/ScreenshotTranslator.exe（单文件，含图标；Tesseract 首运下载或跟随Release）
"""

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 随 exe 分发的只读资源（解包到 _MEIPASS/resources/...）
# Tesseract 运行最小集：若存在则整体打进 exe（由 scripts/collect_ocr_bundle.py 生成）
datas = [
    ('resources/icon.png', 'resources'),
    ('resources/icon.ico', 'resources'),
    ('config.example.ini', '.'),
]

_ocr_bundle = os.path.join(SPECPATH, 'dist', 'ocr-bundle')
if os.path.isdir(_ocr_bundle):
    # 打包为 resources/tesseract/... 与 resources/tessdata/...
    for _root, _dirs, _files in os.walk(_ocr_bundle):
        for _f in _files:
            _abs = os.path.join(_root, _f)
            _arc = os.path.join('resources', os.path.relpath(_abs, _ocr_bundle))
            datas.append((_abs, os.path.dirname(_arc)))
    print(f'>>> 已包含 OCR 最小集: {_ocr_bundle}')
else:
    print('>>> 警告: 未找到 OCR bundle，exe 将不含 Tesseract（运行时需自行提供）')

# 隐藏导入：pystray 的 Windows 后端与键盘库按需动态加载
# 插件包（翻译API / OCR引擎）只在运行时按目录扫描发现，静态分析看不到引用，
# 不显式收集的话 exe 里一个 provider 都没有，翻译与OCR全部失效
hiddenimports = [
    'pystray._win32',
    'PIL._tkinter_finder',
] + collect_submodules('managers.providers') + collect_submodules('managers.ocr_providers')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除明显不需要的大模块，减小体积
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PyQt5.QtBluetooth',
        'PyQt5.QtNetwork',
        'PyQt5.QtSql',
        'PyQt5.QtTest',
        'PyQt5.QtXml',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ScreenshotTranslator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # 无控制台黑窗（GUI应用）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icon.ico',
)
