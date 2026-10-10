#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
脚本运行器：按定义顺序执行调用模块里的全部 test_* 用例

`python tests/test_xxx.py` 直接跑时走这里，不再手抄一份用例名单——抄漏的那几条
不会报错，只会让"全绿"少测几条（本仓库真就这么漏过九条）。需要 pytest fixture
的用例在这里跑不起来，把名字列出来而不是静默跳过。
"""

import inspect


def run_all(module_globals, success_line):
    """
    执行 module_globals 中的无参 test_* 用例

    Args:
        module_globals (dict): 调用方的 globals()
        success_line (str): 全部通过时打印的一行

    Returns:
        bool: 是否全部通过（首个失败即停，跟逐个手敲的行为一致）
    """
    runnable, needs_fixture = [], []
    for name, fn in module_globals.items():
        if not (name.startswith('test_') and callable(fn)):
            continue
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            params = {}
        (runnable if not params else needs_fixture).append((name, fn))

    print(f"发现 {len(runnable)} 条可直接执行的用例"
          f"（另有 {len(needs_fixture)} 条需要 pytest fixture）\n")
    for name, fn in runnable:
        try:
            fn()
        except Exception as e:
            print(f"❌ {name} 失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    if needs_fixture:
        print("以下用例只能由 pytest 跑（python -m pytest tests/ -q）:\n  "
              + "\n  ".join(name for name, _ in needs_fixture))
    print(success_line)
    return True
