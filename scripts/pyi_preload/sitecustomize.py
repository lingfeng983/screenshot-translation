# -*- coding: utf-8 -*-
"""
PyInstaller 隔离构建子进程的预加载垫片

PyInstaller 收集二进制依赖时，会把全部已收集包按"Qt 绑定在前、其余在后"的
固定顺序在每个隔离子进程里导入一次。本机组合（Python 3.14 + PyQt5 5.15.10 +
onnxruntime 1.31）先导入 QtCore、再导入 onnxruntime 会在模块初始化阶段访问
冲突，子进程直接消失，构建报 SubprocessDiedError。

sitecustomize 在解释器启动时自动执行，早于 PyInstaller 的导入序列；这里先把
onnxruntime 带起来，后续再导入 Qt 就不会触发冲突。只在构建期生效，不会进产物。
"""

try:
    import onnxruntime  # noqa: F401
except ImportError:
    pass
