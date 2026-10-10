#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pytest 共用装置

- 项目根进 sys.path：用例直接 `import main` / `from managers import …`，
  不依赖从哪个目录起 pytest
- qapp：整个会话共用一个 QApplication
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        'markers', 'online: 需要真实网络的用例（设 ST_RUN_ONLINE=1 才跑）')
    config.addinivalue_line(
        'markers', 'smoke: 真的拉起 main.py，会注册全局热键（设 ST_RUN_SMOKE=1 才跑）')


@pytest.fixture(scope='session')
def qapp():
    """
    全局唯一且一直被持有的 QApplication

    引用必须留着：QApplication 一旦被 GC，底层 C++ 对象随之销毁，之后
    再构造任何 QWidget 都会触发 Qt 的 qFatal 直接结束进程（没有 Python
    异常、没有回溯）。
    """
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
