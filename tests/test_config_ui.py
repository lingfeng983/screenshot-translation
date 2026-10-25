#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置界面与遮罩结果的交互回归（pytest 用例）

覆盖面：
1. 翻译/OCR 下拉框的选项、默认值与逐个切换时的动态字段
2. 保存流程：写配置 + 同步翻译/OCR 管理器
3. 主题切换的真实渲染、扩展 provider 自定义字段持久化
4. 遮罩结果态：就地覆盖、工具条三态、段落卡片、降级与失败
5. 设置页排版：下拉框滚轮免疫、紧凑间距、标签右对齐、分组标题不压边框
6. 设置窗口单实例
7. 快捷键注册状态在设置页直接可见（被占用时给警示文案）
8. 副作用与生命周期：测试按钮不写配置文件、构建失败的实例不缓存、环境变量密钥
   不落明文、写盘失败点名告警
9. 日志：调用栈里的凭证脱敏、日志文件不可写时降级为控制台

默认全部离线可跑。两类用例要显式开启：
- 真连翻译接口的（`ST_RUN_ONLINE=1`）
- 真拉起 main.py 的（`ST_RUN_SMOKE=1`，会注册全局热键、驻留托盘）
"""

import os
import subprocess
import sys
import time

import pytest

from PyQt5.QtWidgets import QLineEdit

ONLINE = os.environ.get('ST_RUN_ONLINE') == '1'
SMOKE = os.environ.get('ST_RUN_SMOKE') == '1'

online = pytest.mark.skipif(not ONLINE, reason='需要真实网络：ST_RUN_ONLINE=1 才跑')
smoke = pytest.mark.skipif(not SMOKE, reason='会注册全局热键：ST_RUN_SMOKE=1 才跑')

# 各 provider/引擎应有的动态配置字段（字段名按 config_fields 顺序）
EXPECTED_API_FIELDS = {
    'baidu': ('baidu_app_id', 'baidu_api_key'),
    'youdao': ('youdao_app_id', 'youdao_api_key'),
    'bing': ('bing_api_key', 'bing_region'),
    'libretranslate': ('libretranslate_url',),
    'llm': ('llm_base_url', 'llm_api_key', 'llm_model'),
    'edge_free': (),
    'google_free': (),
}
EXPECTED_OCR_FIELDS = {
    'tesseract': ('tesseract_langs',),
    'baidu_cloud': ('baidu_ocr_api_key', 'baidu_ocr_secret_key'),
    'mock': (),
}


def spin(app, condition=None, timeout_ms=20000, duration_ms=150):
    """
    驱动事件循环

    要么按时长驱动，要么等条件满足（超时返回 False）。设置页的动态字段、
    测试按钮的结果都靠事件循环推进，纯 sleep 等不到。
    """
    if condition is None:
        end = time.time() + duration_ms / 1000
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)
        return False
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """
    配置/缓存/日志全部指向每条用例自己的临时目录

    源码态默认把它们写在项目根，用例一点"保存"就真改开发者配置——
    既脏了工作区，也让用例之间互相影响。
    """
    from managers import app_paths

    root = str(tmp_path)
    monkeypatch.setattr(app_paths, 'data_dir', lambda: root)
    monkeypatch.setattr(app_paths, 'config_file',
                        lambda: os.path.join(root, 'config.ini'))
    monkeypatch.setattr(app_paths, 'cache_file',
                        lambda: os.path.join(root, 'translation_cache.json'))
    monkeypatch.setattr(app_paths, 'log_file',
                        lambda: os.path.join(root, 'screenshot_translation.log'))
    monkeypatch.setattr(app_paths, 'ocr_resources_dir',
                        lambda: os.path.join(root, 'resources'))
    # 翻译缓存是模块级单例，导入时就绑定了路径，只能改它的字段
    from managers import cache_manager as cache_module
    cache_module.cache_manager.cache_file = os.path.join(
        root, 'translation_cache.json')
    yield


@pytest.fixture(autouse=True)
def no_modal_boxes():
    """
    把 QMessageBox 的三个静态弹窗换成记录器

    无头跑用例没人点"确定"，真弹窗会永久阻塞。
    """
    from PyQt5.QtWidgets import QMessageBox

    log = []

    def fake(*args, **kwargs):
        log.append(args[1] if len(args) >= 2 else str(args))
        return QMessageBox.Ok

    originals = (QMessageBox.information, QMessageBox.critical, QMessageBox.warning)
    QMessageBox.information = staticmethod(fake)
    QMessageBox.critical = staticmethod(fake)
    QMessageBox.warning = staticmethod(fake)
    yield log
    (QMessageBox.information, QMessageBox.critical,
     QMessageBox.warning) = originals


@pytest.fixture(autouse=True)
def restore_theme(qapp):
    """主题切换是进程级 stylesheet，用例跑完必须还回去"""
    from managers import styles

    before = styles.current_theme()
    yield
    if styles.current_theme() != before:
        styles.set_current_theme(before)
        styles.apply_app(qapp)


@pytest.fixture()
def main_app(qapp):
    """
    只搭管理器骨架、不碰托盘与热键的 MainApp

    hotkey_manager 留空：注册全局热键会影响整机，也不该由用例来验证。
    """
    from config import ConfigManager
    from main import MainApp
    from managers.ocr import OCRManager
    from managers.translation import TranslationManager

    app = MainApp()
    app.app = qapp
    app.config_manager = ConfigManager()
    app.ocr_manager = OCRManager(app.config_manager)
    app.translation_manager = TranslationManager(app.config_manager)
    app.hotkey_manager = None
    yield app
    app.config_window = None


@pytest.fixture()
def win(main_app, qapp):
    """已显示的设置窗口（grab 取像素要求窗口真实存在过）"""
    from main import ConfigWindow

    window = ConfigWindow(main_app)
    window.show()
    spin(qapp, duration_ms=200)
    yield window
    window.close()
    spin(qapp, duration_ms=50)


# ---------------------------------------------------------------- 下拉框


def test_translation_api_combo_lists_every_provider(win):
    from managers.providers import provider_registry

    data = [win.api_type_combo.itemData(i)
            for i in range(win.api_type_combo.count())]
    assert set(provider_registry.names()) <= set(data), f"下拉框缺provider: {data}"
    assert data[win.api_type_combo.currentIndex()] == 'edge_free', \
        f"默认应为 edge_free，实际 {data[win.api_type_combo.currentIndex()]}"
    for i in range(win.api_type_combo.count()):
        assert win.api_type_combo.itemText(i) == \
            provider_registry.display_name_of(win.api_type_combo.itemData(i)), \
            "显示名与 provider 声明不符"


def test_target_lang_and_fallback_defaults(win):
    assert win.target_lang_combo.count() == 9, "目标语言应为9种"
    assert win.target_lang_combo.currentData() == 'zh'
    assert win.fallback_api_combo.count() == len(EXPECTED_API_FIELDS) + 1, \
        "降级API应为 不降级 + 每个provider 一项"
    assert (win.fallback_api_combo.currentData() or '') == '', "默认应是不降级"


@pytest.mark.parametrize('name', sorted(EXPECTED_API_FIELDS))
def test_switching_translation_api_builds_declared_fields(win, qapp, name):
    from managers.providers import provider_registry

    combo = win.api_type_combo
    idx = combo.findData(name)
    assert idx >= 0, f"{name} 不在下拉框"
    combo.setCurrentIndex(idx)
    spin(qapp)

    assert tuple(win._api_field_edits.keys()) == EXPECTED_API_FIELDS[name], \
        f"{name} 动态字段与 config_fields 不符"
    cls = provider_registry.get(name)
    for field_name, _label, is_password in cls.config_fields:
        edit = win._api_field_edits.get(field_name)
        assert edit is not None, f"{name} 缺字段 {field_name}"
        masked = edit.echoMode() == QLineEdit.Password
        assert masked == bool(is_password), f"{name}.{field_name} 密码遮蔽错误"

    if name == 'llm':
        assert win._api_field_edits['llm_base_url'].text(), "llm_base_url 未载入默认值"
    if name == 'libretranslate':
        assert 'libretranslate' in win._api_field_edits['libretranslate_url'].text(), \
            "libretranslate_url 未载入默认值"


@pytest.mark.parametrize('name', sorted(EXPECTED_OCR_FIELDS))
def test_switching_ocr_engine_builds_declared_fields(win, qapp, name):
    from managers.ocr_providers import ocr_provider_registry

    combo = win.ocr_type_combo
    idx = combo.findData(name)
    assert idx >= 0, f"{name} 不在 OCR 下拉框"
    combo.setCurrentIndex(idx)
    spin(qapp)

    assert tuple(win._ocr_field_edits.keys()) == EXPECTED_OCR_FIELDS[name], \
        f"{name} OCR 字段与 config_fields 不符"
    assert combo.itemText(idx) == ocr_provider_registry.display_name_of(name), \
        "OCR 显示名与 provider 声明不符"


# ---------------------------------------------------------------- 保存与持久化


def test_save_writes_config_and_syncs_managers(win, qapp, main_app, no_modal_boxes):
    win.api_type_combo.setCurrentIndex(win.api_type_combo.findData('llm'))
    spin(qapp)
    win._api_field_edits['llm_base_url'].setText('https://api.deepseek.com/v1')
    win._api_field_edits['llm_model'].setText('deepseek-chat')
    win.target_lang_combo.setCurrentIndex(win.target_lang_combo.findData('ja'))
    win.ocr_type_combo.setCurrentIndex(win.ocr_type_combo.findData('baidu_cloud'))
    spin(qapp)

    win._on_save()

    assert no_modal_boxes and no_modal_boxes[-1] == "保存成功", \
        f"应弹出保存成功提示: {no_modal_boxes}"
    cm = win.config_manager
    assert cm.get_api_type() == 'llm'
    assert cm.get_target_lang() == 'ja'
    assert cm.get_ocr_type() == 'baidu_cloud'
    assert cm.get_llm_base_url() == 'https://api.deepseek.com/v1'
    assert cm.get_llm_model() == 'deepseek-chat'
    # 管理器要立刻跟上，不然改完设置得重启才生效
    assert main_app.translation_manager.api_type == 'llm'
    assert main_app.translation_manager.target_lang == 'ja'
    assert main_app.ocr_manager.ocr_type == 'baidu_cloud'


def test_theme_switch_changes_real_rendering(win, qapp, main_app, no_modal_boxes):
    from managers import styles

    assert win.theme_combo.count() == len(styles.available_themes()), "主题下拉项数量不符"

    def panel_lightness():
        image = win.grab().toImage()
        return None if image.isNull() else image.pixelColor(image.width() // 2, 200).lightness()

    win.theme_combo.setCurrentIndex(win.theme_combo.findData('light'))
    spin(qapp)
    assert styles.current_theme() == 'light', "主题未切换到浅色"
    assert '#f2f4f8' in qapp.styleSheet(), "浅色主题未应用到全局样式"
    light_value = panel_lightness()

    win.theme_combo.setCurrentIndex(win.theme_combo.findData('dark'))
    spin(qapp)
    assert styles.current_theme() == 'dark'
    dark_value = panel_lightness()

    # 拿到像素就必须是真的换了色，而不是两套主题画出一样的灰
    if light_value is not None and dark_value is not None:
        assert light_value > 180, f"浅色主题面板仍偏暗: {light_value}"
        assert dark_value < 120, f"深色主题面板不够暗: {dark_value}"

    win.theme_combo.setCurrentIndex(win.theme_combo.findData('light'))
    spin(qapp)
    win._on_save()
    assert win.config_manager.get_theme() == 'light', "主题未保存到配置"


def test_extension_provider_custom_fields_persist(win, qapp, main_app):
    """插件架构的承诺：第三方 provider 声明的字段能存能读回来"""
    from managers.providers import provider_registry
    from managers.providers.base import TranslationProvider

    class ProbeProvider(TranslationProvider):
        name = 'probe_api'
        display_name = '探针翻译'
        config_fields = (('probe_custom_field', '自定义字段', False),)

    provider_registry.register(ProbeProvider, override=True)
    try:
        probe_win = None
        try:
            probe_win = _open_probe_window(main_app, qapp)
            probe_win._api_field_edits['probe_custom_field'].setText('扩展字段值')
            probe_win._save_form_fields()
            assert win.config_manager.get_translation_option('probe_custom_field') \
                == '扩展字段值', "扩展provider字段未写入配置"
            probe_win.close()
            probe_win = _open_probe_window(main_app, qapp)
            assert probe_win._api_field_edits['probe_custom_field'].text() == '扩展字段值', \
                "扩展provider字段未回读"
        finally:
            if probe_win is not None:
                probe_win.close()
                spin(qapp, duration_ms=50)
    finally:
        provider_registry.unregister('probe_api')


def _open_probe_window(main_app, qapp):
    """开一个已选中 probe_api 的设置窗口（动态字段要切换后才生成）"""
    from main import ConfigWindow

    probe_win = ConfigWindow(main_app)
    probe_win.api_type_combo.setCurrentIndex(
        probe_win.api_type_combo.findData('probe_api'))
    spin(qapp)
    assert 'probe_custom_field' in probe_win._api_field_edits, \
        f"扩展provider字段未生成: {list(probe_win._api_field_edits)}"
    return probe_win


def test_config_window_reuses_single_instance(main_app, qapp):
    """
    反复打开设置只置前同一实例

    旧实现每次重建窗口：刚粘进去还没保存的密钥被连带销毁。
    """
    from main import ConfigWindow

    def windows():
        return [w for w in qapp.topLevelWidgets() if isinstance(w, ConfigWindow)]

    main_app.config_window = None
    baseline = len(windows())

    main_app.open_config_window()
    spin(qapp, duration_ms=150)
    first = main_app.config_window
    assert isinstance(first, ConfigWindow), "设置窗口未创建"
    assert first.isVisible(), "设置窗口应显示"
    assert len(windows()) == baseline + 1, \
        f"首次打开应只增一个窗口: {baseline} -> {len(windows())}"

    first.hotkey_edit.setText('ctrl+alt+q')
    for _ in range(3):
        main_app.open_config_window()
        spin(qapp, duration_ms=80)
    assert main_app.config_window is first, "重复打开应复用同一实例而不是重建"
    assert first.hotkey_edit.text() == 'ctrl+alt+q', "再次打开丢失了未保存的表单内容"
    assert first.isVisible(), "复用时应置前显示"
    assert len(windows()) == baseline + 1, \
        f"重复打开叠出了 {len(windows()) - baseline} 个窗口"

    first.close()
    spin(qapp, duration_ms=80)
    main_app.open_config_window()
    spin(qapp, duration_ms=150)
    assert main_app.config_window is first, "关闭后重开应复用同一实例"
    assert first.isVisible(), "关闭后重开应重新显示"
    assert first.hotkey_edit.text() == 'ctrl+alt+q', "重开后表单内容应保留"
    first.close()
    spin(qapp, duration_ms=50)


# ---------------------------------------------------------------- 副作用与生命周期


def test_test_translation_button_does_not_write_config(win, qapp, main_app, monkeypatch):
    """
    「测试翻译」只让未保存的表单值当场生效，不改用户的 config.ini

    旧实现走 _save_form_fields()，测试一次就等于替用户点了一次保存：
    刚粘进去的密钥、还没想好的语言组合全都落盘了。
    """
    from managers import app_paths

    win.api_type_combo.setCurrentIndex(win.api_type_combo.findData('baidu'))
    spin(qapp)
    win._api_field_edits['baidu_app_id'].setText('1234567890')
    win._api_field_edits['baidu_api_key'].setText('abcdef-secret')

    submitted = []
    from managers.thread_pool import thread_pool_manager
    monkeypatch.setattr(thread_pool_manager, 'submit',
                        lambda *a, **k: submitted.append(a) or object())

    config_file = app_paths.config_file()
    assert not os.path.exists(config_file), "用例前提：还没生成过配置文件"

    win.test_button.click()
    spin(qapp, duration_ms=100)

    assert submitted, "测试按钮应提交一次真实请求任务"
    assert not os.path.exists(config_file), \
        f"点测试就写配置文件: {config_file}"
    # 内存里必须已经生效，否则线程里的 provider 读不到刚填的密钥
    cm = win.config_manager
    assert cm.get_baidu_api_info() == ('1234567890', 'abcdef-secret'), \
        "未保存的表单值应在内存中生效"


def test_failed_config_window_construction_is_not_cached(main_app, qapp, monkeypatch):
    """
    构造半途抛错的设置窗口不能被当成可用实例

    旧实现把整个 __init__ 包在 try 里吞掉异常，主程序随后缓存的是"半个窗口"，
    之后每次打开设置都只是 show 一个缺控件的壳。
    """
    from main import ConfigWindow

    def boom(self):
        raise RuntimeError('模拟界面构建失败')

    main_app.config_window = None
    monkeypatch.setattr(ConfigWindow, '_init_ui', boom)
    main_app.open_config_window()
    spin(qapp, duration_ms=150)
    assert main_app.config_window is None, \
        "构建失败的窗口实例不应被缓存"

    monkeypatch.undo()
    main_app.open_config_window()
    spin(qapp, duration_ms=200)
    window = main_app.config_window
    assert window is not None, "失败一次之后设置窗口再也打不开"
    assert hasattr(window, 'theme_combo'), "重开的窗口缺少界面控件"
    assert window.isVisible(), "重开的设置窗口应显示"
    window.close()
    spin(qapp, duration_ms=50)


def test_env_secret_not_written_to_config(win, qapp, monkeypatch, tmp_path):
    """
    环境变量注入的密钥不回填进明文 config.ini

    界面把环境值显示出来是为了让用户知道当前生效的是什么；点保存时原样写回
    文件，等于把"只活在环境里"的凭证落成了随包可读的明文。
    """
    from managers import app_paths

    env_value = 'sk-from-env-abcdef'
    monkeypatch.setenv('LLM_API_KEY', env_value)
    win.api_type_combo.setCurrentIndex(win.api_type_combo.findData('llm'))
    spin(qapp)

    edit = win._api_field_edits['llm_api_key']
    assert edit.text() == env_value, "环境变量密钥应回填到界面"
    assert '环境变量' in edit.placeholderText(), \
        f"应标出值来自环境变量: {edit.placeholderText()}"
    assert 'llm_api_key' in win._env_secret_values

    win._save_form_fields()
    content = _config_text(app_paths.config_file())
    assert env_value not in content, "环境变量密钥被写进了明文配置"

    # 显式重打过就是用户自己的值，按普通字段保存
    edit.setText('sk-typed-by-user')
    win._save_form_fields()
    content = _config_text(app_paths.config_file())
    assert 'sk-typed-by-user' in content, "手填密钥应正常落盘"
    assert env_value not in content


def _config_text(path):
    if not os.path.exists(path):
        return ''
    with open(path, encoding='utf-8') as f:
        return f.read()


def test_save_reports_write_failure(win, qapp, main_app, no_modal_boxes, tmp_path):
    """
    写盘失败必须让用户看见，而不是照旧弹"保存成功"

    ConfigManager 把磁盘异常兜成了日志（一处写入问题不该让设置窗口崩掉），
    代价是界面的默认路径会以为一切正常——用户以为存住了，重启回到旧值。
    """
    cm = win.config_manager
    # 父目录不存在：open 直接抛，走的正是真实失败路径
    cm.config_file = str(tmp_path / 'no-such-dir' / 'config.ini')

    win.target_lang_combo.setCurrentIndex(win.target_lang_combo.findData('ja'))
    spin(qapp)
    win._on_save()

    assert no_modal_boxes and no_modal_boxes[-1] == "配置未能写入", \
        f"写盘失败应点名告警: {no_modal_boxes}"
    assert cm.get_target_lang() == 'ja', "内存值应已生效（本次运行内可用）"
    assert not os.path.exists(cm.config_file)


# ---------------------------------------------------------------- 设置页排版


def test_settings_combos_ignore_wheel_but_page_still_scrolls(win, qapp):
    from PyQt5.QtCore import QPoint, QPointF, Qt
    from PyQt5.QtGui import QWheelEvent
    from PyQt5.QtWidgets import QComboBox, QApplication, QScrollArea

    def wheel_down(widget):
        """投递一次滚轮向下（走 sendEvent，会经过事件过滤器）"""
        center = widget.rect().center()
        ev = QWheelEvent(QPointF(center), widget.mapToGlobal(center),
                         QPoint(), QPoint(0, -120),
                         Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        QApplication.sendEvent(widget, ev)

    # 对照组：没加过滤的下拉框必须被滚轮改变，否则是事件没生效而不是免疫成功
    control = QComboBox()
    control.addItems(['甲', '乙', '丙'])
    control.setCurrentIndex(1)
    wheel_down(control)
    assert control.currentIndex() != 1, "对照组滚轮未改变下拉值，事件构造无效"
    control.deleteLater()

    for combo in (win.theme_combo, win.api_type_combo, win.target_lang_combo,
                  win.fallback_api_combo, win.ocr_type_combo):
        before = combo.currentIndex()
        wheel_down(combo)
        assert combo.currentIndex() == before, \
            f"下拉框响应了滚轮: {before} -> {combo.currentIndex()}"

    # 滚轮被吞掉不等于页面不能滚：鼠标停在下拉框上时该转交滚动区
    scroll = win.findChildren(QScrollArea)[0]
    vbar = scroll.verticalScrollBar()
    if vbar.maximum() > 0:
        vbar.setValue(0)
        spin(qapp, duration_ms=50)
        before_scroll = vbar.value()
        wheel_down(win.theme_combo)
        assert vbar.value() > before_scroll, \
            f"滚轮未转交滚动区: {before_scroll} -> {vbar.value()}"


def test_group_titles_have_their_own_line_above_the_frame(win):
    """
    分组标题必须整条落在卡片边框之上

    Qt 的 QGroupBox 默认把标题画在边框线上，之前 margin-top 只给了 14px
    （比标题本身还矮），于是每个分类的标题都"贴"在内容框上。这里直接问
    style 要两个子控件矩形，比数像素稳。
    """
    from PyQt5.QtWidgets import QGroupBox, QStyle, QStyleOptionGroupBox

    checked = 0
    for group in win.findChildren(QGroupBox):
        if not group.title():
            continue
        option = QStyleOptionGroupBox()
        option.initFrom(group)
        option.text = group.title()
        option.subControls = (QStyle.SC_GroupBoxLabel |
                              QStyle.SC_GroupBoxFrame)
        style = group.style()
        title_rect = style.subControlRect(
            QStyle.CC_GroupBox, option, QStyle.SC_GroupBoxLabel, group)
        frame_rect = style.subControlRect(
            QStyle.CC_GroupBox, option, QStyle.SC_GroupBoxFrame, group)
        assert title_rect.isValid() and frame_rect.isValid(), \
            f"{group.title()} 子控件矩形取不到，断言失效"
        assert title_rect.bottom() <= frame_rect.top(), \
            f"{group.title()} 标题压在内容框上: {title_rect} vs {frame_rect}"
        checked += 1
    assert checked >= 6, f"检查到的分组太少: {checked}"


class _StubHotkeyManager:
    """只汇报状态的快捷键替身（用例不该为了看文案去注册真热键）"""

    def __init__(self, hint):
        self._hint = hint

    def status_hint(self):
        return self._hint


@pytest.mark.parametrize('hint, expected_object_name', [
    (('快捷键 alt+z 已被其他程序占用，已改用兼容方式（键盘钩子）注册', True),
     'hotkeyWarning'),
    (('快捷键 ctrl+alt+z 已由系统注册，随时可用', False), 'hintLabel'),
])
def test_hotkey_status_line_reports_registration_state(
        main_app, qapp, hint, expected_object_name):
    """
    打开设置就得看到占用提示

    占用只影响可靠性（钩子在提权窗口/锁屏切换后收不到键），不影响"按一次响一次"，
    所以以前只在保存时弹一次框——用户平时根本不知道自己处在降级状态。
    """
    from main import ConfigWindow

    main_app.hotkey_manager = _StubHotkeyManager(hint)
    window = ConfigWindow(main_app)
    window.show()
    spin(qapp)
    try:
        label = window.hotkey_status_label
        assert label.text() == hint[0], f"状态行没显示当前状态: {label.text()!r}"
        assert label.objectName() == expected_object_name, \
            f"异常状态没走警示配色: {label.objectName()}"
        assert label.wordWrap(), "两行的占用提示不换行会被裁掉"
    finally:
        window.close()


def test_status_hint_flags_unregistered_hotkey(main_app):
    """状态文案由管理器给：没注册成功就不能说“随时可用”"""
    from managers.hotkey import HotkeyManager

    manager = HotkeyManager(None, main_app.config_manager)
    try:
        text, warning = manager.status_hint()
        assert warning and '未设置' in text, f"空快捷键应提醒: {text}"

        manager.hotkey = 'ctrl+alt+z'
        manager._backend = 'native'
        text, warning = manager.status_hint()
        assert not warning and 'ctrl+alt+z' in text, f"原生注册不该报警: {text}"

        manager._backend = None
        manager.last_error = '快捷键 ctrl+alt+z 注册失败'
        text, warning = manager.status_hint()
        assert warning and '注册失败' in text, f"注册失败必须显式提醒: {text}"
    finally:
        manager.cleanup()


def test_settings_layout_is_compact_and_labels_right_aligned(win):
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QFormLayout, QGroupBox, QPushButton, QScrollArea

    content_layout = win.findChildren(QScrollArea)[0].widget().layout()
    assert content_layout.spacing() <= 10, f"分组间距过大: {content_layout.spacing()}"

    group_count = 0
    for group in win.findChildren(QGroupBox):
        form = group.layout()
        if not isinstance(form, QFormLayout):
            continue
        group_count += 1
        assert form.verticalSpacing() <= 6, \
            f"{group.title()} 行距过大: {form.verticalSpacing()}"
        assert form.labelAlignment() & Qt.AlignRight, \
            f"{group.title()} 标签未右对齐"
    assert group_count >= 6, f"分组数量异常: {group_count}"

    texts = [b.text() for b in win.findChildren(QPushButton)]
    assert '测试翻译' in texts and '保存' in texts, \
        f"操作行按钮不齐: {texts}"


# ---------------------------------------------------------------- 遮罩结果态


def _result_payload():
    """一段三行的原文 + 对应译文，物理像素按 2 倍率画"""
    from PIL import Image, ImageDraw
    from PyQt5.QtCore import QRect

    from managers.text_block import TextBlock

    lines = ['Hello world, this is line one',
             'and this is the second line',
             'third line of the paragraph']
    translated = ['第一行译文', '第二行译文内容较长用于验证缩放', '第三行译文']
    scale = 2.0
    image = Image.new('RGB', (int(300 * scale), int(80 * scale)), (250, 250, 250))
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((int(4 * scale), int(i * 26 * scale)), line, fill=(20, 20, 20))
    blocks = [TextBlock(t, int(4 * scale), int(i * 26 * scale),
                        int(220 * scale), int(20 * scale))
              for i, t in enumerate(lines)]
    return {
        'original_text': '\n'.join(lines),
        'translated_text': '\n'.join(translated),
        'blocks': blocks, 'block_texts': translated,
        'image': image, 'region': QRect(20, 30, 300, 80),
        'languages': [('zh', '中文'), ('ja', '日文')],
        'target_lang': 'zh', 'request_id': 0, 'error': None,
    }


@pytest.fixture()
def overlay(qapp):
    from managers.screenshot import ScreenshotWidget

    widget = ScreenshotWidget()
    widget.resize(900, 520)
    widget._request_id = 0
    yield widget
    widget.close()
    widget.deleteLater()
    spin(qapp, duration_ms=50)


def test_overlay_result_lands_with_logical_blocks_and_paragraph(overlay):
    from managers.text_block import group_paragraphs

    overlay.show_result(_result_payload())

    assert overlay.has_result(), "结果未落地"
    assert overlay.current_payload() is not None
    assert overlay._scale == 2.0, f"像素倍率应为2.0，实际{overlay._scale}"
    assert len(overlay._blocks) == 3
    assert overlay._blocks[0].left == 4, "行块未换算为逻辑坐标"
    assert len(group_paragraphs(overlay._blocks)) == 1, "同段三行被拆开"
    assert len(overlay._paragraphs) == 1, "段落聚合结果不符"


def test_overlay_toolbar_offers_three_modes_and_actions(overlay):
    from PyQt5.QtCore import QPoint
    from PyQt5.QtWidgets import QPushButton

    import managers.result_render as rr

    overlay.show_result(_result_payload())
    toolbar = overlay._toolbar
    assert toolbar is not None, "工具条未创建"
    texts = [b.text() for b in toolbar.findChildren(QPushButton)]
    for expect in ('原文', '双语', '译文', '复制', '钉住', '保存', '重译', '×'):
        assert expect in texts, f"工具条缺少 {expect}: {texts}"
    assert toolbar.current_mode() == rr.MODE_TRANSLATED
    assert toolbar.current_language() == 'zh'

    # 三态各画一次：覆盖重绘最容易在某个模式下炸，异常会让进程静默退出
    for mode in rr.MODES:
        overlay._set_mode(mode)
        assert overlay.grab() is not None, f"{mode} 模式绘制失败"

    para_rect, _indices = overlay._paragraphs[0]
    assert overlay._paragraph_at(para_rect.center()) == 0, "段落命中测试失败"
    assert overlay._paragraph_at(QPoint(860, 500)) is None
    overlay._hover_paragraph = 0
    assert overlay._card_info(), "悬停段落应出气泡卡片"
    overlay._hover_paragraph = None
    assert overlay._card_info() is None, "不悬停时不该再有卡片"


def test_overlay_copy_goes_through_validating_clipboard(overlay, qapp):
    """
    复制必须经 managers.clipboard（带归属校验与重试）

    不直接断言系统剪贴板内容：第三方程序可能长期占用导致写入失败，
    那类失败由 clipboard 模块记警告日志，这里只盯接线与内容。
    """
    import managers.screenshot as screenshot_mod

    overlay.show_result(_result_payload())
    copied = []

    class SpyClipboard:
        @staticmethod
        def set_text(text):
            copied.append(text)
            return True

        @staticmethod
        def set_text_later(text, on_done=None):
            copied.append(text)
            if on_done:
                on_done(True)

    real = screenshot_mod.clipboard
    screenshot_mod.clipboard = SpyClipboard
    try:
        overlay._copy_result()
    finally:
        screenshot_mod.clipboard = real
    assert copied and '第一行译文' in copied[0], \
        f"复制未经过校验写入器或内容不符: {copied}"


def test_overlay_degrades_to_paragraph_card_without_line_texts(overlay):
    payload = _result_payload()
    payload['block_texts'] = []
    overlay.show_result(payload)

    assert overlay._card_info(), "无行级译文时应展示整段卡片"
    assert overlay.grab() is not None


def test_overlay_failure_and_stale_result(main_app, overlay):
    from managers.screenshot import ScreenshotManager

    overlay.show_failure('OCR 服务不可用')
    assert overlay._error and not overlay.has_result()
    assert overlay.grab() is not None

    # 过期请求的结果（用户已经重框/取消）不能覆盖当前状态
    stale = _result_payload()
    stale['request_id'] = 99
    manager = main_app.screenshot_manager or ScreenshotManager()
    previous = manager._screenshot_widget
    manager._screenshot_widget = overlay
    try:
        manager._apply_result(overlay, stale)
    finally:
        manager._screenshot_widget = previous
    assert overlay._error == 'OCR 服务不可用', "过期选区的结果被误应用"


# ---------------------------------------------------------------- 日志与降级


def test_log_redaction_covers_tracebacks():
    """
    调用栈里的 URL 查询串也要脱敏

    脱敏做在 handler 的 Filter 上拦不住 traceback：Filter 跑在
    Formatter.format() 之前，那时 record.exc_text 还没渲染出来，异常消息里
    带着完整请求 URL（签名参数、access_token 就在 query 里）会原样进日志文件。
    """
    import io
    import logging
    from main import _RedactingFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_RedactingFormatter('%(levelname)s - %(message)s'))
    log = logging.getLogger('redact-probe')
    log.propagate = False
    log.addHandler(handler)
    try:
        try:
            raise ValueError(
                "请求失败 https://api.example.com/v1/query?appid=123&sign=SECRET-VALUE")
        except ValueError:
            log.exception("翻译请求失败")
        output = stream.getvalue()
        assert 'Traceback (most recent call last)' in output, "调用栈本身不能丢"
        assert 'SECRET-VALUE' not in output, f"调用栈里的凭证未脱敏: {output}"
        assert 'api.example.com' in output, "脱敏不该把主机名一起抹掉"
    finally:
        log.removeHandler(handler)
        handler.close()


def test_logging_falls_back_to_console_when_log_unwritable(monkeypatch):
    """
    日志文件建不出来时降级成纯控制台，而不是让程序连启动都做不到

    _setup_logging 在模块导入期就跑，数据目录连兜底 TEMP 都创建失败
    （只读介质、权限收紧的环境）时抛出异常等于直接劝退用户。
    """
    import io
    import logging
    import main as main_module

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        root.handlers.clear()
        stderr_lines = io.StringIO()
        monkeypatch.setattr(sys, 'stderr', stderr_lines)

        def refuse(*args, **kwargs):
            raise PermissionError(13, '日志目录不可写')

        monkeypatch.setattr(main_module, 'RotatingFileHandler', refuse)
        main_module._setup_logging()

        assert [h for h in root.handlers
                if isinstance(h, logging.FileHandler)] == [], \
            "日志文件不可写时不该留下文件 handler"
        console = [h for h in root.handlers
                   if type(h) is logging.StreamHandler]
        assert len(console) == 1, f"应降级为一个控制台 handler: {root.handlers}"
        assert isinstance(console[0].formatter, main_module._RedactingFormatter), \
            "控制台也得脱敏，否则等于把密钥原样打到命令行"
        assert '日志文件不可写' in stderr_lines.getvalue(), \
            f"降级要留痕: {stderr_lines.getvalue()}"
    finally:
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        root.handlers.extend(saved_handlers)
        root.setLevel(saved_level)


# ---------------------------------------------------------------- 需显式开启


@online
def test_test_translation_button_succeeds_on_edge_free(win, qapp):
    win._save_form_fields()
    win._on_test_translation()
    done = spin(qapp,
                condition=lambda: win.test_result_label.text().startswith(('✅', '❌')),
                timeout_ms=40000)
    text = win.test_result_label.text()
    assert done, f"测试按钮未返回结果: {text}"
    assert text.startswith('✅'), f"edge_free 真实测试应成功: {text}"


@online
def test_every_provider_degrades_gracefully_on_real_calls(main_app, win):
    from managers.providers import provider_registry

    timeout = 12
    results = {}
    for name in provider_registry.names():
        cls = provider_registry.get(name)
        provider = cls(config_manager=win.config_manager,
                       session=main_app.translation_manager.session,
                       timeout=timeout)
        try:
            r = provider.translate("hello", source='auto', target='zh')
            results[name] = 'OK' if r and not r.startswith('[翻译失败') else 'EMPTY'
        except Exception as e:
            results[name] = f"FAIL({str(e)[:40]})"

    # 没密钥的失败可以，但必须是可预期的结果而不是抛穿
    assert results['edge_free'] == 'OK', f"edge_free必须可用: {results}"


@smoke
def test_main_entry_point_starts():
    """真拉起 main.py：日志出现启动成功即通过（会注册全局热键）"""
    # 允许多开：用户本机往往已经跑着正式安装的实例，单实例守卫会把这次
    # 冒烟直接拦成"程序已在运行"，而那个提示框还是模态的、没人点
    env = dict(os.environ, PYTHONIOENCODING='utf-8', ST_ALLOW_MULTI_INSTANCE='1')
    proc = subprocess.Popen(
        [sys.executable, 'main.py'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True,
        encoding='utf-8', errors='replace',
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        deadline = time.time() + 25
        started = False
        while time.time() < deadline:
            line = proc.stdout.readline()
            if '应用启动成功' in line or '应用事件循环开始运行' in line:
                started = True
                break
            if proc.poll() is not None:
                break
        assert started, "main.py 启动日志未出现'应用启动成功'"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_test_button_recovers_when_pool_rejects(win, qapp, monkeypatch):
    """线程池拒收任务时，测试按钮必须当场复原，不能永久停在“测试中…”"""
    import main as main_mod

    class RejectingPool:
        def submit(self, *args, **kwargs):
            return None

    monkeypatch.setattr(main_mod, 'thread_pool_manager', RejectingPool())
    win.test_button.setEnabled(True)
    win._on_test_translation()
    assert win.test_button.isEnabled(), "任务没提交成功，按钮却仍是禁用态"
    assert '无法启动' in win.test_result_label.text(), \
        f"没有给出可行动提示: {win.test_result_label.text()!r}"


def test_api_type_switch_keeps_unsaved_input(win, qapp):
    """切到别的 API 类型再切回来，尚未保存的输入不能丢"""
    combo = win.api_type_combo
    combo.setCurrentIndex(combo.findData('llm'))
    spin(qapp)
    win._api_field_edits['llm_model'].setText('my-draft-model')

    combo.setCurrentIndex(combo.findData('baidu'))
    spin(qapp)
    combo.setCurrentIndex(combo.findData('llm'))
    spin(qapp)
    assert win._api_field_edits['llm_model'].text() == 'my-draft-model', \
        "切走再切回来把用户没保存的输入丢了"


def test_ocr_type_switch_keeps_unsaved_input(win, qapp):
    """OCR 引擎切换同理：未保存的字段值要保留"""
    combo = win.ocr_type_combo
    combo.setCurrentIndex(combo.findData('baidu_cloud'))
    spin(qapp)
    win._ocr_field_edits['baidu_ocr_api_key'].setText('draft-key')

    combo.setCurrentIndex(combo.findData('tesseract'))
    spin(qapp)
    combo.setCurrentIndex(combo.findData('baidu_cloud'))
    spin(qapp)
    assert win._ocr_field_edits['baidu_ocr_api_key'].text() == 'draft-key', \
        "切走再切回来把云端 OCR 的输入丢了"


def test_test_translation_does_not_write_overlay(win):
    """点“测试翻译”不该改掉本次会话的遮罩参数（只有保存才写）"""
    cm = win.config_manager
    cm.set_overlay_option('snap_threshold', '12')
    cm.set_overlay_option('dim_opacity', '90')
    win.snap_slider.setValue(40)
    win.dim_slider.setValue(150)

    win._save_form_fields(persist=False)
    assert cm.get_overlay_option('snap_threshold') == '12', \
        "测试翻译顺手改了磁吸阈值"
    assert cm.get_overlay_option('dim_opacity') == '90', \
        "测试翻译顺手改了暗角透明度"

    win._save_form_fields(persist=True)
    assert cm.get_overlay_option('snap_threshold') == '40', "真正保存时应当写入"
    assert cm.get_overlay_option('dim_opacity') == '150'

