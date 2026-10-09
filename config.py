#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理模块
负责读取、写入和管理应用配置
"""

import configparser
import contextlib
import logging
import os

from managers import app_paths

logger = logging.getLogger('screenshot-translation')

class ConfigManager:
    """
    配置管理器类
    负责处理配置文件的读写操作
    """

    def __init__(self, config_file=None):
        """
        初始化配置管理器

        Args:
            config_file (str): 配置文件路径；None时按运行形态自动定位
                （源码→项目根，exe→~/.screenshot-translation）
        """
        self.config_file = config_file or app_paths.config_file()
        self.config = configparser.ConfigParser()
        # 批量写盘：配置窗口保存时几十个 setter 只需落盘一次
        self._batch_depth = 0
        self._pending_changes = False
        self._load_config()

    @contextlib.contextmanager
    def batch(self):
        """
        批量修改上下文：块内的 setter 只改内存，退出时统一落盘一次

        配置窗口一次"保存"会接连调用十几个 setter，逐个写文件既慢又
        增加写坏文件的窗口期；套上本上下文后只写一次。
        """
        self._batch_depth += 1
        try:
            yield self
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0 and self._pending_changes:
                self._write_config()

    def _load_config(self):
        """
        加载配置文件

        配置文件不存在时只把默认值装进内存，不落盘：用户什么都没改就丢一个
        config.ini 出来属于“遗留文件”，而这套默认值本来也不需要持久化——
        每个选项都有 fallback。真正改动配置（点保存、注册快捷键）时才写盘。
        """
        if os.path.exists(self.config_file):
            try:
                self.config.read(self.config_file, encoding='utf-8')
                logger.info(f"配置文件加载成功: {self.config_file}")
                # 旧配置文件自动迁移：补充 [OCR]、[Overlay] 与 [UI] 段
                migrated = False
                if not self.config.has_section('OCR'):
                    self._set_ocr_defaults()
                    migrated = True
                if not self.config.has_section('Overlay'):
                    self._set_overlay_defaults()
                    migrated = True
                if not self.config.has_section('UI'):
                    self._set_ui_defaults()
                    migrated = True
                if migrated:
                    self.save_config()
            except Exception as e:
                logger.error(f"配置文件加载失败，回退默认配置: {e}")
                self._set_defaults()
                # 文件已存在但读坏了：用默认值把它修好，
                # 否则每次启动都当成坏配置，用户此前的设置也再无机会恢复
                self.save_config()
        else:
            logger.info(f"配置文件不存在，本次使用内存默认配置: {self.config_file}")
            self._set_defaults()

    def _set_defaults(self):
        """
        设置默认配置值（只写内存，不落盘）

        不在这里调 save_config：默认配置随用随有，写盘会让“从没改过设置”
        的用户也留下一份 config.ini，且掩盖“配置是否被改过”这个信息。
        """
        # 翻译API配置
        if not self.config.has_section('Translation'):
            self.config.add_section('Translation')

        self.config.set('Translation', 'api_type', 'edge_free')  # 默认使用Edge免费翻译（微软翻译，国内可直连）
        self.config.set('Translation', 'libretranslate_url', 'https://libretranslate.com/translate')
        self.config.set('Translation', 'fallback_api_type', '')  # 主API失败后的降级API（空为不降级）
        self.config.set('Translation', 'target_lang', 'zh')  # 目标语言
        self.config.set('Translation', 'bing_api_key', '')
        self.config.set('Translation', 'baidu_api_key', '')
        self.config.set('Translation', 'baidu_app_id', '')
        self.config.set('Translation', 'youdao_api_key', '')
        self.config.set('Translation', 'youdao_app_id', '')
        # LLM（OpenAI兼容接口）翻译配置
        self.config.set('Translation', 'llm_base_url', 'https://api.openai.com/v1')
        self.config.set('Translation', 'llm_api_key', '')
        self.config.set('Translation', 'llm_model', 'gpt-4o-mini')

        # 快捷键配置
        if not self.config.has_section('Hotkey'):
            self.config.add_section('Hotkey')

        self.config.set('Hotkey', 'global_hotkey', 'alt+z')  # 默认快捷键

        # 启动行为配置
        if not self.config.has_section('Startup'):
            self.config.add_section('Startup')

        self.config.set('Startup', 'minimize_to_tray', 'true')  # 默认最小化到托盘

        # 界面外观配置
        self._set_ui_defaults()
        # 截图遮罩默认配置
        self._set_overlay_defaults()
        # OCR 默认配置
        self._set_ocr_defaults()

    def _set_ocr_defaults(self):
        """
        设置OCR段默认配置值

        只碰 [OCR] 段：任何 setter 都不该顺手重置其它配置段的值，
        否则用户调过的遮罩参数会在写 OCR 选项时被悄悄还原成默认值。
        """
        if not self.config.has_section('OCR'):
            self.config.add_section('OCR')

        self.config.set('OCR', 'ocr_type', 'tesseract')  # 默认本地Tesseract
        self.config.set('OCR', 'tesseract_langs', 'eng+chi_sim')  # 识别语言组合
        self.config.set('OCR', 'baidu_ocr_api_key', '')
        self.config.set('OCR', 'baidu_ocr_secret_key', '')

    def _set_overlay_defaults(self):
        """
        设置 [Overlay] 段默认配置值
        """
        if not self.config.has_section('Overlay'):
            self.config.add_section('Overlay')

        self.config.set('Overlay', 'snap_threshold', '12')  # 磁吸阈值（像素）
        self.config.set('Overlay', 'dim_opacity', '90')     # 暗角透明度 (0~180)

    def _set_ui_defaults(self):
        """设置 [UI] 段默认配置值（界面主题）"""
        if not self.config.has_section('UI'):
            self.config.add_section('UI')

        self.config.set('UI', 'theme', 'dark')  # dark / light

    def save_config(self):
        """
        保存配置到文件（batch 上下文中延迟到退出时统一写盘）

        写入后尽量收紧文件权限（POSIX 下 0o600）。config.ini 可能包含明文密钥，
        更安全的做法是通过环境变量注入密钥（读取侧已优先环境变量），
        并将 config.ini 排除出版本库与分发包。
        """
        if self._batch_depth > 0:
            self._pending_changes = True
            return
        self._write_config()

    def _write_config(self):
        """
        真正落盘：先写同目录临时文件再原子替换

        直接覆盖原文件时，若写到一半被中断（退出、断电、杀进程）会留下
        半截文件，下次启动被当成损坏配置整份回退默认值——密钥与快捷键全丢。
        """
        self._pending_changes = False
        tmp_file = f"{self.config_file}.tmp"
        try:
            with open(tmp_file, 'w', encoding='utf-8') as f:
                self.config.write(f)
            os.replace(tmp_file, self.config_file)
            try:
                os.chmod(self.config_file, 0o600)
            except OSError:
                # Windows 等平台 chmod 语义有限，失败不影响主流程
                pass
            logger.debug(f"配置保存成功: {self.config_file}")
        except Exception as e:
            logger.error(f"配置保存失败: {e}")
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except OSError:
                pass

    # 翻译API相关配置
    def get_api_type(self):
        """
        获取翻译API类型

        Returns:
            str: API类型
        """
        return self.config.get('Translation', 'api_type', fallback='edge_free')

    def set_api_type(self, api_type):
        """
        设置翻译API类型

        Args:
            api_type (str): API类型
        """
        self.config.set('Translation', 'api_type', api_type)
        self.save_config()

    def get_fallback_api_type(self):
        """
        获取降级翻译API类型（主API失败后自动切换）

        Returns:
            str: API类型
        """
        return self.config.get('Translation', 'fallback_api_type', fallback='')

    def set_fallback_api_type(self, api_type):
        """
        设置降级翻译API类型

        Args:
            api_type (str): API类型
        """
        self.config.set('Translation', 'fallback_api_type', api_type)
        self.save_config()

    def get_target_lang(self):
        """
        获取目标语言代码

        Returns:
            str: 目标语言代码，如 zh / en / ja
        """
        return self.config.get('Translation', 'target_lang', fallback='zh')

    def set_target_lang(self, target_lang):
        """
        设置目标语言代码

        Args:
            target_lang (str): 目标语言代码
        """
        self.config.set('Translation', 'target_lang', target_lang)
        self.save_config()

    def get_libretranslate_url(self):
        """
        获取LibreTranslate API地址

        Returns:
            str: API地址
        """
        return self.config.get('Translation', 'libretranslate_url', fallback='https://libretranslate.com/translate')

    def set_libretranslate_url(self, url):
        """
        设置LibreTranslate API地址

        Args:
            url (str): API地址
        """
        self.config.set('Translation', 'libretranslate_url', url)
        self.save_config()

    # OCR引擎相关配置
    def get_ocr_type(self):
        """
        获取OCR引擎类型

        Returns:
            str: OCR引擎类型
        """
        return self.config.get('OCR', 'ocr_type', fallback='tesseract')

    def set_ocr_type(self, ocr_type):
        """
        设置OCR引擎类型

        Args:
            ocr_type (str): OCR引擎类型
        """
        if not self.config.has_section('OCR'):
            self._set_ocr_defaults()
        self.config.set('OCR', 'ocr_type', ocr_type)
        self.save_config()

    def get_ocr_option(self, option, fallback=''):
        """
        读取 [OCR] 段的配置项

        Args:
            option (str): 配置项名
            fallback (str): 未设置时的默认值

        Returns:
            str: 配置值
        """
        return self.config.get('OCR', option, fallback=fallback)

    def set_ocr_option(self, option, value):
        """
        写入 [OCR] 段的配置项并保存

        Args:
            option (str): 配置项名
            value (str): 配置值
        """
        if not self.config.has_section('OCR'):
            self._set_ocr_defaults()
        self.config.set('OCR', option, value)
        self.save_config()

    # 翻译API通用配置项（插件自定义字段的后备读写通道）
    def get_translation_option(self, option, fallback=''):
        """
        读取 [Translation] 段的任意配置项

        provider 声明的 config_fields 若没有专用 getter（扩展 provider 的新字段），
        配置界面用本方法读值，保证新插件不动主程序也能保存配置。

        Args:
            option (str): 配置项名
            fallback (str): 未设置时的默认值

        Returns:
            str: 配置值
        """
        return self.config.get('Translation', option, fallback=fallback)

    def set_translation_option(self, option, value):
        """
        写入 [Translation] 段的任意配置项并保存

        Args:
            option (str): 配置项名
            value (str): 配置值
        """
        if not self.config.has_section('Translation'):
            self.config.add_section('Translation')
        self.config.set('Translation', option, value)
        self.save_config()

    # 截图遮罩配置
    def get_overlay_option(self, option, fallback=''):
        """
        读取 [Overlay] 段的配置项

        Args:
            option (str): 配置项名
            fallback (str): 未设置时的默认值

        Returns:
            str: 配置值
        """
        return self.config.get('Overlay', option, fallback=fallback)

    def set_overlay_option(self, option, value):
        """
        写入 [Overlay] 段的配置项并保存

        Args:
            option (str): 配置项名
            value (str): 配置值
        """
        if not self.config.has_section('Overlay'):
            self._set_overlay_defaults()
        self.config.set('Overlay', option, value)
        self.save_config()

    # 界面外观配置
    def get_theme(self):
        """
        获取界面主题标识

        Returns:
            str: 'dark' 或 'light'；非法值回退 dark
        """
        raw = self.config.get('UI', 'theme', fallback='dark')
        value = str(raw or '').strip().lower()
        return value if value in ('dark', 'light') else 'dark'

    def set_theme(self, theme):
        """
        设置界面主题并保存

        Args:
            theme (str): 'dark' / 'light'
        """
        value = str(theme or '').strip().lower()
        if value not in ('dark', 'light'):
            value = 'dark'
        if not self.config.has_section('UI'):
            self._set_ui_defaults()
        self.config.set('UI', 'theme', value)
        self.save_config()

    # 快捷键配置
    def get_global_hotkey(self):
        """
        获取全局快捷键

        Returns:
            str: 快捷键组合
        """
        return self.config.get('Hotkey', 'global_hotkey', fallback='alt+z')

    def set_global_hotkey(self, hotkey):
        """
        设置全局快捷键

        Args:
            hotkey (str): 快捷键组合
        """
        self.config.set('Hotkey', 'global_hotkey', hotkey)
        self.save_config()

    # 启动行为配置
    def is_minimize_to_tray(self):
        """
        获取是否最小化到托盘

        Returns:
            bool: 是否最小化到托盘
        """
        return self.config.getboolean('Startup', 'minimize_to_tray', fallback=True)

    def set_minimize_to_tray(self, minimize):
        """
        设置是否最小化到托盘

        Args:
            minimize (bool): 是否最小化到托盘
        """
        self.config.set('Startup', 'minimize_to_tray', str(minimize).lower())
        self.save_config()

    # 其他API配置方法
    #
    # 密钥优先从环境变量读取，未设置时回退到config.ini。
    # config.ini 中的密钥为明文存储，请勿随项目分发，建议通过环境变量注入。
    def _get_secret(self, env_name, option):
        """
        读取敏感配置项，优先使用环境变量

        Args:
            env_name (str): 环境变量名
            option (str): Translation段中的配置项名

        Returns:
            str: 密钥值，均未设置时返回空字符串
        """
        return os.environ.get(env_name) or self.config.get('Translation', option, fallback='')

    def get_bing_api_key(self):
        """
        获取必应翻译API密钥

        Returns:
            str: API密钥，未设置时返回空字符串
        """
        return self._get_secret('BING_API_KEY', 'bing_api_key')

    def set_bing_api_key(self, api_key):
        """
        设置必应翻译API密钥并保存配置

        Args:
            api_key (str): API密钥
        """
        self.config.set('Translation', 'bing_api_key', api_key)
        self.save_config()

    def get_baidu_api_info(self):
        """
        获取百度翻译API信息

        Returns:
            tuple[str, str]: (app_id, api_key)，未设置的项为空字符串
        """
        app_id = self._get_secret('BAIDU_APP_ID', 'baidu_app_id')
        api_key = self._get_secret('BAIDU_API_KEY', 'baidu_api_key')
        return app_id, api_key

    def set_baidu_api_info(self, app_id, api_key):
        """
        设置百度翻译API信息并保存配置

        Args:
            app_id (str): 应用ID
            api_key (str): 应用密钥
        """
        self.config.set('Translation', 'baidu_app_id', app_id)
        self.config.set('Translation', 'baidu_api_key', api_key)
        self.save_config()

    def get_youdao_api_info(self):
        """
        获取有道翻译API信息

        Returns:
            tuple[str, str]: (app_id, api_key)，未设置的项为空字符串
        """
        app_id = self._get_secret('YOUDAO_APP_ID', 'youdao_app_id')
        api_key = self._get_secret('YOUDAO_API_KEY', 'youdao_api_key')
        return app_id, api_key

    def set_youdao_api_info(self, app_id, api_key):
        """
        设置有道翻译API信息并保存配置

        Args:
            app_id (str): 应用ID
            api_key (str): 应用密钥
        """
        self.config.set('Translation', 'youdao_app_id', app_id)
        self.config.set('Translation', 'youdao_api_key', api_key)
        self.save_config()

    # LLM（OpenAI兼容接口）翻译配置
    # 注意：llm_base_url 和 llm_model 非密钥，直接读写配置文件
    def get_llm_base_url(self):
        """
        获取LLM API地址

        Returns:
            str: API地址
        """
        return self.config.get('Translation', 'llm_base_url', fallback='https://api.openai.com/v1')

    def set_llm_base_url(self, base_url):
        """
        设置LLM API地址

        Args:
            base_url (str): API地址
        """
        self.config.set('Translation', 'llm_base_url', base_url)
        self.save_config()

    def get_llm_api_key(self):
        """
        获取LLM API密钥，优先环境变量

        Returns:
            str: API密钥，未设置时返回空字符串
        """
        return self._get_secret('LLM_API_KEY', 'llm_api_key')

    def set_llm_api_key(self, api_key):
        """
        设置LLM API密钥并保存配置

        Args:
            api_key (str): API密钥
        """
        self.config.set('Translation', 'llm_api_key', api_key)
        self.save_config()

    def get_llm_model(self):
        """
        获取LLM模型名称

        Returns:
            str: 模型名称
        """
        return self.config.get('Translation', 'llm_model', fallback='gpt-4o-mini')

    def set_llm_model(self, model):
        """
        设置LLM模型名称

        Args:
            model (str): 模型名称
        """
        self.config.set('Translation', 'llm_model', model)
        self.save_config()

if __name__ == '__main__':
    # 测试配置管理器
    config = ConfigManager()
    print(f"当前API类型: {config.get_api_type()}")
    print(f"当前快捷键: {config.get_global_hotkey()}")
    print(f"是否最小化到托盘: {config.is_minimize_to_tray()}")

    # 测试修改配置
    # config.set_api_type('bing')
    # config.set_global_hotkey('ctrl+alt+s')
    # config.set_minimize_to_tray(False)
    # print("配置已修改")
    # print(f"新API类型: {config.get_api_type()}")
    # print(f"新快捷键: {config.get_global_hotkey()}")
    # print(f"新启动行为: {config.is_minimize_to_tray()}")
