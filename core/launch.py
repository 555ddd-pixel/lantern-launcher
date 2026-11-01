# -*- coding: utf-8 -*-
"""启动器核心：拼装 JVM/游戏参数并拉起游戏进程。

覆盖从远古 Alpha 到最新快照的参数格式：
  · 1.13+   arguments.game / arguments.jvm（带 rules 的对象数组）
  · 1.6-1.12 minecraftArguments 字符串
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import java as javamod
from . import net
from .auth import current_account
from .config import LOG_DIR, config
from .manifest import get_version_json
from .meta import (CP_SEP, OS_NAME, expand_args, resolve_libraries, resolve_meta,
                   rules_allow, version_dir)

LAUNCHER_NAME = "LaoZhangLauncher"
LAUNCHER_VERSION = "1.0.0"


class LaunchError(Exception):
    pass


@dataclass
class LaunchSpec:
    version_id: str
    cmd: list[str]
    cwd: Path
    java: javamod.JavaRuntime
    log_file: Path
    main_class: str
    detail: dict = field(default_factory=dict)


def _needs_download(runtime: Optional[javamod.JavaRuntime], required: int) -> bool:
    """判断是否需要另下一个 Java：太旧、或者老版本碰上 Java 9+ 都需要。"""
    if runtime is None:
        return True
    if runtime.major < required:
        return True
    # 1.12 及更早的版本在 Java 9+ 上会直接崩，需要 Java 8
    if required <= 8 and runtime.major >= 9:
        return True
    return False


def _natives_ready(version_id: str, meta: dict) -> tuple[Path, bool]:
    d = version_dir(version_id) / "natives"
    d.mkdir(parents=True, exist_ok=True)
    if any(d.iterdir()):
        return d, True
    return d, False


def build_classpath(meta: dict, version_id: str) -> list[str]:
    libs, _ = resolve_libraries(meta)
    jar_id = meta.get("jar") or version_id
    jar = version_dir(jar_id) / f"{jar_id}.jar"
    if not jar.exists():
        jar = version_dir(version_id) / f"{version_id}.jar"
    entries = [str(config.libraries_dir / lib.path) for lib in libs]
    if jar.exists():
        entries.append(str(jar))
    return entries


def prepare(
    version_id: str,
    emit: Optional[Callable[[str], None]] = None,
    auto_java: bool = True,
) -> LaunchSpec:
    say = emit or (lambda *_: None)

    raw = get_version_json(version_id)
    meta = resolve_meta(raw)
    config.ensure_dirs()

    # 整合包实例：用实例目录当 --gameDir（mods / config / saves 都隔离在实例里）
    game_dir = config.game_dir
    try:
        from .modpack import instance_game_dir
        game_dir = instance_game_dir(version_id) or config.game_dir
        game_dir.mkdir(parents=True, exist_ok=True)
        if game_dir != config.game_dir:
            say(f"实例目录：{game_dir}")
    except Exception:
        game_dir = config.game_dir

    account = current_account()
    required = (meta.get("javaVersion") or {}).get("majorVersion") or 8

    # ---- Java ----
    runtime: Optional[javamod.JavaRuntime] = None
    if config["java_path"]:
        runtime = javamod.inspect(Path(config["java_path"]))
    if runtime is None:
        runtime = javamod.pick(int(required))
    if config["auto_download_java"] and _needs_download(runtime, int(required)):
        say(f"本机没有合适的 Java {required}，自动下载中…")
        try:
            runtime = javamod.download(int(required))
        except Exception as e:  # noqa: BLE001
            say(f"自动下载 Java {required} 失败：{e}（继续尝试现有 Java）")
    if runtime is None:
        raise LaunchError("没有找到任何 Java，请在设置中指定，或开启自动下载")
    if runtime.major < int(required):
        say(f"提示：该版本建议 Java {required}，当前是 Java {runtime.major}，可能起不来")
    elif int(required) <= 8 and runtime.major >= 9:
        say(f"提示：该版本需要 Java 8，当前是 Java {runtime.major}，可能无法启动")

    # ---- 文件齐全性 ----
    jar_id = meta.get("jar") or version_id
    jar = version_dir(jar_id) / f"{jar_id}.jar"
    if not jar.exists():
        raise LaunchError(f"缺少客户端 jar，请先下载安装 {version_id}")

    natives_dir, ready = _natives_ready(version_id, meta)
    if not ready:
        from .installer import _extract_natives
        _, natives = resolve_libraries(meta)
        _extract_natives(natives, natives_dir)

    # ---- 参数上下文 ----
    cp_entries = build_classpath(meta, version_id)
    classpath = CP_SEP.join(cp_entries)
    assets_root = str(config.assets_dir)
    assets_index = (meta.get("assetIndex") or {}).get("id") or meta.get("assets") or "legacy"
    # 老版本（pre-1.6）从摊平的资源目录读声音/语言文件
    game_assets = assets_root
    for cand in (config.assets_dir / "virtual" / assets_index, config.game_dir / "resources"):
        if cand.exists():
            game_assets = str(cand)
            break

    ctx = {
        "auth_player_name": account["name"],
        "version_name": version_id,
        "game_directory": str(game_dir),
        "assets_root": assets_root,
        "game_assets": game_assets,
        "assets_index_name": assets_index,
        "auth_uuid": account.get("uuid", ""),
        "auth_access_token": account.get("access_token", "0"),
        "auth_session": f"token:{account.get('access_token', '0')}:{account.get('uuid', '')}",
        "user_type": account.get("user_type", "legacy"),
        "version_type": meta.get("type", "release"),
        "natives_directory": str(natives_dir),
        "launcher_name": LAUNCHER_NAME,
        "launcher_version": LAUNCHER_VERSION,
        "classpath": classpath,
        "classpath_separator": CP_SEP,
        "library_directory": str(config.libraries_dir),
        "resolution_width": str(config["window_width"]),
        "resolution_height": str(config["window_height"]),
        "clientid": "",
        "auth_xuid": "",
        "quickPlayPath": "",
        "quickPlaySingleplayer": "",
        "quickPlayMultiplayer": "",
        "quickPlayRealms": "",
    }

    # ---- JVM 参数 ----
    jvm_args: list[str] = [
        f"-Xmx{int(config['memory_mb'])}M",
        f"-Xms{int(config['min_memory_mb'])}M",
    ]
    if runtime.major >= 9:
        jvm_args += ["-XX:+UseG1GC", "-XX:+ParallelRefProcEnabled",
                     "-XX:MaxGCPauseMillis=50"]
    jvm_args += [
        "-Dfile.encoding=UTF-8",
        f"-Dminecraft.launcher.brand={LAUNCHER_NAME}",
        f"-Dminecraft.launcher.version={LAUNCHER_VERSION}",
    ]

    if config["jvm_args"]:
        jvm_args += _split_args(config["jvm_args"])

    args_obj = meta.get("arguments") or {}
    if args_obj.get("jvm"):
        jvm_args += expand_args(args_obj["jvm"], ctx)
    else:
        jvm_args += [f"-Djava.library.path={natives_dir}", "-cp", classpath]

    # 日志配置（1.7+）：让游戏按官方格式输出日志
    log_cfg = (((meta.get("logging") or {}).get("client")) or {})
    log_file_cfg = (log_cfg.get("file") or {})
    if log_file_cfg.get("id"):
        p = config.assets_dir / "log_configs" / log_file_cfg["id"]
        if p.exists():
            argument = log_cfg.get("argument") or "-Dlog4j.configurationFile=${path}"
            jvm_args.insert(0, argument.replace("${path}", str(p)))

    # ---- 游戏参数 ----
    if args_obj.get("game"):
        game_args = expand_args(args_obj["game"], ctx)
    elif meta.get("minecraftArguments"):
        game_args = expand_args(_split_args(meta["minecraftArguments"]), ctx)
    else:
        game_args = expand_args([
            "--username", "${auth_player_name}",
            "--version", "${version_name}",
            "--gameDir", "${game_directory}",
            "--assetsDir", "${assets_root}",
            "--assetIndex", "${assets_index_name}",
            "--uuid", "${auth_uuid}",
            "--accessToken", "${auth_access_token}",
            "--userType", "${user_type}",
        ], ctx)

    game_args += ["--width", str(config["window_width"]),
                  "--height", str(config["window_height"])]
    if config["game_args"]:
        game_args += _split_args(config["game_args"])

    main_class = meta.get("mainClass")
    if not main_class:
        raise LaunchError("版本 JSON 缺少 mainClass，文件可能损坏")

    cmd = [str(runtime.path)] + jvm_args + [main_class] + game_args

    # Windows 命令行 32K 上限：超长时把 JVM 参数写进 argfile（Java 9+ 支持 @file）
    if sum(len(a) + 1 for a in cmd) > 30000 and runtime.major >= 9:
        argfile = version_dir(version_id) / "launcher-args.txt"
        lines = [_argfile_quote(a) for a in jvm_args + [main_class]]
        argfile.write_text("\n".join(lines), encoding="utf-8")
        cmd = [str(runtime.path), f"@{argfile}"] + game_args

    log_file = LOG_DIR / f"{version_id}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    say(f"Java：{runtime.path}（{runtime.major}）")
    say(f"主类：{main_class}")
    return LaunchSpec(
        version_id=version_id, cmd=cmd, cwd=game_dir,
        java=runtime, log_file=log_file, main_class=main_class,
        detail={"classpath_len": len(classpath), "required_java": required},
    )


def _argfile_quote(arg: str) -> str:
    """argfile 里用正斜杠更安全（双引号内反斜杠是转义符）。"""
    arg = arg.replace("\\", "/")
    return f'"{arg}"' if (" " in arg or arg == "") else arg


def _split_args(text: str) -> list[str]:
    """按空白切分参数，支持双引号包裹（官方 minecraftArguments 的格式）。"""
    import shlex
    try:
        return shlex.split(text, posix=False)
    except ValueError:
        return text.split()


# ------------------------------------------------------------------ 启动


class GameProcess:
    def __init__(self, proc: subprocess.Popen, spec: LaunchSpec) -> None:
        self.proc = proc
        self.spec = spec
        self.lines: list[str] = []
        self._lock = threading.Lock()

    def tail(self, n: int = 60) -> str:
        with self._lock:
            return "\n".join(self.lines[-n:])

    @property
    def running(self) -> bool:
        return self.proc.poll() is None


def launch(spec: LaunchSpec,
           on_line: Optional[Callable[[str], None]] = None) -> GameProcess:
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            spec.cmd, cwd=str(spec.cwd),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
    except OSError as e:
        raise LaunchError(f"无法启动 Java 进程：{e}") from e

    gp = GameProcess(proc, spec)
    spec.log_file.parent.mkdir(parents=True, exist_ok=True)

    def pump() -> None:
        f = open(spec.log_file, "w", encoding="utf-8", buffering=1)
        buf = ""

        def emit(text: str) -> None:
            with gp._lock:
                gp.lines.append(text)
                if len(gp.lines) > 500:
                    del gp.lines[:200]
            try:
                f.write(text + "\n")
            except Exception:
                pass
            if on_line:
                try:
                    on_line(text)
                except Exception:
                    pass

        try:
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.readline()
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                lines, buf = prettify(buf)
                for line in lines:
                    emit(line)
            if buf.strip():
                emit(buf.rstrip())
        finally:
            try:
                f.write(f"\n[退出码] {proc.wait()}\n")
                f.close()
            except Exception:
                pass

    threading.Thread(target=pump, daemon=True).start()
    return gp


# ------------------------------------------------------- 日志美化（LegacyXMLLayout）


_EVENT_START = "<log4j:Event"
_EVENT_END = "</log4j:Event>"
_HEAD_RE = re.compile(
    r'logger="([^"]*)".*?timestamp="([^"]*)".*?level="([^"]*)".*?thread="([^"]*)"', re.S)
_MSG_RE = re.compile(r"<log4j:Message><!\[CDATA\[(.*?)\]\]></log4j:Message>", re.S)
_THROW_RE = re.compile(r"<log4j:Throwable><!\[CDATA\[(.*?)\]\]></log4j:Throwable>", re.S)


def _format_event(block: str) -> list[str]:
    head = _HEAD_RE.search(block)
    msg = _MSG_RE.search(block)
    throw = _THROW_RE.search(block)
    if not head:
        return []
    thread, ts, level = head.group(4), head.group(2), head.group(3)
    try:
        stamp = time.strftime("%H:%M:%S", time.localtime(int(ts) / 1000))
    except Exception:
        stamp = "--:--:--"
    body = (msg.group(1) if msg else "").strip("\n")
    out = []
    for i, line in enumerate(body.split("\n")):
        prefix = f"[{stamp}] [{thread}/{level}]" if i == 0 else " " * len(f"[{stamp}] ")
        out.append(f"{prefix}: {line}" if i == 0 else f"{prefix}{line}")
    if throw:
        out.extend(throw.group(1).split("\n"))
    return [o for o in out if o.strip()]


def prettify(buf: str) -> tuple[list[str], str]:
    """把 log4j 的 XML 事件流还原成可读文本；普通行原样透传。"""
    out: list[str] = []
    while True:
        i = buf.find(_EVENT_START)
        if i < 0:
            nl = buf.rfind("\n")
            if nl < 0:
                break
            plain, buf = buf[:nl], buf[nl + 1:]
            out.extend(x for x in plain.split("\n") if x.strip())
            break
        if i > 0:
            prefix = buf[:i]
            if prefix.strip():
                out.extend(x for x in prefix.split("\n") if x.strip())
            buf = buf[i:]
        j = buf.find(_EVENT_END)
        if j < 0:
            break
        block, buf = buf[: j + len(_EVENT_END)], buf[j + len(_EVENT_END):]
        out.extend(_format_event(block))
    return out, buf
