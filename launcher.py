# -*- coding: utf-8 -*-
"""Lantern Launcher 入口。

默认启动为桌面窗口（原生窗口 + 托盘）；也保留了浏览器 / 无界面模式。

用法：
    LanternLauncher.exe                 打开软件窗口（已在运行则把窗口拉到前台）
    LanternLauncher.exe --browser       用系统默认浏览器打开界面
    LanternLauncher.exe --headless      只起本地服务，不开界面（调试用）
    LanternLauncher.exe --port 9000     指定端口
    LanternLauncher.exe --version       打印版本
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _NullStream:
    """打包成窗口程序后没有控制台，print 不能炸。"""

    def write(self, *_a):
        return 0

    def flush(self):
        return None

    def isatty(self):
        return False

    def fileno(self):
        return -1


if sys.stdout is None or getattr(sys.stdout, "write", None) is None:
    sys.stdout = _NullStream()
if sys.stderr is None or getattr(sys.stderr, "write", None) is None:
    sys.stderr = _NullStream()

import server as srv  # noqa: E402
from core.version import APP_NAME, VERSION  # noqa: E402


def find_running(first: int = 8787, span: int = 20) -> int:
    """找已经在跑的实例，返回端口号；没有则 0。"""
    for port in range(first, first + span):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/ping", timeout=0.25) as r:
                data = json.loads(r.read().decode("utf-8"))
                if data.get("ok"):
                    return port
        except Exception:  # noqa: BLE001 - 端口没人监听会各种报错
            continue
    return 0


def wake_running(port: int) -> bool:
    """让已经运行的实例把窗口显示出来。返回是否成功唤起窗口。"""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/focus", timeout=1.5) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("focused"))
    except Exception:  # noqa: BLE001
        return False


def wait_pid_exit(pid: int, timeout: float = 25.0) -> None:
    """等一个进程退出（更新重启时，新实例要等旧实例让出端口）。"""
    import ctypes

    if not pid or pid <= 0:
        return
    try:
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = k32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return
        try:
            t0 = time.time()
            while time.time() - t0 < timeout:
                if k32.WaitForSingleObject(handle, 500) == 0:
                    return
        finally:
            k32.CloseHandle(handle)
    except Exception:
        time.sleep(1.0)


def run_window(port: int) -> int:
    """窗口模式：服务在后台线程，窗口占主线程。窗口关掉就退出。"""
    from core.desktop import DesktopApp
    from server import hooks

    srv.APP_MODE = "window"
    httpd, port = srv.start(port=port, open_browser=False)

    app = DesktopApp(port)
    hooks["focus"] = app.focus
    hooks["quit"] = app.quit
    try:
        app.run()
    finally:
        hooks["focus"] = None
        hooks["quit"] = None
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:  # noqa: BLE001
            pass
    return 0


def run_browser(port: int) -> int:
    """浏览器模式：起服务 + 打开默认浏览器，进程常驻直到 Ctrl+C。"""
    import webbrowser

    srv.APP_MODE = "browser"
    httpd, real = srv.start(port=port, open_browser=False)
    webbrowser.open(f"http://127.0.0.1:{real}/")
    print("在浏览器里使用；关闭这个窗口即退出。")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


def main() -> None:
    args = sys.argv[1:]
    if "--version" in args or "-v" in args:
        print(f"{APP_NAME} {VERSION}")
        return

    # 更新重启：新实例先等旧实例退出，再做单实例检测
    if "--upgrade-wait" in args:
        idx = args.index("--upgrade-wait")
        try:
            old_pid = int(args[idx + 1])
        except (IndexError, ValueError):
            old_pid = 0
        args = args[:idx] + args[idx + 2:]
        wait_pid_exit(old_pid)

    port = 8787
    if "--port" in args:
        try:
            port = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            port = 8787

    headless = "--headless" in args
    want_browser = "--browser" in args
    if headless:
        srv.APP_MODE = "headless"

    # ---- 单实例：已经有实例在跑，就别再开一个 ----
    if not headless and "--no-single" not in args:
        running = find_running(port)
        if running:
            if want_browser:
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{running}/")
                return
            if wake_running(running):
                print(f"{APP_NAME} 已在运行（端口 {running}），已把窗口切到前台。")
                return
            # 对方是降级/浏览器模式，唤起不了窗口，就把界面开出来
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{running}/?window=1")
            return

    if headless:
        srv.serve(port=port, open_browser=False)
    elif want_browser:
        run_browser(port)
    else:
        code = run_window(port)
        # 窗口已关：给后台线程一点收尾时间，然后干净退出
        try:
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(code)


if __name__ == "__main__":
    main()
