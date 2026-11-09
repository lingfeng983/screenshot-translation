#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RapidOCR 提供者（本地离线识别）

内置 PP-OCR 检测/识别/方向分类模型，经 ONNX Runtime 在本进程内推理：
- 不依赖系统安装任何 OCR 引擎，安装包自带模型即可用
- 返回行级四点坐标，供截图遮罩把译文贴回原文位置

rapidocr 3.x 的返回对象是 ``boxes / txts / scores``（而不是旧版的
[四点坐标, 文本, 置信度] 列表），适配层按该接口取值。
"""

import logging
import threading

from ..provider_common import ProviderConfigError, ProviderEmptyResultError
from ..text_block import TextBlock
from .base import OCRProvider

logger = logging.getLogger('screenshot-translation')

# 低于该置信度的行不参与翻译：小图标、分隔线、背景纹理被误检成文字时，
# 送上去只会换来一段莫名其妙的译文
DEFAULT_SCORE_THRESHOLD = 0.5

# 引擎与推理串行化：
# - 构造一次要加载三个 ONNX 模型（约 1 秒），provider 实例按类缓存在 OCR
#   管理器里，必须复用
# - 同一引擎实例被多次截图并发调用时，ONNX session 不保证线程安全
_engine_lock = threading.Lock()
_infer_lock = threading.Lock()
_engine = None


def _load_engine():
    """
    懒加载全局 RapidOCR 引擎（模型较重，不放在模块导入期）

    Returns:
        引擎实例

    Raises:
        ProviderConfigError: rapidocr/onnxruntime 未安装或模型加载失败
                           （重试无用，直接走降级链）
    """
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            try:
                from rapidocr import RapidOCR
            except ImportError as e:
                raise ProviderConfigError(
                    "RapidOCR 未安装（缺少 rapidocr/onnxruntime），"
                    "请使用完整安装包或改用在线 OCR") from e
            try:
                _engine = RapidOCR()
            except Exception as e:
                raise ProviderConfigError(
                    f"RapidOCR 初始化失败（模型缺失或损坏）: {e}") from e
            logger.info("RapidOCR 引擎加载完成")
    return _engine


def _to_bgr_array(image):
    """PIL 图像转 RapidOCR 需要的 BGR ndarray（它按 OpenCV 读图约定接收）"""
    import numpy as np
    rgb = np.asarray(image.convert('RGB'))
    return np.ascontiguousarray(rgb[:, :, ::-1])


class RapidOCRProvider(OCRProvider):
    """本地 RapidOCR（离线、无需外部引擎）"""

    name = 'rapidocr'
    display_name = 'RapidOCR(本地)'
    config_fields = (
        ('rapidocr_score_threshold', '置信度阈值(0~1)', False),
    )
    # 本地引擎：同一张图重试必然同一结果，空结果可直接定案
    deterministic_results = True

    def _score_threshold(self):
        """
        读取置信度阈值（非法值回退默认，避免一个笔误让所有行都被过滤掉）

        Returns:
            float: 0~1 之间的阈值
        """
        raw = self._get_option(
            'rapidocr_score_threshold', fallback=str(DEFAULT_SCORE_THRESHOLD))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            logger.warning(f"置信度阈值非法: {raw!r}，按默认 {DEFAULT_SCORE_THRESHOLD} 处理")
            return DEFAULT_SCORE_THRESHOLD
        return max(0.0, min(1.0, value))

    def _infer(self, image):
        """
        跑一次推理，返回按阅读顺序排列的 (文本, 置信度, 包围盒) 列表

        Args:
            image (PIL.Image.Image): 待识别图像

        Returns:
            list[tuple[str, float, tuple]]: [(文本, 置信度, (left, top, width, height))]

        Raises:
            ProviderConfigError: 引擎不可用
            ProviderEmptyResultError: 引擎跑通但没有可用文本
        """
        engine = _load_engine()
        # 引擎构造与首次推理都会加载模型，异常按“组件损坏”归类为配置问题：
        # 同一张图再跑一次结果一样，重试只会白等
        with _infer_lock:
            try:
                output = engine(_to_bgr_array(image))
            except Exception as e:
                raise ProviderConfigError(f"RapidOCR 识别失败: {e}") from e

        boxes = getattr(output, 'boxes', None)
        txts = getattr(output, 'txts', None)
        scores = getattr(output, 'scores', None)
        if boxes is None or txts is None:
            raise ProviderEmptyResultError("RapidOCR 未识别到任何文本")
        if scores is None:
            # 部分版本/配置不返回置信度：按满分放行，避免后面 zip(txts, None, boxes)
            # 抛 TypeError 被误报成「引擎坏了」。
            scores = [1.0] * len(txts)

        threshold = self._score_threshold()
        lines = []
        for text, score, polygon in zip(txts, scores, boxes):
            text = (text or '').strip()
            try:
                confidence = float(score)
            except (TypeError, ValueError):
                confidence = 0.0
            if not text or confidence < threshold:
                continue
            xs = [float(point[0]) for point in polygon]
            ys = [float(point[1]) for point in polygon]
            left, top = min(xs), min(ys)
            width = max(1.0, max(xs) - left)
            height = max(1.0, max(ys) - top)
            lines.append((text, confidence, (left, top, width, height)))

        if not lines:
            raise ProviderEmptyResultError("RapidOCR 未识别到任何文本")
        # 检测框本身就是行级粒度，按上→下、左→右回到阅读顺序
        lines.sort(key=lambda item: (item[2][1], item[2][0]))
        return lines

    def recognize(self, image):
        """
        对图像执行 OCR 识别

        Args:
            image (PIL.Image.Image): 待识别的图像

        Returns:
            str: 按行拼接的识别文本

        Raises:
            ProviderConfigError: 引擎不可用
            ProviderEmptyResultError: 未识别到文本
        """
        lines = self._infer(image)
        text = self.clean_text('\n'.join(line[0] for line in lines))
        logger.info(f"RapidOCR 识别完成，{len(lines)} 行 / {len(text)} 个字符")
        if not text:
            raise ProviderEmptyResultError("RapidOCR 未识别到任何文本")
        return text

    def recognize_blocks(self, image):
        """
        行级块识别（就地覆盖原文所需的坐标来源）

        Args:
            image (PIL.Image.Image): 待识别的图像

        Returns:
            list[TextBlock]: 按阅读顺序排列的行块

        Raises:
            ProviderConfigError: 引擎不可用
            ProviderEmptyResultError: 未识别到文本
        """
        blocks = [
            TextBlock(text, left, top, width, height)
            for text, _confidence, (left, top, width, height) in self._infer(image)
        ]
        logger.info(f"RapidOCR 块级识别完成，{len(blocks)} 行")
        return blocks

    def get_available_languages(self):
        """内置 PP-OCR 模型覆盖中英文（源语言自动识别）"""
        return ['chi_sim', 'eng']


def register(registry):
    """注册到全局OCR provider注册表"""
    registry.register(RapidOCRProvider)
