# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 单文件打包配置

构建：pyinstaller screenshot-translator.spec
产物：dist/ScreenshotTranslator.exe（单文件，含图标；Tesseract 首运下载或跟随Release）
"""

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# 随 exe 分发的只读资源（解包到 _MEIPASS/resources/...）
datas = [
    ('resources/icon.png', 'resources'),
    ('resources/icon.ico', 'resources'),
    ('config.example.ini', '.'),
]

# 隐藏导入：pystray 的 Windows 后端与键盘库按需动态加载
hiddenimports = [
    'pystray._win32',
    'PIL._tkinter_finder',
]

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
