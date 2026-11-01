# -*- coding: utf-8 -*-
"""安装器：把一个版本所需的全部文件下载到位。

流程与官方启动器一致：
  版本 JSON -> 客户端 jar -> libraries(+natives 解压) -> assets 索引 -> assets 对象 -> 日志配置
任意 Minecraft 版本（正式版/快照/远古版）都走同一条路径。
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import net
from .config import config
from .manifest import get_version_json, list_versions
from .meta import Library, asset_url, resolve_libraries, resolve_meta, version_dir


class Cancelled(Exception):
    pass


@dataclass
class DownloadTask:
    url: str
    dest: Path
    sha1: str = ""
    size: int = 0
    kind: str = "file"
    quick: bool = False


@dataclass
class InstallPlan:
    version_id: str
    meta: dict
    jar: Optional[Path] = None
    tasks: list[DownloadTask] = field(default_factory=list)
    natives: list[Library] = field(default_factory=list)
    natives_dir: Path = Path()
    assets_index_id: str = ""
    raw_meta: dict = field(default_factory=dict)
    virtual_assets: bool = False
    map_to_resources: bool = False
    asset_index: dict = field(default_factory=dict)
    total_bytes: int = 0

    @property
    def total_files(self) -> int:
        return len(self.tasks)


def _mirror() -> str:
    return net.resolve_mirror(config["mirror"])


def find_version_url(version_id: str) -> str:
    for v in list_versions():
        if v.id == version_id:
            return v.url
    return ""


def build_plan(
    version_id: str,
    version_url: str = "",
    include_assets: bool = True,
    emit: Optional[Callable[[str], None]] = None,
) -> InstallPlan:
    """解析版本，生成下载计划（不实际下载）。"""
    say = emit or (lambda *_: None)
    config.ensure_dirs()

    say(f"读取版本 {version_id} 的元数据…")
    raw = get_version_json(version_id, version_url)
    meta = resolve_meta(raw)
    plan = InstallPlan(version_id=version_id, meta=meta, raw_meta=raw)
    vdir = version_dir(version_id)

    own_jar = not raw.get("inheritsFrom")
    jar_id = meta.get("jar") or version_id

    # 1. 客户端 jar
    client = (raw.get("downloads") or {}).get("client") or {}
    if own_jar and client.get("url"):
        plan.jar = vdir / f"{version_id}.jar"
        plan.tasks.append(
            DownloadTask(client["url"], plan.jar, client.get("sha1", ""),
                         client.get("size", 0), "客户端")
        )
    else:
        cand = version_dir(jar_id) / f"{jar_id}.jar"
        if cand.exists():
            plan.jar = cand

    # 2. libraries + natives
    libs, natives = resolve_libraries(meta)
    plan.natives = natives
    plan.natives_dir = vdir / "natives"
    for lib in libs + natives:
        if not lib.url:
            continue
        plan.tasks.append(
            DownloadTask(lib.url, config.libraries_dir / lib.path,
                         lib.sha1, lib.size, "依赖库")
        )

    # 3. assets
    if include_assets:
        idx = meta.get("assetIndex") or {}
        if idx.get("url"):
            plan.assets_index_id = idx.get("id") or version_id
            idx_path = config.assets_dir / "indexes" / f"{plan.assets_index_id}.json"
            plan.tasks.append(
                DownloadTask(idx["url"], idx_path, idx.get("sha1", ""),
                             idx.get("size", 0), "资源索引")
            )
            # 索引内容需要立刻拿到才能列出对象
            if idx_path.exists() and idx_path.stat().st_size > 10:
                try:
                    plan.asset_index = json.loads(idx_path.read_text(encoding="utf-8"))
                except Exception:
                    plan.asset_index = {}
            else:
                plan.asset_index = net.get_json(idx["url"])
                idx_path.parent.mkdir(parents=True, exist_ok=True)
                idx_path.write_text(json.dumps(plan.asset_index, ensure_ascii=False),
                                    encoding="utf-8")

            plan.virtual_assets = bool(plan.asset_index.get("virtual"))
            plan.map_to_resources = bool(plan.asset_index.get("map_to_resources"))

            for name, obj in (plan.asset_index.get("objects") or {}).items():
                h = obj.get("hash")
                if not h:
                    continue
                plan.tasks.append(
                    DownloadTask(asset_url(h),
                                 config.assets_dir / "objects" / h[:2] / h,
                                 h, obj.get("size", 0), "游戏资源", quick=True)
                )
        else:
            plan.assets_index_id = meta.get("assets") or "legacy"

    # 4. 日志配置（1.7+ 才有）
    log = ((meta.get("logging") or {}).get("client") or {}).get("file") or {}
    if log.get("url"):
        plan.tasks.append(
            DownloadTask(log["url"],
                         config.assets_dir / "log_configs" / (log.get("id") or "client.xml"),
                         log.get("sha1", ""), log.get("size", 0), "日志配置")
        )

    plan.total_bytes = sum(t.size for t in plan.tasks)
    return plan


# ------------------------------------------------------------------ 执行


def _download_one(task: DownloadTask, meter: net.ProgressMeter,
                  cancel: Optional[threading.Event]) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    meter.set_current(f"{task.kind} · {task.dest.name}")
    net.download_file(
        task.url, task.dest,
        expected_sha1=task.sha1 or None,
        expected_size=task.size or None,
        progress=meter.add_bytes,
        quick=task.quick,
    )
    meter.file_done()


def _extract_natives(natives: list[Library], natives_dir: Path) -> int:
    natives_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for lib in natives:
        src = config.libraries_dir / lib.path
        if not src.exists():
            continue
        try:
            with zipfile.ZipFile(src) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename
                    if any(name.startswith(pre) for pre in lib.exclude):
                        continue
                    if name.startswith("META-INF/"):
                        continue
                    target = natives_dir / Path(name).name
                    if target.exists():
                        continue
                    with zf.open(info) as fsrc, open(target, "wb") as fdst:
                        shutil.copyfileobj(fsrc, fdst)
                    count += 1
        except zipfile.BadZipFile:
            continue
    return count


def _link_virtual_assets(plan: InstallPlan, emit: Callable[[str], None]) -> None:
    """老版本（1.6 以前）需要把资源摊平到 assets/virtual/<index>/ 或 resources/。

    部分远古索引两个标记同时为 true，那就两边都放，启动时按存在性取用。
    """
    objects = (plan.asset_index.get("objects") or {})
    if not objects:
        return
    targets = []
    if plan.virtual_assets:
        targets.append(config.assets_dir / "virtual" / (plan.assets_index_id or "legacy"))
    if plan.map_to_resources:
        targets.append(config.game_dir / "resources")
    if not targets:
        return
    emit("整理老版本资源到 " + " / ".join(t.name for t in targets) + " …")
    for name, obj in objects.items():
        h = obj.get("hash")
        if not h:
            continue
        src = config.assets_dir / "objects" / h[:2] / h
        if not src.exists():
            continue
        for base in targets:
            dst = base / name
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copyfile(src, dst)
            except OSError:
                pass


def install(
    version_id: str,
    version_url: str = "",
    progress: Optional[Callable[[dict], None]] = None,
    emit: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
    include_assets: bool = True,
) -> InstallPlan:
    """下载并安装一个版本，返回安装计划（含完成后的路径信息）。"""
    log = emit or (lambda *_: None)
    plan = build_plan(version_id, version_url, include_assets=include_assets, emit=log)

    vdir = version_dir(version_id)
    vdir.mkdir(parents=True, exist_ok=True)
    # 原样保存 JSON，启动器/其他工具都靠它识别版本
    (vdir / f"{version_id}.json").write_text(
        json.dumps(plan.raw_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    meter = net.ProgressMeter(plan.total_bytes, plan.total_files)
    stop = threading.Event()

    def reporter() -> None:
        while not stop.is_set():
            if progress:
                progress(meter.snapshot())
            time.sleep(0.35)
        if progress:
            progress(meter.snapshot())

    th = threading.Thread(target=reporter, daemon=True)
    th.start()

    log(f"开始下载 {plan.total_files} 个文件，共 {plan.total_bytes / 1048576:.1f} MB…")
    workers = max(1, int(config["concurrent"]))
    errors: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_download_one, t, meter, cancel): t for t in plan.tasks}
            for fut in as_completed(futures):
                task = futures[fut]
                try:
                    fut.result()
                except Cancelled:
                    for f in futures:
                        f.cancel()
                    raise
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{task.kind} {task.dest.name}: {e}")
                    meter.file_done()
                if cancel is not None and cancel.is_set():
                    for f in futures:
                        f.cancel()
                    raise Cancelled()

        if errors:
            # 少数资源失败不阻断，但要让用户知道
            log(f"有 {len(errors)} 个文件下载失败（可重试补齐）")
            for e in errors[:5]:
                log("  · " + e)

        _extract_natives(plan.natives, plan.natives_dir)
        _link_virtual_assets(plan, log)

        marker = version_dir(version_id) / ".lantern.json"
        marker.write_text(json.dumps({
            "id": version_id,
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": plan.total_files,
            "bytes": plan.total_bytes,
            "assets": plan.assets_index_id,
            "errors": errors[:50],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        log(f"安装完成：{version_id}")
        return plan
    finally:
        stop.set()
        th.join(timeout=1.0)
        if progress:
            progress(meter.snapshot())


def is_installed(version_id: str) -> bool:
    d = version_dir(version_id)
    return (d / f"{version_id}.json").exists() and (d / f"{version_id}.jar").exists()


def installed_size(version_id: str) -> int:
    d = version_dir(version_id)
    total = 0
    if d.exists():
        for p in d.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    return total
