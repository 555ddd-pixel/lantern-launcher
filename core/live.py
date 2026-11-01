# -*- coding: utf-8 -*-
"""实时监听 Minecraft 版本清单：出现新版本/新快照时立刻推给界面。

用 ETag / If-Modified-Since 做条件请求，没变化时服务器只回 304，
不浪费流量，所以可以放心高频轮询。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from . import net
from .config import CACHE_DIR, config
from .manifest import MANIFEST_CACHE, MANIFEST_URL, fetch_manifest

STATE_CACHE = CACHE_DIR / "live_state.json"
MAX_EVENTS = 40

_lock = threading.Lock()
_events: list[dict] = []
_unknown: Optional[set[str]] = None
_headers = {"User-Agent": net.USER_AGENT, "If-None-Match": "", "If-Modified-Since": ""}
_etag = ""
_last_modified = ""
_last_check = 0.0
_last_error = ""
_started = False


def _load() -> None:
    global _etag, _last_modified, _unknown
    if not STATE_CACHE.exists():
        return
    try:
        raw = json.loads(STATE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return
    _etag = str(raw.get("etag") or "")
    _last_modified = str(raw.get("last_modified") or "")
    ids = raw.get("ids")
    if isinstance(ids, list) and ids:
        _unknown = set(ids)


def _save() -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with _lock:
            payload = {"etag": _etag, "last_modified": _last_modified,
                       "ids": sorted(_unknown or set())}
        STATE_CACHE.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def _fetch(url: str) -> Optional[dict]:
    """条件请求版本清单。返回 None 表示 304 未修改。"""
    global _etag, _last_modified
    req = urllib.request.Request(url, headers={
        "User-Agent": net.USER_AGENT,
        **({"If-None-Match": _etag} if _etag else {}),
        **({"If-Modified-Since": _last_modified} if _last_modified else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            _etag = r.headers.get("ETag") or ""
            _last_modified = r.headers.get("Last-Modified") or ""
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None
        raise


def _announce(v: dict) -> None:
    with _lock:
        _events.append({
            "time": time.time(),
            "id": v.get("id", ""),
            "type": v.get("type", "release"),
            "url": v.get("url", ""),
            "label": {"release": "正式版", "snapshot": "快照",
                      "old_beta": "远古 Beta", "old_alpha": "远古 Alpha"
                      }.get(v.get("type", ""), v.get("type", "")),
        })
        del _events[:-MAX_EVENTS]


def poll(announce: bool = True) -> list[dict]:
    """检查一次。返回这次新出现的版本列表。"""
    global _unknown, _last_check, _last_error
    data = None
    try:
        data = _fetch(net.rewrite(MANIFEST_URL, net.resolve_mirror(config["mirror"])))
    except Exception:
        try:
            data = _fetch(MANIFEST_URL)
        except Exception as e:  # noqa: BLE001
            _last_error = str(e)
            _last_check = time.time()
            return []
    _last_check = time.time()
    _last_error = ""
    if data is None:
        return []  # 304，没变化

    versions = data.get("versions") or []
    ids = {str(v.get("id")) for v in versions}
    fresh: list[dict] = []
    with _lock:
        first_run = _unknown is None
        if first_run:
            _unknown = ids
        else:
            new_ids = ids - _unknown
            _unknown |= ids
            if new_ids:
                for v in versions:
                    if str(v.get("id")) in new_ids:
                        fresh.append(v)
                for v in reversed(fresh):
                    _announce(v)
        # 缓存文件一起更新，其他模块立刻能读到新清单
    if fresh:
        try:
            MANIFEST_CACHE.write_text(json.dumps(data, ensure_ascii=False),
                                      encoding="utf-8")
            from . import manifest as m
            m._manifest_cache = data          # noqa: SLF001 - 同一进程内共享缓存
            m._manifest_time = time.time()    # noqa: SLF001
        except Exception:
            pass
    _save()
    return fresh


def events_since(ts: float) -> list[dict]:
    with _lock:
        return [e for e in _events if e["time"] > ts]


def status() -> dict:
    with _lock:
        return {
            "events": list(_events[-10:]),
            "last_check": _last_check,
            "error": _last_error,
            "watching": bool(config["live_refresh"]),
            "interval": int(config["live_interval_sec"] or 300),
        }


def start_background() -> None:
    global _started
    if _started:
        return
    _started = True
    _load()

    def loop() -> None:
        time.sleep(4.0)
        while True:
            try:
                if config["live_refresh"]:
                    fresh = poll()
                    if fresh:
                        names = ", ".join(v.get("id", "") for v in fresh[:4])
                        print(f"[实时] 发现新版本：{names}")
            except Exception:
                pass
            try:
                interval = max(60, int(config["live_interval_sec"] or 300))
            except Exception:
                interval = 300
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()
