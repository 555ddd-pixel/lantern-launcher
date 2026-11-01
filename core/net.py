# -*- coding: utf-8 -*-
"""网络层：镜像改写、重试、带校验的下载。

只依赖标准库，避免用户需要 pip install 任何东西。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

USER_AGENT = "LanternLauncher/1.0 (+https://github.com/) Python-urllib"
_CHUNK = 256 * 1024

# 官方域名 -> BMCLAPI 镜像（国内加速，由 bangbang93 提供，PCL 等启动器同样在用）
MIRROR_RULES = (
    ("https://launchermeta.mojang.com", "https://bmclapi2.bangbang93.com"),
    ("https://piston-meta.mojang.com", "https://bmclapi2.bangbang93.com"),
    ("https://launcher.mojang.com", "https://bmclapi2.bangbang93.com"),
    ("https://libraries.minecraft.net", "https://bmclapi2.bangbang93.com/maven"),
    ("https://resources.download.minecraft.net", "https://bmclapi2.bangbang93.com/assets"),
)

_probe_lock = threading.Lock()
_probe_result: Optional[str] = None

_ssl_ctx = ssl.create_default_context()


_no_proxy_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 用户自填的加速代理（Clash / v2rayN 之类），只作用于整合包相关请求，
# 不去动 Mojang / BMCLAPI / 本机地址，免得把本来通的线路也绕坏。
_proxy_url = ""
_proxy_opener = None
_proxy_lock = threading.Lock()


def set_proxy(url: str) -> None:
    """设置加速代理，如 http://127.0.0.1:7890；空字符串 = 直连（默认）。"""
    global _proxy_url, _proxy_opener
    url = (url or "").strip()
    with _proxy_lock:
        if url == _proxy_url:
            return
        _proxy_url = url
        _proxy_opener = None


def proxy_url() -> str:
    with _proxy_lock:
        return _proxy_url


def _use_proxy_opener():
    """有配置代理时才返回专用 opener，否则返回 None（走系统默认逻辑）。"""
    with _proxy_lock:
        if not _proxy_url:
            return None
        global _proxy_opener
        if _proxy_opener is None:
            _proxy_opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": _proxy_url, "https": _proxy_url}))
        return _proxy_opener



def open_local(url: str, data: Optional[bytes] = None,
               headers: Optional[dict] = None, timeout: float = 5.0):
    """访问本机 / 局域网地址专用的 opener：不走系统代理。

    用户开了代理软件（很常见）时，HTTP_PROXY 会把这些请求也扔给代理，
    127.0.0.1、192.168.x.x 反而连不上——单实例检测、UPnP 端口映射都会失效。
    """
    req = urllib.request.Request(url, data=data, headers=headers or {})
    return _no_proxy_opener.open(req, timeout=timeout)


class NetworkError(Exception):
    pass


def rewrite(url: str, mirror: str) -> str:
    """把官方 URL 改写到镜像。mirror: official / bmclapi / auto。"""
    if mirror not in ("bmclapi", "auto"):
        return url
    for official, mirrored in MIRROR_RULES:
        if url.startswith(official):
            rest = url[len(official):]
            # 版本 JSON 在镜像上的路径与官方不同，由 get_version_json 处理
            return mirrored.rstrip("/") + rest
    return url


def probe_mirror(timeout: float = 6.0) -> str:
    """测速决定用官方还是镜像（结果本进程内缓存）。"""
    global _probe_result
    with _probe_lock:
        if _probe_result:
            return _probe_result
        best, best_cost = "official", 99.0
        for name, url in (
            ("official", "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"),
            ("bmclapi", "https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json"),
        ):
            for _ in range(2):
                start = time.time()
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as r:
                        r.read(4096)
                    cost = time.time() - start
                    if cost < best_cost:
                        best, best_cost = name, cost
                    break
                except Exception:
                    continue
        _probe_result = best
        return best


def resolve_mirror(mirror: str) -> str:
    return probe_mirror() if mirror == "auto" else mirror


def _open(url: str, timeout: float = 30.0, headers: Optional[dict] = None,
          retries: int = 3, use_proxy: bool = False):
    last = None
    for attempt in range(retries):
        try:
            h = {"User-Agent": USER_AGENT, "Accept": "*/*"}
            if headers:
                h.update(headers)
            req = urllib.request.Request(url, headers=h)
            opener = _use_proxy_opener() if use_proxy else None
            if opener is not None:
                return opener.open(req, timeout=timeout)
            return urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (404, 403, 401):
                raise NetworkError(f"HTTP {e.code}: {url}") from e
        except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError) as e:
            last = e
        time.sleep(0.6 * (attempt + 1))
    raise NetworkError(f"请求失败：{url}（{last}）")


