# -*- coding: utf-8 -*-
"""运行位置解析：源码运行与打包成 exe 后都能找到正确的读写目录。

打包（PyInstaller onefile）后：
  * 只读资源（web 页面、图标）在 sys._MEIPASS 临时解包目录里；
  * 可写数据（config.json / .minecraft / runtime / logs）放在 exe 同级目录，
    保持绿色（便携）风格，不往系统目录里塞东西。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """只读资源根目录。"""
    if FROZEN:
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """可写数据根目录（源码运行时是项目根，打包后是 exe 所在目录）。"""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def exe_path() -> Path:
    """当前可执行文件路径（源码运行时返回入口脚本）。"""
    if FROZEN:
        return Path(sys.executable).resolve()
    return Path(sys.argv[0]).resolve()


def is_portable() -> bool:
    """exe 所在目录可写 → 便携模式。不可写时退回用户目录。"""
    base = app_dir()
    probe = base / ".write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """实际使用的数据目录。exe 放在只读位置（如 Program Files）时改写用户目录。"""
    if FROZEN and not is_portable():
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "LanternLauncher"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return app_dir()


RES_DIR = resource_dir()
DATA_DIR = data_dir()
