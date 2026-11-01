# -*- coding: utf-8 -*-
"""实时自更新：检查新版本 → 后台下载 → 替换自身并重启。

更新清单（JSON）格式::

    {
      "version": "1.1.1",
      "published": "2026-11-05",
      "notes": "修复了什么",
      "min_version": "1.0.0",
      "files": {
        "windows-x64": {"url": "https://example.com/LanternLauncher-1.1.1.exe",
                        "size": 12345678, "sha1": "..."}
      }
    }

清单可以放在 http(s) 地址上（在设置里填「更新源」），也可以直接放在
exe 同级的 update.json（离线/U 盘分发场景）。默认先看 update.json，
所以打包出来的程序开箱就能检查更新。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import net
from .config import CACHE_DIR, ROOT, config
from .paths import FROZEN, RES_DIR, exe_path
from .version import BUILD, VERSION, version_tuple

UPDATE_DIR = ROOT / "updates"
STATE_CACHE = CACHE_DIR / "update_state.json"
CHECK_TTL = 300  # 5 分钟内不重复请求

_lock = threading.Lock()

state: dict = {
    "current": VERSION,
    "build": BUILD,
    "latest": "",
    "has_update": False,
    "mandatory": False,
    "notes": "",
    "published": "",
    "size": 0,
    "sha1": "",
    "url": "",
    "source": "",
    "checked_at": 0.0,
    "checking": False,
    "error": "",
    "phase": "",          # "" | checking | downloading | ready | applying
    "progress": None,     # dict(ratio, done_bytes, total_bytes, speed)
    "downloaded": "",     # 已下载好的新版本 exe 路径
    "applying": False,
    "frozen": FROZEN,
    "exe": str(exe_path()),
}


# ---------------------------------------------------------------- 工具


def platform_key() -> str:
    bits = "x64" if sys.maxsize > 2 ** 32 else "x86"
    if sys.platform.startswith("win"):
        return f"windows-{bits}"
    if sys.platform == "darwin":
        return f"macos-{'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else bits}"
    return f"linux-{bits}"


def _sources() -> list[str]:
    """按优先级列出可能的清单地址。"""
    out = []
    custom = str(config["update_url"] or "").strip()
    if custom:
        out.append(custom)
    out.append(str(RES_DIR / "update.json"))
    out.append(str(ROOT / "update.json"))
    seen, uniq = set(), []
    for src in out:
        if src not in seen:
            seen.add(src)
            uniq.append(src)
    return uniq


def _read_manifest(src: str) -> dict:
    if src.startswith(("http://", "https://")):
        data = net.get_json(src, timeout=10, retries=2)
    else:
        p = Path(src)
        if not p.exists():
            raise FileNotFoundError(src)
        data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "version" not in data:
        raise ValueError("清单格式不对（缺少 version 字段）")
    return data


def _pick_file(manifest: dict) -> dict:
    files = manifest.get("files") or {}
    key = platform_key()
    entry = files.get(key) or files.get("all") or {}
    if not entry and manifest.get("url"):
        entry = {"url": manifest["url"],
                 "size": manifest.get("size", 0), "sha1": manifest.get("sha1", "")}
    return entry or {}


def _label(src: str) -> str:
    if src.startswith(("http://", "https://")):
        return src
    try:
        return str(Path(src).name)
    except Exception:
        return src


def _probe_size(url: str, timeout: float = 8.0) -> int:
    """清单里没写文件大小时，用 HEAD 探一下，好让进度条有分母。"""
    import urllib.request
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": net.USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def snapshot() -> dict:
    with _lock:
        return dict(state)

def _set(**kw) -> None:
    with _lock:
        state.update(kw)


def _load_cache() -> None:
    if not STATE_CACHE.exists():
        return
    try:
        raw = json.loads(STATE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return
    with _lock:
        for k in ("latest", "has_update", "notes", "published", "size", "sha1",
                  "url", "source", "checked_at"):
            if k in raw:
                state[k] = raw[k]
        # 缓存里的「有新版本」是按旧版本号算出来的，自己版本变了就作废
        if raw.get("current") and raw["current"] != VERSION:
            state.update(latest="", has_update=False, notes="", checked_at=0)


def _save_cache() -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with _lock:
            payload = {k: state[k] for k in ("current", "latest", "has_update", "notes",
                                             "published", "size", "sha1", "url",
                                             "source", "checked_at")}
        STATE_CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- 检查


def check(force: bool = False, emit: Optional[Callable[[str], None]] = None) -> dict:
    """检查是否有新版本。失败不抛异常，结果写在 state['error'] 里。"""
    log = emit or (lambda *_: None)
    with _lock:
        if state["checking"]:
            return dict(state)
        if (not force and state["checked_at"]
                and time.time() - state["checked_at"] < CHECK_TTL):
            return dict(state)
        state["checking"] = True
        state["error"] = ""

    errors = []
    try:
        for src in _sources():
            try:
                manifest = _read_manifest(src)
            except Exception as e:  # noqa: BLE001 - 挨个源试
                errors.append(f"{_label(src)}: {e}")
                continue
            entry = _pick_file(manifest)
            latest = str(manifest.get("version") or "").strip()
            if not latest:
                errors.append(f"{_label(src)}: 缺少 version")
                continue
            has_new = version_tuple(latest) > version_tuple(VERSION)
            mandatory = False
            try:
                mandatory = has_new and version_tuple(VERSION) < version_tuple(
                    manifest.get("min_version") or "0")
            except Exception:
                mandatory = False
            with _lock:
                state.update(
                    latest=latest,
                    has_update=has_new,
                    mandatory=mandatory,
                    notes=str(manifest.get("notes") or ""),
                    published=str(manifest.get("published") or ""),
                    size=int(entry.get("size") or 0),
                    sha1=str(entry.get("sha1") or ""),
                    url=str(entry.get("url") or ""),
                    source=_label(src),
                    checked_at=time.time(),
                    error="",
                )
            if has_new:
                log(f"发现新版本 {latest}（当前 {VERSION}）")
            else:
                log(f"已是最新版本 {VERSION}")
            _save_cache()
            return snapshot()

        # 所有源都失败：区分「没配置」和「连不上」
        if all(not Path(s).exists() for s in _sources() if not s.startswith("http")):
            msg = "未配置更新源（可在设置里填写更新清单地址）"
        else:
            msg = "；".join(errors)[:300]
        with _lock:
            state.update(checked_at=time.time(), error=msg, checking=False)
        return snapshot()
    finally:
        with _lock:
            state["checking"] = False


def check_async(emit: Optional[Callable[[str], None]] = None, force: bool = False) -> None:
    threading.Thread(target=check, kwargs={"force": force, "emit": emit},
                     daemon=True).start()


# ---------------------------------------------------------------- 下载


def download(progress: Optional[Callable[[dict], None]] = None,
             emit: Optional[Callable[[str], None]] = None,
             cancel: Optional[threading.Event] = None) -> Path:
    """下载新版本到 updates/ 目录，返回文件路径。"""
    log = emit or (lambda *_: None)
    snap = snapshot()
    if not snap["has_update"]:
        raise RuntimeError("当前没有可用更新")
    url = snap["url"]
    if not url:
        raise RuntimeError("更新清单里没有当前平台的下载地址")
    if not url.startswith(("http://", "https://")):
        # 允许清单里的 url 是相对路径（相对清单文件所在目录）
        src = snap["source"]
        base = src.rsplit("/", 1)[0] if src.startswith("http") else str(Path(src).parent)
        url = f"{base}/{url.lstrip('./')}" if base else url
        if not url.startswith(("http://", "https://")):
            # 本地文件：直接复制
            srcp = Path(url)
            if not srcp.exists():
                raise RuntimeError(f"找不到新版本文件：{srcp}")
            UPDATE_DIR.mkdir(parents=True, exist_ok=True)
            dst = UPDATE_DIR / f"LanternLauncher-{snap['latest']}{srcp.suffix or '.exe'}"
            dst.write_bytes(srcp.read_bytes())
            _set(downloaded=str(dst), phase="ready")
            log(f"新版本已就绪：{dst.name}")
            return dst

    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    dst = UPDATE_DIR / f"LanternLauncher-{snap['latest']}.exe"
    tmp = dst.with_suffix(".exe.part")
    total = int(snap["size"] or 0) or _probe_size(url)
    meter = net.ProgressMeter(total, 0)
    _set(phase="downloading", progress=meter.snapshot(), error="")

    def tick(n: int) -> None:
        meter.add_bytes(n)
        s = meter.snapshot()
        _set(progress=s)
        if progress:
            progress(s)

    log(f"开始下载 {snap['latest']} …")
    try:
        # 上次中断留下的半截文件不能被当成已下载
        for stale in (tmp, dst):
            try:
                if stale.exists():
                    stale.unlink()
            except OSError:
                pass
        net.download_file(url, tmp, expected_size=None,
                          expected_sha1=snap["sha1"] or None,
                          progress=tick)
        if snap["size"] and tmp.stat().st_size != int(snap["size"]):
            raise RuntimeError(
                f"大小不符：期望 {snap['size']} 实际 {tmp.stat().st_size}")
        os.replace(tmp, dst)
    except Exception as e:  # noqa: BLE001
        try:
            tmp.unlink()
        except OSError:
            pass
        _set(phase="", error=str(e))
        raise
    _set(downloaded=str(dst), phase="ready", progress=meter.snapshot())
    log(f"下载完成，{dst.stat().st_size / 1048576:.1f} MB")
    return dst


# ---------------------------------------------------------------- 应用


def _write_apply_script(target: Path, new_exe: Path, pid: int) -> Path:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    script = UPDATE_DIR / "apply_update.cmd"
    body = f"""@echo off
