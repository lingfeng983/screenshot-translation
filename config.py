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
import shutil
import threading
from datetime import datetime

from managers import app_paths

logger = logging.getLogger('screenshot-translation')

# [Translation] 段的隐私相关开关及默认值（老配置升级时按这张表补齐）
_PRIVACY_DEFAULTS = {
    'cache_enabled': 'true',
    'clear_cache_on_exit': 'false',
    'allow_free_fallback': 'false',
    'warm_up': 'false',
}


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
        # 关闭 BasicInterpolation：文件名模板里的 %Y%m%d 等 strftime 占位符
        # 会被当成插值语法直接抛 ValueError，配置值应当一律按字面读写
        self.config = configparser.ConfigParser(interpolation=None)
        # 写侧锁：池线程读、主线程写并发存在，写盘与批量改值必须串行
        self._lock = threading.RLock()
        # 批量写盘：配置窗口保存时几十个 setter 只需落盘一次
        self._batch_depth = 0
        self._pending_changes = False
        # 每层 batch 的 persist 标志：只禁止「不落盘」嵌套在「会落盘」的
        # 外层里，允许同为不落盘的嵌套（那种组合没有写盘冲突）
        self._batch_persist = []
        # 最近一次落盘是否成功：写盘失败会被 _write_config 兜住（不能让设置
        # 窗口直接崩），但用户必须知道改动没进文件
        self.write_ok = True
        # 配置损坏被默认配置覆盖时留给用户看的说明（由主程序弹托盘通知）
        self.recovery_notice = ''
        self._load_config()

    @contextlib.contextmanager
    def batch(self, persist=True):
        """
        批量修改上下文：块内的 setter 只改内存，退出时统一落盘一次

        配置窗口一次"保存"会接连调用十几个 setter，逐个写文件既慢又
        增加写坏文件的窗口期；套上本上下文后只写一次。

        Args:
            persist (bool): 退出时是否写盘。False 用于"测试翻译"这类只想让
                未保存的表单值当场生效的场合——没点保存就改用户的配置文件
                是不该发生的。不能嵌套在会落盘的 batch() 里（整份内存配置
                会被一起写盘），嵌套时直接抛 RuntimeError；同为不落盘的
                嵌套是允许的。内层批次不写盘，落盘统一由最外层负责。
        """
        if not persist and any(self._batch_persist):
            # 外层批次退出时会把整个 config 对象序列化落盘，内层「只活内存」
            # 的改动无法被单独排除。与其静默写进文件，不如当场拦下。
            raise RuntimeError(
                "batch(persist=False) 不能嵌套在会落盘的 batch() 内："
                "外层落盘会把整个内存配置一起写盘，无法只排除内层的改动")
        self._batch_persist.append(persist)
        self._batch_depth += 1
        try:
            yield self
        finally:
            self._batch_persist.pop()
            self._batch_depth -= 1
            if self._batch_depth == 0:
                if persist and self._pending_changes:
                    self._write_config()
                elif not persist:
                    # 不落盘的这批改动只活在内存里，挂着的"待写盘"标记要一起丢掉，
                    # 否则下次任何一次 save_config 会把它们偷偷补写到盘上
                    self._pending_changes = False

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
                # 旧配置文件自动迁移：七个段逐个补齐。缺段的危害不只是读不到值
                # （读侧有 fallback 兜着），而是 setter 里的 config.set 会直接
                # 抛 NoSectionError——用户点一次“保存”就崩一次。
                # 两步都要跑：短路成 `or` 会让缺段的老文件这一轮跳过值迁移，
                # 死键与非法值要等到下次启动才被清掉
                sections_changed = self._migrate_sections()
                values_changed = self._migrate_values()
                if sections_changed or values_changed:
                    self.save_config()
            except Exception as e:
                logger.error(f"配置文件加载失败，回退默认配置: {e}")
                # 先留一份再修：备份名带时间戳与 PID，连着坏两次也不会把上一份
                # 还能恢复的配置顶掉（旧实现固定写 config.ini.bak，第二次损坏
                # 就把唯一一份备份覆盖成了同样坏的内容）。文件里有明文密钥，
                # 覆盖前必须让用户有机会找回。
                # 微秒 + PID：同一秒内连续损坏两次也不会互相覆盖
                backup = (f"{self.config_file}."
                          f"{datetime.now():%Y%m%d-%H%M%S-%f}-{os.getpid()}.bak")
                saved = ''
                with contextlib.suppress(OSError):
                    shutil.copy2(self.config_file, backup)
                    saved = backup
                self.recovery_notice = (
                    '配置文件损坏，已用默认配置覆盖。'
                    + (f'原文件已备份到 {saved}，可手动找回密钥与快捷键。'
                       if saved else '原文件备份失败，请检查目录权限。'))
                logger.warning(self.recovery_notice)
                # 半截解析会留下几个"存在但缺键"的段，而各段的默认值填充器
                # 见段即跳，修出来的文件会整段整段缺键；先扔掉半成品
                self.config = configparser.ConfigParser(interpolation=None)
                self._set_defaults()
                # 文件已存在但读坏了：用默认值把它修好，
                # 否则每次启动都当成坏配置，用户此前的设置也再无机会恢复
                self.save_config()
        else:
            logger.info(f"配置文件不存在，本次使用内存默认配置: {self.config_file}")
            self._set_defaults()

    # 全部配置段与其默认值填充器；老文件缺哪段就补哪段
    _SECTION_DEFAULTS = (
        ('Translation', '_set_translation_defaults'),
        ('Hotkey', '_set_hotkey_defaults'),
        ('Startup', '_set_startup_defaults'),
        ('Update', '_set_update_defaults'),
        ('Overlay', '_set_overlay_defaults'),
        ('Capture', '_set_capture_defaults'),
        ('OCR', '_set_ocr_defaults'),
    )

    def _migrate_sections(self):
        """
        补齐缺失的配置段并填入该段默认值

        Returns:
            bool: 是否有段落被补齐（需要落盘，避免每次启动重复迁移）
        """
        migrated = False
        for section, filler in self._SECTION_DEFAULTS:
            if not self.config.has_section(section):
                getattr(self, filler)()
                migrated = True
        return migrated

    def _migrate_values(self):
        """
        迁移旧版本留在配置里的引擎名与废弃选项

        - OCR 已固定为本地 RapidOCR：ocr_type、旧 Tesseract 语言与云端密钥
          都已无对应实现，留着只会让人以为还能配
        - 旧的 LLM 配置同样已无实现
        - 长截图不再注入滚轮：scroll_step / scroll_delay_ms 已无实现
        - 目标语言收窄为中文/英文，旧值（如 ja）回落到中文
        - 界面固定浅色，旧配置里的主题选择不再生效

        Returns:
            bool: 是否有配置项被改写（需要落盘）
        """
        migrated = False
        # 界面固定浅色：旧配置里的主题选择已无意义
        if self.config.has_option('UI', 'theme'):
            self.config.remove_option('UI', 'theme')
            migrated = True
        if self.config.has_section('UI') and not self.config.options('UI'):
            self.config.remove_section('UI')
        # OCR 已固定为本地 RapidOCR：旧配置里的引擎选择与云端密钥都不再有任何作用
        for option in ('ocr_type', 'tesseract_langs',
                       'baidu_ocr_api_key', 'baidu_ocr_secret_key'):
            if self.config.has_option('OCR', option):
                self.config.remove_option('OCR', option)
                migrated = True
        for option in ('llm_base_url', 'llm_api_key', 'llm_model'):
            if self.config.has_option('Translation', option):
                self.config.remove_option('Translation', option)
                migrated = True
        # 长截图改成用户自己滚动：注入滚轮的步长与延时已无对应实现
        for option in ('scroll_step', 'scroll_delay_ms'):
            if self.config.has_option('Capture', option):
                self.config.remove_option('Capture', option)
                migrated = True
        if not self.config.has_option('OCR', 'rapidocr_score_threshold'):
            self.config.set('OCR', 'rapidocr_score_threshold', '0.5')
            migrated = True
        if self.config.has_option('Translation', 'target_lang'):
            if self.config.get('Translation', 'target_lang').strip().lower() \
                    not in ('zh', 'en'):
                self.config.set('Translation', 'target_lang', 'zh')
                migrated = True
        # 老配置里 [Translation] 段已经存在，_set_translation_defaults 见段即
        # 跳，新加的隐私开关必须在这里逐项补齐，否则读侧永远拿 fallback。
        self._ensure_section('Translation')
        for option, default in _PRIVACY_DEFAULTS.items():
            if not self.config.has_option('Translation', option):
                self.config.set('Translation', option, default)
                migrated = True
        return migrated

    def _ensure_section(self, section):
        """
        保证配置段存在（缺段时补该段默认值）

        Args:
            section (str): 段名

        Returns:
            bool: True 表示本次新建了该段
        """
        if self.config.has_section(section):
            return False
        getattr(self, dict(self._SECTION_DEFAULTS)[section])()
        return True

    def _set(self, section, option, value):
        """
        写单个配置项：缺段自动补，写入串行化

        直接 self.config.set 在缺段的旧配置上会抛 NoSectionError；配置段的
        默认值只在“文件不存在”分支里建过，手改或被裁剪过的 ini 随时会缺。
        补建只改内存，落盘由调用方的 save_config 完成。

        Args:
            section (str): 段名
            option (str): 配置项名
            value (str): 值
        """
        with self._lock:
            self._ensure_section(section)
            self.config.set(section, option, str(value))

    def _set_defaults(self):
        """
        设置默认配置值（只写内存，不落盘）

        不在这里调 save_config：默认配置随用随有，写盘会让“从没改过设置”
        的用户也留下一份 config.ini，且掩盖“配置是否被改过”这个信息。
        """
        # 翻译API配置
        self._set_translation_defaults()
        # 快捷键配置
        self._set_hotkey_defaults()
        # 启动行为配置
        self._set_startup_defaults()
        # 更新检查配置
        self._set_update_defaults()
        # 截图遮罩默认配置
        self._set_overlay_defaults()
        # 纯截图（编辑/输出/滚动截图）默认配置
        self._set_capture_defaults()
        # OCR 默认配置
        self._set_ocr_defaults()

    def _set_translation_defaults(self):
        """
        设置 [Translation] 段默认配置值（段已存在时原样返回，不重置用户值）
        """
        if not self.config.has_section('Translation'):
            self.config.add_section('Translation')
        else:
            return

        self.config.set('Translation', 'api_type', 'edge_free')  # 默认使用Edge免费翻译（微软翻译，国内可直连）
        self.config.set('Translation', 'libretranslate_url', 'https://libretranslate.com/translate')
        self.config.set('Translation', 'fallback_api_type', '')  # 主API失败后的降级API（空为不降级）
        self.config.set('Translation', 'target_lang', 'zh')  # 目标语言
        self.config.set('Translation', 'bing_api_key', '')
        self.config.set('Translation', 'baidu_api_key', '')
        self.config.set('Translation', 'baidu_app_id', '')
        self.config.set('Translation', 'youdao_api_key', '')
        self.config.set('Translation', 'youdao_app_id', '')
        # 隐私选项：缓存默认开（历史行为），其余一律默认关。
        # 免费兜底与启动预热都会在用户没截图时把文本发给第三方接口，
        # 必须显式同意才启用。
        for option, default in _PRIVACY_DEFAULTS.items():
            self.config.set('Translation', option, default)

    def _set_hotkey_defaults(self):
        """设置 [Hotkey] 段默认配置值"""
        if not self.config.has_section('Hotkey'):
            self.config.add_section('Hotkey')
        else:
            return

        self.config.set('Hotkey', 'global_hotkey', 'alt+z')  # 默认快捷键
        self.config.set('Hotkey', 'capture_hotkey', 'ctrl+alt+z')  # 纯截图快捷键
        self.config.set('Hotkey', 'translate_hotkey', 'ctrl+alt+t')  # 文本翻译页快捷键
        self.config.set('Hotkey', 'fullscreen_hotkey', 'ctrl+alt+f')  # 全屏截图
        self.config.set('Hotkey', 'window_hotkey', 'ctrl+alt+w')  # 活动窗口截图
        self.config.set('Hotkey', 'repeat_hotkey', 'ctrl+alt+r')  # 重复上次区域

    def _set_startup_defaults(self):
        """设置 [Startup] 段默认配置值"""
        if not self.config.has_section('Startup'):
            self.config.add_section('Startup')
        else:
            return

        self.config.set('Startup', 'minimize_to_tray', 'true')  # 默认最小化到托盘
        self.config.set('Startup', 'auto_start', 'false')       # 默认不开机自启动

    def _set_update_defaults(self):
        """设置 [Update] 段默认配置值"""
        if not self.config.has_section('Update'):
            self.config.add_section('Update')
        else:
            return

        self.config.set('Update', 'auto_check', 'true')       # 启动时检查更新
        # 已提醒过的版本：同一个版本只弹一次托盘通知，避免每次启动都打扰
        self.config.set('Update', 'last_notified_version', '')

    def _set_ocr_defaults(self):
        """
        设置OCR段默认配置值

        只碰 [OCR] 段：任何 setter 都不该顺手重置其它配置段的值，
        否则用户调过的遮罩参数会在写 OCR 选项时被悄悄还原成默认值。
        """
        if not self.config.has_section('OCR'):
            self.config.add_section('OCR')
        else:
            return

        # 引擎固定为本地 RapidOCR，[OCR] 段只保留识别参数
        self.config.set('OCR', 'rapidocr_score_threshold', '0.5')  # 行置信度下限

    def _set_overlay_defaults(self):
        """
        设置 [Overlay] 段默认配置值
        """
        if not self.config.has_section('Overlay'):
            self.config.add_section('Overlay')
        else:
            return

        self.config.set('Overlay', 'snap_threshold', '12')  # 磁吸阈值（像素）
        self.config.set('Overlay', 'dim_opacity', '90')     # 暗角透明度 (0~180)

    def _set_capture_defaults(self):
        """设置 [Capture] 段默认配置值（纯截图的标注与输出参数）"""
        if not self.config.has_section('Capture'):
            self.config.add_section('Capture')
        else:
            return

        self.config.set('Capture', 'format', 'png')             # png/jpg/webp
        self.config.set('Capture', 'jpg_quality', '90')         # 1~100
        self.config.set('Capture', 'save_dir', '')              # 空=系统图片目录
        self.config.set('Capture', 'filename_template', '截图_%Y%m%d_%H%M%S')
        self.config.set('Capture', 'quick_save', 'false')       # true 时不弹保存框
        self.config.set('Capture', 'after_save', 'none')        # none/copy/close/copy_close
        self.config.set('Capture', 'pen_color', '#ff3b30')
        self.config.set('Capture', 'pen_width', '4')
        self.config.set('Capture', 'font_size', '18')
        self.config.set('Capture', 'magnifier', 'true')         # 框选时显示放大镜
        self.config.set('Capture', 'scroll_poll_ms', '120')     # 长截图抓帧间隔
        self.config.set('Capture', 'scroll_max_height', '12000')  # 长图高度上限(px)

    def save_config(self):
        """
        保存配置到文件（batch 上下文中延迟到退出时统一写盘）

        写入后尽量收紧文件权限（POSIX 下 0o600）。config.ini 可能包含明文密钥，
        更安全的做法是通过环境变量注入密钥（读取侧已优先环境变量），
        并将 config.ini 排除出版本库与分发包。

        Returns:
            bool: 配置当前处于"已全部落盘"的状态（batch 里延后写盘时也算成功）
        """
        if self._batch_depth > 0:
            # 内层批次一律不写盘，只记「待写盘」；最外层退出时统一落盘，
            # 若最外层是 persist=False 则会清掉这个标记。嵌套冲突由
            # batch() 的守卫挡住，不需要在这里再区分内层。
            self._pending_changes = True
            return True
        return self._write_config()

    def _write_config(self):
        """
        真正落盘：先写同目录临时文件再原子替换

        直接覆盖原文件时，若写到一半被中断（退出、断电、杀进程）会留下
        半截文件，下次启动被当成损坏配置整份回退默认值——密钥与快捷键全丢。
        临时名带进程与线程标识：多实例共用固定 ".tmp" 会互相顶替。

        Returns:
            bool: 是否写成功。失败时保持"待落盘"标记，下一次保存会连带
                这次的改动一起重写——吞掉失败等于让用户以为设置存住了
        """
        tmp_file = (f"{self.config_file}.{os.getpid()}.{threading.get_ident()}.tmp")
        with self._lock:
            try:
                with open(tmp_file, 'w', encoding='utf-8') as f:
                    self.config.write(f)
                try:
                    # 收紧权限要在 replace 之前：替换后再 chmod，密钥有一段
                    # 按默认权限可读的窗口期
                    os.chmod(tmp_file, 0o600)
                except OSError:
                    # Windows 等平台 chmod 语义有限，失败不影响主流程
                    pass
                os.replace(tmp_file, self.config_file)
                self._pending_changes = False
                self.write_ok = True
                logger.debug(f"配置保存成功: {self.config_file}")
                return True
            except Exception as e:
                logger.error(f"配置保存失败: {e}")
                self.write_ok = False
                with contextlib.suppress(OSError):
                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)
                return False

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
        self._set('Translation', 'api_type', api_type)
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
        self._set('Translation', 'fallback_api_type', api_type)
        self.save_config()

    def get_target_lang(self):
        """
        获取目标语言代码

        Returns:
            str: 目标语言代码：zh / en
        """
        value = self.config.get('Translation', 'target_lang', fallback='zh')
        value = str(value or '').strip().lower()
        return value if value in ('zh', 'en') else 'zh'

    def set_target_lang(self, target_lang):
        """
        设置目标语言代码

        Args:
            target_lang (str): 目标语言代码
        """
        self._set('Translation', 'target_lang', target_lang)
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
        self._set('Translation', 'libretranslate_url', url)
        self.save_config()

    # OCR 识别参数（引擎固定为本地 RapidOCR，不再提供选择/密钥配置）
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
        self._set('OCR', option, value)
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
        self._set('Translation', option, value)
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
        self._set('Overlay', option, value)
        self.save_config()

    # 纯截图配置
    def get_capture_option(self, option, fallback=''):
        """
        读取 [Capture] 段的配置项

        Args:
            option (str): 配置项名
            fallback (str): 未设置时的默认值

        Returns:
            str: 配置值
        """
        return self.config.get('Capture', option, fallback=fallback)

    def set_capture_option(self, option, value):
        """写入 [Capture] 段的配置项并保存"""
        self._set('Capture', option, value)
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
        self._set('Hotkey', 'global_hotkey', hotkey)
        self.save_config()

    def get_capture_hotkey(self):
        """
        获取纯截图（不翻译）的全局快捷键

        Returns:
            str: 快捷键组合
        """
        return self.config.get('Hotkey', 'capture_hotkey',
                               fallback='ctrl+alt+z')

    def set_capture_hotkey(self, hotkey):
        """
        设置纯截图（不翻译）的全局快捷键

        Args:
            hotkey (str): 快捷键组合
        """
        self._set('Hotkey', 'capture_hotkey', hotkey)
        self.save_config()

    def get_translate_hotkey(self):
        """
        获取打开文本翻译页的全局快捷键

        Returns:
            str: 快捷键组合
        """
        return self.config.get('Hotkey', 'translate_hotkey',
                               fallback='ctrl+alt+t')

    def set_translate_hotkey(self, hotkey):
        """
        设置打开文本翻译页的全局快捷键

        Args:
            hotkey (str): 快捷键组合
        """
        self._set('Hotkey', 'translate_hotkey', hotkey)
        self.save_config()

    def get_fullscreen_hotkey(self):
        """获取全屏截图的全局快捷键"""
        return self.config.get('Hotkey', 'fullscreen_hotkey',
                               fallback='ctrl+alt+f')

    def set_fullscreen_hotkey(self, hotkey):
        """设置全屏截图的全局快捷键"""
        self._set('Hotkey', 'fullscreen_hotkey', hotkey)
        self.save_config()

    def get_window_hotkey(self):
        """获取活动窗口截图的全局快捷键"""
        return self.config.get('Hotkey', 'window_hotkey',
                               fallback='ctrl+alt+w')

    def set_window_hotkey(self, hotkey):
        """设置活动窗口截图的全局快捷键"""
        self._set('Hotkey', 'window_hotkey', hotkey)
        self.save_config()

    def get_repeat_hotkey(self):
        """获取重复上次区域的全局快捷键"""
        return self.config.get('Hotkey', 'repeat_hotkey',
                               fallback='ctrl+alt+r')

    def set_repeat_hotkey(self, hotkey):
        """设置重复上次区域的全局快捷键"""
        self._set('Hotkey', 'repeat_hotkey', hotkey)
        self.save_config()

    # 启动行为配置
    def is_minimize_to_tray(self):
        """
        获取是否最小化到托盘

        Returns:
            bool: 是否最小化到托盘；值不是合法布尔字面量时按默认 true
        """
        try:
            return self.config.getboolean('Startup', 'minimize_to_tray',
                                          fallback=True)
        except ValueError:
            # fallback 只兜“缺项/缺段”，值写坏了（如 maybe=）照样抛 ValueError，
            # 而启动路径会因此跳过整段启动行为
            logger.warning("Startup.minimize_to_tray 不是合法布尔值，按默认 true 处理")
            return True

    def set_minimize_to_tray(self, minimize):
        """
        设置是否最小化到托盘

        Args:
            minimize (bool): 是否最小化到托盘
        """
        self._set('Startup', 'minimize_to_tray', str(minimize).lower())
        self.save_config()

    def is_auto_start(self):
        """
        获取是否开机自启动

        Returns:
            bool: 是否开机自启动；值不是合法布尔字面量时按默认 false
        """
        try:
            return self.config.getboolean('Startup', 'auto_start',
                                          fallback=False)
        except ValueError:
            logger.warning("Startup.auto_start 不是合法布尔值，按默认 false 处理")
            return False

    def set_auto_start(self, enabled):
        """
        设置是否开机自启动

        Args:
            enabled (bool): 是否开机自启动
        """
        self._set('Startup', 'auto_start', str(enabled).lower())
        self.save_config()

    # 更新检查配置
    def is_auto_check_update(self):
        """
        获取是否在启动时检查更新

        Returns:
            bool: 是否自动检查；值不是合法布尔字面量时按默认 true
        """
        try:
            return self.config.getboolean('Update', 'auto_check', fallback=True)
        except ValueError:
            logger.warning("Update.auto_check 不是合法布尔值，按默认 true 处理")
            return True

    def set_auto_check_update(self, enabled):
        """
        设置是否在启动时检查更新

        Args:
            enabled (bool): 是否自动检查
        """
        self._set('Update', 'auto_check', str(enabled).lower())
        self.save_config()

    def get_last_notified_version(self):
        """
        获取最近一次已推送过更新的版本号

        Returns:
            str: 版本号；从未推送过时为空字符串
        """
        return self.config.get('Update', 'last_notified_version', fallback='')

    def set_last_notified_version(self, version):
        """
        记录最近一次已推送过更新的版本号（同一版本不再重复通知）

        Args:
            version (str): 版本号
        """
        self._set('Update', 'last_notified_version', version)
        self.save_config()

    # ---- 隐私配置 ----
    #
    # 翻译缓存以明文保存截图识别出的原文与译文；免费兜底/预热会把文本发给
    # 第三方。四个开关都能在设置页「隐私」分组里改，读侧对缺项与坏值都
    # 按上表的默认值处理，老配置升级时由 _migrate_values 补齐。

    def _privacy_bool(self, option):
        """
        读取 [Translation] 里的布尔开关

        configparser 的 fallback 只兜"缺项/缺段"，值写坏了照样抛 ValueError；
        这里统一收成默认值，别让一处手误把整个启动流程带崩。
        """
        default = _PRIVACY_DEFAULTS[option].strip().lower() == 'true'
        try:
            return self.config.getboolean('Translation', option, fallback=default)
        except ValueError:
            logger.warning(f"Translation.{option} 不是合法布尔值，按默认 {default} 处理")
            return default

    def _set_privacy_bool(self, option, enabled):
        """写入 [Translation] 里的布尔开关"""
        self._set('Translation', option, str(bool(enabled)).lower())
        self.save_config()

    def is_translation_cache_enabled(self):
        """是否把原文/译文写入本地翻译缓存"""
        return self._privacy_bool('cache_enabled')

    def set_translation_cache_enabled(self, enabled):
        """开关本地翻译缓存（关闭时会由主程序清掉已落盘的明文）"""
        self._set_privacy_bool('cache_enabled', enabled)

    def is_clear_cache_on_exit(self):
        """退出时是否清空翻译缓存"""
        return self._privacy_bool('clear_cache_on_exit')

    def set_clear_cache_on_exit(self, enabled):
        """设置退出时清空翻译缓存"""
        self._set_privacy_bool('clear_cache_on_exit', enabled)

    def is_free_fallback_allowed(self):
        """主接口失败时是否允许自动改用免费第三方接口"""
        return self._privacy_bool('allow_free_fallback')

    def set_free_fallback_allowed(self, enabled):
        """设置是否允许自动切换到免费第三方接口"""
        self._set_privacy_bool('allow_free_fallback', enabled)

    def is_translation_warmup_enabled(self):
        """启动时是否预热当前免费翻译接口"""
        return self._privacy_bool('warm_up')

    def set_translation_warmup_enabled(self, enabled):
        """设置启动预热开关"""
        self._set_privacy_bool('warm_up', enabled)

    # 其他API配置方法
    #
    # 密钥优先从环境变量读取，未设置时回退到config.ini。
    # config.ini 中的密钥为明文存储，请勿随项目分发，建议通过环境变量注入。

    # 配置项 -> 对应环境变量名；设置界面据此判断某个值是"环境给的"还是
    # "配置文件里的"，前者不能回写进明文 config.ini
    SECRET_ENV_OPTIONS = {
        'bing_api_key': 'BING_API_KEY',
        'baidu_app_id': 'BAIDU_APP_ID',
        'baidu_api_key': 'BAIDU_API_KEY',
        'youdao_app_id': 'YOUDAO_APP_ID',
        'youdao_api_key': 'YOUDAO_API_KEY',
    }

    @classmethod
    def is_env_secret(cls, option):
        """
        该配置项当前是否由环境变量提供（环境变量的值存在且非空）

        Args:
            option (str): Translation 段中的配置项名

        Returns:
            bool: True 表示值来自环境，不该被写回明文配置
        """
        env_name = cls.SECRET_ENV_OPTIONS.get(option)
        return bool(env_name and os.environ.get(env_name))

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
        self._set('Translation', 'bing_api_key', api_key)
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
        self._set('Translation', 'baidu_app_id', app_id)
        self._set('Translation', 'baidu_api_key', api_key)
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
        self._set('Translation', 'youdao_app_id', app_id)
        self._set('Translation', 'youdao_api_key', api_key)
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
