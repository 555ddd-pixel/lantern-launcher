# -*- coding: utf-8 -*-
"""本地 HTTP 服务：给前端提供 REST API（只监听 127.0.0.1）。"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from core import auth, installer, java as javamod, launch as launcher, live
from core import modpack, updater
from core.config import CACHE_DIR, LOG_DIR, ROOT, config
from core.manifest import fetch_manifest, get_version_json, list_local_versions, list_versions
from core.paths import DATA_DIR, FROZEN, RES_DIR
from core.version import APP_NAME, BUILD, VERSION

WEB_DIR = RES_DIR / "web"


class TaskManager:
    """同一时刻只跑一个安装/下载任务，状态供前端轮询。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.state = {
            "kind": "idle",        # idle | install | java | login
            "version": "",
            "phase": "",
            "running": False,
            "message": "",
            "error": "",
            "progress": None,
            "logs": [],
            "started_at": 0,
            "result": {},
            "label": "",
        }

    # ---------- 状态 ----------
    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self.state)
            s["logs"] = self.state["logs"][-120:]
            return s

    def _log(self, text: str) -> None:
        with self._lock:
            self.state["logs"].append(text)
            if len(self.state["logs"]) > 400:
                del self.state["logs"][:150]
            self.state["message"] = text

    def _update(self, **kw) -> None:
        with self._lock:
            self.state.update(kw)

    def _progress(self, snap: dict) -> None:
        with self._lock:
            self.state["progress"] = snap

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self.state["running"])

    def cancel(self) -> None:
        self._cancel.set()
        self._log("正在取消…")

    # ---------- 安装 ----------
    def start_install(self, version_id: str, version_url: str = "") -> bool:
        if self.busy:
            return False
        self._cancel = threading.Event()
        self._update(kind="install", version=version_id, phase="准备中", running=True,
                     message="", error="", progress=None, logs=[], started_at=time.time())

        def run() -> None:
            try:
                installer.install(version_id, version_url, progress=self._progress,
                                  emit=self._log, cancel=self._cancel)
                self._update(phase="完成", running=False)
            except installer.Cancelled:
                self._update(phase="已取消", running=False)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self._update(phase="失败", error=str(e), running=False)
                self._log(f"安装失败：{e}")

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return True

    # ---------- 整合包 ----------
    def start_pack(self, label: str, work) -> bool:
        """通用任务壳：work(cancel) 里跑整合包的下载 + 安装。"""
        if self.busy:
            return False
        self._cancel = threading.Event()
        self._update(kind="modpack", version=label, phase="准备中", running=True,
                     message="", error="", progress=None, logs=[], result={},
                     started_at=time.time())

        def run() -> None:
            try:
                self._update(phase="安装中")
                res = work(self._cancel)
                self._update(phase="完成", running=False, result=res or {})
            except modpack.Cancelled:
                self._update(phase="已取消", running=False)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self._update(phase="失败", error=str(e), running=False)
                self._log(f"安装失败：{e}")

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return True

    def start_java(self, major: int) -> bool:
        if self.busy:
            return False
        self._cancel = threading.Event()
        self._update(kind="java", version=f"Java {major}", phase="下载中", running=True,
                     message="", error="", progress=None, logs=[], started_at=time.time())

        def run() -> None:
            try:
                javamod.download(major, progress=self._progress, emit=self._log)
                self._update(phase="完成", running=False)
            except Exception as e:  # noqa: BLE001
                self._update(phase="失败", error=str(e), running=False)
                self._log(f"下载 Java 失败：{e}")

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return True


