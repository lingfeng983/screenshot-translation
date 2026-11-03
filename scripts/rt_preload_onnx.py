# -*- coding: utf-8 -*-
"""
冻结进程的运行时钩子：先把 ONNX Runtime 带起来

PyInstaller 在 main 之前会执行内置运行时钩子，其中 PyQt5 的钩子会先把 Qt 加载
进来。本机组合（Python 3.14 + PyQt5 5.15.10 + onnxruntime 1.31）里"先 Qt 后
onnxruntime"会在模块初始化阶段访问冲突、进程直接退出（源码态由 main.py 顶部的
预导入兜住，冻结态 main 还没开始跑）。自定义钩子排在内置钩子之前执行，正好补上
这个空档。
"""

try:
    import onnxruntime  # noqa: F401
except ImportError:
    pass
