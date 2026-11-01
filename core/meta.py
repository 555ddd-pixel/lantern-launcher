# -*- coding: utf-8 -*-
"""版本元数据解析：rules 判定、库/资源展开、启动参数占位符替换。

这里实现的是 Minecraft 官方公开的启动协议，与 PCL2 无代码关系。
"""
from __future__ import annotations

import platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from . import net
from .config import config

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_64BIT = platform.machine().lower() in ("amd64", "x86_64", "arm64", "aarch64")

OS_NAME = "windows" if IS_WINDOWS else ("osx" if IS_MACOS else "linux")
ARCH_NAME = "x86_64" if IS_64BIT else "x86"
CP_SEP = ";" if IS_WINDOWS else ":"

# 当前不需要的 feature（demo / 快捷进入世界等），全部按 false 处理
FEATURES: dict[str, bool] = {
    "is_demo_user": False,
    "has_custom_resolution": False,
    "has_quick_plays_support": False,
    "is_quick_play_singleplayer": False,
    "is_quick_play_multiplayer": False,
    "is_quick_play_realms": False,
}


def _rule_matches(rule: dict) -> bool:
    os_rule = rule.get("os")
    if os_rule:
        name = os_rule.get("name")
        if name and name != OS_NAME:
            return False
        arch = os_rule.get("arch")
        if arch == "x86" and IS_64BIT:
            return False
        if arch in ("x86_64", "arm64") and ARCH_NAME != arch:
            return False
        ver = os_rule.get("version")
        if ver:
            try:
                if not re.search(ver, platform.release()):
                    return False
            except re.error:
                return False
    feats = rule.get("features")
    if feats:
        for key, expected in feats.items():
            if bool(FEATURES.get(key, False)) != bool(expected):
                return False
    return True


def rules_allow(rules: Optional[list]) -> bool:
    """按官方语义：默认禁止，逐条匹配后取最后一条 action。"""
    if not rules:
        return True
    allowed = False
    for rule in rules:
        if _rule_matches(rule):
            allowed = rule.get("action") == "allow"
    return allowed


# ---------------------------------------------------------------- 库


@dataclass
class Library:
    path: str                 # libraries 下的相对路径
    url: str = ""
    sha1: str = ""
    size: int = 0
    native: bool = False      # 是否需要解压到 natives 目录
    exclude: list[str] = field(default_factory=list)
    raw_name: str = ""

    @property
    def file_name(self) -> str:
        return Path(self.path).name


def maven_to_path(name: str, base_url: str = "") -> Optional[Library]:
    """把 group:artifact:version[:classifier][@ext] 转成路径与下载地址。"""
    ext = "jar"
    if "@" in name:
        name, ext = name.split("@", 1)
    parts = name.split(":")
    if len(parts) < 3:
        return None
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else ""
    fn = f"{artifact}-{version}"
    if classifier:
        fn += f"-{classifier}"
    fn += f".{ext}"
    rel = "/".join(group.split(".") + [artifact, version, fn])
    base = (base_url or "https://libraries.minecraft.net/").rstrip("/")
    if not base.endswith("/maven") and "minecraft.net" not in base:
        pass
    return Library(path=rel, url=f"{base}/{rel}", raw_name=name)