setlocal
set "PID={pid}"
set "TARGET={target}"
set "NEW={new_exe}"
set "TL=%SystemRoot%\\System32\\tasklist.exe"
set "FIND=%SystemRoot%\\System32\\find.exe"
rem 用 ping 做延时：timeout 在无控制台的进程里会报错
:wait
"%TL%" /FI "PID eq %PID%" 2>nul | "%FIND%" "%PID%" >nul
if not errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto wait
)
move /y "%TARGET%" "%TARGET%.old" >nul 2>&1
copy /y "%NEW%" "%TARGET%" >nul 2>&1
if errorlevel 1 (
  ping -n 3 127.0.0.1 >nul
  copy /y "%NEW%" "%TARGET%" >nul 2>&1
)
start "" "%TARGET%"
ping -n 3 127.0.0.1 >nul
del /f /q "%TARGET%.old" >nul 2>&1
del /f /q "%NEW%" >nul 2>&1
del /f /q "%~f0" >nul 2>&1
exit
"""
    # cmd 默认按系统代码页（中文系统是 GBK）读取批处理
    try:
        script.write_text(body, encoding="gbk", errors="replace")
    except LookupError:
        script.write_text(body, encoding="utf-8")
    return script


def apply_and_restart(delay: float = 1.2) -> None:
    """替换自身并重启。调用后本进程会退出。

    主方案：Windows 允许重命名正在运行的 exe，所以直接
        旧 exe → .old，再把新 exe 放到原位，最后启动新进程。
    兜底：改名失败（被占用/杀软）时，交给一个分离的 cmd 脚本等进程退出后再替换。
    """
    snap = snapshot()
    if not FROZEN:
        raise RuntimeError("源码运行模式不支持自动替换，请直接运行新版本")
    if not snap["downloaded"]:
        raise RuntimeError("还没有下载新版本")
    target = exe_path()
    new_exe = Path(snap["downloaded"])
    if not new_exe.exists():
        raise RuntimeError("新版本文件已丢失，请重新下载")

    _set(applying=True, phase="applying")
    try:
        _swap_exe(target, new_exe)
        _spawn(target)
        _say(f"已替换为 {snap['latest']}，正在重启…")
    except Exception as e:  # noqa: BLE001 - 退回脚本方案
        _say(f"直接替换失败（{e}），改用脚本方式…")
        script = _write_apply_script(target, new_exe, os.getpid())
        _spawn_detached(["cmd", "/c", str(script)], cwd=script.parent)

    def bye() -> None:
        time.sleep(delay)
        os._exit(0)

    threading.Thread(target=bye, daemon=True).start()


def _say(text: str) -> None:
    with _lock:
        state["notes"] = state.get("notes") or ""
    try:
        print("[更新] " + text)
    except Exception:
        pass


def _swap_exe(target: Path, new_exe: Path) -> Path:
    """旧 exe 改名备份 → 新 exe 就位。失败时回滚。"""
    bak = target.with_name(target.name + ".old")
    try:
        if bak.exists():
            bak.unlink()
    except OSError:
        pass
    os.replace(target, bak)          # 运行中的 exe 可以改名
    try:
        shutil.copyfile(new_exe, target)
        shutil.copystat(new_exe, target)
    except Exception:
        try:
            if not target.exists() and bak.exists():
                os.replace(bak, target)
        except OSError:
            pass
        raise
    return bak


def _spawn(target: Path) -> None:
    """启动替换后的程序，沿用本次的启动参数。"""
    args = [str(target), "--upgrade-wait", str(os.getpid())]
    for a in sys.argv[1:]:
        if a.startswith("--port") or a == "--upgrade-wait":
            continue
        args.append(a)
    _spawn_detached(args, cwd=target.parent)


def _spawn_detached(cmd: list, cwd: Optional[Path] = None) -> None:
    base = 0
    if os.name == "nt":
        base = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | \
               getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        # 允许脱离当前作业对象（有些环境会把整棵进程树一起清掉）
        try:
            subprocess.Popen(cmd, close_fds=True,
                             creationflags=base | 0x01000000,  # CREATE_BREAKAWAY_FROM_JOB
                             cwd=str(cwd) if cwd else None)
            return
        except OSError:
            pass
    subprocess.Popen(cmd, close_fds=True, creationflags=base,
                     cwd=str(cwd) if cwd else None)


def cleanup_stale() -> None:
    """清掉上次更新留下的 .old 备份。"""
    try:
        exe = exe_path()
        bak = exe.with_name(exe.name + ".old")
        if bak.exists():
            bak.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------- 后台


_bg_started = False


def start_background(emit: Optional[Callable[[str], None]] = None) -> None:
    """启动时检查一次，之后按设置间隔轮询；发现新版本可选自动下载。"""
    global _bg_started
    if _bg_started:
        return
    _bg_started = True
    _load_cache()
    # .old 备份此时可能还被旧进程占用，稍后再清一次
    cleanup_stale()
    threading.Timer(6.0, cleanup_stale).start()

    def loop() -> None:
        # 启动后稍等，别和版本清单抢带宽
        time.sleep(2.5)
        while True:
            try:
                if config["auto_check_update"]:
                    snap = check(force=True, emit=emit)
                    if (snap.get("has_update") and config["auto_download_update"]
                            and not snap.get("downloaded") and not snap.get("applying")):
                        try:
                            download(emit=emit)
                        except Exception as e:  # noqa: BLE001
                            _set(error=f"自动下载失败：{e}")
            except Exception as e:  # noqa: BLE001
                _set(error=str(e))
            try:
                minutes = max(5, int(config["update_interval_min"] or 30))
            except Exception:
                minutes = 30
            time.sleep(minutes * 60)

    threading.Thread(target=loop, daemon=True).start()
