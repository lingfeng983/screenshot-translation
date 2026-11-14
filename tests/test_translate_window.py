#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文本翻译页与遮罩加载动画的用例

覆盖两件事：
1. 文本翻译页能输入、能拿到译文、失败与过期结果的处理
2. 截图遮罩等结果时只有一个小转圈，不再把"识别并翻译中"写在选区中央

用例刻意不落地到线程池与 Qt 事件循环：本机（Python 3.14 + PyQt5 5.15.10）
上一旦在 QtCore 之后加载 onnxruntime 会直接终止进程，而"起线程 + 跨线程信号 +
processEvents 忙等"会让这类冲突在整套用例连跑时偶发触发（实测约 20%）。
所以提交用记录型假线程池，回投用主线程直调（qt_dispatcher 在主线程就是同步执行），
既覆盖了参数、状态与回填，也不把用例变成概率性崩溃。
"""

import os

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication

from managers.translation import TRANSLATION_FAILURE_PREFIX


class _FakeConfig:
    """只提供翻译页用到的目标语言读取"""

    def get_target_lang(self):
        """默认目标语言"""
        return 'zh'


class _FakeTranslationManager:
    """返回固定译文的假管理器：用例不碰网络"""

    def __init__(self, reply='你好，世界'):
        """
        Args:
            reply (str): perform_translation 的返回值
        """
        self.reply = reply
        self.calls = []

    def get_target_languages(self):
        """目标语言列表"""
        from managers.providers.base import TARGET_LANGUAGES
        return TARGET_LANGUAGES

    def perform_translation(self, text, target_lang=None, **kwargs):
        """记录调用并返回预设译文"""
        self.calls.append((text, target_lang))
        return self.reply


class _FakePool:
    """
    记录型线程池：只记下提交的参数，不真的起线程

    submit 返回一个非 None 的句柄，表示"已受理"；需要模拟拒收时传 accept=False。
    """

    def __init__(self, accept=True):
        self.accept = accept
        self.calls = []

    def submit(self, func, *args):
        """记录一次提交"""
        self.calls.append((func, args))
        return object() if self.accept else None


class _FakeMainApp:
    """翻译页只需要配置与翻译管理器两个依赖"""

    def __init__(self, manager):
        self.config_manager = _FakeConfig()
        self.translation_manager = manager


def _make_window(qapp, manager):
    """建一个不显示的翻译页（用例不弹窗，避免打断人）"""
    from managers.translate_window import TranslateWindow
    return TranslateWindow(_FakeMainApp(manager))


def _use_pool(monkeypatch, pool):
    """把窗口模块里的线程池换成记录型假池"""
    import managers.translate_window as tw
    monkeypatch.setattr(tw, 'thread_pool_manager', pool, raising=True)


def test_translate_empty_input_does_not_start(qapp, monkeypatch):
    """空输入直接提示，不进忙碌态、也不发请求"""
    pool = _FakePool()
    _use_pool(monkeypatch, pool)
    manager = _FakeTranslationManager()
    window = _make_window(qapp, manager)
    try:
        window._on_translate()
        assert window.status_label.text() == '请先输入要翻译的文本'
        assert window._busy is False
        assert pool.calls == []
    finally:
        window.close()


def test_translate_submits_text_and_language(qapp, monkeypatch):
    """提交给后台的是去空白后的原文与当前目标语言，同时进入忙碌态"""
    pool = _FakePool()
    _use_pool(monkeypatch, pool)
    manager = _FakeTranslationManager()
    window = _make_window(qapp, manager)
    try:
        window.source_edit.setPlainText('  Hello, world  ')
        window._on_translate()

        assert len(pool.calls) == 1, '没有提交翻译任务'
        _func, args = pool.calls[0]
        assert args == ('Hello, world', 'zh', 1), f'提交参数不对: {args}'

        assert window._busy is True
        assert window.translate_button.isEnabled() is False
        assert window.lang_combo.isEnabled() is False
        assert window.spinner._timer.isActive() is True, '忙碌时转圈没转起来'
    finally:
        window.close()


def test_translate_rejected_submission_recovers(qapp, monkeypatch):
    """线程池拒收时当场复原按钮，并给出可重试提示"""
    _use_pool(monkeypatch, _FakePool(accept=False))
    window = _make_window(qapp, _FakeTranslationManager())
    try:
        window.source_edit.setPlainText('Hello')
        window._on_translate()
        assert window._busy is False
        assert window.translate_button.isEnabled() is True
        assert '后台任务无法启动' in window.status_label.text()
    finally:
        window.close()


def test_translate_result_fills_and_stops_spinner(qapp):
    """后台译文回投主线程后回填结果区，按钮恢复、转圈停止"""
    manager = _FakeTranslationManager(reply='你好，世界')
    window = _make_window(qapp, manager)
    try:
        window._request_id = 1
        window._set_busy(True)
        # 直接在主线程跑后台函数：qt_dispatcher 判定已在主线程，同步执行回调
        window._run_translation('Hello, world', 'zh', 1)

        assert manager.calls == [('Hello, world', 'zh')]
        assert window.result_edit.toPlainText() == '你好，世界'
        assert window._busy is False
        assert window.spinner._timer.isActive() is False
        assert window.copy_button.isEnabled() is True
        assert window.translate_button.isEnabled() is True
    finally:
        window.close()


def test_translate_failure_shows_error_status(qapp):
    """翻译链全挂时给出红字提示，结果区保持空白"""
    manager = _FakeTranslationManager(reply=f'{TRANSLATION_FAILURE_PREFIX}] hi')
    window = _make_window(qapp, manager)
    try:
        window._request_id = 1
        window._set_busy(True)
        window._run_translation('hi', 'zh', 1)

        assert '翻译失败' in window.status_label.text(), '失败时没有可见提示'
        assert window.status_label.objectName() == 'statusError'
        assert window.result_edit.toPlainText() == ''
        assert window.copy_button.isEnabled() is False
        assert window.spinner._timer.isActive() is False
    finally:
        window.close()


def test_ctrl_enter_triggers_translation(qapp, monkeypatch):
    """输入框里 Ctrl+Enter 等同于点翻译按钮"""
    pool = _FakePool()
    _use_pool(monkeypatch, pool)
    window = _make_window(qapp, _FakeTranslationManager())
    try:
        window.source_edit.setPlainText('Hello')
        event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Return, Qt.ControlModifier)
        QApplication.sendEvent(window.source_edit, event)
        assert pool.calls, 'Ctrl+Enter 没有触发翻译'
    finally:
        window.close()


def test_auto_translate_after_input_stops(qapp, monkeypatch):
    """输入/粘贴停止后自动翻译；同一份文本不重复提交"""
    pool = _FakePool()
    _use_pool(monkeypatch, pool)
    window = _make_window(qapp, _FakeTranslationManager())
    try:
        window.source_edit.setPlainText('Hello')
        assert window._auto_timer.isActive(), '原文变化后没有安排自动翻译'

        window._on_auto_translate()
        assert len(pool.calls) == 1, '停止输入后没有自动翻译'
        _func, args = pool.calls[0]
        assert args == ('Hello', 'zh', 1), f'自动翻译提交参数不对: {args}'

        # 文本与目标语言都没变：再次触发不应重复请求
        window._on_auto_translate()
        assert len(pool.calls) == 1, '同一份文本被重复自动翻译'
    finally:
        window.close()


def test_auto_translate_defers_while_busy(qapp, monkeypatch):
    """请求进行中继续输入：等结果回来再补一次，不并发提交"""
    pool = _FakePool()
    _use_pool(monkeypatch, pool)
    window = _make_window(qapp, _FakeTranslationManager())
    try:
        window.source_edit.setPlainText('Hello')
        window._on_auto_translate()
        assert window._busy is True

        window.source_edit.setPlainText('Hello world')
        window._on_auto_translate()
        assert len(pool.calls) == 1, '忙碌时仍并发提交了第二次翻译'
        assert window._auto_pending is True, '没有记下待补发的自动翻译'

        # 结果回投后应重新安排自动翻译
        window._run_translation('Hello', 'zh', window._request_id)
        assert window._auto_timer.isActive(), '结果返回后没有补发自动翻译'
        window._on_auto_translate()
        assert len(pool.calls) == 2, '新文本没有被自动翻译'
    finally:
        window.close()


def test_clear_cancels_pending_auto_translate(qapp, monkeypatch):
    """清空原文要取消待触发的自动翻译，避免把空文本或旧请求发出去"""
    pool = _FakePool()
    _use_pool(monkeypatch, pool)
    window = _make_window(qapp, _FakeTranslationManager())
    try:
        window.source_edit.setPlainText('Hello')
        assert window._auto_timer.isActive()

        window._on_clear()
        assert window._auto_timer.isActive() is False, '清空后自动翻译定时器仍在跑'
        assert window._last_request is None, '清空后仍记着上一次请求'
        window._on_auto_translate()
        assert pool.calls == [], '清空后仍发起了翻译'
    finally:
        window.close()


def test_stale_result_is_dropped(qapp):
    """窗口已关（序号推进）后迟到的译文不再回填"""
    window = _make_window(qapp, _FakeTranslationManager(reply='迟到的译文'))
    try:
        stale_id = window._request_id
        window._request_id += 1        # 模拟关闭/新一轮请求
        window._apply_result(stale_id, '迟到的译文', '')
        assert window.result_edit.toPlainText() == ''
    finally:
        window.close()


def test_toolbar_busy_keeps_text(qapp):
    """重译中工具条只置灰，不写出"翻译中"字样（进度交给转圈）"""
    from managers.overlay_toolbar import OverlayToolbar

    bar = OverlayToolbar(None, [('zh', '中文')], current_lang='zh')
    try:
        assert bar.retranslate_button.text() == '重译'
        bar.set_busy(True)
        assert bar.retranslate_button.text() == '重译'
        assert bar.retranslate_button.isEnabled() is False
        assert bar.lang_combo.isEnabled() is False

        bar.set_busy(False)
        assert bar.retranslate_button.isEnabled() is True
        assert bar.lang_combo.isEnabled() is True
    finally:
        bar.deleteLater()


def test_overlay_loading_spinner_replaces_text(qapp):
    """遮罩等结果时只有转圈：定时器在跑、圆盘位置已知，源码里不再有提示文字"""
    from managers.screenshot import ScreenshotWidget

    widget = ScreenshotWidget()
    try:
        widget._result_rect = QRect(10, 10, 240, 120)
        widget._loading = True
        widget._start_loading_spinner()
        assert widget._spinner_timer is not None
        assert widget._spinner_timer.isActive(), '加载动画定时器未启动'

        widget.grab()      # 走一次绘制，圆盘位置才会被记下来
        assert widget._spinner_rect is not None
        assert not widget._spinner_rect.isEmpty()

        angle = widget._spinner_angle
        widget._tick_loading_spinner()
        assert widget._spinner_angle != angle, '角度没有推进，动画是静止的'

        widget._stop_loading_spinner()
        assert widget._spinner_timer.isActive() is False
    finally:
        widget.close()

    source_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'managers', 'screenshot.py')
    with open(source_path, encoding='utf-8') as fp:
        source = fp.read()
    # 加载态走的是转圈绘制，不再走 "文字胶囊"
    assert '_draw_loading_spinner(painter, rect)' in source, '加载态没有画转圈'
    assert "_draw_pill(painter, rect, '识别并翻译中" not in source, \
        '加载态又出现了文字提示'


# ---------------------------------------------------------------- 自动识别中英互译

def test_auto_detect_translates_chinese_to_english(qapp, monkeypatch):
    """默认自动识别：中文原文提交的目标语言是英文"""
    pool = _FakePool()
    _use_pool(monkeypatch, pool)
    window = _make_window(qapp, _FakeTranslationManager())
    try:
        assert window.lang_combo.currentData() == 'auto', '默认应为自动识别'
        window.source_edit.setPlainText('你好，世界')
        assert window.current_language('你好，世界') == 'en'
        assert '中文 → 译为英文' in window.hint_label.text()
        window._on_translate()
        _func, args = pool.calls[0]
        assert args == ('你好，世界', 'en', 1), f'中文没译为英文: {args}'
    finally:
        window.close()


def test_auto_detect_translates_english_to_chinese(qapp, monkeypatch):
    """英文原文提交的目标语言是中文；同一份文本不重复自动翻译"""
    pool = _FakePool()
    _use_pool(monkeypatch, pool)
    window = _make_window(qapp, _FakeTranslationManager())
    try:
        window.source_edit.setPlainText('Hello, world')
        assert window.current_language('Hello, world') == 'zh'
        assert '英文 → 译为中文' in window.hint_label.text()
        window._on_auto_translate()
        window._on_auto_translate()
        assert len(pool.calls) == 1, '自动识别结果没参与去重'
        assert pool.calls[0][1][1] == 'zh'
    finally:
        window.close()


def test_manual_language_overrides_auto_detection(qapp, monkeypatch):
    """手动选定目标语言时不再按原文自动切换"""
    pool = _FakePool()
    _use_pool(monkeypatch, pool)
    window = _make_window(qapp, _FakeTranslationManager())
    try:
        window.source_edit.setPlainText('你好，世界')
        window.lang_combo.setCurrentIndex(window.lang_combo.findData('zh'))
        window._on_auto_translate()
        assert window.current_language('你好，世界') == 'zh'
        assert pool.calls[0][1] == ('你好，世界', 'zh', 1)
    finally:
        window.close()


def test_translate_page_combo_has_drawn_chevron(qapp):
    """文本翻译页的下拉也要用自绘箭头控件，不能是样式表方块"""
    from managers.toolbar_combo import ChevronComboBox
    window = _make_window(qapp, _FakeTranslationManager())
    try:
        assert isinstance(window.lang_combo, ChevronComboBox)
    finally:
        window.close()


def test_clear_invalidates_inflight_request(qapp, monkeypatch):
    """忙碌时点清空：作废请求序号、立刻结束忙碌态，旧译文不得回填"""
    pool = _FakePool()
    _use_pool(monkeypatch, pool)
    window = _make_window(qapp, _FakeTranslationManager('迟到的译文'))
    try:
        window.source_edit.setPlainText('Hello')
        window._on_translate()
        _func, args = pool.calls[0]
        request_id = args[2]
        assert window._busy is True

        window._on_clear()
        assert window._busy is False, '清空后按钮还灰着'
        assert window._request_id > request_id, '没有作废在飞请求'

        window._apply_result(request_id, '迟到的译文', '')
        assert window.result_edit.toPlainText() == '', '清空后旧译文又冒出来了'
        assert window.copy_button.isEnabled() is False

        # 清空后仍能正常发起新请求
        window.source_edit.setPlainText('World')
        window._on_translate()
        assert len(pool.calls) == 2 and pool.calls[1][1][2] == window._request_id
    finally:
        window.close()


def test_source_language_detection_handles_mixed_and_other_scripts(qapp):
    """中文夹英文按比例判方向；日文/韩文/纯符号不再被误判成中文或英文"""
    from managers.translate_window import TranslateWindow

    cases = (
        ('你好，世界', 'zh'),
        ('Hello, world', 'en'),
        ('这是 Windows 快捷键', 'zh'),
        ('This is 中文 test', 'en'),
        ('こんにちは世界', None),      # 假名 + 汉字，不该当成中文
        ('안녕하세요', None),          # 谚文
        ('12345 !@#', None),          # 纯数字/符号
    )
    for text, expected in cases:
        assert TranslateWindow._detect_source_language(text) == expected, text

    window = _make_window(qapp, _FakeTranslationManager())
    try:
        # 认不出语种时按全局目标语言兜底
        assert window._detect_target_language('12345 !@#') == 'zh'
    finally:
        window.close()
