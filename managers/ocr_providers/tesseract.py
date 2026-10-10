#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tesseract OCR 提供者（本地离线识别）

通过 pytesseract 调用本地 Tesseract 引擎：
- 优先使用项目 resources/tesseract/ 下的便携版可执行文件
- tessdata 训练数据目录通过 TESSDATA_PREFIX 环境变量传递
- 原有图像预处理逻辑（放大/灰度/二值化/对比度）迁入此provider
"""

import logging
import os
import subprocess
import sys
import threading

from .. import app_paths
from ..provider_common import (
    ProviderConfigError, ProviderEmptyResultError, ProviderTimeoutError)
from ..text_block import TextBlock
from .base import OCRProvider

logger = logging.getLogger('screenshot-translation')

# 预处理参数
_MIN_SCALE_FACTOR = 2      # 常规图至少放大2倍
_MAX_SCALE_FACTOR = 4      # 小图最多放大4倍（再大清晰度不增反降）
_TARGET_HEIGHT = 64        # 小图放大到约这个高度，Tesseract 对 12px 左右的
                           # ClearType 中文几乎无法识别，需要拉到更舒适的字号
_MIN_SCALE_HEIGHT = 40     # 选区高度低于此值时启用自适应放大（2~4倍）
_AUTOCONTRAST_CUTOFF = 1   # autocontrast 裁掉两侧1%的极值噪声
_PAD = 12                  # 二值化后四周留白，避免字符贴边被截断

# 单次识别的硬时限（秒）：识别跑在固定大小的线程池里，一次挂死就永久占住
# 一个 worker，几次之后整个截图翻译就会停摆；pytesseract 超时后会 kill
# 子进程，所以设时限同时也在回收进程
_TESSERACT_TIMEOUT = 60

# pytesseract 的引擎路径与训练数据目录是进程级全局态（模块变量 + 环境变量），
# 并发构造 provider 或重载配置时必须串行改写，否则正在运行的子进程会读到
# 换了一半的配置
_engine_lock = threading.Lock()

try:
    import pytesseract
    pytesseract_available = True
except ImportError as e:
    logger.warning(f"无法导入pytesseract: {e}，将使用模拟OCR")
    pytesseract = None
    pytesseract_available = False


def _no_window_flags():
    """Windows下禁止子进程分配控制台窗口的标志"""
    if sys.platform != 'win32':
        return 0
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)


class _HiddenSpawnSubprocess:
    """
    pytesseract 内 subprocess 的代理：给所有进程创建补上 no-window 标志

    pytesseract 只在 STARTUPINFO 里设了 SW_HIDE。父进程没有控制台时
    （打包成 exe 后就是这种情况），Windows 会为 tesseract.exe 另开一个
    终端宿主窗口，表现为翻译时一闪而逝的弹框；只有 CREATE_NO_WINDOW
    能阻止控制台分配。它有多处裸调用（识别、版本探测、语言列表），
    所以整体代理它的 subprocess 引用。
    """

    def __init__(self, module, flags):
        self._module = module
        self._flags = flags
        for name in ('Popen', 'run', 'call', 'check_call', 'check_output'):
            setattr(self, name, self._bind(name))

    def _bind(self, name):
        flags = self._flags

        def spawn(*args, **kwargs):
            kwargs.setdefault('creationflags', flags)
            return getattr(self._module, name)(*args, **kwargs)

        return spawn

    def __getattr__(self, name):
        return getattr(self._module, name)


def _hide_pytesseract_console():
    """把代理装到 pytesseract 的模块命名空间（幂等，重复导入不会叠加）"""
    if not pytesseract_available or sys.platform != 'win32':
        return
    module = pytesseract.pytesseract
    flags = _no_window_flags()
    if not flags or isinstance(module.subprocess, _HiddenSpawnSubprocess):
        return
    module.subprocess = _HiddenSpawnSubprocess(module.subprocess, flags)


_hide_pytesseract_console()


def _find_tesseract_cmd():
    """
    探测 tesseract 可执行文件路径（由 app_paths 统一提供候选）

    Returns:
        str 或 None: 首个存在的路径；均未命中返回None（用系统PATH）
    """
    for exe in app_paths.tesseract_cmd_candidates():
        if exe and os.path.isfile(exe):
            return exe
    return None


def _tessdata_has(path, langs):
    """
    判断目录里指定语言的训练数据是否齐备

    Args:
        path (str): tessdata 目录
        langs (list[str]): 语言代码

    Returns:
        bool: 每个语言都有对应 .traineddata
    """
    if not path or not os.path.isdir(path):
        return False
    return all(os.path.isfile(os.path.join(path, f'{lang}.traineddata'))
               for lang in langs)


def _find_tessdata_path(exe=None, langs=None):
    """
    查找可用的 tessdata 训练数据目录

    先看所选 exe 的同级 tessdata：可执行文件与训练数据必须成对，便携版
    配上系统安装的语言包（版本或语言集不同）只会得到一句
    "Error opening data file"，现场根本诊断不出来。

    Args:
        exe (str 或 None): 已确定的 tesseract.exe 路径
        langs (list[str] 或 None): 需要的语言，默认 eng 与 chi_sim

    Returns:
        str: 首个语言齐备的目录；都不齐备时返回首个存在的候选
    """
    langs = langs or ['eng', 'chi_sim']
    candidates = ([os.path.join(os.path.dirname(exe), 'tessdata')] if exe else [])
    candidates += app_paths.tessdata_candidates()
    existing = [p for p in candidates if p and os.path.isdir(p)]
    for path in existing:
        if _tessdata_has(path, langs):
            return path
    if existing:
        logger.warning(f"候选 tessdata 目录缺 {','.join(langs)} 训练数据: {existing}")
        return existing[0]
    logger.warning("未找到任何 tessdata 目录，使用默认路径")
    return candidates[0] if candidates else ''


# 本模块设过的主变量值：只清自己那一份，用户环境里原有的前缀不动
_applied_prefix = None


def _apply_tessdata_prefix(path):
    """
    通过环境变量把训练数据目录交给 tesseract

    pytesseract 会按空白切分 config 字符串，用 --tessdata-dir 传含空格或引号的
    路径会被截断，所以走环境变量。空值比不设更糟：tesseract 会去找
    "/eng.traineddata"（相对当前工作目录），而什么都不设时它用的是 exe 自带的
    默认目录——探测失败时设个空串等于把一个本来能用的引擎判死。

    Args:
        path (str): tessdata 目录，空串表示没定位到
    """
    global _applied_prefix
    if path:
        os.environ['TESSDATA_PREFIX'] = path
        _applied_prefix = path
    elif _applied_prefix is not None and \
            os.environ.get('TESSDATA_PREFIX') == _applied_prefix:
        os.environ.pop('TESSDATA_PREFIX', None)
        _applied_prefix = None


class TesseractProvider(OCRProvider):
    """本地 Tesseract OCR（离线、免费）"""

    name = 'tesseract'
    display_name = 'Tesseract(本地)'
    config_fields = (
        ('tesseract_langs', '识别语言(如 eng+chi_sim)', False),
    )
    # 本地引擎：同一张图重试必然同一结果，空结果可直接定案
    deterministic_results = True

    def __init__(self, config_manager=None):
        """
        初始化：探测引擎路径与训练数据目录

        Args:
            config_manager: 配置管理器实例
        """
        super().__init__(config_manager)
        self._engine_ok = False

        if pytesseract_available:
            with _engine_lock:
                exe = _find_tesseract_cmd()
                if exe:
                    pytesseract.pytesseract.tesseract_cmd = exe
                    logger.info(f"使用的tesseract路径: {exe}")
                else:
                    logger.info("未找到内置tesseract，将依赖系统PATH中的tesseract")

                # 通过环境变量传递训练数据目录
                self.tessdata_path = _find_tessdata_path(exe, self._lang_list())
                _apply_tessdata_prefix(self.tessdata_path)
                logger.info(f"使用的tessdata路径: {self.tessdata_path or '（未定位到，按引擎默认路径）'}")
                self.exe_path = exe

            self._check_engine()

    def _lang_list(self):
        """
        配置里的识别语言拆成列表（tessdata 完整性检查按单个语言判断）

        Returns:
            list[str]: 语言代码，默认 eng 与 chi_sim
        """
        langs = [l.strip() for l in self._get_langs().split('+') if l.strip()]
        return langs or ['eng', 'chi_sim']

    def _probe(self, args, timeout=10):
        """
        跑一次 tesseract 的辅助命令（带硬时限，超时 kill）

        不走 pytesseract 的 get_tesseract_version/get_languages：它们内部
        是裸 Popen + communicate()，没有 timeout，引擎卡住时探测本身先挂死。

        Args:
            args (list[str]): 命令参数
            timeout (int): 时限（秒）

        Returns:
            tuple: (CompletedProcess 或 None, 异常或 None)
        """
        cmd = [self.exe_path or 'tesseract'] + list(args)
        try:
            return subprocess.run(cmd, capture_output=True, timeout=timeout,
                                  creationflags=_no_window_flags()), None
        except Exception as e:      # OSError/TimeoutExpired 等，调用方按不可用处理
            return None, e

    def _check_engine(self):
        """
        探测引擎与语言包是否真能用，结果记入 _engine_ok

        以前只打一行 warning 就当没事，用户要等到识别失败才发现组件没装好，
        提示还指向 GitHub 而不是本项目的下载链路；现在识别入口按结果直接抛
        带指引的非重试错误。
        """
        out, err = self._probe(['--version'])
        if err is not None or out is None or out.returncode != 0:
            self._engine_ok = False
            detail = err if err is not None else (out.stderr or b'').decode(
                errors='replace').strip() if out else '无输出'
            logger.warning(f"Tesseract 引擎不可用: {detail}")
            return

        missing = [l for l in self._lang_list()
                   if not _tessdata_has(self.tessdata_path, [l])]
        if missing:
            self._engine_ok = False
            logger.warning(f"tessdata 缺少语言包 {missing}（目录 {self.tessdata_path}）")
            return

        version = (out.stdout or b'').decode(errors='replace').strip()
        self._engine_ok = True
        logger.info(f"Tesseract OCR版本: {version.splitlines()[0] if version else '?'}")

    def _require_engine(self):
        """
        引擎/语言包不可用时抛出带安装指引的非重试错误

        Raises:
            ProviderConfigError: 本地组件缺失
        """
        if self._engine_ok:
            return
        raise ProviderConfigError(
            "本地 Tesseract 不可用（缺少引擎或语言包）。请用安装包重新安装并勾选"
            f"“下载 OCR 离线识别组件”，组件目录 {app_paths.ocr_resources_dir()}；"
            "或改用在线 OCR")

    def _get_langs(self):
        """
        获取识别语言配置

        Returns:
            str: 语言组合串，默认 eng+chi_sim
        """
        langs = self._get_option('tesseract_langs', fallback='eng+chi_sim').strip()
        return langs or 'eng+chi_sim'

    def _run(self, fn, image, **kwargs):
        """
        带硬时限地调用 pytesseract 的识别接口

        不传 timeout 时一次卡住就是永久占住线程池的一个 worker（几次之后
        截图翻译整体停摆）。pytesseract 超时会先 kill 子进程再抛
        RuntimeError，这里换成非重试的 ProviderTimeoutError：同一张图再跑
        一次只会再等满一个时限，不如立刻降级。

        Args:
            fn: pytesseract.image_to_string / image_to_data
            image (PIL.Image.Image): 预处理后的图像
            **kwargs: 透传给 pytesseract 的参数

        Returns:
            识别结果（str 或 dict）

        Raises:
            ProviderTimeoutError: 超过 _TESSERACT_TIMEOUT 秒
        """
        kwargs.setdefault('timeout', _TESSERACT_TIMEOUT)
        message = (f"Tesseract 识别超过 {_TESSERACT_TIMEOUT} 秒已中止，"
                   f"选区可能过大；可缩小选区或改用在线 OCR")
        try:
            return fn(image, **kwargs)
        except RuntimeError as e:
            if 'timeout' in str(e).lower():
                raise ProviderTimeoutError(message) from e
            raise
        except subprocess.TimeoutExpired as e:
            raise ProviderTimeoutError(message) from e

    def recognize(self, image):
        """
        对图像执行OCR识别

        Args:
            image (PIL.Image.Image): 待识别的图像

        Returns:
            str: 识别出的文本

        Raises:
            Exception: pytesseract不可用或识别失败时抛出
        """
        if not pytesseract_available:
            raise ProviderConfigError("pytesseract未安装，无法使用Tesseract识别")
        self._require_engine()

        logger.info("开始Tesseract OCR识别")
        processed = self._preprocess_image(image)

        # tessdata目录通过TESSDATA_PREFIX环境变量指定（见__init__说明）
        custom_config = f"--psm 6 -l {self._get_langs()}"
        text = self._run(pytesseract.image_to_string, processed,
                         config=custom_config)

        cleaned = self.clean_text(text)
        logger.info(f"Tesseract识别完成，识别到 {len(cleaned)} 个字符")
        if not cleaned:
            # 本地引擎确定性结果：同一张图重试也是空，不浪费时间退避
            raise ProviderEmptyResultError("Tesseract未识别到任何文本")
        return cleaned

    def recognize_blocks(self, image):
        """
        行级块识别：词框按 段落/行 合并成行块

        Args:
            image (PIL.Image.Image): 待识别图像

        Returns:
            list[TextBlock]: 按阅读顺序排列的行块

        Raises:
            Exception: pytesseract不可用或未识别到文本
        """
        if not pytesseract_available:
            raise ProviderConfigError("pytesseract未安装，无法使用Tesseract识别")
        self._require_engine()

        processed, scale, pad = self._preprocess_with_geometry(image)
        custom_config = f"--psm 6 -l {self._get_langs()}"
        data = self._run(pytesseract.image_to_data, processed,
                         config=custom_config,
                         output_type=pytesseract.Output.DICT)

        lines = {}
        for i in range(len(data.get('text', []))):
            text = (data['text'][i] or '').strip()
            try:
                conf = int(data['conf'][i])
            except (TypeError, ValueError):
                conf = 0
            if not text or conf <= 0:
                continue
            key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
            # 先扣掉四周留白再交给 merge_words 乘 scale：词框在留白图上满足
            # x_pad = x_orig * factor + pad，只减 left/top 不改变宽高，
            # 等价于把整个框平移回原图坐标系
            lines.setdefault(key, []).append({
                'text': text,
                'word_num': data['word_num'][i],
                'left': data['left'][i] - pad,
                'top': data['top'][i] - pad,
                'width': data['width'][i],
                'height': data['height'][i],
            })

        blocks = []
        for key in sorted(lines):
            words = sorted(lines[key], key=lambda w: w['word_num'])
            block = TextBlock.merge_words(words, scale=scale)
            if block is not None:
                blocks.append(block)

        logger.info(f"Tesseract块级识别完成，{len(blocks)} 行")
        if not blocks:
            raise ProviderEmptyResultError("Tesseract未识别到任何文本")
        return blocks

    def get_available_languages(self):
        """
        获取当前可用的OCR语言列表

        Returns:
            list[str]: 语言代码列表；pytesseract不可用或查询失败时返回模拟列表
        """
        if not pytesseract_available:
            return ['eng', 'chi_sim']
        out, err = self._probe(['--list-langs'])
        if err is not None or out is None or out.returncode != 0:
            logger.warning(f"获取可用语言失败: {err or '引擎返回非零'}")
            return ['eng', 'chi_sim']
        # 首行是 "List of available languages in "...""，其余每行一个语言
        languages = [l.strip() for l in
                     (out.stdout or b'').decode(errors='replace').splitlines()[1:]
                     if l.strip()]
        logger.info(f"可用的OCR语言: {languages}")
        return languages or ['eng', 'chi_sim']

    @staticmethod
    def _scale_factor(height):
        """
        根据选区高度计算放大倍数

        固定2倍对很小的选区远远不够：一个 20~30px 高的选区里中文只有约
        12px，ClearType 亚像素渲染下 Tesseract 基本识别不出，会出现“框
        住了字却翻译不出来”。高度不足时把图放大到约 _TARGET_HEIGHT，
        放大倍数夹在 _MIN_SCALE_FACTOR~_MAX_SCALE_FACTOR 之间。

        Args:
            height (int): 原图高度（像素）

        Returns:
            int: 放大倍数
        """
        if height >= _MIN_SCALE_HEIGHT:
            return _MIN_SCALE_FACTOR
        factor = -(-_TARGET_HEIGHT // height)   # ceil，保证达到目标高度
        return max(_MIN_SCALE_FACTOR, min(_MAX_SCALE_FACTOR, factor))

    @staticmethod
    def _otsu_threshold(gray):
        """
        按类间方差最大原则求二值化阈值（Otsu）

        固定 128 阈值在深底浅字、低对比度文字上会把整图判成背景，
        字迹直接消失；Otsu 按直方图自适应，两种主题都能保住笔画。

        Args:
            gray (PIL.Image.Image): 'L' 模式灰度图

        Returns:
            int: 0~255 的阈值
        """
        hist = gray.histogram()
        total = sum(hist)
        if not total:
            return 128
        sum_all = sum(i * count for i, count in enumerate(hist))
        sum_b = w_b = 0
        best_t, best_var = 128, -1.0
        for t in range(256):
            w_b += hist[t]
            if not w_b:
                continue
            w_f = total - w_b
            if not w_f:
                break
            sum_b += t * hist[t]
            var = w_b * w_f * (sum_b / w_b - (sum_all - sum_b) / w_f) ** 2
            # 平局取更大的 t：保证阈值落在两峰之间而不是压在前景灰度上
            if var >= best_var:
                best_var, best_t = var, t
        return best_t

    @classmethod
    def _preprocess_image(cls, image):
        """
        预处理图像以提升识别率（只取图，坐标换算见 _preprocess_with_geometry）

        Args:
            image (PIL.Image.Image): 原始图像

        Returns:
            PIL.Image.Image: 预处理后的图像；处理失败时返回原图
        """
        return cls._preprocess_with_geometry(image)[0]

    @classmethod
    def _preprocess_with_geometry(cls, image):
        """
        预处理图像以提升识别率：自适应放大、灰度、自动对比度、留白

        不再做高斯模糊与固定 128 二值化：实测真实 UI 的小号 ClearType
        文字，模糊会把笔画糊掉、固定阈值在深色主题下直接抹掉字迹，
        两者都显著降低识别率；灰度 + autocontrast 交给 Tesseract 自己
        自适应，效果更好。

        同时给出词框坐标回映射所需的几何信息：识别图是在原图放大 factor
        倍后再四周各加 pad 像素留白，因此留白图上某点的坐标满足
        x_pad = x_orig * factor + pad，回映射必须**先减 pad 再除以 factor**。
        只按 image.width / processed.width 缩放会漏算留白，使得块位置随
        横向位置线性偏移（PAD 固定，图越宽倍数越小、偏差越大：2 倍放大的
        1000px 图两端各有约 6px 误差），就地覆盖译文就会对不齐原文。

        Args:
            image (PIL.Image.Image): 原始图像

        Returns:
            tuple: (预处理图, 缩放系数, 留白像素)。缩放系数为 1/factor，
                供 TextBlock.merge_words 把像素坐标缩回原图；返回原图本身
                （未放大也未留白）时系数为 1.0、留白为 0
        """
        from PIL import Image, ImageOps

        try:
            width, height = image.size
            if width <= 0 or height <= 0:
                return image, 1.0, 0

            # 1. 自适应放大：小图放大到约 _TARGET_HEIGHT 高
            factor = cls._scale_factor(height)
            resized = image.resize((width * factor, height * factor), Image.LANCZOS)

            # 2. 灰度 + 自动对比度（自动裁掉极值噪声，深浅主题通吃）
            gray = resized.convert('L')
            enhanced = ImageOps.autocontrast(gray, cutoff=_AUTOCONTRAST_CUTOFF)

            # 3. Otsu 自适应二值化，并加四周留白防止字符贴边截断
            threshold = cls._otsu_threshold(enhanced)
            binary = enhanced.point(lambda x: 255 if x > threshold else 0, mode='L')
            padded = Image.new('L', (binary.width + 2 * _PAD, binary.height + 2 * _PAD), 255)
            padded.paste(binary, (_PAD, _PAD))
            return padded, 1.0 / factor, _PAD
        except Exception as e:
            logger.warning(f"图像预处理失败: {e}")
            return image, 1.0, 0


def register(registry):
    """注册到全局OCR provider注册表"""
    registry.register(TesseractProvider)
