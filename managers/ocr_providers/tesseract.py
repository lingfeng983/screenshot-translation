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

from .. import app_paths
from .base import OCRProvider

logger = logging.getLogger('screenshot-translation')

try:
    import pytesseract
    pytesseract_available = True
except ImportError as e:
    logger.warning(f"无法导入pytesseract: {e}，将使用模拟OCR")
    pytesseract = None
    pytesseract_available = False


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


def _find_tessdata_path():
    """
    查找可用的 tessdata 训练数据目录

    Returns:
        str: 首个包含 eng 与 chi_sim 训练数据的目录；均未命中时返回首个候选路径
    """
    paths = app_paths.tessdata_candidates()
    for path in paths:
        if os.path.exists(path) and os.path.isdir(path):
            if (os.path.exists(os.path.join(path, 'eng.traineddata'))
                    and os.path.exists(os.path.join(path, 'chi_sim.traineddata'))):
                return path
            logger.warning(f"tessdata目录存在但缺少训练数据文件: {path}")
    logger.info("未找到有效的tessdata目录，使用默认路径")
    return paths[0]


class TesseractProvider(OCRProvider):
    """本地 Tesseract OCR（离线、免费）"""

    name = 'tesseract'
    display_name = 'Tesseract(本地)'
    config_fields = (
        ('tesseract_langs', '识别语言(如 eng+chi_sim)', False),
    )

    def __init__(self, config_manager=None):
        """
        初始化：探测引擎路径与训练数据目录

        Args:
            config_manager: 配置管理器实例
        """
        super().__init__(config_manager)

        if pytesseract_available:
            exe = _find_tesseract_cmd()
            if exe:
                pytesseract.pytesseract.tesseract_cmd = exe
                logger.info(f"使用的tesseract路径: {exe}")
            else:
                logger.info("未找到内置tesseract，将依赖系统PATH中的tesseract")
            self._check_engine()

        # 通过环境变量传递训练数据目录：pytesseract会按空白切分config字符串，
        # 用 --tessdata-dir 传含空格或引号的路径会被截断
        self.tessdata_path = _find_tessdata_path()
        os.environ['TESSDATA_PREFIX'] = self.tessdata_path
        logger.info(f"使用的tessdata路径: {self.tessdata_path}")

    def _check_engine(self):
        """检查Tesseract引擎可用性，不可用时输出安装指引"""
        try:
            version = pytesseract.get_tesseract_version()
            logger.info(f"Tesseract OCR版本: {version}")
        except Exception as e:
            logger.warning(f"Tesseract OCR检查失败: {e}")
            logger.warning("请确保已安装Tesseract OCR并添加到系统PATH")
            logger.warning(
                "Windows下载地址: https://github.com/UB-Mannheim/tesseract/wiki")

    def _get_langs(self):
        """
        获取识别语言配置

        Returns:
            str: 语言组合串，默认 eng+chi_sim
        """
        langs = self._get_option('tesseract_langs', fallback='eng+chi_sim').strip()
        return langs or 'eng+chi_sim'

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
            raise Exception("pytesseract未安装，无法使用Tesseract识别")

        logger.info("开始Tesseract OCR识别")
        processed = self._preprocess_image(image)

        # tessdata目录通过TESSDATA_PREFIX环境变量指定（见__init__说明）
        custom_config = f"--psm 6 -l {self._get_langs()}"
        text = pytesseract.image_to_string(processed, config=custom_config)

        cleaned = self.clean_text(text)
        logger.info(f"Tesseract识别完成，识别到 {len(cleaned)} 个字符")
        if not cleaned:
            raise Exception("Tesseract未识别到任何文本")
        return cleaned

    def get_available_languages(self):
        """
        获取当前可用的OCR语言列表

        Returns:
            list[str]: 语言代码列表；pytesseract不可用或查询失败时返回模拟列表
        """
        if not pytesseract_available:
            return ['eng', 'chi_sim']
        try:
            languages = pytesseract.get_languages()
            logger.info(f"可用的OCR语言: {languages}")
            return languages
        except Exception as e:
            logger.warning(f"获取可用语言失败: {e}")
            return ['eng', 'chi_sim']

    @staticmethod
    def _preprocess_image(image):
        """
        预处理图像以提升识别率（放大、灰度、模糊、增强对比度、二值化）

        Args:
            image (PIL.Image.Image): 原始图像

        Returns:
            PIL.Image.Image: 预处理后的图像；处理失败时返回原图
        """
        from PIL import Image, ImageEnhance, ImageFilter

        try:
            # 1. 调整图像大小，提高分辨率
            width, height = image.size
            resized = image.resize((width * 2, height * 2), Image.LANCZOS)

            # 2. 转换为灰度
            gray = resized.convert('L')

            # 3. 高斯模糊，减少噪声
            blurred = gray.filter(ImageFilter.GaussianBlur(radius=1))

            # 4. 在灰度图上增强对比度
            # 必须在二值化之前做：ImageEnhance 内部走 Image.blend，
            # 不支持 1 位图像（mode '1'）
            enhanced = ImageEnhance.Contrast(blurred).enhance(2.0)

            # 5. 最后二值化，输出 'L' 模式以兼容 pytesseract
            threshold = 128
            return enhanced.point(lambda x: 255 if x > threshold else 0, mode='L')
        except Exception as e:
            logger.warning(f"图像预处理失败: {e}")
            return image


def register(registry):
    """注册到全局OCR provider注册表"""
    registry.register(TesseractProvider)
