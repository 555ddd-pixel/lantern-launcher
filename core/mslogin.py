# -*- coding: utf-8 -*-
"""微软登录的「浏览器授权 + 自动抓回调」实现。

做法和 PCL2 一样：不弹自己的表单，直接打开微软官方登录页，用户登录后浏览器会跳到
    https://login.live.com/oauth20_desktop.srf?code=...
这一步是跨域的，网页里的脚本读不到，所以：
  · 能用 Chromium 内核（Edge / Chrome）时，用它开一个「应用窗口」，附带给窗口开一个
    本机调试端口（--remote-debugging-port），后台每秒读一次窗口当前网址，
    一旦看到 code 就立刻关掉窗口并把授权码交给 auth 模块；
  · 找不到 Chromium 内核时退回系统默认浏览器，界面上给一个输入框，
    用户把地址栏链接粘回来同样能完成（不依赖任何外部服务）。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import uuid as uuidlib
from typing import Callable, Optional

from . import auth, net
from .config import LOG_DIR
from .desktop import find_browser
from .paths import DATA_DIR

TIMEOUT_SEC = 600          # 授权页最多等 10 分钟
POLL_SEC = 1.0
CALLBACK_HOST = "oauth20_desktop.srf"

_lock = threading.Lock()
_active: dict = {"state": "", "proc": None, "port": 0}


def _note(msg: str) -> None:
    print(msg)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "login.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _tabs(port: int) -> list:
    """读浏览器当前打开的页面（Chromium 调试协议的 HTTP 部分，本机免鉴权）。"""
    try:
        with net.open_local(f"http://127.0.0.1:{port}/json/list", timeout=1.2) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        return [t for t in data if t.get("type") == "page"]
    except Exception:  # noqa: BLE001
        return []


def _callback_url(port: int) -> str:
    for tab in _tabs(port):
        url = tab.get("url") or ""
        if CALLBACK_HOST in url and ("code=" in url or "error=" in url):
            return url
    return ""


def _kill(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
    except Exception:  # noqa: BLE001
        pass


def open_login_window(url: str) -> Optional[dict]:
    """用 Edge/Chrome 的应用窗口打开授权页，并开一个调试端口便于读回调。"""
    exe = find_browser()
    if not exe:
        return None
    port = _free_port()
    profile = DATA_DIR / ".cache" / "mslogin-profile"
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        str(exe),
        f"--app={url}",
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={port}",
        "--window-size=560,780",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter,msEdgeWelcomePage",
        "--disable-sync",
    ]
    try:
        flags = 0x00000200 if os.name == "nt" else 0   # CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(args, close_fds=True, creationflags=flags)
    except OSError as e:
        _note(f"[登录] 打开登录窗口失败：{e}")
        return None
    _note(f"[登录] 登录窗口已打开（pid {proc.pid}，调试端口 {port}）")
    return {"proc": proc, "port": port}


def _open_in_browser(url: str) -> bool:
    try:
        import webbrowser

        return bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001
        return False


def _finish(callback: str, expect_state: str,
            emit: Callable[[str], None]) -> None:
    """抓/贴到回调链接后：校验 → 换令牌 → 拉档案 → 收尾。"""
    try:
        auth._check_state(callback, expect_state)
        code = auth.parse_code(callback)
        acc = auth.complete_login(code, emit=emit)
        _note(f"[登录] 成功：{acc.get('name', '')}（{acc.get('uuid', '')}）")
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        _note(f"[登录] 失败：{msg}")
        auth._set(status="error", stage="登录失败", error=msg)


def _watch(expect_state: str, proc: Optional[subprocess.Popen], port: int,
           emit: Callable[[str], None]) -> None:
    deadline = time.time() + TIMEOUT_SEC
    closed_at = 0.0
    while time.time() < deadline:
        with _lock:
            if _active.get("state") != expect_state:
                return                     # 被新的登录请求接管了
        if port:
            url = _callback_url(port)
            if url:
                _finish(url, expect_state, emit)
                _kill(proc)
                return
        if proc is not None:
            if proc.poll() is None:
                closed_at = 0.0
            else:
                # 用户自己把窗口关了：再给两秒（可能刚跳转完我们还没读到）
                closed_at = closed_at or time.time()
                if time.time() - closed_at > 2:
                    auth._set(status="error", stage="登录窗口已关闭",
                              error="登录窗口被关掉了，可以重新点「开始登录」")
                    return
        time.sleep(POLL_SEC)

    auth._set(status="error", stage="登录超时",
              error="等了 10 分钟还没完成授权，请重新点「开始登录」")


def begin(emit: Callable[[str], None] = print) -> dict:
    """点「开始登录」：开登录页并开始等回调。"""
    state = uuidlib.uuid4().hex
    url = auth.auth_url(state)
    win = open_login_window(url)

    if win:
        mode, proc, port = "window", win["proc"], win["port"]
        stage = "已打开微软登录页，请在窗口里登录并授权"
    else:
        mode, proc, port = "browser", None, 0
        _open_in_browser(url)
        stage = "已在浏览器打开微软登录页，登录授权后把地址栏链接粘贴回启动器"

    with _lock:
        old = _active.get("proc")
        _active.update({"state": state, "proc": proc, "port": port})
    if old is not None and old is not proc:
        _kill(old)

    auth._set(status="waiting", stage=stage, error="", name="", uuid="",
              uri=url, mode=mode, started=time.time())
    _note(f"[登录] 开始登录（{mode}）")
    threading.Thread(target=_watch, args=(state, proc, port, emit),
                     daemon=True, name="lantern-ms-login").start()
    return {"url": url, "mode": mode, "state": state}


def submit(text: str, emit: Callable[[str], None] = print) -> dict:
    """手工粘贴回调链接（默认浏览器兜底，或窗口没自动抓到时用）。"""
    with _lock:
        expect = _active.get("state", "")
        proc = _active.get("proc")
    callback = (text or "").strip()
    auth._set(status="waiting", stage="正在校验你粘贴的链接…", error="")
    _finish(callback, expect, emit)
    _kill(proc)
    status = auth.login_status()
    if status["status"] == "error":
        raise RuntimeError(status.get("error") or "登录失败")
    return status


def submit_async(text: str, emit: Callable[[str], None] = print) -> None:
    """界面线程不等待：粘贴的链接交给后台校验，进度照常轮询 login/state。"""
    def run() -> None:
        try:
            submit(text, emit)
        except Exception:  # noqa: BLE001 - 失败原因已写进 state
            pass

    threading.Thread(target=run, daemon=True, name="lantern-ms-code").start()


def cancel() -> None:
    """关掉登录窗口、清理状态（切换账号类型 / 关闭弹层时用）。"""
    with _lock:
        proc = _active.get("proc")
        _active.update({"state": "", "proc": None, "port": 0})
    _kill(proc)
    if auth.login_status()["status"] == "waiting":
        auth.reset_state()
