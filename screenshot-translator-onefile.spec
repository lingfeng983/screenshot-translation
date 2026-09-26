# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置（onefile 单文件形态，便携分发用的补充形态）

构建：python -m PyInstaller screenshot-translator-onefile.spec --noconfirm
产物：dist/ScreenshotTranslator-Onefile.exe（单文件，免安装，双击即跑）

与 onedir 的取舍详见 screenshot-translator.spec 头部注释：
单文件每次启动都要把整包解到 %TEMP%\\_MEI*（实测多花约 2.5s），
进程被强杀时解包目录会残留在磁盘上——主分发形态仍是 onedir，
本 spec 只为「就要一个 exe」的场景出包。Analysis/裁剪逻辑与主 spec 保持一致。
"""

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# 随包分发的只读资源（onefile 下解包到 _MEIPASS）
datas = [
    ('resources/icon.png', 'resources'),
    ('resources/icon.ico', 'resources'),
    ('config.example.ini', '.'),
]

_ocr_bundle = os.path.join(SPECPATH, 'dist', 'ocr-bundle')
if os.path.isdir(_ocr_bundle):
    for _root, _dirs, _files in os.walk(_ocr_bundle):
        for _f in _files:
            _abs = os.path.join(_root, _f)
            _arc = os.path.join('resources', os.path.relpath(_abs, _ocr_bundle))
            datas.append((_abs, os.path.dirname(_arc)))
    print(f'>>> 已包含 OCR 最小集: {_ocr_bundle}')
else:
    print('>>> 警告: 未找到 OCR bundle，产物将不含 Tesseract（运行时需自行提供）')

# 隐藏导入：pystray 的 Windows 后端与键盘库按需动态加载，
# 插件包（翻译API / OCR引擎）静态分析看不到引用，必须显式收集
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
        'PyQt5.QtQml',
        'PyQt5.QtQuick',
        'PyQt5.QtQuickWidgets',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)


# ----------------------------------------------------------------------
# 收集结果裁剪（与主 spec 同一套规则）
# ----------------------------------------------------------------------

def _base(entry):
    return os.path.basename(entry[0]).lower()


def _dirname(entry):
    return os.path.dirname(entry[0]).lower()


_TESS_SUBDIR = os.path.join('resources', 'tesseract').lower()
_tess_owned = {_base(e) for e in a.binaries if _dirname(e).startswith(_TESS_SUBDIR)}
_ROOT_NEEDLE = {'libffi-8.dll', 'zlib1.dll'}
_dup_at_root = _tess_owned - _ROOT_NEEDLE

_QT_UNUSED = {
    'qt5quick.dll', 'qt5qml.dll', 'qt5qmlmodels.dll', 'qt5qmlxmllistmodel.dll',
    'qt5quickcontrols2.dll', 'qt5quicktemplates2.dll', 'qt5quickdialogs2.dll',
    'qt5designer.dll', 'qt5quick3d.dll', 'qt5quick3druntimerender.dll',
    'qwebgl.dll',
    'opengl32sw.dll',
}
_DROP_DIR_PREFIXES = (
    os.path.join('pyqt5', 'qt5', 'translations').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'qml').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'qmltoolbinding').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'scenegraph').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'vk').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'renderplugins').lower(),
)
_PIL_UNUSED_PREFIX = ('_avif', '_webp', '_webpmux', '_imagingtk', '_fpdf')


def _dropped(entry):
    name = entry[0]
    base, dirname = _base(entry), _dirname(entry)
    if dirname == '' and base in _dup_at_root:
        return True
    if base in _QT_UNUSED:
        return True
    if any(dirname.startswith(p) for p in _DROP_DIR_PREFIXES):
        return True
    if dirname.startswith('pil') and base.startswith(_PIL_UNUSED_PREFIX):
        return True
    return False


def _filter(entries, label):
    kept = [e for e in entries if not _dropped(e)]
    print(f'>>> 裁剪 {label}: {len(entries)} -> {len(kept)} 项')
    return kept


a.binaries = _filter(a.binaries, 'binaries')
a.datas = _filter(a.datas, 'datas')

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onefile：二进制与数据直接嵌进 exe（不走 exclude_binaries + COLLECT）
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ScreenshotTranslator-Onefile',
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
