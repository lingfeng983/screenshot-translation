# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置（onedir 目录形态）

构建：pyinstaller screenshot-translator.spec
产物：dist/ScreenshotTranslator/ScreenshotTranslator.exe（配套依赖在同目录 _internal/）

为什么不是单文件：单文件每次启动都要把 486 MiB 解包到 %TEMP%\\_MEI*，
实测启动 2.97s 里有 2.55s 花在解包；进程被强杀时（退出会 join 网络任务，
用户等不及就会任务管理器杀掉）临时目录整份留在磁盘上。
onedir 不解包，启动只剩导入耗时，磁盘也不再生成 _MEI。

OCR 组件（Tesseract 便携版）：先跑 scripts/collect_ocr_bundle.py 生成
dist/ocr-bundle/，存在则随包分发，否则运行时自行下载。
"""

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# 随包分发的只读资源（onedir 下落在 _internal/resources/...）
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
    print('>>> 警告: 未找到 OCR bundle，产物将不含 Tesseract（运行时需自行提供）')

# 隐藏导入：pystray 的 Windows 后端与键盘库按需动态加载
# 插件包（翻译API / OCR引擎）只在运行时按目录扫描发现，静态分析看不到引用，
# 不显式收集的话产物里一个 provider 都没有，翻译与OCR全部失效
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
        # 纯 QtWidgets（raster 绘制）项目，QML/Quick 全套用不到
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
# 收集结果裁剪
# ----------------------------------------------------------------------

def _base(entry):
    return os.path.basename(entry[0]).lower()


def _dirname(entry):
    return os.path.dirname(entry[0]).lower()


# tesseract.exe 的依赖 DLL 已经整套放在 resources/tesseract/ 里（子进程从自身
# 目录加载）；PyInstaller 把 tesseract.exe 当作二进制分析后又往根目录复制了一遍，
# 实测重复 47 个文件、解包体积白占 155 MiB。根目录只保留 CPython 自己链接的两个。
_TESS_SUBDIR = os.path.join('resources', 'tesseract').lower()
_tess_owned = {_base(e) for e in a.binaries if _dirname(e).startswith(_TESS_SUBDIR)}
_ROOT_NEEDLE = {'libffi-8.dll', 'zlib1.dll'}
_dup_at_root = _tess_owned - _ROOT_NEEDLE

# Qt 侧确认无人引用的大件
_QT_UNUSED = {
    'qt5quick.dll', 'qt5qml.dll', 'qt5qmlmodels.dll', 'qt5qmlxmllistmodel.dll',
    'qt5quickcontrols2.dll', 'qt5quicktemplates2.dll', 'qt5quickdialogs2.dll',
    'qt5designer.dll', 'qt5quick3d.dll', 'qt5quick3druntimerender.dll',
    # WebGL 平台插件，只链接上面被剔除的 Qt5Quick；光栅 QWidget 永远不会加载它
    'qwebgl.dll',
    # OpenGL 软件渲染兜底；Windows 自带 opengl32.dll，QWidget 光栅绘制不需要它
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
# Pillow 的图片格式扩展：本应用只读写 PNG/JPEG（截屏与保存结果）
_PIL_UNUSED_PREFIX = ('_avif', '_webp', '_webpmux', '_imagingtk', '_fpdf')


def _dropped(entry):
    """该收集项是否应当剔除"""
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ScreenshotTranslator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=False,           # 无控制台黑窗（GUI应用）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='ScreenshotTranslator',
)