class GameManager:
    def __init__(self) -> None:
        self.proc: Optional[launcher.GameProcess] = None
        self.version = ""
        self.started_at = 0.0
        self.logs: list[str] = []
        self._lock = threading.Lock()

    def status(self) -> dict:
        with self._lock:
            return {
                "running": bool(self.proc and self.proc.running),
                "version": self.version,
                "uptime": time.time() - self.started_at if self.started_at else 0,
                "logs": self.logs[-200:],
            }

    def start(self, version_id: str) -> dict:
        if self.proc and self.proc.running:
            raise RuntimeError("游戏已经在运行了")
        spec = launcher.prepare(version_id, emit=lambda s: self._push("[启动] " + s))
        self.logs = []

        def on_line(line: str) -> None:
            self._push(line)

        gp = launcher.launch(spec, on_line=on_line)
        with self._lock:
            self.proc = gp
            self.version = version_id
            self.started_at = time.time()
        return {"ok": True, "version": version_id, "java": str(spec.java.path),
                "log_file": str(spec.log_file)}

    def _push(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)
            if len(self.logs) > 800:
                del self.logs[:300]

    def stop(self) -> None:
        if self.proc and self.proc.running:
            self.proc.proc.terminate()


task = TaskManager()
game = GameManager()

# 桌面窗口层注册进来：窗口唤起 / 退出 / 模式标识
hooks: dict = {"focus": None, "quit": None}
APP_MODE = "browser"


class UpdateTask:
    """启动器自更新：检查 / 下载 / 替换重启。"""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None

    def check(self, force: bool = True) -> dict:
        return updater.check(force=force, emit=lambda s: task._log("[更新] " + s))

    def start_download(self) -> bool:
        snap = updater.snapshot()
        if snap["phase"] == "downloading":
            return False
        if not snap["has_update"]:
            raise RuntimeError("当前没有可用更新")
        updater._set(phase="downloading", error="", progress=None)

        def run() -> None:
            try:
                updater.download(emit=lambda s: task._log("[更新] " + s),
                                 progress=task._progress)
            except Exception as e:  # noqa: BLE001
                updater._set(phase="", error=str(e))
                task._log(f"[更新] 下载失败：{e}")

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return True

    def apply(self) -> None:
        updater.apply_and_restart()


update_task = UpdateTask()


