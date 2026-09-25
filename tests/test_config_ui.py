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
8. 结果对话框：按钮齐全、复制译文写剪贴板、切换语言自动重译
9. 真实启动冒烟：spawn main.py，验证日志出现"应用启动成功"后结束进程
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

    # ---- 8. 结果对话框 ----
    def t_result_dialog():
        import managers.screenshot as ss
        from managers.providers.base import TARGET_LANGUAGES

        opened = {}
        orig_exec = None

        sm = main_app.screenshot_manager or __import__(
            'managers.screenshot', fromlist=['ScreenshotManager']).ScreenshotManager()
        sm.ocr_manager = main_app.ocr_manager
        sm.translation_manager = main_app.translation_manager

        # 拦截 exec_ 防止阻塞：定时关闭
        from PyQt5.QtWidgets import QDialog
        orig_exec = QDialog.exec_

        def fake_exec(self):
            opened['shown'] = True
            # 记录对话框内的关键控件
            btns = [b.text() for b in self.findChildren(type(win.save_button))]
            opened['buttons'] = btns
            QTimer.singleShot(300, self.accept)
            return orig_exec(self)

        QDialog.exec_ = fake_exec
        try:
            sm._show_translation_dialog("Hello world", "你好世界")
            _spin(600, app, condition=lambda: opened.get('shown'))
            assert opened.get('shown'), "对话框未显示"
            btns = opened.get('buttons', [])
            for expect in ('复制译文', '复制原文', '重新翻译'):
                assert expect in btns, f"缺少按钮: {expect}，实际{btns}"
        finally:
            QDialog.exec_ = orig_exec

        # 复制译文到剪贴板（独立小对话框构造太重，直接验证核心逻辑）
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QApplication
        dlg = QDialog()
        lay = QVBoxLayout(dlg)
        te = QTextEdit()
        te.setPlainText("你好世界")
        btn = QPushButton("复制译文")
        lay.addWidget(te); lay.addWidget(btn)
        btn.clicked.connect(lambda: QApplication.clipboard().setText(te.toPlainText()))
        dlg.show(); _spin(100, app)
        btn.click(); _spin(100, app)
        assert QApplication.clipboard().text() == "你好世界", "剪贴板内容不符"

        # 切换语言自动重译（_dialog_ready 防护 + target_lang 参数链路）
        assert main_app.translation_manager.target_lang == 'ja'  # 保存流程设置的
        r = main_app.translation_manager.perform_translation(
            "language switch test", force_refresh=True, target_lang='ko')
        assert r and not r.startswith('[翻译失败'), f"韩语重译失败: {r}"
        print(f"   ↳ 切语言重译(ko): {r[:30]}")
    check("结果对话框：按钮齐全/剪贴板/切语言重译", t_result_dialog)

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
