#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置界面完整回归测试

覆盖：
1. 翻译API下拉框：7个provider全部出现、默认选中与配置一致
2. 目标语言/降级API下拉框：选项与默认值
3. 逐个切换每个翻译API：动态字段按 config_fields 生成、密码遮蔽正确、默认值载入
4. OCR引擎下拉框：3个引擎逐个切换，动态字段正确
5. 测试翻译按钮：edge_free 真实网络点击应成功
6. 全部API真实连通性矩阵（限定超时，失败必须优雅）
7. 保存流程：写入配置、同步翻译/OCR管理器（测试后还原config.ini）
8. 主题切换：浅色/深色渲染实测、保存落盘；扩展provider自定义字段保存与回读
9. 遮罩结果态：就地译文覆盖、工具条三态、段落卡片、降级与失败态
10. 真实启动冒烟：spawn main.py，验证日志出现"应用启动成功"后结束进程
11. 设置页排版：下拉框滚轮免疫（带对照组）、分组/行距紧凑、标签右对齐
"""

import os
import sys
import time
import shutil
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication, QLineEdit
from PyQt5.QtCore import QTimer


def _spin(wait_ms, app, condition=None, timeout_ms=30000):
    """驱动事件循环直到条件满足或超时（无condition时按时长驱动）"""
    if condition is None:
        end = time.time() + wait_ms / 1000
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


def main():
    print("开始配置界面回归测试...\n")
    app = QApplication.instance() or QApplication(sys.argv)

    # ---- 备份配置，测试后还原 ----
    shutil.copy('config.ini', 'config.ini.bak')

    failures = []
    try:
        _run_tests(app, failures)
    finally:
        shutil.move('config.ini.bak', 'config.ini')
        print("\nconfig.ini 已还原")

    if failures:
        print(f"\n❌ {len(failures)} 项失败:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n🎉 配置界面回归测试全部通过！")
    return 0


def _run_tests(app, failures):
    from config import ConfigManager
    from main import MainApp, ConfigWindow
    from managers.translation import TranslationManager
    from managers.ocr import OCRManager
    from PyQt5.QtWidgets import QMessageBox

    # 本文件会真的走一次翻译：全局缓存单例必须指向临时文件，
    # 否则测试把结果写进项目根的 translation_cache.json
    import tempfile
    from managers import cache_manager as cache_manager_module
    _cache_dir = tempfile.TemporaryDirectory()
    _cache_singleton = cache_manager_module.cache_manager
    _cache_singleton.cache_file = os.path.join(_cache_dir.name, 'cache.json')
    _cache_singleton._keep_tmpdir = _cache_dir

    # 保存流程会弹模态 QMessageBox，无头测试没人点确定会永久阻塞：
    # 把 information/critical/warning 全部替换为记录型假弹窗
    messagebox_log = []

    def _fake_box(*args, **kwargs):
        messagebox_log.append(args[1] if len(args) >= 2 else str(args))
        return QMessageBox.Ok

    QMessageBox.information = staticmethod(_fake_box)
    QMessageBox.critical = staticmethod(_fake_box)
    QMessageBox.warning = staticmethod(_fake_box)

    def check(name, fn):
        print(f"▶ 开始: {name}", flush=True)
        try:
            fn()
            print(f"✅ {name}", flush=True)
        except Exception as e:
            failures.append(f"{name}: {e}")
            print(f"❌ {name}: {e}", flush=True)

    main_app = MainApp()
    main_app.app = app
    main_app.config_manager = ConfigManager()
    main_app.ocr_manager = OCRManager(main_app.config_manager)
    main_app.translation_manager = TranslationManager(main_app.config_manager)
    main_app.hotkey_manager = None  # 不注册热键，避免污染系统

    win = ConfigWindow(main_app)
    win.show()
    _spin(200, app)

    from managers.providers import provider_registry
    from managers.ocr_providers import ocr_provider_registry

    # ---- 1. 翻译API下拉框 ----
    def t_api_combo():
        data = [win.api_type_combo.itemData(i) for i in range(win.api_type_combo.count())]
        assert set(provider_registry.names()) <= set(data), f"下拉框缺provider: {data}"
        assert data[win.api_type_combo.currentIndex()] == 'edge_free', \
            f"默认应为edge_free, 实际{data[win.api_type_combo.currentIndex()]}"
        for i in range(win.api_type_combo.count()):
            assert win.api_type_combo.itemText(i) == \
                provider_registry.display_name_of(win.api_type_combo.itemData(i)), "显示名不符"
    check("翻译API下拉框：7个provider、默认edge_free、显示名一致", t_api_combo)

    # ---- 2. 目标语言/降级API ----
    def t_lang_combo():
        assert win.target_lang_combo.count() == 9, "目标语言应为9种"
        assert win.target_lang_combo.currentData() == 'zh'
        assert win.fallback_api_combo.count() == 8, "降级API应为 不降级+7个provider"
        assert win.fallback_api_combo.currentData() in ('', None) or \
            win.fallback_api_combo.currentData() == '', "默认不降级"
    check("目标语言9种/降级API默认不降级", t_lang_combo)

    # ---- 3. 逐个切换翻译API：动态字段 ----
    EXPECTED_API_FIELDS = {
        'baidu': ('baidu_app_id', 'baidu_api_key'),
        'youdao': ('youdao_app_id', 'youdao_api_key'),
        'bing': ('bing_api_key',),
        'libretranslate': ('libretranslate_url',),
        'llm': ('llm_base_url', 'llm_api_key', 'llm_model'),
        'edge_free': (),
        'google_free': (),
    }

    def t_switch_all_apis():
        combo = win.api_type_combo
        for name in EXPECTED_API_FIELDS:
            idx = combo.findData(name)
            assert idx >= 0, f"{name} 不在下拉框"
            combo.setCurrentIndex(idx)
            _spin(150, app)
            fields = tuple(win._api_field_edits.keys())
            assert fields == EXPECTED_API_FIELDS[name], \
                f"{name} 动态字段 {fields} != {EXPECTED_API_FIELDS[name]}"
            # 密码框遮蔽校验
            cls = provider_registry.get(name)
            for field_name, label, is_password in cls.config_fields:
                edit = win._api_field_edits.get(field_name)
                assert edit is not None
                masked = edit.echoMode() == QLineEdit.Password
                assert masked == bool(is_password), f"{name}.{field_name} 遮蔽错误"
            # 默认值载入（llm_base_url / libretranslate_url）
            if name == 'llm':
                assert 'openai' in win._api_field_edits['llm_base_url'].text() or \
                    win._api_field_edits['llm_base_url'].text(), "llm_base_url 未载入默认值"
            if name == 'libretranslate':
                assert 'libretranslate' in win._api_field_edits['libretranslate_url'].text()
        combo.setCurrentIndex(combo.findData('edge_free'))
        _spin(100, app)
    check("逐个切换7个翻译API：动态字段/密码遮蔽/默认值正确", t_switch_all_apis)

    # ---- 4. OCR引擎逐个切换 ----
    EXPECTED_OCR_FIELDS = {
        'tesseract': ('tesseract_langs',),
        'baidu_cloud': ('baidu_ocr_api_key', 'baidu_ocr_secret_key'),
        'mock': (),
    }

    def t_switch_ocr():
        combo = win.ocr_type_combo
        for name in EXPECTED_OCR_FIELDS:
            idx = combo.findData(name)
            assert idx >= 0, f"{name} 不在OCR下拉框"
            combo.setCurrentIndex(idx)
            _spin(150, app)
            fields = tuple(win._ocr_field_edits.keys())
            assert fields == EXPECTED_OCR_FIELDS[name], \
                f"{name} OCR字段 {fields} != {EXPECTED_OCR_FIELDS[name]}"
            assert win.ocr_type_combo.itemText(idx) == \
                ocr_provider_registry.display_name_of(name), "OCR显示名不符"
        combo.setCurrentIndex(combo.findData('tesseract'))
        _spin(100, app)
    check("逐个切换3个OCR引擎：动态字段/显示名正确", t_switch_ocr)

    # ---- 5. 测试翻译按钮（真实网络，edge_free）----
    def t_test_button():
        win._save_form_fields()  # 模拟点击前表单状态
        win._on_test_translation()
        ok = _spin(200, app,
                   condition=lambda: win.test_result_label.text().startswith(('✅', '❌')),
                   timeout_ms=40000)
        assert ok, f"测试按钮未返回结果: {win.test_result_label.text()}"
        text = win.test_result_label.text()
        print(f"   ↳ 测试按钮结果: {text}")
        assert text.startswith('✅'), f"edge_free 真实测试应成功: {text}"
    check("测试翻译按钮：edge_free真实网络成功", t_test_button)

    # ---- 6. 全API连通性矩阵 ----
    def t_api_matrix():
        import requests
        timeout = 12
        results = {}
        for name in provider_registry.names():
            cls = provider_registry.get(name)
            p = cls(config_manager=win.config_manager,
                    session=main_app.translation_manager.session, timeout=timeout)
            try:
                r = p.translate("hello", source='auto', target='zh')
                results[name] = 'OK' if r and not r.startswith('[翻译失败') else 'EMPTY'
            except Exception as e:
                results[name] = f"FAIL({str(e)[:40]})"
        print("   ↳ API矩阵:", results)
        # edge_free 必须通；其余允许因密钥/网络失败，但不能崩溃
        assert results['edge_free'] == 'OK', f"edge_free必须可用: {results}"
    check("全API真实连通性矩阵：无崩溃、edge_free可用", t_api_matrix)

    # ---- 7. 保存流程 ----
    def t_save():
        win.api_type_combo.setCurrentIndex(win.api_type_combo.findData('llm'))
        _spin(100, app)
        win._api_field_edits['llm_base_url'].setText('https://api.deepseek.com/v1')
        win._api_field_edits['llm_model'].setText('deepseek-chat')
        win.target_lang_combo.setCurrentIndex(win.target_lang_combo.findData('ja'))
        win.ocr_type_combo.setCurrentIndex(win.ocr_type_combo.findData('baidu_cloud'))
        _spin(100, app)
        win._on_save()  # QMessageBox 已被替换为假弹窗，不会阻塞
        assert messagebox_log and messagebox_log[-1] == "保存成功", \
            f"应弹出保存成功提示: {messagebox_log}"
        cm = win.config_manager
        assert cm.get_api_type() == 'llm'
        assert cm.get_target_lang() == 'ja'
        assert cm.get_ocr_type() == 'baidu_cloud'
        assert cm.get_llm_base_url() == 'https://api.deepseek.com/v1'
        assert cm.get_llm_model() == 'deepseek-chat'
        # 管理器同步
        assert main_app.translation_manager.api_type == 'llm'
        assert main_app.translation_manager.target_lang == 'ja'
        assert main_app.ocr_manager.ocr_type == 'baidu_cloud'
        # 恢复下拉选择，避免影响后续测试
        win.api_type_combo.setCurrentIndex(win.api_type_combo.findData('edge_free'))
    check("保存流程：配置写盘+翻译/OCR管理器同步", t_save)

    # ---- 7b. 主题切换（浅色渲染实测）与扩展 provider 字段持久化 ----
    def t_theme_and_extension():
        from managers import styles
        from managers.providers import provider_registry
        from managers.providers.base import TranslationProvider

        assert win.theme_combo.count() == len(styles.available_themes()), "主题下拉项数量不符"

        def panel_lightness():
            """取分组面板区域的亮度（窗口已关闭时 grab 仍会重新渲染）"""
            image = win.grab().toImage()
            if image.isNull():
                return None
            return image.pixelColor(image.width() // 2, 200).lightness()

        # 浅色：进程级样式立即生效，且渲染出来确实是浅色
        win.theme_combo.setCurrentIndex(win.theme_combo.findData('light'))
        _spin(150, app)
        assert styles.current_theme() == 'light', "主题未切换到浅色"
        assert '#f2f4f8' in app.styleSheet(), "浅色主题未应用到全局样式"
        light_value = panel_lightness()
        if light_value is not None:
            assert light_value > 180, f"浅色主题面板仍偏暗: {light_value}"

        # 深色：换回来必须是暗底，证明两套主题真的换了色
        win.theme_combo.setCurrentIndex(win.theme_combo.findData('dark'))
        _spin(150, app)
        assert styles.current_theme() == 'dark'
        dark_value = panel_lightness()
        if dark_value is not None:
            assert dark_value < 120, f"深色主题面板不够暗: {dark_value}"

        # 浅色 + 保存：主题应落盘
        win.theme_combo.setCurrentIndex(win.theme_combo.findData('light'))
        _spin(120, app)
        win._on_save()
        assert win.config_manager.get_theme() == 'light', "主题未保存到配置"
        print(f"   ↳ 主题渲染亮度: 浅色={light_value} 深色={dark_value}")

        # 扩展 provider 声明的自定义字段必须能保存与回读（插件架构承诺）
        class ProbeProvider(TranslationProvider):
            name = 'probe_api'
            display_name = '探针翻译'
            config_fields = (('probe_custom_field', '自定义字段', False),)

        provider_registry.register(ProbeProvider, override=True)
        probe_win = None
        try:
            probe_win = ConfigWindow(main_app)
            probe_win.api_type_combo.setCurrentIndex(
                probe_win.api_type_combo.findData('probe_api'))
            _spin(150, app)
            assert 'probe_custom_field' in probe_win._api_field_edits, \
                f"扩展provider字段未生成: {list(probe_win._api_field_edits)}"
            probe_win._api_field_edits['probe_custom_field'].setText('扩展字段值')
            probe_win._save_form_fields()
            assert win.config_manager.get_translation_option('probe_custom_field') == '扩展字段值', \
                "扩展provider字段未写入配置"
            probe_win.close()
            probe_win = ConfigWindow(main_app)
            probe_win.api_type_combo.setCurrentIndex(
                probe_win.api_type_combo.findData('probe_api'))
            _spin(150, app)
            assert probe_win._api_field_edits['probe_custom_field'].text() == '扩展字段值', \
                "扩展provider字段未回读"
        finally:
            if probe_win is not None:
                probe_win.close()
            provider_registry.unregister('probe_api')
            # 还原：主题回深色，清掉探针字段（config.ini 最后整体还原）
            win.theme_combo.setCurrentIndex(win.theme_combo.findData('dark'))
            _spin(100, app)
            win.config_manager.set_translation_option('probe_custom_field', '')
    check("主题切换（浅色渲染实测）与扩展provider字段持久化", t_theme_and_extension)

    # ---- 8. 遮罩结果态：就地覆盖 + 工具条 + 气泡卡片 ----
    def t_overlay_result():
        from PIL import Image, ImageDraw
        from PyQt5.QtCore import QPoint, QRect
        from PyQt5.QtWidgets import QApplication, QPushButton

        import managers.result_render as rr
        from managers.screenshot import ScreenshotWidget
        from managers.text_block import TextBlock, group_paragraphs

        lines = ['Hello world, this is line one',
                 'and this is the second line',
                 'third line of the paragraph']
        translated = ['第一行译文', '第二行译文内容较长用于验证缩放', '第三行译文']
        scale = 2.0
        image = Image.new('RGB', (int(300 * scale), int(80 * scale)), (250, 250, 250))
        draw = ImageDraw.Draw(image)
        for i in range(3):
            draw.text((int(4 * scale), int(i * 26 * scale)), lines[i],
                      fill=(20, 20, 20))
        blocks = [TextBlock(t, int(4 * scale), int(i * 26 * scale),
                            int(220 * scale), int(20 * scale))
                  for i, t in enumerate(lines)]
        payload = {
            'original_text': '\n'.join(lines),
            'translated_text': '\n'.join(translated),
            'blocks': blocks, 'block_texts': translated,
            'image': image, 'region': QRect(20, 30, 300, 80),
            'languages': [('zh', '中文'), ('ja', '日文')],
            'target_lang': 'zh', 'request_id': 0, 'error': None,
        }

        widget = ScreenshotWidget()
        widget.resize(900, 520)
        try:
            widget._request_id = 0
            widget.show_result(payload)
            assert widget.has_result(), "结果未落地"
            assert widget.current_payload() is payload
            assert widget._scale == scale, f"像素倍率应为{scale}，实际{widget._scale}"
            assert len(widget._blocks) == 3
            assert widget._blocks[0].left == 4, "行块未换算为逻辑坐标"
            assert len(group_paragraphs(widget._blocks)) == 1, "同段三行被拆开"
            assert len(widget._paragraphs) == 1, "段落聚合结果不符"

            # 工具条：三态按钮 + 语言下拉 + 操作按钮齐全
            toolbar = widget._toolbar
            assert toolbar is not None, "工具条未创建"
            btn_texts = [b.text() for b in toolbar.findChildren(QPushButton)]
            for expect in ('原文', '双语', '译文', '复制', '钉住', '保存', '重译', '×'):
                assert expect in btn_texts, f"工具条缺少 {expect}: {btn_texts}"
            assert toolbar.current_mode() == rr.MODE_TRANSLATED
            assert toolbar.current_language() == 'zh'

            # 三态切换 + 悬停/点击段落 + 复制，逐状态绘制一次确认不抛异常
            for mode in rr.MODES:
                widget._set_mode(mode)
                assert widget.grab() is not None, f"{mode} 模式绘制失败"
            widget._set_mode(rr.MODE_TRANSLATED)

            para_rect, indices = widget._paragraphs[0]
            assert widget._paragraph_at(para_rect.center()) == 0, "段落命中测试失败"
            assert widget._paragraph_at(QPoint(860, 500)) is None
            widget._hover_paragraph = 0
            assert widget._card_info(), "悬停段落应出气泡卡片"
            widget._hover_paragraph = None
            assert widget._card_info() is None, "无行级译文缺失时不该再压卡片"

            widget._copy_result()
            clipboard = QApplication.clipboard().text()
            assert '第一行译文' in clipboard, f"剪贴板内容不符: {clipboard}"

            # 行级译文缺失时降级为整段卡片
            degraded = dict(payload)
            degraded['block_texts'] = []
            widget.show_result(degraded)
            assert widget._card_info(), "无行级译文时应展示整段卡片"
            assert widget.grab() is not None

            # 失败态与过期结果
            widget.show_failure('OCR 服务不可用')
            assert widget._error and not widget.has_result()
            assert widget.grab() is not None
            from managers.screenshot import ScreenshotManager
            sm = main_app.screenshot_manager or ScreenshotManager()
            stale = dict(payload)
            stale['request_id'] = 99
            previous_widget = sm._screenshot_widget
            sm._screenshot_widget = widget
            sm._apply_result(widget, stale)
            assert widget._error == 'OCR 服务不可用', "过期选区的结果被误应用"
            sm._screenshot_widget = previous_widget
        finally:
            widget.close()
            widget.deleteLater()
    check("遮罩结果态：就地覆盖/工具条/段落卡片/降级/失败", t_overlay_result)

    # ---- 11. 设置页排版：滚轮免疫 / 紧凑间距 / 标签右对齐 ----
    def t_layout_polish():
        from PyQt5.QtCore import QPointF, QPoint, Qt
        from PyQt5.QtGui import QWheelEvent
        from PyQt5.QtWidgets import QComboBox, QFormLayout, QGroupBox, QScrollArea

        def wheel_down(widget):
            """向控件投递一次滚轮向下事件（走 QApplication.notify，会经过事件过滤器）"""
            center = widget.rect().center()
            ev = QWheelEvent(QPointF(center), widget.mapToGlobal(center),
                             QPoint(), QPoint(0, -120),
                             Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
            QApplication.sendEvent(widget, ev)

        # 对照组：未加过滤的下拉框必须能被滚轮改变，证明事件本身有效
        ctrl = QComboBox()
        ctrl.addItems(['甲', '乙', '丙'])
        ctrl.setCurrentIndex(1)
        wheel_down(ctrl)
        assert ctrl.currentIndex() != 1, "对照组滚轮未改变下拉值，事件构造无效"
        ctrl.deleteLater()

        # 设置页下拉框：滚轮必须被忽略，选项不变
        combos = (win.theme_combo, win.api_type_combo, win.target_lang_combo,
                  win.fallback_api_combo, win.ocr_type_combo)
        for combo in combos:
            before = combo.currentIndex()
            wheel_down(combo)
            assert combo.currentIndex() == before, \
                f"下拉框响应了滚轮: {before} -> {combo.currentIndex()}"

        # 紧凑间距：分组间 ≤10px，各表单行距 ≤6px
        scroll = win.findChildren(QScrollArea)[0]
        content_layout = scroll.widget().layout()
        assert content_layout.spacing() <= 10, f"分组间距过大: {content_layout.spacing()}"

        # 滚轮转交：鼠标停在下拉框上时页面照常滚动（不是被吞掉）
        vbar = scroll.verticalScrollBar()
        if vbar.maximum() > 0:
            vbar.setValue(0)
            _spin(50, app)
            before_scroll = vbar.value()
            wheel_down(win.theme_combo)
            assert vbar.value() > before_scroll, \
                f"滚轮未转交滚动区: {before_scroll} -> {vbar.value()}"
        group_count = 0
        for group in win.findChildren(QGroupBox):
            form = group.layout()
            if isinstance(form, QFormLayout):
                group_count += 1
                assert form.verticalSpacing() <= 6, \
                    f"{group.title()} 行距过大: {form.verticalSpacing()}"
                assert form.labelAlignment() & Qt.AlignRight, \
                    f"{group.title()} 标签未右对齐"
        assert group_count >= 6, f"分组数量异常: {group_count}"

        # 操作行：测试按钮与保存按钮同排固定在滚动区外
        from PyQt5.QtWidgets import QPushButton
        btn_texts = [b.text() for b in win.findChildren(QPushButton)]
        assert '测试翻译' in btn_texts and '保存' in btn_texts
    check("设置页排版：滚轮免疫/紧凑间距/标签右对齐", t_layout_polish)

    # ---- 12. 设置窗口单实例：反复打开只置前，不重建 ----
    def t_single_config_window():
        def windows():
            return [w for w in app.topLevelWidgets()
                    if isinstance(w, ConfigWindow)]

        # 本用例自行接管这一状态，不受其他用例影响
        main_app.config_window = None
        baseline = len(windows())

        main_app.open_config_window()
        _spin(120, app)
        first = main_app.config_window
        assert isinstance(first, ConfigWindow), "设置窗口未创建"
        assert first.isVisible(), "设置窗口应显示"
        assert len(windows()) == baseline + 1, \
            f"首次打开应只增一个窗口: {baseline} -> {len(windows())}"

        # 托盘双击/菜单「设置」可反复触发：必须置前同一实例。
        # 旧实现每次重建窗口，旧窗口连同未保存的表单输入（如刚粘的密钥）
        # 一起被销毁 —— 改完配置还没点保存就被清空。
        first.hotkey_edit.setText('ctrl+alt+q')
        for _ in range(3):
            main_app.open_config_window()
            _spin(80, app)
        assert main_app.config_window is first, \
            "重复打开设置应复用同一实例，而不是重建窗口"
        assert first.hotkey_edit.text() == 'ctrl+alt+q', \
            "再次打开设置丢失了未保存的表单内容"
        assert first.isVisible(), "复用时应置前显示"
        assert len(windows()) == baseline + 1, \
            f"重复打开设置叠出了 {len(windows()) - baseline} 个窗口"

        # 用户关掉后再打开：复用同一实例并重新显示（而不是新建一个）
        first.close()
        _spin(80, app)
        main_app.open_config_window()
        _spin(120, app)
        assert main_app.config_window is first, "关闭后重开应复用同一实例"
        assert first.isVisible(), "关闭后重开应重新显示"
        assert first.hotkey_edit.text() == 'ctrl+alt+q', "重开后表单内容应保留"
        assert len(windows()) == baseline + 1, "关闭后重开不应新建窗口"
        first.close()
        _spin(50, app)
    check("设置窗口单实例：反复打开只置前不重建", t_single_config_window)


    win.close()


def smoke_run_main():
    """真实启动冒烟：spawn main.py，日志出现'应用启动成功'即通过"""
    print("\n=== 真实启动冒烟 ===")
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    proc = subprocess.Popen(
        [sys.executable, 'main.py'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True,
        encoding='utf-8', errors='replace')
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
        print("✅ main.py 真实启动成功（含配置窗口/托盘初始化）")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == '__main__':
    code = main()
    if code == 0:
        try:
            smoke_run_main()
        except Exception as e:
            print(f"❌ 启动冒烟失败: {e}")
            code = 1
    sys.exit(code)
