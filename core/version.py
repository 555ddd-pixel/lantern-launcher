# -*- coding: utf-8 -*-
"""启动器自身的版本信息。改版本号只需要改这里。"""

APP_NAME = "Lantern Launcher"
VERSION = "1.2.0"
BUILD = "2026-11-01"
CHANNEL = "stable"


def version_tuple(text: str) -> tuple:
    """把 "1.2.3" / "v1.2.3-beta" 变成可比较的元组。"""
    text = str(text).strip().lstrip("vV")
    core = text.split("-")[0].split("+")[0]
    parts = []
    for piece in core.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])
