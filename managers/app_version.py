#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
应用版本号

发布脚本（scripts/publish_release.py）在构建前会把这里的常量改成本次发版的
版本号，并随该版本一起提交；运行期不依赖 exe 的版本资源，源码态与打包态
读到的是同一个值。
"""

APP_VERSION = '1.9.0'


def current_version():
    """
    当前运行版本

    Returns:
        str: 形如 '1.8.0'
    """
    return APP_VERSION
