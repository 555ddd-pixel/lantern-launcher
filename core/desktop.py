# -*- coding: utf-8 -*-
"""桌面窗口层：把界面装进一个原生窗口，做成真正的桌面软件。

对外只暴露 DesktopApp：
    app = DesktopApp(port)
    app.run()          # 阻塞，直到窗口关闭 / 退出

窗口用 WebView2（pywebview）渲染，和系统里其他软件一样有标题栏、任务栏图标、
最小化 / 最大化。若本机缺 WebView2 运行时，可降级用 Edge/Chrome 的 --app 模式。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from .config import config
from .paths import DATA_DIR, RES_DIR
from .version import APP_NAME, VERSION

ICON_ICO = RES_DIR / "assets" / "lantern.ico"
ICON_PNG = RES_DIR / "assets" / "lantern.png"

WIN_DEFAULT = (1320, 880)
WIN_MIN = (980, 660)


# ------------------------------------------------------------------ 工具


def _geo() -> dict:
    """当前保存的窗口几何。"""
    g = config["app_window"] or {}
    out = {
        "x": int(g.get("x", -1)),
        "y": int(g.get("y", -1)),
        "w": max(WIN_MIN[0], int(g.get("w", WIN_DEFAULT[0]))),
        "h": max(WIN_MIN[1], int(g.get("h", WIN_DEFAULT[1]))),
        "maximized": bool(g.get("maximized", False)),
    }
    return out


def _screen_size() -> tuple:
    """主屏幕尺寸，用来纠正越界的窗口位置。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:  # noqa: BLE001
        return 1920, 1080


def _clamp(geo: dict) -> dict:
    sw, sh = _screen_size()
    if geo["w"] > sw:
        geo["w"] = sw
    if geo["h"] > sh:
        geo["h"] = sh
    # 位置无效（负值 / 跑到屏幕外）就交给系统居中
    if geo["x"] < 0 or geo["y"] < 0 or geo["x"] > sw - 120 or geo["y"] > sh - 120:
        geo["x"] = -1
        geo["y"] = -1
    return geo


def _ping(port: int, timeout: float = 0.4) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping",
                                    timeout=timeout) as r:
            return b'"ok"' in r.read()
    except Exception:  # noqa: BLE001
        return False


def _note(msg: str) -> None:
    """降级原因写进日志：窗口程序没有控制台，事后要能查。"""
    print(msg)
    try:
        from .config import LOG_DIR

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "window.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def find_browser() -> Optional[Path]:
    """找一个支持 --app 的 Chromium 内核浏览器（WebView2 不可用时的兜底）。"""
    local = os.environ.get("LOCALAPPDATA") or ""
    cands = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(local) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft/EdgeCore/msedge.exe",
    ]
    for p in cands:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    for name in ("chrome", "msedge"):
        hit = shutil.which(name)
        if hit:
            return Path(hit)
    return None


def open_app_window(url: str, width: int = WIN_DEFAULT[0],
                    height: int = WIN_DEFAULT[1]) -> Optional[subprocess.Popen]:
    """用浏览器内核以「应用窗口」形式打开：没有地址栏，看起来就是个独立软件。"""
    exe = find_browser()
    if not exe:
        return None
    profile = DATA_DIR / ".cache" / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        str(exe),
        f"--app={url}",
        f"--window-size={width},{height}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
    ]
    try:
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        return subprocess.Popen(args, close_fds=True, creationflags=flags)
    except OSError:
        return None


# ------------------------------------------------------------------ 主体


