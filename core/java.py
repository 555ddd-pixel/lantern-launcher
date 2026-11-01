# -*- coding: utf-8 -*-
"""Java 运行时：探测本机 JVM、按版本需求挑选、缺失时自动下载（Adoptium Temurin）。"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import net
from .config import ROOT, config

RUNTIME_DIR = ROOT / "runtime"
IS_WINDOWS = platform.system() == "Windows"
EXE = "java.exe" if IS_WINDOWS else "java"
IS_64BIT = platform.machine().lower() in ("amd64", "x86_64", "arm64", "aarch64")


@dataclass
class JavaRuntime:
    path: Path
    major: int
    vendor: str = ""
    arch: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "major": self.major,
            "vendor": self.vendor,
            "arch": self.arch,
            "source": self.source,
        }


def _run(path: Path, args: list[str], timeout: float = 8.0) -> str:
    try:
        flags = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0  # type: ignore[attr-defined]
    except AttributeError:
        flags = 0
    try:
        p = subprocess.run([str(path), *args], capture_output=True, timeout=timeout,
                           creationflags=flags)
        out = (p.stdout or b"") + (p.stderr or b"")
        return out.decode("utf-8", "ignore")
    except Exception:
        return ""


def parse_major(text: str) -> int:
    m = re.search(r'version "(\d+)(?:\.(\d+))?', text)
    if not m:
        return 0
    first = int(m.group(1))
    if first == 1 and m.group(2):
        return int(m.group(2))
    return first


def inspect(java_exe: Path, source: str = "") -> Optional[JavaRuntime]:
    if not java_exe.exists():
        return None
    text = _run(java_exe, ["-version"])
    major = parse_major(text)
    if not major:
        return None
    vendor = ""
    for key in ("Temurin", "Adoptium", "Oracle", "Microsoft", "Zulu", "Corretto",
                "GraalVM", "OpenJDK", "Java HotSpot", "IBM"):
        if key.lower() in text.lower():
            vendor = key
            break
    arch = "x86_64" if IS_64BIT else "x86"
    m = re.search(r"(\d+)-bit", text)
    if m:
        arch = "x86_64" if m.group(1) == "64" else "x86"
    return JavaRuntime(path=java_exe, major=major, vendor=vendor, arch=arch, source=source)


def _candidate_dirs() -> list[tuple[Path, str]]:
    cands: list[tuple[Path, str]] = []
    if IS_WINDOWS:
        bases = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        ]
        vendors = ["Java", "Eclipse Adoptium", "Eclipse Foundation", "Microsoft",
                   "Zulu", "Amazon Corretto", "BellSoft", "BellSoft\\Liberica",
                   "Semeru", "AdoptOpenJDK", "JetBrains\\Runtime"]
        for base in bases:
            for v in vendors:
                d = base / v
                if d.exists():
                    for sub in d.iterdir():
                        if sub.is_dir():
                            cands.append((sub, "系统安装"))
    else:
        for d in ("/Library/Java/JavaVirtualMachines", "/usr/lib/jvm"):
            base = Path(d)
            if base.exists():
                for sub in base.iterdir():
                    cands.append((sub, "系统安装"))
    return cands


def find_all(deep: bool = True) -> list[JavaRuntime]:
    """扫描本机所有可用 JVM（去重，按大版本排序）。"""
    paths: list[Path] = []

    # 1) 用户手动指定
    manual = config["java_path"]
    if manual:
        p = Path(manual)
        paths.append(p if p.name.lower().startswith("java") else p / "bin" / EXE)

    # 2) 启动器自己下载的运行时
    if RUNTIME_DIR.exists():
        for d in sorted(RUNTIME_DIR.iterdir()):
            if d.is_dir():
                paths.append(d / "bin" / EXE)
                for sub in d.glob("*/bin/java.exe"):
                    paths.append(sub)

    # 3) JAVA_HOME / PATH
    jh = os.environ.get("JAVA_HOME")
    if jh:
        paths.append(Path(jh) / "bin" / EXE)
    which = shutil.which("java")
    if which:
        paths.append(Path(which))
    # javapath（Oracle 的转发器）
    if IS_WINDOWS:
        jp = Path(r"C:\Program Files\Common Files\Oracle\Java\javapath\java.exe")
        if jp.exists():
            paths.append(jp)

    # 4) 常见安装目录
    if deep:
        for d, _ in _candidate_dirs():
            paths.append(d / "bin" / EXE)
            jre = d / "Contents" / "Home" / "bin" / "java"
            if not IS_WINDOWS:
                paths.append(jre)

    result: dict[str, JavaRuntime] = {}
    for p in paths:
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key in result:
            continue
        rt = inspect(p)
        if rt:
            # 同一个 JDK 通过 javapath 会重复，按真实路径去重
            real = str(Path(rt.path).resolve()).lower()
            result.setdefault(real, rt)
    return sorted(result.values(), key=lambda r: (-r.major, r.path.name.lower()))


def pick(required_major: int) -> Optional[JavaRuntime]:
    """优先精确匹配，其次更高版本，最后退回最高可用版本。"""
    runtimes = find_all()
    if not runtimes:
        return None
    exact = [r for r in runtimes if r.major == required_major]
    if exact:
        return exact[0]
    higher = sorted([r for r in runtimes if r.major > required_major], key=lambda r: r.major)
    if higher:
        return higher[0]
    return runtimes[0]


def compatible(runtime: JavaRuntime, required_major: int) -> bool:
    return runtime.major == required_major


# ------------------------------------------------------------ 自动下载

# Mojang 官方 Java 运行时清单（BMCLAPI 亦有镜像，国内速度快）
RUNTIME_MANIFEST = ("https://piston-meta.mojang.com/v1/products/java-runtime/"
                    "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json")


def adoptium_url(major: int) -> str:
    arch = "x64" if IS_64BIT else "x86"
    os_name = {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "windows")
    return (
        f"https://api.adoptium.net/v3/binary/latest/{major}/ga/{os_name}/{arch}/jre/"
        f"hotspot/normal/eclipse"
    )


def _mojang_platform() -> str:
    if not IS_WINDOWS:
        return "mac-os" if platform.system() == "Darwin" else "linux"
    return "windows-x64" if IS_64BIT else "windows-x86"


def _pick_mojang_component(data: dict, major: int) -> Optional[tuple[str, dict]]:
    """在运行时清单里找匹配 major 的组件，返回 (组件名, 清单入口)。"""
    plat = data.get(_mojang_platform()) or {}
    best = None
    for cname, entries in plat.items():
        for e in entries or []:
            name = str((e.get("version") or {}).get("name") or "")
            m = re.match(r"(\d+)", name)
            if not m:
                continue
            vmajor = int(m.group(1))
            if vmajor == major:
                return cname, e
            if vmajor > major and best is None:
                best = (cname, e)
    return best


def _download_mojang(major: int, progress=None, emit=None,
                     cancel: Optional[threading.Event] = None) -> Optional[JavaRuntime]:
    """从 Mojang 官方源下载对应版本的 JRE，成功返回路径信息。"""
    log = emit or (lambda *_: None)
    from . import net as _net
    mirror = _net.resolve_mirror("auto")
    data = _net.get_json(_net.rewrite(RUNTIME_MANIFEST, mirror))
    picked = _pick_mojang_component(data, major)
    if not picked:
        return None
    cname, entry = picked
    comp_meta = _net.get_json(_net.rewrite(entry["manifest"]["url"], mirror))
    target = RUNTIME_DIR / cname
    files = {k: v for k, v in (comp_meta.get("files") or {}).items()
             if isinstance(v, dict) and v.get("type") == "file"}
    total = sum((v.get("downloads", {}).get("raw") or {}).get("size", 0) for v in files.values())
    log(f"使用 Mojang 官方运行时 {cname}（Java {(entry.get('version') or {}).get('name')}，"
        f"{len(files)} 个文件，{total/1048576:.0f} MB）…")
    from concurrent.futures import ThreadPoolExecutor, as_completed
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    meter = _net.ProgressMeter(total, len(files))
    errors = []

    def one(rel: str, meta_f: dict) -> None:
        raw = (meta_f.get("downloads") or {}).get("raw") or {}
        url = raw.get("url")
        if not url:
            return
        dest = target / rel
        _net.download_file(url, dest, raw.get("sha1"), raw.get("size"),
                           progress=meter.add_bytes, quick=False)
        meter.file_done()

    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = {pool.submit(one, rel, m): rel for rel, m in files.items()}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:  # noqa: BLE001
                errors.append(str(futs[f]))
    if errors:
        raise RuntimeError(f"运行时下载失败 {len(errors)} 个文件，如 {errors[0]}")
    exe = target / "bin" / EXE
    if not exe.exists():
        return None
    if not IS_WINDOWS:
        os.chmod(exe, 0o755)
    rt = inspect(exe, source=f"Mojang {cname}")
    return rt


def download(major: int, progress: Optional[Callable[[dict], None]] = None,
             emit: Optional[Callable[[str], None]] = None) -> JavaRuntime:
    """下载 JRE：优先 Mojang 官方源（走镜像），失败再退回 Adoptium Temurin。"""
    log = emit or (lambda *_: None)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    try:
        rt = _download_mojang(major, progress=progress, emit=emit)
        if rt:
            log(f"Java {rt.major} 就绪：{rt.path}")
            return rt
        log(f"官方源没有 Java {major} 的运行时，改用 Adoptium…")
    except Exception as e:  # noqa: BLE001
        log(f"Mojang 源下载失败（{e}），改用 Adoptium…")

    target = RUNTIME_DIR / f"java-{major}"
    zip_path = RUNTIME_DIR / f"temurin-{major}.zip"

    log(f"正在下载 Java {major} 运行时（Temurin JRE）…")
    meter = net.ProgressMeter(0, 1)

    def report_tick() -> None:
        if progress:
            progress(meter.snapshot())

    net.download_file(adoptium_url(major), zip_path,
                      progress=lambda n: (meter.add_bytes(n), report_tick()))
    log("下载完成，正在解压…")
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(target)
    # 解压后通常多一层目录
    exe = target / "bin" / EXE
    if not exe.exists():
        for sub in target.glob("*/bin/" + EXE):
            exe = sub
            break
    if not exe.exists():
        raise RuntimeError("Java 解压后未找到可执行文件")
    if not IS_WINDOWS:
        os.chmod(exe, 0o755)
    try:
        zip_path.unlink()
    except OSError:
        pass
    rt = inspect(exe, source="自动下载")
    if not rt:
        raise RuntimeError("下载的 Java 无法运行")
    log(f"Java {rt.major} 就绪：{exe}")
    return rt


def java_info() -> dict:
    runtimes = find_all()
    return {
        "runtimes": [r.to_dict() for r in runtimes],
        "selected": config["java_path"],
        "adoptium_available": True,
    }