def status_payload() -> dict:
    return {
        "task": task.snapshot(),
        "game": game.status(),
        "installed": [v.id for v in list_local_versions()],
        "update": updater.snapshot(),
        "live": live.status(),
        "app": {
            "name": APP_NAME,
            "version": VERSION,
            "build": BUILD,
            "frozen": FROZEN,
            "platform": updater.platform_key(),
            "data_dir": str(DATA_DIR),
            "mode": APP_MODE,
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "LanternLauncher/1.0"

    # ---------- 基础 ----------
    def log_message(self, fmt, *args):  # 静音
        return

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _error(self, msg: str, code: int = 400):
        self._json({"ok": False, "error": msg}, code)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return {}

    # ---------- 路由 ----------
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        qs = parse_qs(url.query)
        try:
            if path.startswith("/api/"):
                return self._api_get(path, qs)
            return self._static(path)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._error(str(e), 500)

    def do_POST(self):
        url = urlparse(self.path)
        try:
            if url.path == "/api/modpack/upload":
                return self._upload_pack()
            return self._api_post(url.path, self._body())
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._error(str(e), 500)

    # ---------- 静态文件 ----------
    def _static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        target = (WEB_DIR / rel).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.exists():
            return self._error("not found", 404)
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
            ".json": "application/json; charset=utf-8",
        }.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    # ---------- 实时推送（SSE） ----------
    def _stream(self):
        """Server-Sent Events：状态有变化才推，前端无需轮询。"""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
        except Exception:
            return
        last_sig = None
        deadline = time.time() + 3600
        tick = 0
        try:
            while time.time() < deadline:
                payload = status_payload()
                t, g, u, lv = payload["task"], payload["game"], payload["update"], payload["live"]
                sig = (t["phase"], t["running"], len(t["logs"]),
                       (t.get("progress") or {}).get("done_bytes"),
                       g["running"], len(g["logs"]),
                       u["phase"], u["has_update"], u["checked_at"], len(u["downloaded"]),
                       len(lv["events"]))
                tick += 1
                if sig != last_sig or tick % 40 == 0:
                    last_sig = sig
                    body = json.dumps(payload, ensure_ascii=False)
                    self.wfile.write(b"data: " + body.encode("utf-8") + b"\n\n")
                    self.wfile.flush()
                time.sleep(0.4)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ---------- API ----------
    def _api_get(self, path: str, qs: dict):
        if path == "/api/status":
            return self._json(status_payload())
        if path == "/api/ping":
            return self._json({"ok": True, "app": APP_NAME, "version": VERSION,
                               "frozen": FROZEN, "mode": APP_MODE})
        if path == "/api/focus":
            # 第二次双击程序时，把已经在跑的窗口拉到前台
            called = False
            try:
                if hooks.get("focus"):
                    hooks["focus"]()
                    called = True
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            return self._json({"ok": True, "focused": called})
        if path == "/api/app/info":
            return self._json({
                "name": APP_NAME, "version": VERSION, "build": BUILD,
                "frozen": FROZEN, "mode": APP_MODE,
                "data_dir": str(DATA_DIR),
                "exe": str(updater.exe_path()),
                "python": sys.version.split()[0],
            })
        if path == "/api/stream":
            return self._stream()
        if path == "/api/update":
            return self._json({"update": updater.snapshot(),
                               "live": live.status(),
                               "app": {"name": APP_NAME, "version": VERSION,
                                       "build": BUILD, "frozen": FROZEN,
                                       "platform": updater.platform_key(),
                                       "exe": str(updater.exe_path())}})
        if path == "/api/versions":
            force = qs.get("refresh", ["0"])[0] == "1"
            versions = list_versions(force=force)
            return self._json({
                "latest_release": (fetch_manifest().get("latest") or {}).get("release", ""),
                "latest_snapshot": (fetch_manifest().get("latest") or {}).get("snapshot", ""),
                "count": len(versions),
                "versions": [v.to_dict() for v in versions],
            })
        if path == "/api/local":
            out = []
            for v in list_local_versions():
                meta = v.meta
                pack = modpack.load_pack_meta(v.id)
                out.append({
                    "id": v.id, "has_jar": v.has_jar, "loader": v.loader,
                    "type": meta.get("type", "release"),
                    "java": (meta.get("javaVersion") or {}).get("majorVersion", 8),
                    "size": installer.installed_size(v.id),
                    "pack": pack.get("name") or "",
                    "pack_version": pack.get("version") or "",
                    "source": pack.get("source") or "",
                    "instance": pack.get("instance") or "",
                    "mc_version": pack.get("mc_version") or "",
                })
            return self._json({"versions": out})
        if path == "/api/config":
            return self._json({"config": config.data})
        if path == "/api/java":
            runtimes = javamod.find_all()
            return self._json({
                "runtimes": [r.to_dict() for r in runtimes],
                "required": {v.id: (v.meta.get("javaVersion") or {}).get("majorVersion", 8)
                             for v in list_local_versions()},
            })
        if path == "/api/modpack/search":
            q = (qs.get("q", [""])[0] or "").strip()
            loader = (qs.get("loader", [""])[0] or "").strip()
            mc = (qs.get("mc", [""])[0] or "").strip()
            index = (qs.get("index", ["relevance"])[0] or "relevance")
            try:
                offset = max(0, int(qs.get("offset", ["0"])[0]))
            except ValueError:
                offset = 0
            try:
                limit = max(1, min(40, int(qs.get("limit", ["24"])[0])))
            except ValueError:
                limit = 24
            try:
                return self._json({"ok": True, **modpack.search(
                    q, loader=loader, mc=mc, index=index, limit=limit, offset=offset)})
            except Exception as e:  # noqa: BLE001
                return self._error(f"搜索失败：{e}")

        if path == "/api/modpack/versions":
            project = (qs.get("project", [""])[0] or "").strip()
            if not project:
                return self._error("缺少 project")
            mc = (qs.get("mc", [""])[0] or "").strip()
            loader = (qs.get("loader", [""])[0] or "").strip()
            try:
                rows = modpack.project_versions(project, mc=mc, loader=loader)
            except Exception as e:  # noqa: BLE001
                return self._error(f"读取版本失败：{e}")
            return self._json({"ok": True, "versions": rows})

        if path == "/api/login/state":
            return self._json({"ok": True, "account": auth.device_state(),
                               "config_account": config.data.get("account") or {}})

        if path == "/api/logs":
            files = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime,
                           reverse=True)[:30]
            return self._json({"logs": [{"name": p.name, "size": p.stat().st_size,
                                         "time": p.stat().st_mtime} for p in files]})
        return self._error("unknown endpoint", 404)

    # ---------- 整合包：拖拽上传（原始字节流，避免解析 multipart） ----------
    def _upload_pack(self):
        raw_name = unquote(self.headers.get("X-Filename") or "pack.zip")
        name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", raw_name).strip() or "pack.zip"
        try:
            total = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            total = 0
        if total <= 0:
            return self._error("没有收到文件内容")
        if total > 1024 ** 3:
            return self._error("文件太大（上限 1 GB）")
        if not name.lower().endswith((".zip", ".mrpack")):
            return self._error("只支持 .mrpack 或 .zip 格式的整合包")
        if task.busy:
            return self._error("已有任务在进行中，等它完成后再拖入")

        dest_dir = CACHE_DIR / "imports"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
        got = 0
        try:
            with open(dest, "wb") as f:
                while got < total:
                    chunk = self.rfile.read(min(1 << 20, total - got))
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
        except Exception as e:  # noqa: BLE001
            return self._error(f"接收文件失败：{e}")
        if got != total:
            try:
                dest.unlink()
            except OSError:
                pass
            return self._error("文件上传中断，请重试")

        def work(cancel):
            return modpack.install_from_file(dest, progress=task._progress,
                                             emit=task._log, cancel=cancel)

        if not task.start_pack(Path(name).stem, work):
            return self._error("已有任务在进行中")
        return self._json({"ok": True, "name": name, "size": got,
                           "message": f"已接收 {name}，开始安装"})

    def _api_post(self, path: str, body: dict):
        if path == "/api/app/quit":
            # 界面上点“退出”时走这里；延迟一下让响应先发出去
            def bye() -> None:
                time.sleep(0.35)
                fn = hooks.get("quit")
                if fn:
                    try:
                        fn()
                        return
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()
                os._exit(0)

            threading.Thread(target=bye, daemon=True).start()
            return self._json({"ok": True})
        if path == "/api/install":
            vid = (body.get("id") or "").strip()
            if not vid:
                return self._error("缺少版本 ID")
            # 校验版本是否存在
            match = [v for v in list_versions() if v.id == vid]
            if not match:
                return self._error(f"版本 {vid} 不存在")
            if task.start_install(vid, match[0].url):
                return self._json({"ok": True, "version": vid})
            return self._error("已有任务在进行中")

        if path == "/api/modpack/install":
            project = (body.get("project") or "").strip()
            version = (body.get("version") or "").strip()
            title = (body.get("title") or project).strip()
            if not project or not version:
                return self._error("缺少 project / version")
            if task.busy:
                return self._error("已有任务在进行中")

            def work(cancel):
                return modpack.install_from_version(
                    project, version, progress=task._progress,
                    emit=task._log, cancel=cancel)

            if task.start_pack(title, work):
                return self._json({"ok": True, "message": f"开始安装《{title}》"})
            return self._error("已有任务在进行中")

        if path == "/api/cancel":
            task.cancel()
            return self._json({"ok": True})

        # ---------- 自更新 ----------
        if path == "/api/update/check":
            snap = update_task.check(force=True)
            return self._json({"ok": True, "update": snap})

        if path == "/api/update/download":
            try:
                started = update_task.start_download()
            except Exception as e:  # noqa: BLE001
                return self._error(str(e))
            return self._json({"ok": True, "started": started,
                               "update": updater.snapshot()})

        if path == "/api/update/apply":
            if not body.get("confirm"):
                return self._error("需要 confirm=true 才会替换程序")
            try:
                update_task.apply()
            except Exception as e:  # noqa: BLE001
                return self._error(str(e))
            return self._json({"ok": True, "message": "正在替换并重启，稍后界面会自动恢复"})

        # ---------- 实时版本监听 ----------
        if path == "/api/live/check":
            fresh = live.poll(announce=True)
            return self._json({"ok": True, "new": fresh, "live": live.status()})

        if path == "/api/launch":
            vid = (body.get("id") or "").strip()
            if not vid:
                return self._error("缺少版本 ID")
            try:
                return self._json(game.start(vid))
            except Exception as e:  # noqa: BLE001
                return self._error(str(e))

        if path == "/api/stop":
            game.stop()
            return self._json({"ok": True})

        if path == "/api/config":
            patch = body.get("config") or {}
            config.update(patch)
            return self._json({"ok": True, "config": config.data})

        if path == "/api/java/download":
            try:
                major = int(body.get("major") or 21)
            except Exception:
                return self._error("major 无效")
            if task.start_java(major):
                return self._json({"ok": True})
            return self._error("已有任务在进行中")

        if path == "/api/account/offline":
            name = (body.get("name") or "").strip() or "Steve"
            acc = auth.offline_account(name)
            config.update({"account": acc, "player_name": name})
            return self._json({"ok": True, "account": acc})

        if path == "/api/account/microsoft":
            cid = (body.get("client_id") or config["ms_client_id"] or "").strip()
            if not cid:
                return self._error("请先在设置里填写 Azure 客户端 ID")
            config.update({"ms_client_id": cid})
            result: dict = {}

            def done(acc, err):
                result["account"] = acc
                result["error"] = err
                task._update(running=False,
                             phase="完成" if acc else "失败",
                             error=err or "",
                             result={"account": acc} if acc else {})

            def emit(text: str) -> None:
                task._log(text)
                task._update(kind="login", running=True)

            task._update(kind="login", running=True, logs=[], error="", phase="等待授权")
            auth.ms_login_async(cid, emit, done)
            return self._json({"ok": True, "message": "已发起登录，请查看日志中的链接与代码"})

        if path == "/api/version/delete":
            vid = (body.get("id") or "").strip()
            if not vid:
                return self._error("缺少版本 ID")
            inst = modpack.instance_game_dir(vid)
            target = config.versions_dir / vid
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            # 实例目录只属于这个整合包，一并删掉（存档也会没，界面已提示）
            if inst and inst.exists() and str(inst).startswith(str(modpack.instances_dir())):
                shutil.rmtree(inst, ignore_errors=True)
            return self._json({"ok": True})

        if path == "/api/open":
            target = (body.get("target") or "game_dir")
            if target == "game_dir":
                p = config.game_dir
            elif target == "version":
                p = config.versions_dir / (body.get("id") or "")
            elif target == "logs":
                p = LOG_DIR
            else:
                return self._error("未知目录")
            if not p.exists():
                return self._error("目录不存在")
            try:
                if os.name == "nt":
                    os.startfile(str(p))  # type: ignore[attr-defined]
                else:
                    import subprocess
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])
            except Exception as e:  # noqa: BLE001
                return self._error(f"打开失败：{e}")
            return self._json({"ok": True})

        return self._error("unknown endpoint", 404)


class LauncherServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # 界面关闭时客户端随时断连，不必刷一屏堆栈
        import sys as _sys
        exc = _sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def start(port: int = 8787, open_browser: bool = False) -> tuple:
    """在后台线程里起服务，立刻返回 (httpd, 实际端口)。

    桌面窗口层用它：服务跑线程，窗口跑主线程。
    """
    config.ensure_dirs()
    updater.start_background(emit=lambda s: task._log("[更新] " + s))
    live.start_background()
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = LauncherServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        raise RuntimeError("端口被占用，无法启动本地服务")

    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="lantern-http").start()
    url = f"http://127.0.0.1:{port}/"
    print(f"{APP_NAME} {VERSION} 已启动：{url}")
    print(f"数据目录：{DATA_DIR}")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    return httpd, port


def serve(port: int = 8787, open_browser: bool = True) -> None:
    """纯浏览器模式：前台阻塞（Ctrl+C 退出）。"""
    httpd, port = start(port=port, open_browser=open_browser)
    print("关闭此窗口即可退出启动器。")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    serve()