class DesktopApp:
    """原生窗口 + 托盘。run() 在主线程调用，服务由调用方先起好。"""

    def __init__(self, port: int, on_quit=None, fallback_browser: bool = True) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}/?window=1"
        self.on_quit = on_quit
        self.fallback_browser = fallback_browser
        self.window = None
        self.tray = None
        self._quitting = False
        self._geometry = _clamp(_geo())
        self._started_at = time.time()

    # ---------------- 对外：供 server 的 /api/focus、/api/app/quit 调用
    def focus(self) -> None:
        """把窗口从托盘/最小化状态拉回前台。"""
        w = self.window
        if w is None:
            return
        try:
            w.show()
        except Exception:  # noqa: BLE001
            pass
        try:
            w.restore()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._force_foreground()
        except Exception:  # noqa: BLE001
            pass

    def quit(self) -> None:
        """退出整个软件。"""
        if self._quitting:
            return
        self._quitting = True
        self._save_geometry()
        self._stop_tray()
        try:
            if self.window is not None:
                self.window.destroy()
        except Exception:  # noqa: BLE001
            pass
        # 窗口没关掉也不能让进程赖着
        threading.Timer(2.0, lambda: os._exit(0)).start()

    # ---------------- 内部
    def _force_foreground(self) -> None:
        """Windows 前台窗口切换受限制，这里用 SetForegroundWindow 补一刀。"""
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        hwnds = []

        def each(hwnd, _lparam):
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))  # type: ignore[attr-defined]
            if pid.value == os.getpid():
                buf = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)  # type: ignore[attr-defined]
                if buf.value and ctypes.windll.user32.IsWindowVisible(hwnd):  # type: ignore[attr-defined]
                    hwnds.append(hwnd)
            return True

        try:
            ctypes.windll.user32.EnumWindows(  # type: ignore[attr-defined]
                ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(each), 0)
            for hwnd in hwnds[:1]:
                ctypes.windll.user32.ShowWindow(hwnd, 9)          # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)    # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def _save_geometry(self) -> None:
        w = self.window
        if w is None:
            return
        geo = dict(self._geometry)
        try:
            if not geo.get("maximized"):
                if getattr(w, "width", None):
                    geo["w"] = int(w.width)
                if getattr(w, "height", None):
                    geo["h"] = int(w.height)
                if getattr(w, "x", None) is not None:
                    geo["x"] = int(w.x)
                if getattr(w, "y", None) is not None:
                    geo["y"] = int(w.y)
            config.update({"app_window": geo})
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 窗口事件
    def _on_closing(self):
        if self._quitting:
            return True
        self._save_geometry()
        # 关窗口 → 缩到托盘
        if bool(config["close_to_tray"]) and self.tray is not None:
            try:
                self.window.hide()
                self._notify("仍在后台运行", "点托盘图标可以重新打开主界面")
                return False
            except Exception:  # noqa: BLE001
                pass
        self._stop_tray()
        return True

    def _on_loaded(self) -> None:
        # 首帧渲染完再显示，避免白屏闪一下
        try:
            self.window.show()
        except Exception:  # noqa: BLE001
            pass
        if self._geometry.get("maximized"):
            try:
                self.window.maximize()
            except Exception:  # noqa: BLE001
                pass
        if bool(config["close_to_tray"]):
            self._start_tray()

    # 窗口状态变化都记下来，下次启动恢复原样
    def _on_resized(self, width, height) -> None:
        if not self._geometry.get("maximized"):
            self._geometry["w"] = int(width)
            self._geometry["h"] = int(height)

    def _on_moved(self, x, y) -> None:
        if not self._geometry.get("maximized"):
            self._geometry["x"] = int(x)
            self._geometry["y"] = int(y)

    def _on_maximized(self) -> None:
        self._geometry["maximized"] = True

    def _on_restored(self) -> None:
        self._geometry["maximized"] = False

    # ---------------- 托盘
    def _start_tray(self) -> None:
        if self.tray is not None:
            return
        try:
            import pystray
            from PIL import Image
        except Exception:  # noqa: BLE001 - 缺依赖就安静跳过，不影响主窗口
            return
        try:
            image = Image.open(ICON_PNG if ICON_PNG.exists() else ICON_ICO)
        except Exception:  # noqa: BLE001
            return

        menu = pystray.Menu(
            pystray.MenuItem("打开主界面", lambda *_: self.focus(), default=True),
            pystray.MenuItem("在浏览器中打开", lambda *_: self._open_in_browser()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda *_: self.quit()),
        )
        icon = pystray.Icon("lantern-launcher", image,
                            f"{APP_NAME} {VERSION}", menu)
        self.tray = icon
        threading.Thread(target=icon.run, daemon=True, name="lantern-tray").start()

    def _stop_tray(self) -> None:
        if self.tray is None:
            return
        try:
            self.tray.stop()
        except Exception:  # noqa: BLE001
            pass
        self.tray = None

    def _notify(self, title: str, message: str) -> None:
        if self.tray is None:
            return
        try:
            self.tray.notify(message, title)
        except Exception:  # noqa: BLE001
            pass

    def _open_in_browser(self) -> None:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{self.port}/")

    # ---------------- 主循环
    def run(self) -> None:
        """阻塞运行。返回时窗口已关闭（调用方负责退出进程）。"""
        try:
            import webview
        except Exception as e:  # noqa: BLE001
            _note(f"[窗口] 缺少 pywebview（{e}），改用浏览器窗口模式")
            self._run_fallback()
            return

        geo = self._geometry
        kwargs = dict(
            title=f"{APP_NAME} {VERSION}",
            url=self.url,
            width=geo["w"],
            height=geo["h"],
            min_size=WIN_MIN,
            resizable=True,
            background_color="#F7F5F1",
            text_select=False,
            easy_drag=False,
            confirm_close=False,
        )
        if geo["x"] >= 0 and geo["y"] >= 0:
            kwargs["x"] = geo["x"]
            kwargs["y"] = geo["y"]
        try:
            window = webview.create_window(**kwargs)
        except Exception as e:  # noqa: BLE001
            _note(f"[窗口] 创建窗口失败（{e}），改用浏览器窗口模式")
            self._run_fallback()
            return

        self.window = window
        window.events.loaded += self._on_loaded
        window.events.closing += self._on_closing
        window.events.resized += self._on_resized
        window.events.moved += self._on_moved
        window.events.maximized += self._on_maximized
        window.events.restored += self._on_restored

        storage = DATA_DIR / ".cache" / "webview"
        storage.mkdir(parents=True, exist_ok=True)
        try:
            webview.start(  # 阻塞到窗口关闭
                self._after_start,
                window,
                gui="edgechromium",
                debug=False,
                private_mode=False,
                storage_path=str(storage),
            )
        except Exception as e:  # noqa: BLE001
            _note(f"[窗口] WebView2 启动失败：{e}")
            _note("[窗口] 退回浏览器窗口模式；如需原生窗口请安装 WebView2 运行时")
            if self.fallback_browser:
                self._run_fallback()
            else:
                raise
        finally:
            self._stop_tray()

    def _after_start(self, window) -> None:
        """GUI 事件循环起来后调用（GUI 线程）。"""
        if bool(config["close_to_tray"]):
            self._start_tray()

    def _wait_forever(self) -> None:
        """降级模式：窗口在别的进程里，本进程保持服务常驻。"""
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return

    def _run_fallback(self) -> None:
        url = f"http://127.0.0.1:{self.port}/?window=1"
        geo = self._geometry
        self._start_tray()          # 没有原生窗口时，托盘是唯一的入口
        proc = open_app_window(url, geo["w"], geo["h"]) if self.fallback_browser else None
        if proc is not None:
            _note("[窗口] 已用浏览器内核的应用窗口打开（无地址栏）")
            try:
                proc.wait()         # 窗口关掉 → 程序退出
                return
            except Exception:  # noqa: BLE001
                pass
        else:
            import webbrowser
            webbrowser.open(url)
            _note("[窗口] 找不到 Chromium 内核浏览器，已在默认浏览器中打开")
        self._wait_forever()
