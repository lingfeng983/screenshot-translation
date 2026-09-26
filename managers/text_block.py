#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR 文本块模型

带像素坐标的一行文本，供截图遮罩把译文贴回原文位置（就地覆盖）使用。
坐标一律使用被识别图像的像素坐标系：把整屏图 crop 到选区后再识别，
得到的块坐标就是"选区内相对坐标"，加选区原点即得屏幕绝对坐标。
"""


def _is_cjk(char):
    """判断字符是否为中日韩文字（这类文字词间不需要补空格）"""
    if not char:
        return False
    code = ord(char)
    return (0x3040 <= code <= 0x30FF or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF
            or 0xFF00 <= code <= 0xFF60)


class TextBlock:
    """一行识别结果及其在图像内的位置"""

    __slots__ = ('text', 'left', 'top', 'width', 'height')

    def __init__(self, text, left, top, width, height):
        """
        Args:
            text (str): 行文本
            left (int): 左上角 x（像素）
            top (int): 左上角 y（像素）
            width (int): 宽（像素）
            height (int): 高（像素）
        """
        self.text = text
        self.left = int(left)
        self.top = int(top)
        self.width = int(width)
        self.height = int(height)

    @property
    def right(self):
        """右边界（含）"""
        return self.left + self.width

    @property
    def bottom(self):
        """下边界（含）"""
        return self.top + self.height

    def moved(self, dx, dy):
        """返回平移后的新块，原块不变"""
        return TextBlock(self.text, self.left + dx, self.top + dy,
                         self.width, self.height)

    def union(self, other):
        """返回覆盖两块的包围盒（文本取本块）"""
        left = min(self.left, other.left)
        top = min(self.top, other.top)
        right = max(self.right, other.right)
        bottom = max(self.bottom, other.bottom)
        return TextBlock(self.text, left, top, right - left, bottom - top)

    @classmethod
    def merge_words(cls, words, scale=1.0):
        """
        把词级条目合并成一个行块；无有效框时返回None

        Args:
            words (list[dict]): 词条目，含 text/left/top/width/height
            scale (float): 坐标缩放系数（识别前放大的图需要缩回原图坐标）

        Returns:
            TextBlock 或 None
        """
        items = [w for w in words if (w.get('text') or '').strip()]
        if not items:
            return None

        left = min(int(w['left']) for w in items)
        top = min(int(w['top']) for w in items)
        right = max(int(w['left']) + int(w['width']) for w in items)
        bottom = max(int(w['top']) + int(w['height']) for w in items)
        line_height = bottom - top

        # 词间距接近一个空格宽度时补1个空格；明显更宽（如表格列）补2个，
        # 保住列间相对位置又不至于把英文单词拆成两词
        wide_gap = max(2, int(line_height * 0.5))
        parts = [items[0]['text'].strip()]
        for prev, cur in zip(items, items[1:]):
            distance = int(cur['left']) - (int(prev['left']) + int(prev['width']))
            next_text = cur['text'].strip()
            if not (_is_cjk(parts[-1][-1:]) and _is_cjk(next_text[:1])):
                parts.append('  ' if distance > wide_gap else ' ')
            parts.append(next_text)
        text = ''.join(parts).strip()
        if not text:
            return None

        return cls(text, left * scale, top * scale,
                   (right - left) * scale, (bottom - top) * scale)

    def __repr__(self):
        return f"TextBlock({self.text!r}, {self.left},{self.top},{self.width}x{self.height})"


def group_paragraphs(blocks, gap_ratio=0.7, overlap_ratio=0.15):
    """
    把行块聚成段落，供悬停高亮与按段取词使用

    相邻两行同时满足"垂直间距小于行高的 gap_ratio 倍"和"水平投影有重叠"
    时并入同一段，据此区分同一行的两列与同一段的换行。

    Args:
        blocks (list[TextBlock]): 行块列表（可为任意坐标顺序）
        gap_ratio (float): 行距阈值系数，越大越容易并段
        overlap_ratio (float): 水平重叠占较窄行宽的比例下限

    Returns:
        list[list[int]]: 每段对应 blocks 下标列表，按阅读顺序排列
    """
    if not blocks:
        return []

    order = sorted(range(len(blocks)), key=lambda i: (blocks[i].top, blocks[i].left))
    heights = sorted(blocks[i].height for i in order)
    line_height = heights[len(heights) // 2] or 12
    max_gap = line_height * gap_ratio

    groups = [[order[0]]]
    for index in order[1:]:
        current = blocks[index]
        group = groups[-1]
        previous = blocks[group[-1]]
        # 行距为负说明两行重叠，本就属于同一段，不必单独判断
        gap = current.top - previous.bottom
        if gap <= max_gap and _horizontally_related(previous, current, overlap_ratio):
            group.append(index)
        else:
            groups.append([index])
    return groups


def _horizontally_related(one, other, overlap_ratio):
    """两行的水平投影是否足够接近，用于排除左右分栏被并成一段"""
    overlap = min(one.right, other.right) - max(one.left, other.left)
    narrower = min(one.width, other.width) or 1
    if overlap / narrower >= overlap_ratio:
        return True
    # 无重叠但起始位置几乎对齐（如首行缩进为0的连续行），也视为同段
    return abs(one.left - other.left) <= (one.width + other.width) * 0.05
