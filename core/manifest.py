# -*- coding: utf-8 -*-
"""版本清单：Minecraft 全部版本（正式版 / 快照 / 远古 Alpha·Beta）的获取与缓存。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import net
from .config import CACHE_DIR, config

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
MANIFEST_CACHE = CACHE_DIR / "version_manifest_v2.json"
MANIFEST_TTL = 600  # 10 分钟

TYPE_LABEL = {
    "release": "正式版",
    "snapshot": "快照",
    "old_beta": "远古 Beta",
    "old_alpha": "远古 Alpha",
}


@dataclass
class VersionInfo:
    id: str
    type: str
    url: str
    time: str = ""
    release_time: str = ""
    sha1: str = ""
    local: bool = False
    installed_java: bool = False

    @property
    def type_label(self) -> str:
        return TYPE_LABEL.get(self.type, self.type)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "type_label": self.type_label,
            "time": self.time,
            "release_time": self.release_time,
            "local": self.local,
        }


_manifest_cache: Optional[dict] = None
_manifest_time: float = 0.0


def fetch_manifest(force: bool = False) -> dict:
    """获取全部版本清单（优先本地缓存）。"""
    global _manifest_cache, _manifest_time

    if not force and _manifest_cache and time.time() - _manifest_time < MANIFEST_TTL:
        return _manifest_cache

    if not force and MANIFEST_CACHE.exists():
        age = time.time() - MANIFEST_CACHE.stat().st_mtime
        if age < MANIFEST_TTL:
            try:
                _manifest_cache = json.loads(MANIFEST_CACHE.read_text(encoding="utf-8"))
                _manifest_time = time.time()
                return _manifest_cache
            except Exception:
                pass

    mirror = net.resolve_mirror(config["mirror"])
    url = net.rewrite(MANIFEST_URL, mirror)
    try:
        data = net.get_json(url)
    except Exception:
        # 镜像挂了就退回官方源
        if url != MANIFEST_URL:
            data = net.get_json(MANIFEST_URL)
        else:
            raise

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _manifest_cache, _manifest_time = data, time.time()
    return data


def list_versions(force: bool = False) -> list[VersionInfo]:
    data = fetch_manifest(force=force)
    local_ids = {v.id for v in list_local_versions()}
    out = []
    for v in data.get("versions", []):
        out.append(
            VersionInfo(
                id=v["id"],
                type=v.get("type", "release"),
                url=v.get("url", ""),
                time=v.get("time", ""),
                release_time=v.get("releaseTime", ""),
                sha1=v.get("sha1", ""),
                local=v["id"] in local_ids,
            )
        )
    return out


def latest_release() -> str:
    data = fetch_manifest()
    return (data.get("latest") or {}).get("release", "")


def get_version_json(version_id: str, version_url: str = "", force: bool = False) -> dict:
    """获取某个版本的完整 JSON（含 libraries / assets / arguments）。"""
    cache = CACHE_DIR / "versions" / f"{version_id}.json"
    if not force and cache.exists() and cache.stat().st_size > 100:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass

    mirror = net.resolve_mirror(config["mirror"])
    candidates = []
    if mirror == "bmclapi":
        candidates.append(f"https://bmclapi2.bangbang93.com/version/{version_id}/json")
    if version_url:
        candidates.append(version_url)
    if version_url:
        candidates.append(net.rewrite(version_url, "bmclapi"))
    candidates.append(f"https://bmclapi2.bangbang93.com/version/{version_id}/json")

    seen, data, last = set(), None, None
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            data = net.get_json(url)
            if "id" in data:
                break
            data = None
        except Exception as e:  # noqa: BLE001
            last = e
    if data is None:
        raise RuntimeError(f"无法获取版本 {version_id} 的 JSON（{last}）")

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


@dataclass
class LocalVersion:
    id: str
    path: Path
    has_jar: bool = False
    loader: str = ""
    meta: dict = field(default_factory=dict)


def list_local_versions() -> list[LocalVersion]:
    """扫描 .minecraft/versions 下已安装的版本。"""
    root = config.versions_dir
    result: list[LocalVersion] = []
    if not root.exists():
        return result
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        js = d / f"{d.name}.json"
        if not js.exists():
            continue
        try:
            meta = json.loads(js.read_text(encoding="utf-8"))
        except Exception:
            continue
        jar = d / f"{d.name}.jar"
        inherit = meta.get("inheritsFrom")
        if not jar.exists():
            # 继承版本（Forge/Fabric 等）用的是父版本的 jar
            parent_jar = config.versions_dir / str(inherit) / f"{inherit}.jar"
            if not inherit:
                continue
            if not parent_jar.exists():
                continue
        loader = ""
        lid = (meta.get("id") or "").lower()
        name = d.name.lower()
        for key, label in (("fabric", "Fabric"), ("forge", "Forge"),
                           ("quilt", "Quilt"), ("neoforge", "NeoForge"),
                           ("optifine", "OptiFine")):
            if key in lid or key in name:
                loader = label
                break
        result.append(LocalVersion(id=d.name, path=d, has_jar=jar.exists(),
                                   loader=loader, meta=meta))
    return result
