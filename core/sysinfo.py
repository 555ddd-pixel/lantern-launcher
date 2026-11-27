# -*- coding: utf-8 -*-
"""系统信息：物理内存检测 + 智能内存推荐（零第三方依赖）。

PCL/HMCL 的「内存优化」本质就是按物理内存给一个合理的 -Xmx，并留足系统余量。
这里把推荐算法单独放出来，前端展示、一键优化、启动参数共用同一套逻辑。
"""
from __future__ import annotations

import ctypes
import os
import platform
import re
from pathlib import Path
from typing import Optional

# 三档预设：ratio 为占物理内存比例，min/max 为最终钳制范围（MB）
PROFILES = {
    "vanilla": {"label": "原版 / 轻量", "ratio": 0.16, "min": 1024, "max": 2048,
                "hint": "只玩原版或装几个小模组，别分太多，留着系统更流畅"},
    "light":   {"label": "少量模组", "ratio": 0.28, "min": 1536, "max": 6144,
                "hint": "十几到几十个模组，兼顾游戏与系统余量"},
    "heavy":   {"label": "大型整合包", "ratio": 0.45, "min": 2048, "max": 16384,
                "hint": "上百模组的大型包，尽量多给，但别吃掉系统内存"},
}

# 内存按 128 MB 对齐，避免零碎数字
_ALIGN = 128


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _windows_memory() -> Optional[dict]:
    if os.name != "nt":
        return None
    try:
        stat = _MemoryStatusEx()
        stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        return {
            "total_mb": int(stat.ullTotalPhys // 1048576),
            "avail_mb": int(stat.ullAvailPhys // 1048576),
            "used_pct": int(stat.dwMemoryLoad),
            "source": "GlobalMemoryStatusEx",
        }
    except Exception:
        return None


def _linux_memory() -> Optional[dict]:
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("MemTotal", 0) // 1024
        avail = info.get("MemAvailable", info.get("MemFree", 0)) // 1024
        if total <= 0:
            return None
        return {"total_mb": int(total), "avail_mb": int(avail),
                "used_pct": int((total - avail) * 100 / total), "source": "/proc/meminfo"}
    except Exception:
        return None


def memory() -> dict:
    """返回物理内存信息；探测失败时给保守默认值，不让界面崩。"""
    data = _windows_memory() or _linux_memory()
    if data is None:
        data = {"total_mb": 8192, "avail_mb": 4096, "used_pct": 50, "source": "fallback"}
    data["cpu_count"] = os.cpu_count() or 4
    return data


def safe_max(total_mb: int) -> int:
    """最大安全分配：至少给系统留 1.5 GB 或 12%，取较大者。"""
    reserve = max(1536, int(total_mb * 0.12))
    return max(1024, int(total_mb) - reserve)


def recommend(total_mb: int, profile: str = "light") -> int:
    """按物理内存与使用场景推荐 -Xmx（MB）。"""
    p = PROFILES.get(profile) or PROFILES["light"]
    total = max(1024, int(total_mb))
    value = int(total * p["ratio"])
    value = (value // _ALIGN) * _ALIGN
    value = max(p["min"], min(value, p["max"]))
    value = min(value, safe_max(total))
    return max(1024, (value // _ALIGN) * _ALIGN)


def recommend_all(total_mb: int) -> dict:
    return {key: recommend(total_mb, key) for key in PROFILES}


def min_heap(memory_mb: int) -> int:
    """初始堆：给大内存一点起步空间，小内存保守，避免一上来就占满。"""
    value = max(512, min(1024, int(memory_mb) // 4))
    return (value // 128) * 128


def info() -> dict:
    """给 /api/sysinfo 用的完整视图。"""
    mem = memory()
    total = mem["total_mb"]
    return {
        **mem,
        "os": platform.system(),
        "arch": platform.machine(),
        "profiles": PROFILES,
        "recommend": recommend_all(total),
        "safe_max_mb": safe_max(total),
        "min_heap_mb": min_heap(total),
    }


def parse_memory_text(text: str) -> Optional[int]:
    """把 '8G'/'8192M' 这类输入转成 MB，给手动输入框做容错。"""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([gGmM]?)\s*", text or "")
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "M").upper()
    return int(num * 1024) if unit == "G" else int(num)
