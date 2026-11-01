# -*- coding: utf-8 -*-
"""全局配置：便携式存储，所有数据都放在程序目录下（类似 PCL 的绿色版）。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .paths import DATA_DIR, RES_DIR

# ROOT：可写数据目录（源码运行时=项目目录；打包后=exe 同级目录）
ROOT = DATA_DIR
# WEB_DIR 等只读资源从 RES_DIR 取，打包后在临时解包目录里
CONFIG_PATH = ROOT / "config.json"
CACHE_DIR = ROOT / ".cache"
LOG_DIR = ROOT / "logs"

_lock = threading.Lock()

DEFAULTS = {
    "game_dir": str(ROOT / ".minecraft"),
    "mirror": "auto",              # auto | official | bmclapi
    "java_path": "",               # 空 = 自动挑选
    "auto_download_java": True,
    "memory_mb": 2048,
    "min_memory_mb": 512,
    "player_name": "Steve",
    "account": {"type": "offline", "name": "Steve", "uuid": "", "access_token": ""},
    "jvm_args": "",
    "game_args": "",
    "concurrent": 16,
    "window_width": 854,           # 游戏窗口宽（不是启动器窗口）
    "window_height": 480,          # 游戏窗口高
    "font_anti_aliasing": True,
    "ms_client_id": "",            # 正版登录需要自备 Azure 公共客户端 ID

    # ---------- 桌面窗口 ----------
    "app_window": {"x": -1, "y": -1, "w": 1320, "h": 880, "maximized": False},
    "close_to_tray": False,        # 关窗口时缩到托盘而不是退出

    # ---------- 实时更新 ----------
    # 更新清单地址：留空则用内置默认值；也支持填本地文件路径或 http 地址
    "update_url": "",
    "auto_check_update": True,     # 启动时 + 定时检查启动器新版本
    "update_interval_min": 30,     # 自动检查间隔（分钟）
    "auto_download_update": False,  # 发现新版本后自动后台下载（不自动安装）
    "live_refresh": True,          # 实时监听 Minecraft 版本清单变化
    "live_interval_sec": 60,       # 版本清单实时检查间隔（秒）
}


class Config:
    def __init__(self) -> None:
        self._data = dict(DEFAULTS)
        self._data["account"] = dict(DEFAULTS["account"])
        self.load()

    # ---------- 读写 ----------
    def load(self) -> dict:
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if k in DEFAULTS:
                            self._data[k] = v
                    if isinstance(raw.get("account"), dict):
                        self._data["account"] = {**DEFAULTS["account"], **raw["account"]}
                    if isinstance(raw.get("app_window"), dict):
                        self._data["app_window"] = {**DEFAULTS["app_window"],
                                                    **raw["app_window"]}
            except Exception:
                pass  # 配置损坏时用默认值继续，不让启动器起不来
        return self._data

    def save(self) -> None:
        with _lock:
            tmp = CONFIG_PATH.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, CONFIG_PATH)

    # ---------- 访问 ----------
    def __getitem__(self, key):
        return self._data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self._data[key] = value

    def update(self, patch: dict) -> dict:
        for k, v in patch.items():
            if k in DEFAULTS:
                if k == "account" and isinstance(v, dict):
                    self._data["account"] = {**self._data.get("account", {}), **v}
                elif k == "app_window" and isinstance(v, dict):
                    self._data["app_window"] = {**self._data.get("app_window", {}), **v}
                elif k == "memory_mb":
                    try:
                        self._data[k] = max(512, min(65536, int(v)))
                    except Exception:
                        pass
                elif k == "concurrent":
                    try:
                        self._data[k] = max(1, min(64, int(v)))
                    except Exception:
                        pass
                elif k in ("window_width", "window_height"):
                    try:
                        self._data[k] = max(200, int(v))
                    except Exception:
                        pass
                else:
                    self._data[k] = v
        self.save()
        return self._data

    @property
    def data(self) -> dict:
        return self._data

    @property
    def game_dir(self) -> Path:
        return Path(self._data.get("game_dir") or DEFAULTS["game_dir"])

    @property
    def versions_dir(self) -> Path:
        return self.game_dir / "versions"

    @property
    def libraries_dir(self) -> Path:
        return self.game_dir / "libraries"

    @property
    def assets_dir(self) -> Path:
        return self.game_dir / "assets"

    def ensure_dirs(self) -> None:
        for p in (self.game_dir, self.versions_dir, self.libraries_dir,
                  self.assets_dir, CACHE_DIR, LOG_DIR):
            p.mkdir(parents=True, exist_ok=True)


config = Config()
