# -*- coding: utf-8 -*-
"""崩溃日志中文诊断：扫描游戏日志，给出人能看懂的原因和处理建议。

借鉴 PCL2 / HMCL 的崩溃分析思路：日志里其实写得很清楚，
但新手看不懂英文堆栈，这里把常见模式翻译成中文结论。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .config import LOG_DIR

# 规则按优先级从上到下匹配；level: error / warn / info
RULES = [
    {
        "id": "java_version",
        "patterns": [r"UnsupportedClassVersionError", r"class file version (\d+\.\d+)",
                     r"Unsupported major\.minor version", r"needs Java (\d+)"],
        "level": "error",
        "title": "Java 版本太低",
        "advice": "这个版本需要更高版本的 Java（1.18+ 要 Java 17，1.21+ 建议 Java 21）。"
                  "到「设置 → 下载与 Java」点自动下载，或手动选择对应版本的 java.exe。",
    },
    {
        "id": "java8_needed",
        "patterns": [r"1\.12(?:\.2)?[\s\S]{0,200}?java\.lang\.UnsupportedClassVersionError",
                     r"Java 9.*1\.12"],
        "level": "error",
        "title": "老版本用了新 Java",
        "advice": "1.12.2 及更早的版本必须用 Java 8 启动。到「设置 → 下载与 Java」"
                  "下载 Java 8 并指定路径，别用 Java 17/21。",
    },
    {
        "id": "out_of_memory",
        "patterns": [r"OutOfMemoryError", r"Java heap space", r"GC overhead limit exceeded"],
        "level": "error",
        "title": "分配的内存不够",
        "advice": "内存被撑爆了。到「设置 → 内存优化」切到更激进的预设或手动加大内存；"
                  "如果是大型整合包，建议至少 6～8 GB 并关闭其它占内存的程序。",
    },
    {
        "id": "mod_conflict",
        "patterns": [r"ModLoadingException", r"LoaderExceptionModCrash", r"mod loading error",
                     r"DuplicateMod", r"mod .* failed to load", r"MixinApplyError",
                     r"Mixin apply for mod .* failed"],
        "level": "error",
        "title": "模组冲突或加载失败",
        "advice": "某个模组加载失败。看日志里出错的模组名，先在「模组」页临时禁用它，"
                  "再确认模组版本和当前 MC 版本、加载器（Forge/Fabric）是否匹配。",
    },
    {
        "id": "missing_dependency",
        "patterns": [r"MissingDependenciesException", r"missing dependenc", r"requires mod",
                     r"Missing required dependency"],
        "level": "error",
        "title": "模组缺前置",
        "advice": "有模组依赖的前置没装。按日志提示补装对应前置模组（比如 JEI、Architectury API 等）。",
    },
    {
        "id": "loader_mismatch",
        "patterns": [r"Forge.*Fabric|Fabric.*Forge", r"Invalid mod file format",
                     r"is not a valid mod file", r"Wrong mod loader"],
        "level": "error",
        "title": "加载器不匹配",
        "advice": "把 Forge 模组放进了 Fabric 实例（或反过来）。到「模组」页删掉不匹配的模组，"
                  "或者重新安装对应加载器的版本。",
    },
    {
        "id": "class_missing",
        "patterns": [r"NoClassDefFoundError", r"ClassNotFoundException", r"Could not find or load main class"],
        "level": "error",
        "title": "版本文件缺失或损坏",
        "advice": "游戏核心文件不完整。到「版本」页删掉这个版本，重新下载安装一次即可。",
    },
    {
        "id": "native_lib",
        "patterns": [r"no lwjgl", r"Failed to load native", r"java\.library\.path",
                     r"UnsatisfiedLinkError", r"natives"],
        "level": "error",
        "title": "本地库没加载成功",
        "advice": "显卡/音频的本地库加载失败。先重装该版本；如果还不行，"
                  "更新显卡驱动并检查是否装了 32 位/64 位混用的 Java。",
    },
    {
        "id": "opengl",
        "patterns": [r"GLException", r"OpenGL.*(error|failed|not supported)", r"Pixel format",
                     r"Failed to create (GL|window)"],
        "level": "warn",
        "title": "显卡驱动或渲染问题",
        "advice": "更新显卡驱动后重试；笔记本请确认游戏用的是独立显卡。",
    },
    {
        "id": "account",
        "patterns": [r"Failed to verify", r"Invalid token", r"authentication", r"登录失败",
                     r"Account.*expired"],
        "level": "warn",
        "title": "账号验证失败",
        "advice": "正版账号的登录凭证过期了。到「主页」退出后重新登录一次微软账号。",
    },
    {
        "id": "encoding",
        "patterns": [r"锟斤拷|[\uFFFD]{3,}", r"Malformed.*encoding"],
        "level": "info",
        "title": "日志乱码",
        "advice": "日志里有乱码，不影响游戏；如果出现异常，把截图发给我看。",
    },
]


def _first_evidence(text: str, pattern: str, limit: int = 160) -> str:
    """取第一条命中的日志行作为证据，截短避免刷屏。"""
    try:
        m = re.search(pattern, text, re.I | re.S)
    except re.error:
        return ""
    if not m:
        return ""
    start = text.rfind("\n", 0, m.start()) + 1
    end = text.find("\n", m.end())
    if end < 0:
        end = min(len(text), m.end() + 60)
    line = text[start:end].strip()
    return line[:limit]


def analyze(text: str, max_issues: int = 5) -> dict:
    """分析日志文本，返回中文诊断结果。"""
    text = text or ""
    issues = []
    for rule in RULES:
        hit = None
        for pattern in rule["patterns"]:
            ev = _first_evidence(text, pattern)
            if ev:
                hit = ev
                break
        if hit:
            issues.append({
                "id": rule["id"],
                "level": rule["level"],
                "title": rule["title"],
                "advice": rule["advice"],
                "evidence": hit,
            })
        if len(issues) >= max_issues:
            break

    # 抽取崩溃现场的第一条 Caused by，帮助定位真正原因
    cause = ""
    m = re.search(r"Caused by:\s*([^\n]{1,200})", text)
    if m:
        cause = m.group(1).strip()

    if not issues:
        summary = ("没发现已知的崩溃模式。如果游戏确实闪退了，把日志全文发给维护者排查。" if text.strip()
                   else "日志是空的，启动一次游戏再分析。")
        level = "info"
    else:
        level = "error" if any(i["level"] == "error" for i in issues) else "warn"
        summary = f"发现 {len(issues)} 个可能的问题，最可能是：{issues[0]['title']}"

    return {
        "ok": True,
        "level": level,
        "count": len(issues),
        "summary": summary,
        "cause": cause,
        "issues": issues,
        "lines": len(text.splitlines()),
    }


def latest_log_text(max_bytes: int = 262144) -> str:
    """从日志目录取最新一份游戏日志的尾部文本（避免整文件太大）。"""
    try:
        files = [p for p in LOG_DIR.glob("*.log") if p.is_file()]
        if not files:
            return ""
        latest = max(files, key=lambda p: p.stat().st_mtime)
        with latest.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
        return data.decode("utf-8", "replace")
    except Exception:
        return ""


def diagnose_latest() -> dict:
    return analyze(latest_log_text())
