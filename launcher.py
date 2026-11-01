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
from typing import Optional

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


def log_note(msg: str) -> None:
    """窗口程序没有控制台，关键判断写到日志里，出问题才有据可查。"""
    print(msg)
    try:
        from core.config import LOG_DIR

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "launcher.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def _ping(port: int, timeout: float = 0.3) -> Optional[dict]:
    """问一下端口上的服务，返回 /api/ping 的内容；不是我们的服务就 None。"""
    try:
        from core.net import open_local

        with open_local(f"http://127.0.0.1:{port}/api/ping", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 端口没人监听会各种报错
        return None
    if not data.get("ok"):
        return None
    if str(data.get("app") or "") != APP_NAME:
        return None
    return data


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = k32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def _instance_path():
    from core.paths import DATA_DIR

    return DATA_DIR / ".cache" / "instance.json"


def write_instance(port: int, mode: str) -> None:
    """记下「我正开着哪个端口」，下次双击直接问它，不用扫端口。"""
    try:
        p = _instance_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "pid": os.getpid(), "port": port, "mode": mode,
            "started": int(time.time()), "version": VERSION,
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def clear_instance() -> None:
    try:
        p = _instance_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if int(data.get("pid") or 0) == os.getpid():
                p.unlink()
    except Exception:  # noqa: BLE001
        pass


def find_running(first: int = 8787, span: int = 20) -> Optional[dict]:
    """找已经在跑的「带界面」实例，返回 {port, mode, pid}；没有则 None。

    优先看上次自己写下的 instance.json（pid 活着 + 端口回答的确实是同一个进程），
    再退回扫端口。扫端口时只认 window / browser 模式——headless 调试实例、
    别的程序恰好占了区间里的端口都不算，否则双击后会误判成「已在运行」，
    转而打开一个网页，看起来就像程序退化成网页版了。
    """
    try:
        data = json.loads(_instance_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {}

    pid = int(data.get("pid") or 0)
    port = int(data.get("port") or 0)
    if pid and port and _pid_alive(pid):
        info = _ping(port)
        if info and int(info.get("pid") or 0) == pid:
            if info.get("mode") in ("window", "browser"):
                return {"port": port, "mode": info["mode"], "pid": pid}
    elif pid:
        log_note(f"上次的实例（pid {pid}）已退出，忽略记录")

    for p in range(first, first + span):
        info = _ping(p)
        if not info:
            continue
        if info.get("mode") != "window":
            # 只有真正开着窗口的实例才值得唤起；server.serve 之类的后台服务不算
            log_note(f"端口 {p} 是 {info.get('mode')} 实例，不当作已运行")
            continue
        log_note(f"端口 {p} 上有窗口实例（未登记在案）")
        return {"port": p, "mode": info["mode"], "pid": int(info.get("pid") or 0)}
    return None


def wake_running(port: int) -> bool:
    """让已经运行的实例把窗口显示出来。返回是否成功唤起窗口。"""
    try:
        from core.net import open_local

        with open_local(f"http://127.0.0.1:{port}/api/focus", timeout=1.5) as r:
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
    write_instance(port, "window")

    app = DesktopApp(port)
    hooks["focus"] = app.focus
    hooks["quit"] = app.quit
    try:
        app.run()
    finally:
        hooks["focus"] = None
        hooks["quit"] = None
        clear_instance()
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
    write_instance(real, "browser")
    webbrowser.open(f"http://127.0.0.1:{real}/")
    print("在浏览器里使用；关闭这个窗口即退出。")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        clear_instance()
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
            rport = running["port"]
            if want_browser:
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{rport}/")
                return
            if wake_running(rport):
                log_note(f"{APP_NAME} 已在运行（端口 {rport}），已把窗口切到前台。")
                return
            if running["mode"] == "browser":
                # 对方本来就是浏览器模式，没有窗口可唤起，那就接着用浏览器
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{rport}/")
                return
            # 窗口模式却唤不起来：宁可新开一个自己的窗口，也不要退化成网页
            log_note(f"实例 {rport} 唤不起窗口，改为新开一个窗口")

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
