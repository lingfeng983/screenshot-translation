# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置（onedir 目录形态）

构建：pyinstaller screenshot-translator.spec
产物：dist/ScreenshotTranslator/ScreenshotTranslator.exe（配套依赖在同目录 _internal/）

为什么不是单文件：单文件每次启动都要把 486 MiB 解包到 %TEMP%\\_MEI*，
实测启动 2.97s 里有 2.55s 花在解包；进程被强杀时（退出会 join 网络任务，
用户等不及就会任务管理器杀掉）临时目录整份留在磁盘上。
onedir 不解包，启动只剩导入耗时，磁盘也不再生成 _MEI。

OCR 组件（RapidOCR 的 PP-OCRv6 检测/识别/方向分类模型，约 30 MiB）随 exe
一起分发：装在 _internal/rapidocr/models/ 下，运行时由 ONNX Runtime 在本进程
内推理，既不需要系统装 Tesseract，也不再从 Release 下载任何东西。
"""

import os
import re
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# PyInstaller 的隔离子进程会先导入 Qt 绑定、再导入 onnxruntime，本机组合下这个
# 顺序会访问冲突（见 scripts/pyi_preload/sitecustomize.py）。把垫片目录塞进
# PYTHONPATH，子进程启动时自动先加载 onnxruntime。只影响构建期，不进产物。
_preload_dir = os.path.join(SPECPATH, 'scripts', 'pyi_preload')
os.environ['PYTHONPATH'] = os.pathsep.join(
    [_preload_dir]
    + [p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p])

# 随包分发的只读资源（onedir 下落在 _internal/resources/...）
datas = [
    ('resources/icon.png', 'resources'),
    ('resources/icon.ico', 'resources'),
    ('config.example.ini', '.'),
]
# 发版脚本签名成功后写下的发布者证书指纹；客户端自动更新会强制核对它。
# 未启用代码签名时文件不存在，不能放进静态清单，否则构建直接失败。
if os.path.isfile('resources/publisher_thumbprint.txt'):
    datas.append(('resources/publisher_thumbprint.txt', '.'))

# RapidOCR 的模型、默认配置与语言字典不是 py 模块，静态分析收不到；
# 只收 yaml 与 models/，不要把 pytorch 分支的架构配置等无关文件带进来
datas += collect_data_files('rapidocr', includes=['*.yaml', 'models/*'])

# 隐藏导入：pystray 的 Windows 后端与键盘库按需动态加载
# 插件包（翻译API / OCR引擎）只在运行时按目录扫描发现，静态分析看不到引用，
# 不显式收集的话产物里一个 provider 都没有，翻译与OCR全部失效
# （PIL._tkinter_finder 不收：它一进来就 import tkinter，而 tkinter 在 excludes 里）
# RapidOCR 的 onnxruntime 后端不列 hiddenimport：它是 `from .onnxruntime import`
# 的函数级导入，静态分析能收到；显式列出来反而会让 PyInstaller 的隔离子进程
# 在导入 PyQt5 之后再导入 onnxruntime，触发本机 Qt 的访问冲突直接崩掉
hiddenimports = [
    'pystray._win32',
    # 纯截图编辑态：capture_ocr / capture_scroll 是函数级延迟导入，
    # 静态扫描可能漏掉，显式列出保证 Nuitka / PyInstaller 产物里都在
    'managers.capture_annotation',
    'managers.capture_editor',
    'managers.capture_magnifier',
    'managers.capture_output',
    'managers.capture_toolbar',
    'managers.capture_ocr',
    'managers.capture_scroll',
] + collect_submodules('managers.providers') + collect_submodules('managers.ocr_providers')


def _version_file():
    """
    生成 Windows 版本资源（右键属性里的文件版本）

    程序本体不显示版本号，出问题时唯一能核对版本的地方就是 exe 属性。
    版本由 scripts/publish_release.py 通过 ST_VERSION 传进来；没传就不带
    版本资源（源码态随手构建时不该编出一个假的 1.x.0）。
    """
    version = os.environ.get('ST_VERSION', '').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        return None
    nums = tuple(int(n) for n in version.split('.')) + (0,)
    path = os.path.join('build', 'version_info.txt')
    os.makedirs('build', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(
            "VSVersionInfo(\n"
            "  ffi=FixedFileInfo(filevers=%s, prodvers=%s,\n"
            "    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
            "  kids=[\n"
            "    StringFileInfo([StringTable('080404b0', [\n"
            "      StringStruct('CompanyName', 'ScreenshotTranslation Project'),\n"
            "      StringStruct('FileDescription', '截图翻译'),\n"
            "      StringStruct('FileVersion', '%s'),\n"
            "      StringStruct('InternalName', 'ScreenshotTranslator'),\n"
            "      StringStruct('OriginalFilename', 'ScreenshotTranslator.exe'),\n"
            "      StringStruct('ProductName', '截图翻译'),\n"
            "      StringStruct('ProductVersion', '%s')])]),\n"
            "    VarFileInfo([VarStruct('Translation', [2052, 1200])])\n"
            "  ]\n"
            ")\n" % (nums, nums, version, version)
        )
    print(f'>>> 写入版本资源: {version}')
    return path


_version = _version_file()

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    # 必须在 PyQt5 的内置运行时钩子之前导入 onnxruntime（见脚本内注释）
    runtime_hooks=[os.path.join(SPECPATH, 'scripts', 'rt_preload_onnx.py')],
    excludes=[
        # 排除明显不需要的大模块，减小体积
        'tkinter',
        'matplotlib',
        'pandas',
        'scipy',
        # RapidOCR 只走 ONNX Runtime：其它推理后端在本项目里都不装，
        # 显式排除防止静态分析顺着可选分支把它们（及其依赖）带进产物
        'torch',
        'paddle',
        'paddleocr',
        'tensorflow',
        'openvino',
        'mnn',
        'tensorrt',
        'rapidocr.inference_engine.paddle',
        'rapidocr.inference_engine.torch',
        'rapidocr.inference_engine.pytorch',
        'rapidocr.inference_engine.openvino',
        'rapidocr.inference_engine.mnn',
        'rapidocr.inference_engine.tensorrt',
        # requests 走系统 OpenSSL，不用 pyOpenSSL 后端
        'cryptography',
        'cffi',
        '_cffi_backend',
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


# Qt 侧确认无人引用的大件
_QT_UNUSED = {
    'qt5quick.dll', 'qt5qml.dll', 'qt5qmlmodels.dll', 'qt5qmlxmllistmodel.dll',
    'qt5quickcontrols2.dll', 'qt5quicktemplates2.dll', 'qt5quickdialogs2.dll',
    'qt5designer.dll', 'qt5quick3d.dll', 'qt5quick3druntimerender.dll',
    # WebGL 平台插件，只链接上面被剔除的 Qt5Quick；光栅 QWidget 永远不会加载它
    'qwebgl.dll',
    # 硬件/软件 OpenGL 兜底；Windows 自带 opengl32.dll，QWidget 光栅绘制不需要它
    'opengl32sw.dll', 'libegl.dll', 'libglesv2.dll', 'd3dcompiler_47.dll',
    # 图像格式插件：只读写 PNG/JPEG（PNG 编解码在 Qt5Gui 内，不需要插件）
    'qtiff.dll', 'qwebp.dll',
    # 本项目不建数据库、不解析 SVG/TIFF
    'qsqlite.dll', 'qt5sql.dll', 'qt5svg.dll',
    # OpenCV 的视频 IO 后端（约 29MB）：只做图像解码与几何变换，永不打开摄像头/视频
    'opencv_videoio_ffmpeg500_64.dll',
}
_DROP_DIR_PREFIXES = (
    os.path.join('pyqt5', 'qt5', 'translations').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'qml').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'qmltoolbinding').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'scenegraph').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'vk').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'renderplugins').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'assetimporters').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'renderers').lower(),
    os.path.join('pyqt5', 'qt5', 'plugins', 'geoservices').lower(),
    # OpenCV 自带的 Haar 分类器、示例数据等，本项目一个都用不到
    os.path.join('cv2', 'data').lower(),
)
# Pillow 的图片格式扩展：本应用只读写 PNG/JPEG（截屏与保存结果）
_PIL_UNUSED_PREFIX = ('_avif', '_webp', '_webpmux', '_imagingtk', '_fpdf')


def _dropped(entry):
    """该收集项是否应当剔除"""
    name = entry[0]
    base, dirname = _base(entry), _dirname(entry)
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
    version=_version,        # None 时不带版本资源，见 _version_file()
    console=False,           # 无控制台黑窗（GUI应用）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    # Windows 的 Authenticode 签名不在这里做：codesign_identity 只对 macOS
    # 有效。最终交付物是 Inno Setup 编出来的 Setup.exe，由
    # scripts/publish_release.py 的 step_sign() 签名并固化发布者指纹，
    # 客户端更新时再用 update_installer.verify_publisher_signature 校验。
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