def resolve_libraries(meta: dict) -> tuple[list[Library], list[Library]]:
    """返回 (classpath 库, natives 库)。"""
    classpath: list[Library] = []
    natives: list[Library] = []
    seen: set[str] = set()

    for lib in meta.get("libraries", []):
        if not rules_allow(lib.get("rules")):
            continue
        downloads = lib.get("downloads") or {}
        artifact = downloads.get("artifact") or {}
        natives_map = lib.get("natives") or {}

        native_key = natives_map.get(OS_NAME)
        if native_key:
            native_key = native_key.replace("${arch}", "64" if IS_64BIT else "32")
            nat = (downloads.get("classifiers") or {}).get(native_key) or {}
            if nat.get("path") and nat["path"] not in seen:
                seen.add(nat["path"])
                natives.append(
                    Library(
                        path=nat["path"],
                        url=nat.get("url", ""),
                        sha1=nat.get("sha1", ""),
                        size=nat.get("size", 0),
                        native=True,
                        exclude=list((lib.get("extract") or {}).get("exclude") or []),
                        raw_name=lib.get("name", ""),
                    )
                )

        if artifact.get("path"):
            if artifact["path"] not in seen:
                seen.add(artifact["path"])
                classpath.append(
                    Library(
                        path=artifact["path"],
                        url=artifact.get("url", ""),
                        sha1=artifact.get("sha1", ""),
                        size=artifact.get("size", 0),
                        raw_name=lib.get("name", ""),
                    )
                )
        elif not artifact:
            # 没有 downloads 字段（部分第三方库 / 老 Forge 库），按 Maven 坐标推断
            m = maven_to_path(lib.get("name", ""), lib.get("url", ""))
            if m and m.path not in seen:
                seen.add(m.path)
                classpath.append(m)
    return classpath, natives


# ---------------------------------------------------------------- 参数


def expand_args(args: Iterable[Any], ctx: dict[str, str]) -> list[str]:
    out: list[str] = []
    for item in args:
        if isinstance(item, str):
            out.append(_sub(item, ctx))
        elif isinstance(item, dict):
            if rules_allow(item.get("rules")):
                value = item.get("value")
                if isinstance(value, str):
                    out.append(_sub(value, ctx))
                elif isinstance(value, list):
                    out.extend(_sub(v, ctx) for v in value)
    return [a for a in out if a != ""]


def _sub(text: str, ctx: dict[str, str]) -> str:
    if "${" not in text:
        return text

    def repl(m: re.Match) -> str:
        key = m.group(1)
        return str(ctx.get(key, m.group(0)))

    return re.sub(r"\$\{([a-zA-Z0-9_]+)\}", repl, text)


def asset_url(hash_: str) -> str:
    return f"https://resources.download.minecraft.net/{hash_[:2]}/{hash_}"


# ---------------------------------------------------------------- 合并 inheritsFrom


def resolve_meta(meta: dict, depth: int = 0) -> dict:
    """处理 inheritsFrom：安装版（Forge/Fabric）只有差异部分，需要与父版本合并。"""
    parent_id = meta.get("inheritsFrom")
    if not parent_id or depth > 5:
        return meta
    from .manifest import get_version_json  # 延迟导入，避免循环

    parent = resolve_meta(get_version_json(parent_id), depth + 1)
    merged = dict(parent)
    merged["id"] = meta.get("id") or parent.get("id")
    merged["type"] = meta.get("type", parent.get("type"))
    if meta.get("mainClass"):
        merged["mainClass"] = meta["mainClass"]
    merged["libraries"] = list(parent.get("libraries", [])) + list(meta.get("libraries", []))
    merged["arguments"] = _merge_arguments(parent, meta)
    if meta.get("minecraftArguments"):
        merged["minecraftArguments"] = meta["minecraftArguments"]
    merged["downloads"] = meta.get("downloads") or parent.get("downloads")
    merged["assetIndex"] = parent.get("assetIndex")
    merged["assets"] = parent.get("assets")
    merged["javaVersion"] = meta.get("javaVersion") or parent.get("javaVersion")
    merged["logging"] = parent.get("logging")
    # 客户端 jar 用的是父版本（原版）的
    merged["jar"] = meta.get("jar") or parent.get("id") or parent_id
    return merged


def _merge_arguments(parent: dict, child: dict) -> dict:
    pa = parent.get("arguments") or {}
    ca = child.get("arguments") or {}
    return {
        "game": list(pa.get("game", [])) + list(ca.get("game", [])),
        "jvm": list(ca.get("jvm", [])) + list(pa.get("jvm", [])),
    }


def required_java_major(meta: dict) -> int:
    jv = meta.get("javaVersion") or {}
    major = jv.get("majorVersion")
    if isinstance(major, int) and major > 0:
        return major
    # 没有声明时按发布年代粗略推断
    return 8


def version_dir(version_id: str) -> Path:
    return config.versions_dir / version_id
