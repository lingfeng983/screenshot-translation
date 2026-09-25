#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 翻译提供者（OpenAI 兼容接口）

支持任何 OpenAI /chat/completions 兼容服务：OpenAI、DeepSeek、
通义千问、Kimi、本地 Ollama（http://localhost:11434/v1）等，
填入对应的 base_url / api_key / model 即可。

翻译提示词要求只输出译文，适合截图OCR文本的场景。
"""

import json
import logging

from .base import TranslationProvider

logger = logging.getLogger('screenshot-translation')

DEFAULT_BASE_URL = 'https://api.openai.com/v1'
DEFAULT_MODEL = 'gpt-4o-mini'

# 语言代码 -> 名称，供提示词使用；未知代码原样返回
LANGUAGE_NAMES = {
    'zh': '中文', 'zh-TW': '繁体中文', 'en': '英语', 'ja': '日语',
    'ko': '韩语', 'fr': '法语', 'de': '德语', 'es': '西班牙语', 'ru': '俄语',
}


class LLMProvider(TranslationProvider):
    """通过 OpenAI 兼容的大模型接口进行翻译"""

    name = 'llm'
    display_name = '大模型(LLM)'
    config_fields = (
        # (字段名, UI显示名, 是否密码框)；字段名同时是 Translation 配置段中的选项名
        ('llm_base_url', 'API地址(Base URL)', False),
        ('llm_api_key', 'API密钥', True),
        ('llm_model', '模型名称', False),
    )

    def translate(self, text, source='auto', target='zh'):
        """
        调用LLM进行翻译

        Args:
            text (str): 要翻译的文本
            source (str): 源语言代码，'auto' 表示自动检测
            target (str): 目标语言代码

        Returns:
            str: 翻译后的文本
        """
        base_url = (self._get_secret('llm_base_url') or DEFAULT_BASE_URL).rstrip('/')
        api_key = self._get_secret('llm_api_key')
        model = self._get_secret('llm_model') or DEFAULT_MODEL

        if not api_key:
            # Ollama 等本地服务可以不填密钥，此处不做强制要求
            logger.info("LLM API密钥为空，尝试匿名请求（本地服务适用）")

        payload = {
            'model': model,
            'messages': self._build_messages(text, source, target),
            'temperature': 0.2,   # 翻译需要稳定输出，用较低温度
            'stream': False,
        }

        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        try:
            response = self.session.post(
                f'{base_url}/chat/completions',
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

            content = (data.get('choices') or [{}])[0].get('message', {}).get('content', '')
            content = self._strip_reasoning(content).strip()
            if not content:
                raise Exception(f"LLM返回空结果: {str(data)[:200]}")
            return content

        except Exception as e:
            logger.error(f"LLM翻译失败: {e}")
            raise Exception(f"LLM翻译失败: {e}") from e

    def _build_messages(self, text, source, target):
        """构造翻译提示词"""
        target_name = LANGUAGE_NAMES.get(target, target)
        if source and source != 'auto':
            source_name = LANGUAGE_NAMES.get(source, source)
            lang_hint = f"将以下{source_name}内容翻译成{target_name}"
        else:
            lang_hint = f"将以下内容翻译成{target_name}"

        system = (
            "你是一个专业翻译引擎。只输出译文本身，"
            "不要解释、不要添加注释或任何额外内容。"
            "保留原文的换行结构和数字、专有名词。"
        )
        return [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': f"{lang_hint}：\n{text}"},
        ]

    @staticmethod
    def _strip_reasoning(content):
        """
        去掉部分推理模型夹带的 <think>...</think> 段落，
        避免思考内容混入翻译结果
        """
        if '<think>' in content and '</think>' in content:
            before = content.split('<think>')[0]
            after = content.split('</think>')[-1]
            content = (before + after).strip()
        return content


def register(registry):
    """注册到全局provider注册表"""
    registry.register(LLMProvider)