def get_bytes(url: str, timeout: float = 30.0, retries: int = 3,
              use_proxy: bool = False) -> bytes:
    with _open(url, timeout=timeout, retries=retries, use_proxy=use_proxy) as r:
        return r.read()


def get_json(url: str, timeout: float = 30.0, retries: int = 3,
             use_proxy: bool = False) -> dict:
    raw = get_bytes(url, timeout=timeout, retries=retries, use_proxy=use_proxy)
    return json.loads(raw.decode("utf-8"))


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(_CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def download_file(
    url: str,
    dest: Path,
    expected_sha1: Optional[str] = None,
    expected_size: Optional[int] = None,
    progress: Optional[Callable[[int], None]] = None,
    retries: int = 3,
    quick: bool = False,
    use_proxy: bool = False,
) -> int:
    """下载到 dest（原子写入）。若已存在且校验通过则直接跳过。

    progress: 每读取一块回调本次新增字节数。
    quick: 只比大小、不算 SHA1（资源文件动辄数千个，用于加速）。
    返回实际下载的字节数（跳过时为 0）。
    """
    if dest.exists():
        if expected_size is not None and dest.stat().st_size != expected_size:
            pass  # 大小不符，重下
        elif quick and expected_size is not None and dest.stat().st_size == expected_size:
            if progress:
                progress(expected_size)
            return 0
        elif expected_sha1:
            if sha1_of(dest) == expected_sha1:
                if progress:
                    progress(dest.stat().st_size)
                return 0
        else:
            st = dest.stat()
            if st.st_size > 0:
                if progress:
                    progress(st.st_size)
                return 0

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")

    last_error = None
    tried_urls = {url}
    current = url
    for attempt in range(retries + 1):
        try:
            done = 0
            with _open(current, timeout=60.0, retries=1,
                       use_proxy=use_proxy) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(len(chunk))
            if expected_size is not None and done != expected_size:
                raise NetworkError(f"文件大小不符：{dest.name} 期望 {expected_size} 实际 {done}")
            if expected_sha1:
                actual = sha1_of(tmp)
                if actual.lower() != expected_sha1.lower():
                    raise NetworkError(f"SHA1 校验失败：{dest.name}")
            os.replace(tmp, dest)
            return done
        except NetworkError as e:
            # 404 时换镜像源再试（官方库有的文件镜像有）
            if "HTTP 404" in str(e) and attempt == 0:
                alt = rewrite(url, "bmclapi")
                if alt != current and alt not in tried_urls:
                    tried_urls.add(alt)
                    current = alt
                    continue
            last_error = e
            break
        except Exception as e:  # noqa: BLE001 - 统一重试
            last_error = e
            time.sleep(0.8 * (attempt + 1))
    try:
        if tmp.exists():
            tmp.unlink()
    except OSError:
        pass
    raise NetworkError(f"下载失败：{url}（{last_error}）")


class ProgressMeter:
    """多线程共享的进度统计：文件数 + 字节数 + 速度。"""

    def __init__(self, total_bytes: int = 0, total_files: int = 0) -> None:
        self.total_bytes = total_bytes
        self.total_files = total_files
        self.done_bytes = 0
        self.done_files = 0
        self.current = ""
        self._lock = threading.Lock()
        self._t0 = time.time()
        self._last_report = 0.0
        self._last_bytes = 0
        self.speed = 0.0

    def add_bytes(self, n: int) -> None:
        with self._lock:
            self.done_bytes += n
            now = time.time()
            if now - self._last_report >= 0.5:
                self.speed = (self.done_bytes - self._last_bytes) / (now - self._last_report)
                self._last_report, self._last_bytes = now, self.done_bytes

    def file_done(self) -> None:
        with self._lock:
            self.done_files += 1

    def set_current(self, name: str) -> None:
        self.current = name

    def snapshot(self) -> dict:
        elapsed = max(0.001, time.time() - self._t0)
        byte_ratio = (self.done_bytes / self.total_bytes) if self.total_bytes else 0.0
        file_ratio = (self.done_files / self.total_files) if self.total_files else 0.0
        ratio = max(byte_ratio, file_ratio) if self.total_bytes else file_ratio
        return {
            "done_bytes": self.done_bytes,
            "total_bytes": self.total_bytes,
            "done_files": self.done_files,
            "total_files": self.total_files,
            "speed": self.speed or (self.done_bytes / elapsed),
            "ratio": max(0.0, min(1.0, ratio)),
            "current": self.current,
            "elapsed": elapsed,
        }
