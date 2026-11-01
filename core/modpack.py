# -*- coding: utf-8 -*-
"""整合包：Modrinth 实时搜索 + .mrpack / CurseForge zip 一键安装。

设计要点
--------
* 只依赖标准库，和启动器其余部分保持一致。
* .mrpack（Modrinth 官方格式）= modrinth.index.json + overrides/，里面的每个
  文件都自带下载地址，因此可以完整安装。
* CurseForge 导出的 zip = manifest.json + overrides/。如果 zip 里没有 mods 文件，
  只有 projectID/fileID，那么补全需要 CurseForge API Key，这里只导入已有内容并
  在日志里说明。
* 每个整合包装进独立实例目录 `<游戏目录>/instances/<slug>/`，版本目录里放一个
  `.lantern-instance.json` 侧车文件，启动时用它替换 --gameDir。
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import net
from .config import CACHE_DIR, config
from .meta import version_dir

API_OFFICIAL = "https://api.modrinth.com/v2"
API_MIRROR = "https://mod.mcimirror.top/modrinth/v2"   # MCIM：大陆公益镜像
CDN_OFFICIAL = "https://cdn.modrinth.com"
CDN_MIRROR = "https://mod.mcimirror.top"               # 同一路径前缀，换域名即可
INSTANCE_FILE = ".lantern-instance.json"
PACK_FILE = ".lantern-pack.json"

SOURCE_LABEL = {"auto": "自动", "official": "官方", "mirror": "国内镜像"}

# auto 模式下第一个入口最多等这么久，超时就换另一个（官方线路常卡在手握手上）
AUTO_FIRST_TIMEOUT = 6.0

# 上次连通的 API 入口：auto 模式下优先复用，避免每次都踩一遍慢的那个
_live_api = ""
_probe_started = False

LOADER_LABEL = {"fabric": "Fabric", "quilt": "Quilt", "forge": "Forge",
                "neoforge": "NeoForge", "": "原版", "vanilla": "原版"}


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- 数据结构


@dataclass
class PackFile:
    url: str
    path: str
    sha1: str = ""
    size: int = 0


@dataclass
class PackInfo:
    kind: str = "mrpack"            # mrpack | curseforge
    name: str = ""
    version: str = ""
    summary: str = ""
    mc_version: str = ""
    loader: str = ""
    loader_version: str = ""
    files: list[PackFile] = field(default_factory=list)
    icon: str = ""
    source: str = ""
    project: str = ""
    missing_note: str = ""

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}".strip()


# ---------------------------------------------------------------- 工具


def _q(params: dict) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "", [], {})}
    return urllib.parse.urlencode(clean, doseq=True)


def source_mode() -> str:
    mode = str(config["pack_source"] or "auto").lower()
    return mode if mode in ("auto", "official", "mirror") else "auto"


def api_bases() -> list[str]:
    """按偏好排好的 API 入口列表（auto 会依次尝试）。"""
    global _live_api
    mode = source_mode()
    if mode == "official":
        return [API_OFFICIAL]
    if mode == "mirror":
        return [API_MIRROR]
    order = [API_OFFICIAL, API_MIRROR]
    if _live_api in order:
        order.remove(_live_api)
        order.insert(0, _live_api)
    return order


def _api_get(path: str, params: Optional[dict] = None, timeout: float = 20.0):
    """带换源的 GET：官方连不上就自动走镜像，成功的入口记下来。"""
    global _live_api
    net.set_proxy(str(config["net_proxy"] or ""))
    bases = api_bases()
    errors = []
    for idx, base in enumerate(bases):
        url = f"{base}{path}"
        if params:
            url = f"{url}?{_q(params)}"
        # auto 模式还没判定过线路时，先试的那个别死等：官方偶尔会卡在手握手上
        limit = min(timeout, AUTO_FIRST_TIMEOUT) if idx == 0 and len(bases) > 1 else timeout
        try:
            data = net.get_json(url, timeout=limit, retries=1, use_proxy=True)
            if base != _live_api:
                _live_api = base
            return data
        except Exception as e:  # noqa: BLE001 - 换下一个入口
            errors.append(f"{'镜像' if base == API_MIRROR else '官方'}: {e}")
    raise RuntimeError("连不上 Modrinth（" + "；".join(errors) + "）。"
                       "可在「设置 → 整合包网络」里切换到国内镜像或填加速代理。")


def _get(url: str, params: Optional[dict] = None, timeout: float = 25.0):
    if params:
        url = f"{url}?{_q(params)}"
    return net.get_json(url, timeout=timeout, retries=2)


def to_mirror(url: str) -> str:
    """cdn.modrinth.com 上的文件换成镜像域名（路径结构相同）。"""
    for host in (CDN_OFFICIAL, "https://cdn-raw.modrinth.com"):
        if url.startswith(host):
            return CDN_MIRROR + url[len(host):]
    return url


def mirror_icon(url: str) -> str:
    return to_mirror(url) if url else ""


def file_candidates(url: str) -> list[str]:
    """文件下载的候选地址：按当前源排序，失败再换另一个。"""
    mirror = to_mirror(url)
    if source_mode() == "mirror":
        return [mirror] if mirror != url else [url]
    if mirror == url:
        return [url]
    return [url, mirror] if source_mode() == "auto" else [url, mirror]


def download(url: str, dest: Path, expected_sha1: Optional[str] = None,
             expected_size: Optional[int] = None,
             progress: Optional[Callable[[int], None]] = None) -> int:
    """下载一个整合包文件：主地址失败自动换另一个源。"""
    net.set_proxy(str(config["net_proxy"] or ""))
    last: Optional[Exception] = None
    for cand in file_candidates(url):
        try:
            return net.download_file(cand, dest, expected_sha1=expected_sha1,
                                     expected_size=expected_size,
                                     progress=progress, use_proxy=True)
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"下载失败：{dest.name}（{last}）")


def instances_dir() -> Path:
    return config.game_dir / "instances"


def instance_game_dir(version_id: str) -> Optional[Path]:
    """整合包实例的游戏目录（没有侧车文件则返回 None）。"""
    p = version_dir(version_id) / INSTANCE_FILE
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    rel = raw.get("game_dir") or ""
    if not rel:
        return None
    base = config.game_dir / rel
    return base


def load_pack_meta(version_id: str) -> dict:
    p = version_dir(version_id) / PACK_FILE
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _slug(text: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5]+", "-", text or "").strip("-").lower()
    return s or "modpack"


def probe_sources(timeout: float = 8.0) -> dict:
    """测一下官方 / 镜像哪个能连、多快，给设置页的「测试」用（两路并发，最多等一个超时）。"""
    global _live_api
    net.set_proxy(str(config["net_proxy"] or ""))
    results: dict = {}
    lock = threading.Lock()

    def one(name: str, base: str) -> None:
        start = time.time()
        try:
            net.get_json(f"{base}/search?query=fabric&limit=1",
                         timeout=timeout, retries=1, use_proxy=True)
            item = {"ok": True, "ms": int((time.time() - start) * 1000)}
        except Exception as e:  # noqa: BLE001
            item = {"ok": False, "ms": 0, "error": str(e)[:160]}
        with lock:
            results[name] = item

    threads = [threading.Thread(target=one, args=(n, b), daemon=True)
               for n, b in (("official", API_OFFICIAL), ("mirror", API_MIRROR))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 2)
    with lock:
        pairs = sorted((v["ms"], k) for k, v in results.items() if v.get("ok"))
    best = pairs[0][1] if pairs else ""
    if best and source_mode() == "auto":
        _live_api = API_MIRROR if best == "mirror" else API_OFFICIAL
    return {"source": source_mode(), "source_label": SOURCE_LABEL[source_mode()],
            "proxy": net.proxy_url(), "results": results, "best": best}


def start_probe() -> None:
    """启动时后台探一次线路，用户点开整合包页时就不用再等超时了。"""
    global _probe_started
    if _probe_started:
        return
    _probe_started = True

    def run() -> None:
        try:
            probe_sources(timeout=6.0)
        except Exception:  # noqa: BLE001 - 探测失败不影响功能
            pass

    threading.Thread(target=run, daemon=True, name="lantern-pack-probe").start()


# ---------------------------------------------------------------- Modrinth


def search(query: str = "", loader: str = "", mc: str = "",
           index: str = "relevance", limit: int = 24, offset: int = 0) -> dict:
    """实时搜索整合包。facets 里限定 project_type:modpack。"""
    facets = [["project_type:modpack"]]
    if loader:
        facets.append([f"categories:{loader.lower()}"])
    if mc:
        facets.append([f"versions:{mc}"])
    data = _api_get("/search", {
        "query": query, "limit": int(limit), "offset": int(offset), "index": index,
        "facets": json.dumps(facets, separators=(",", ":")),
    })
    hits = []
    for h in data.get("hits", []) or []:
        hits.append({
            "id": h.get("project_id") or "",
            "slug": h.get("slug") or "",
            "title": h.get("title") or "",
            "description": h.get("description") or "",
            "author": h.get("author") or "",
            "icon": h.get("icon_url") or "",
            "icon_mirror": mirror_icon(h.get("icon_url") or ""),
            "downloads": h.get("downloads") or 0,
            "follows": h.get("follows") or 0,
            "categories": [c for c in (h.get("display_categories") or h.get("categories") or [])][:6],
            "versions": (h.get("versions") or [])[-6:],
            "updated": (h.get("date_modified") or "")[:10],
            "client": h.get("client_side") or "required",
        })
    return {"total": data.get("total_hits") or len(hits), "offset": int(offset),
            "limit": int(limit), "hits": hits}


def project_detail(project: str) -> dict:
    return _api_get(f"/project/{project}")


def project_versions(project: str, mc: str = "", loader: str = "") -> list[dict]:
    params: dict = {}
    if mc:
        params["game_versions"] = json.dumps([mc])
    if loader:
        params["loaders"] = json.dumps([loader.lower()])
    rows = _api_get(f"/project/{project}/version", params)
    out = []
    for v in rows or []:
        files = v.get("files") or []
        primary = next((f for f in files if f.get("primary")), files[0] if files else {})
        out.append({
            "id": v.get("id") or "",
            "name": v.get("name") or v.get("version_number") or "",
            "number": v.get("version_number") or "",
            "game_versions": v.get("game_versions") or [],
            "loaders": v.get("loaders") or [],
            "date": (v.get("date_published") or "")[:10],
            "downloads": v.get("downloads") or 0,
            "file": {
                "url": primary.get("url") or "",
                "name": primary.get("filename") or "",
                "size": primary.get("size") or 0,
                "sha1": ((primary.get("hashes") or {}).get("sha1") or ""),
            },
        })
    return out


# ---------------------------------------------------------------- 解析整合包


def read_zip_info(path: Path) -> PackInfo:
    """识别 .mrpack / CurseForge zip，返回文件清单（不下载）。"""
    if not zipfile.is_zipfile(path):
        raise RuntimeError("不是有效的整合包压缩包（需要 .mrpack 或 .zip）")
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if "modrinth.index.json" in names:
            raw = json.loads(zf.read("modrinth.index.json").decode("utf-8"))
            return _parse_mrpack(raw, path)
        if "manifest.json" in names:
            raw = json.loads(zf.read("manifest.json").decode("utf-8", "ignore"))
            return _parse_curseforge(raw, zf, path)
    raise RuntimeError("无法识别的整合包：缺少 modrinth.index.json 或 manifest.json")


def _parse_mrpack(raw: dict, path: Path) -> PackInfo:
    deps = raw.get("dependencies") or {}
    loader, loader_ver = "", ""
    for key, name in (("fabric-loader", "fabric"), ("quilt-loader", "quilt"),
                      ("forge", "forge"), ("neoforge", "neoforge")):
        if deps.get(key):
            loader, loader_ver = name, str(deps[key])
            break
    info = PackInfo(
        kind="mrpack",
        name=raw.get("name") or path.stem,
        version=raw.get("versionId") or "",
        summary=raw.get("summary") or "",
        mc_version=str(deps.get("minecraft") or ""),
        loader=loader,
        loader_version=loader_ver,
        source="Modrinth",
    )
    for item in raw.get("files") or []:
        env = (item.get("env") or {}).get("client")
        if env == "unsupported":
            continue
        urls = item.get("downloads") or []
        if not urls:
            continue
        info.files.append(PackFile(
            url=urls[0], path=str(item.get("path") or "").lstrip("/"),
            sha1=((item.get("hashes") or {}).get("sha1") or ""),
            size=int(item.get("fileSize") or 0),
        ))
    return info


def _parse_curseforge(raw: dict, zf: zipfile.ZipFile, path: Path) -> PackInfo:
    mc = raw.get("minecraft") or {}
    loader, loader_ver = "", ""
    for entry in mc.get("modLoaders") or []:
        lid = str(entry.get("id") or "")
        if "-" in lid:
            kind, ver = lid.split("-", 1)
            if kind.lower() in ("forge", "neoforge", "fabric", "quilt"):
                loader, loader_ver = kind.lower(), ver
                break
    info = PackInfo(
        kind="curseforge",
        name=raw.get("name") or path.stem,
        version=raw.get("version") or "",
        summary=(raw.get("author") and f"作者 {raw.get('author')}") or "",
        mc_version=str(mc.get("version") or ""),
        loader=loader,
        loader_version=loader_ver,
        source="CurseForge",
    )
    # zip 里自带的 mods（很多第三方转载包会这样打包）
    names = zf.namelist()
    embedded_mods = [n for n in names if re.match(r"^(overrides/)?mods/[^/]+\.jar$", n)]
    refs = raw.get("files") or []
    if refs and not embedded_mods:
        info.missing_note = (f"该包通过 {len(refs)} 条 CurseForge 文件引用分发，"
                             f"压缩包里没有 mods 文件；补全需要 CurseForge API Key。")
    return info


# ---------------------------------------------------------------- 安装


def _find_mc_url(mc_version: str) -> str:
    from .manifest import list_versions
    for v in list_versions():
        if v.id == mc_version:
            return v.url
    return ""


def _ensure_base(mc_version: str, emit: Callable[[str], None],
                 cancel: Optional[threading.Event]) -> None:
    from . import installer
    if not mc_version:
        raise RuntimeError("整合包没有声明 Minecraft 版本")
    if installer.is_installed(mc_version):
        emit(f"原版 {mc_version} 已安装")
        return
    emit(f"先安装原版 {mc_version} …")
    installer.install(mc_version, _find_mc_url(mc_version), emit=emit, cancel=cancel)


def _pick_version(rows: list[dict], mc: str) -> Optional[dict]:
    if not rows:
        return None
    if mc:
        match = [r for r in rows if mc in (r.get("game_versions") or [])]
        if match:
            return match[0]
    return rows[0]


def _latest_loader(mc: str, loader: str) -> str:
    """没指定 loader 版本时，从官方 meta 里挑一个稳定版。"""
    try:
        if loader == "fabric":
            rows = _get(f"https://meta.fabricmc.net/v2/versions/loader/{mc}")
            stable = [r for r in rows if (r.get("loader") or {}).get("stable")]
            pick = (stable or rows)[0]
            return str((pick.get("loader") or {}).get("version") or "")
        if loader == "quilt":
            rows = _get(f"https://meta.quiltmc.org/v3/versions/loader/{mc}")
            return str((rows[0].get("loader") or {}).get("version") or "") if rows else ""
        if loader == "forge":
            text = net.get_bytes(
                "https://maven.minecraftforge.net/net/minecraftforge/forge/"
                "maven-metadata.xml", timeout=20, retries=1).decode("utf-8", "ignore")
            versions = re.findall(r"<version>([^<]+)</version>", text)
            mine = [v.split("-", 1)[1] for v in versions if v.startswith(mc + "-")]
            return mine[-1] if mine else ""
    except Exception:
        return ""
    return ""


def ensure_loader(mc: str, loader: str, loader_version: str,
                  emit: Callable[[str], None],
                  cancel: Optional[threading.Event]) -> str:
    """装好 loader，返回可直接启动的版本 ID。"""
    from . import installer
    loader = (loader or "").lower()
    if loader in ("", "vanilla", "none"):
        return mc
    lv = loader_version or _latest_loader(mc, loader)
    if not lv:
        raise RuntimeError(f"找不到 {LOADER_LABEL.get(loader, loader)} 的可用版本")

    if loader in ("fabric", "quilt"):
        base = ("https://meta.fabricmc.net/v2/versions/loader"
                if loader == "fabric" else
                "https://meta.quiltmc.org/v3/versions/loader")
        emit(f"获取 {LOADER_LABEL[loader]} {lv} 的安装描述…")
        profile = _get(f"{base}/{mc}/{lv}/profile/json")
        vid = str(profile.get("id") or f"{loader}-loader-{lv}-{mc}")
        vdir = version_dir(vid)
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / f"{vid}.json").write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(f"下载 {LOADER_LABEL[loader]} 依赖库…")
        installer.install_from_meta(vid, profile, emit=emit, cancel=cancel,
                                    include_assets=False)
        return vid

    # Forge / NeoForge：跑官方 installer（它会自己写 versions/ 目录）
    return _run_forge_installer(mc, loader, lv, emit, cancel)


def _run_forge_installer(mc: str, loader: str, lv: str,
                         emit: Callable[[str], None],
                         cancel: Optional[threading.Event]) -> str:
    import subprocess
    from . import installer, java as javamod

    if loader == "forge":
        rel = f"net/minecraftforge/forge/{mc}-{lv}/forge-{mc}-{lv}-installer.jar"
        expected = f"{mc}-forge-{lv}"
    else:
        rel = f"net/neoforged/neoforge/{lv}/neoforge-{lv}-installer.jar"
        expected = f"neoforge-{lv}"
    # 大陆直连 neoforged / forge 官方 maven 常超时，失败后换 BMCLAPI 镜像
    urls = [
        (f"https://maven.minecraftforge.net/{rel}" if loader == "forge"
         else f"https://maven.neoforged.net/releases/{rel}"),
        f"https://bmclapi2.bangbang93.com/maven/{rel}",
    ]

    java = javamod.pick(8)
    if java is None:
        raise RuntimeError("安装 Forge/NeoForge 需要 Java 11+，请先在设置里指定 Java")

    jar = CACHE_DIR / "installers" / Path(urllib.parse.urlparse(urls[0]).path).name
    emit(f"下载 {LOADER_LABEL[loader]} 安装器…")
    last_err: Optional[Exception] = None
    for u in urls:
        try:
            net.download_file(u, jar)
            last_err = None
            break
        except Exception as e:  # noqa: BLE001 - 换镜像再试
            last_err = e
            emit(f"  下载失败（{u.split('/')[2]}），换下一个源…")
    if last_err is not None:
        raise RuntimeError(f"下载 {LOADER_LABEL[loader]} 安装器失败：{last_err}")

    emit(f"运行 {LOADER_LABEL[loader]} 安装器（可能要 1~3 分钟）…")
    cmd = [str(java.path), "-jar", str(jar), "--installClient", str(config.game_dir)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, cwd=str(config.game_dir), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            creationflags=flags)
    assert proc.stdout is not None
    queue_tail: list[str] = []
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        if not line:
            continue
        queue_tail.append(line)
        if len(queue_tail) > 40:
            queue_tail.pop(0)
        if cancel is not None and cancel.is_set():
            proc.terminate()
            raise Cancelled()
        if any(k in line for k in ("Downloading", "Installing", "Extracting", "Building")):
            emit(f"  {line[:120]}")
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"{LOADER_LABEL[loader]} 安装器返回 {code}："
                           + " / ".join(queue_tail[-3:]))

    vid = expected if version_dir(expected).exists() else ""
    if not vid:
        cands = sorted(p.name for p in config.versions_dir.iterdir()
                       if p.is_dir() and (loader in p.name.lower()))
        vid = cands[-1] if cands else ""
    if not vid:
        raise RuntimeError(f"{LOADER_LABEL[loader]} 安装器没有生成版本目录")

    emit(f"{LOADER_LABEL[loader]} 安装完成：{vid}")
    # 补齐 installer 没下完的库
    try:
        raw = json.loads((version_dir(vid) / f"{vid}.json").read_text(encoding="utf-8"))
        installer.install_from_meta(vid, raw, emit=emit, cancel=cancel,
                                    include_assets=False)
    except Exception as e:  # noqa: BLE001
        emit(f"（补全依赖库时出错：{e}）")
    return vid


def _clone_version(src_id: str, new_id: str) -> str:
    """把 src 版本目录复制成 new_id，返回实际使用的版本 ID。

    同 loader 版本（例如两个包都用 fabric-loader-0.15.11-1.20.1）共用一份版本目录时，
    侧车文件会互相覆盖，所以每个整合包都拿一份自己的副本。
    """
    src = version_dir(src_id)
    dst = version_dir(new_id)
    if not src.exists():
        raise RuntimeError(f"版本 {src_id} 不存在，loader 可能没装成功")
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    src_json = src / f"{src_id}.json"
    if src_json.exists():
        try:
            meta = json.loads(src_json.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        meta["id"] = new_id
        (dst / f"{new_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        # 副本里那份旧名字的 JSON 要删掉，否则同一个目录里有两个版本描述
        stale = dst / f"{src_id}.json"
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                pass
    src_jar = src / f"{src_id}.jar"
    if src_jar.exists():
        try:
            shutil.copyfile(src_jar, dst / f"{new_id}.jar")
            (dst / f"{src_id}.jar").unlink(missing_ok=True)
        except OSError:
            pass
    return new_id


def _unique_version_id(slug: str) -> str:
    vid, n = slug, 2
    while version_dir(vid).exists():
        vid = f"{slug}-{n}"
        n += 1
    return vid


def _extract_overrides(pack_path: Path, info: PackInfo, target: Path,
                       emit: Callable[[str], None]) -> int:
    """把 overrides/ 解到实例目录；CurseForge 包则是除 manifest 外的全部内容。"""
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(pack_path) as zf:
        for info_ in zf.infolist():
            if info_.is_dir():
                continue
            name = info_.filename
            if info.kind == "mrpack":
                rel = ""
                for pre in ("overrides/", "client-overrides/"):
                    if name.startswith(pre):
                        rel = name[len(pre):]
                        break
                if not rel:
                    continue
            else:
                if name in ("manifest.json", "modrinth.index.json"):
                    continue
                rel = name[len("overrides/"):] if name.startswith("overrides/") else name
            if not rel or rel.endswith("/"):
                continue
            dst = target / rel
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info_) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count


def _download_files(files: list[PackFile], target: Path,
                    progress: Optional[Callable[[dict], None]],
                    emit: Callable[[str], None],
                    cancel: Optional[threading.Event]) -> list[str]:
    total = sum(f.size for f in files)
    meter = net.ProgressMeter(total, len(files))
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
    errors: list[str] = []

    def one(item: PackFile) -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        dest = target / item.path
        meter.set_current(item.path)
        download(item.url, dest, expected_sha1=item.sha1 or None,
                 expected_size=item.size or None, progress=meter.add_bytes)
        meter.file_done()

    workers = max(4, min(16, int(config["concurrent"] or 8)))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(one, f): f for f in files}
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    fut.result()
                except Cancelled:
                    for f in futures:
                        f.cancel()
                    raise
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{item.path}: {e}")
                    meter.file_done()
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
    finally:
        stop.set()
        th.join(timeout=1.0)
        if progress:
            progress(meter.snapshot())
    return errors


def install_info(info: PackInfo, pack_path: Optional[Path],
                 progress: Optional[Callable[[dict], None]] = None,
                 emit: Optional[Callable[[str], None]] = None,
                 cancel: Optional[threading.Event] = None) -> dict:
    """按 PackInfo 落盘：原版 → loader → 实例目录 → overrides → 模组文件。"""
    log = emit or (lambda *_: None)
    if not info.mc_version:
        raise RuntimeError("整合包没有写明 Minecraft 版本，无法安装")

    _ensure_base(info.mc_version, log, cancel)
    loader_id = ensure_loader(info.mc_version, info.loader, info.loader_version, log, cancel)

    # 每个整合包一份独立版本目录 + 独立实例目录
    slug = _slug(f"{info.name}-{info.mc_version}-{info.loader or 'vanilla'}")
    vid = _unique_version_id(slug)
    _clone_version(loader_id, vid)
    base = instances_dir() / vid
    base.mkdir(parents=True, exist_ok=True)
    log(f"实例目录：{base}")

    rel_inst = base.relative_to(config.game_dir).as_posix()
    (version_dir(vid) / INSTANCE_FILE).write_text(json.dumps({
        "game_dir": rel_inst,
        "pack": info.name,
        "mc_version": info.mc_version,
        "loader": info.loader,
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    if pack_path is not None:
        log("解压 overrides / 配置…")
        n_files = _extract_overrides(pack_path, info, base, log)
        log(f"已写入 {n_files} 个配置文件")

    errors: list[str] = []
    if info.files:
        log(f"开始下载 {len(info.files)} 个模组文件，共 "
            f"{sum(f.size for f in info.files) / 1048576:.1f} MB…")
        errors = _download_files(info.files, base, progress, log, cancel)
        if errors:
            log(f"有 {len(errors)} 个文件失败（可在「版本」页点补全重试）")
            for e in errors[:5]:
                log("  · " + e)

    meta = {
        "name": info.name, "version": info.version, "summary": info.summary,
        "source": info.source, "mc_version": info.mc_version, "loader": info.loader,
        "loader_version": info.loader_version, "version_id": vid,
        "instance": rel_inst, "files": len(info.files), "errors": errors[:20],
        "note": info.missing_note,
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (version_dir(vid) / PACK_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (base / PACK_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"整合包安装完成：{info.name}（版本 {vid}）")
    return {"ok": True, "version_id": vid, "instance": rel_inst,
            "files": len(info.files), "errors": errors[:20],
            "note": info.missing_note}


# ---------------------------------------------------------------- 入口


def install_from_version(project: str, version_id: str,
                         progress: Optional[Callable[[dict], None]] = None,
                         emit: Optional[Callable[[str], None]] = None,
                         cancel: Optional[threading.Event] = None) -> dict:
    """从 Modrinth 的某个版本一键安装。"""
    log = emit or (lambda *_: None)
    rows = _api_get(f"/project/{project}/version")
    ver = next((v for v in rows if v.get("id") == version_id), None)
    if ver is None:
        raise RuntimeError("找不到该整合包版本（可能已被作者删除）")
    files = ver.get("files") or []
    primary = next((f for f in files if f.get("primary")), files[0] if files else None)
    if not primary or not primary.get("url"):
        raise RuntimeError("该版本没有可下载的文件")

    name = primary.get("filename") or f"{project}.mrpack"
    dest = CACHE_DIR / "modpacks" / name
    log(f"下载整合包文件 {name}（{(primary.get('size') or 0) / 1048576:.1f} MB）…")
    download(primary["url"], dest,
             expected_sha1=(primary.get("hashes") or {}).get("sha1") or None,
             expected_size=primary.get("size") or None)
    info = read_zip_info(dest)
    info.project = project
    if not info.name:
        info.name = project
    if not info.version:
        info.version = str(ver.get("version_number") or "")
    return install_info(info, dest, progress=progress, emit=log, cancel=cancel)


def install_from_file(path: Path,
                      progress: Optional[Callable[[dict], None]] = None,
                      emit: Optional[Callable[[str], None]] = None,
                      cancel: Optional[threading.Event] = None) -> dict:
    """安装本地拖进来的整合包。"""
    log = emit or (lambda *_: None)
    info = read_zip_info(path)
    if not info.name:
        info.name = path.stem
    log(f"识别为 {info.source or '本地'} 整合包：《{info.name}》")
    if info.missing_note:
        log(info.missing_note)
    return install_info(info, path, progress=progress, emit=log, cancel=cancel)
