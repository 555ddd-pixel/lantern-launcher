# -*- coding: utf-8 -*-
"""模组 / 光影 / 资源包管理，以及加载器（Fabric / Forge / NeoForge / Quilt）版本目录。

几条约定：

  * **启用 / 禁用靠文件名**：`xxx.jar` ↔ `xxx.jar.disabled`。游戏只认 `.jar`，
    所以改名就等于停用，不用改配置、也不会丢文件（和 PCL / HMCL 思路一致）。
  * **删除先进回收站**：挪到游戏目录下的 `.lantern-trash/`，误删还能翻回来。
  * **一切路径都做越界校验**：只允许操作对应目录里的直接子项，拒绝 `../`。
  * 模组信息从 jar 里读：`fabric.mod.json` / `quilt.mod.json` /
    `META-INF/mods.toml` / `META-INF/neoforge.mods.toml` / `mcmod.info`。
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional

from . import net
from .config import CACHE_DIR, config
from .meta import version_dir
from .modpack import instance_game_dir, instances_dir, INSTANCE_FILE

DISABLED = ".disabled"
TRASH = ".lantern-trash"

# 区域：键 -> (目录名, 允许的扩展名, 显示名)
AREAS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "mods": ("mods", (".jar",), "模组"),
    "shaderpacks": ("shaderpacks", (".zip",), "光影"),
    "resourcepacks": ("resourcepacks", (".zip",), "资源包"),
}

LOADER_LABEL = {
    "vanilla": "原版", "fabric": "Fabric", "forge": "Forge",
    "neoforge": "NeoForge", "quilt": "Quilt", "liteloader": "LiteLoader",
}
LOADER_ORDER = ["vanilla", "fabric", "neoforge", "forge", "quilt"]

# 版本列表缓存：内存 + 磁盘（离线也能显示上次拉到的列表）
_cache: dict[str, tuple[float, list]] = {}
_cache_lock = threading.Lock()
LOADER_CACHE = CACHE_DIR / "loaders.json"
CACHE_TTL = 1800          # 30 分钟
DISK_TTL = 86400 * 7      # 磁盘缓存最多用一周


# ---------------------------------------------------------------- 目录

def game_dir_for(version_id: str) -> Path:
    """实例目录优先，其次是全局游戏目录（和启动时 --gameDir 的判断保持一致）。"""
    inst = instance_game_dir(version_id)
    if inst is not None:
        return inst
    return config.game_dir


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(config.game_dir.resolve()).as_posix()
    except Exception:
        return str(path)


def _safe_slug(text: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z._-]+", "-", text or "").strip("-.")
    return s or "instance"


def ensure_instance(version_id: str) -> Path:
    """给这个版本建一份独立游戏目录（已有的直接用）。

    设了独立目录后，mods / shaderpacks / saves / options.txt 都只属于这个版本，
    不会和别的版本互相污染。返回实例目录。
    """
    existing = instance_game_dir(version_id)
    if existing is not None:
        existing.mkdir(parents=True, exist_ok=True)
        return existing
    base = instances_dir() / _safe_slug(version_id)
    base.mkdir(parents=True, exist_ok=True)
    for folder, _, _ in AREAS.values():
        (base / folder).mkdir(parents=True, exist_ok=True)
    sidecar = version_dir(version_id) / INSTANCE_FILE
    if not sidecar.exists():
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps({
            "game_dir": _relative(base),
            "instance": True,
            "loader": detect_loader(version_id),
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return base


def detect_loader(version_id: str) -> str:
    """从版本 JSON 里推断加载器（看 inheritsFrom 与依赖库坐标）。"""
    p = version_dir(version_id) / f"{version_id}.json"
    raw: dict = {}
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    text = (version_id + " " + json.dumps(raw, ensure_ascii=False)).lower()
    if "neoforge" in text:
        return "neoforge"
    if "fabric" in text:
        return "fabric"
    if "quilt" in text:
        return "quilt"
    if "forge" in text:
        return "forge"
    if "liteloader" in text:
        return "liteloader"
    return "vanilla"


# ---------------------------------------------------------------- 模组信息

def _read_member(zf: zipfile.ZipFile, name: str, limit: int = 1 << 20) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return b""
    if info.file_size > limit:
        return b""
    try:
        return zf.read(info)
    except Exception:
        return b""


def _toml_field(block: str, key: str) -> str:
    m = re.search(rf'^\s*{key}\s*=\s*"([^"]*)"', block, re.M)
    if m:
        return m.group(1).strip()
    m = re.search(rf"^\s*{key}\s*=\s*'([^']*)'", block, re.M)
    return m.group(1).strip() if m else ""


def _first_mods_block(text: str) -> str:
    m = re.search(r"\[\[mods\]\](.*?)(?=\n\s*\[\[|\Z)", text, re.S)
    return m.group(1) if m else text[:4000]


def _from_toml(text: str) -> dict:
    block = _first_mods_block(text)
    return {
        "title": _toml_field(block, "displayName") or _toml_field(block, "modId"),
        "version": _toml_field(block, "version"),
        "modid": _toml_field(block, "modId"),
        "description": _toml_field(block, "description"),
    }


def read_mod_meta(path: Path) -> dict:
    """尽量从 jar 里读出显示名 / 版本 / 加载器，读不到就用文件名兜底。"""
    fname = path.name
    if fname.endswith(DISABLED):
        fname = fname[: -len(DISABLED)]
    stem = Path(fname).stem
    info = {"title": stem, "version": "", "mod_loader": "", "modid": "",
            "description": ""}
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "fabric.mod.json" in names or "quilt.mod.json" in names:
                key = "fabric.mod.json" if "fabric.mod.json" in names else "quilt.mod.json"
                try:
                    raw = json.loads(_read_member(zf, key).decode("utf-8", "ignore") or "{}")
                except Exception:
                    raw = {}
                info["mod_loader"] = "fabric" if key.startswith("fabric") else "quilt"
                info["title"] = str(raw.get("name") or raw.get("id") or stem)
                info["version"] = str(raw.get("version") or "")
                info["modid"] = str(raw.get("id") or "")
                desc = raw.get("description")
                if isinstance(desc, str):
                    info["description"] = desc[:200]
            elif "META-INF/neoforge.mods.toml" in names:
                text = _read_member(zf, "META-INF/neoforge.mods.toml").decode("utf-8", "ignore")
                info.update({k: v for k, v in _from_toml(text).items() if v})
                info["mod_loader"] = "neoforge"
            elif "META-INF/mods.toml" in names:
                text = _read_member(zf, "META-INF/mods.toml").decode("utf-8", "ignore")
                info.update({k: v for k, v in _from_toml(text).items() if v})
                info["mod_loader"] = "forge"
            elif "mcmod.info" in names:
                try:
                    raw = json.loads(_read_member(zf, "mcmod.info").decode("utf-8", "ignore"))
                except Exception:
                    raw = []
                if isinstance(raw, dict):
                    raw = raw.get("modList") or []
                if isinstance(raw, list) and raw and isinstance(raw[0], dict):
                    first = raw[0]
                    info["title"] = str(first.get("name") or stem)
                    info["version"] = str(first.get("version") or "")
                    info["modid"] = str(first.get("modid") or "")
                    info["description"] = str(first.get("description") or "")[:200]
                    info["mod_loader"] = "forge"
    except (zipfile.BadZipFile, OSError, KeyError):
        pass

    if not info["title"] or info["title"] == stem:
        # 文件名兜底：Sodium-0.5.8+mc1.20.1 → Sodium / 0.5.8
        m = re.match(r"^(.+?)[-_ ]v?(\d[0-9A-Za-z.\-_+]*)\.jar$", fname, re.I)
        if m:
            info["title"] = m.group(1)
            info["version"] = info["version"] or m.group(2)
    return info


# ---------------------------------------------------------------- 列表 / 开关

def _safe_child(folder: Path, name: str) -> Path:
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError("文件名不合法")
    folder = folder.resolve()
    target = (folder / name).resolve()
    if target.parent != folder:
        raise ValueError("文件名不合法")
    return target


def list_area(game_dir: Path, area: str) -> list[dict]:
    folder_name, exts, _ = AREAS[area]
    folder = game_dir / folder_name
    items: list[dict] = []
    if not folder.exists():
        return items
    for p in folder.iterdir():
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        name = p.name
        enabled = not name.endswith(DISABLED)
        base = name if enabled else name[: -len(DISABLED)]
        if not base.lower().endswith(exts):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        item = {"file": name, "name": base, "enabled": enabled,
                "size": st.st_size, "mtime": int(st.st_mtime)}
        if area == "mods":
            item.update(read_mod_meta(p))
        else:
            item["title"] = Path(base).stem
        items.append(item)
    items.sort(key=lambda x: (not x["enabled"], (x.get("title") or x["name"]).lower()))
    return items


def state(version_id: str) -> dict:
    game_dir = game_dir_for(version_id)
    try:
        game_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    isolated = instance_game_dir(version_id) is not None
    loader = detect_loader(version_id)
    data = {
        "version": version_id,
        "game_dir": str(game_dir),
        "game_dir_rel": _relative(game_dir),
        "isolated": isolated,
        "loader": loader,
        "loader_label": LOADER_LABEL.get(loader, loader or "原版"),
        "areas": {},
    }
    for area in AREAS:
        items = list_area(game_dir, area)
        data["areas"][area] = {
            "label": AREAS[area][2],
            "dir": str(game_dir / AREAS[area][0]),
            "items": items,
            "total": len(items),
            "enabled": sum(1 for i in items if i["enabled"]),
        }
    return data


def toggle(version_id: str, area: str, name: str, enabled: bool) -> dict:
    if area not in AREAS:
        raise ValueError("未知分类")
    folder = game_dir_for(version_id) / AREAS[area][0]
    src = _safe_child(folder, name)
    if not src.exists():
        raise FileNotFoundError("文件不存在，可能已被移动")
    currently = not name.endswith(DISABLED)
    if currently == bool(enabled):
        return {"ok": True, "file": name, "enabled": bool(enabled), "changed": False}
    if enabled:
        dest_name = name[: -len(DISABLED)] if name.endswith(DISABLED) else name
    else:
        dest_name = name + DISABLED if not name.endswith(DISABLED) else name
    dest = src.with_name(dest_name)
    if dest.exists():
        raise FileExistsError(f"{dest.name} 已存在，先处理同名文件")
    src.rename(dest)
    return {"ok": True, "file": dest.name, "enabled": bool(enabled), "changed": True}


def toggle_all(version_id: str, area: str, enabled: bool) -> dict:
    if area not in AREAS:
        raise ValueError("未知分类")
    game_dir = game_dir_for(version_id)
    changed = 0
    errors: list[str] = []
    for item in list_area(game_dir, area):
        if item["enabled"] == bool(enabled):
            continue
        try:
            if toggle(version_id, area, item["file"], enabled).get("changed"):
                changed += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{item['name']}: {e}")
    return {"ok": True, "changed": changed, "errors": errors[:5]}


def remove(version_id: str, area: str, name: str) -> dict:
    if area not in AREAS:
        raise ValueError("未知分类")
    game_dir = game_dir_for(version_id)
    src = _safe_child(game_dir / AREAS[area][0], name)
    if not src.exists():
        raise FileNotFoundError("文件不存在")
    trash = game_dir / TRASH / time.strftime("%Y%m%d-%H%M%S")
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / src.name
    if dest.exists():
        dest = trash / f"{int(time.time() * 1000)}-{src.name}"
    shutil.move(str(src), str(dest))
    return {"ok": True, "trash": str(dest)}


# ---------------------------------------------------------------- 导入

def classify(path: Path) -> str:
    """判断拖进来 / 上传的东西属于哪一类，返回区域名或 'modpack'。"""
    suffix = path.suffix.lower()
    if suffix == ".jar":
        return "mods"
    if suffix == ".mrpack":
        return "modpack"
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
        except (zipfile.BadZipFile, OSError):
            return ""
        if "modrinth.index.json" in names or "manifest.json" in names:
            return "modpack"
        if any(n.startswith("shaders/") for n in names):
            return "shaderpacks"
        if "pack.mcmeta" in names:
            return "resourcepacks"
        return "modpack"
    return ""


def _unique_dest(folder: Path, filename: str) -> Path:
    dest = folder / filename
    if not dest.exists():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    for i in range(2, 100):
        dest = folder / f"{stem}-{i}{suffix}"
        if not dest.exists():
            return dest
    return folder / f"{stem}-{int(time.time())}{suffix}"


def place(version_id: str, src: Path, kind: str = "auto",
          filename: str = "") -> dict:
    """把一个已落地的临时文件放进对应目录，返回归类结果。"""
    name = filename or src.name
    area = kind if kind in AREAS else classify(src)
    if area not in AREAS:
        raise RuntimeError("不认识这个文件类型（支持 .jar 模组、.zip 光影 / 资源包）")
    game_dir = game_dir_for(version_id)
    folder = game_dir / AREAS[area][0]
    folder.mkdir(parents=True, exist_ok=True)
    clean = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip() or src.name
    if area == "mods" and not clean.lower().endswith(".jar"):
        clean += ".jar"
    if area != "mods" and not clean.lower().endswith(".zip"):
        clean += ".zip"
    dest = _unique_dest(folder, clean)
    shutil.move(str(src), str(dest))
    info = {"ok": True, "area": area, "file": dest.name, "dir": str(folder)}
    if area == "mods":
        info.update(read_mod_meta(dest))
    return info


def item_count(version_id: str, area: str = "mods") -> int:
    """只数文件个数（不读 jar 元信息），给版本列表用，够快。"""
    if area not in AREAS:
        return 0
    folder = game_dir_for(version_id) / AREAS[area][0]
    if not folder.exists():
        return 0
    exts = AREAS[area][1]
    n = 0
    for p in folder.iterdir():
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        name = p.name[: -len(DISABLED)] if p.name.endswith(DISABLED) else p.name
        if name.lower().endswith(exts):
            n += 1
    return n


# ---------------------------------------------------------------- 加载器目录

def _ver_key(text: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0
                 for x in re.split(r"[^0-9]+", text) if x != "")


def _sort_rows(rows: list[dict]) -> list[dict]:
    rows.sort(key=lambda r: _ver_key(r.get("version") or ""), reverse=True)
    return rows


def _neo_prefix(mc: str) -> str:
    # NeoForge 的版本号跟 MC 对应：1.21.1 → 21.1.x，1.21 → 21.0.x
    # （1.20.1 时期的 NeoForge 用的是 net.neoforged:forge 坐标，不在这里，
    #   所以 1.20.1 会返回空列表，界面会提示改用 Forge）
    parts = (mc or "").split(".")
    if len(parts) >= 3 and parts[0] == "1":
        return f"{parts[1]}.{parts[2]}"
    if len(parts) == 2 and parts[0] == "1":
        return f"{parts[1]}.0"
    return ""


def _cache_get(key: str, ttl: float = CACHE_TTL) -> Optional[list]:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    try:
        raw = json.loads(LOADER_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None
    item = raw.get(key) or {}
    if not item.get("rows"):
        return None
    # 磁盘缓存最多用 DISK_TTL：临时断网也不会让界面直接空掉
    if time.time() - float(item.get("at") or 0) > DISK_TTL:
        return None
    return item["rows"]


def _cache_put(key: str, rows: list[dict]) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), rows)
    try:
        raw = json.loads(LOADER_CACHE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw[key] = {"at": time.time(), "rows": rows[:200]}
    try:
        LOADER_CACHE.parent.mkdir(parents=True, exist_ok=True)
        LOADER_CACHE.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _fetch_text(urls: list[str], timeout: float = 20.0) -> str:
    """按顺序试多个地址，第一个成功的就用（大陆常连不上 neoforged 官方 maven）。"""
    errors: list[str] = []
    for url in urls:
        try:
            return net.get_bytes(url, timeout=timeout, retries=1).decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url.split('/')[2]}: {e}")
    raise RuntimeError("；".join(errors))


def _fetch_loader_versions(mc: str, loader: str) -> list[dict]:
    if loader == "fabric":
        rows = net.get_json(f"https://meta.fabricmc.net/v2/versions/loader/{mc}",
                            timeout=15, retries=1)
        out = []
        for r in rows or []:
            l = r.get("loader") or {}
            v = str(l.get("version") or "")
            if v:
                out.append({"version": v, "stable": bool(l.get("stable")), "note": ""})
        return _sort_rows(out)

    if loader == "quilt":
        rows = net.get_json(f"https://meta.quiltmc.org/v3/versions/loader/{mc}",
                            timeout=15, retries=1)
        out = []
        for r in rows or []:
            l = r.get("loader") or {}
            v = str(l.get("version") or "")
            if v:
                out.append({"version": v, "stable": True, "note": ""})
        return _sort_rows(out)

    if loader == "forge":
        text = _fetch_text([
            "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml",
            "https://bmclapi2.bangbang93.com/maven/net/minecraftforge/forge/maven-metadata.xml",
        ])
        versions = re.findall(r"<version>([^<]+)</version>", text)
        out = [{"version": v[len(mc) + 1:], "stable": True, "note": "", "full": v}
               for v in versions if v.startswith(mc + "-")]
        return _sort_rows(out)

    if loader == "neoforge":
        prefix = _neo_prefix(mc)
        if not prefix:
            return []
        text = _fetch_text([
            "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml",
            "https://bmclapi2.bangbang93.com/maven/net/neoforged/neoforge/maven-metadata.xml",
        ])
        versions = re.findall(r"<version>([^<]+)</version>", text)
        out = [{"version": v, "stable": True, "note": ""}
               for v in versions if v.startswith(prefix + ".")]
        return _sort_rows(out)

    return []


def loader_versions(mc: str, loader: str, refresh: bool = False) -> dict:
    loader = (loader or "vanilla").lower()
    label = LOADER_LABEL.get(loader, loader)
    if loader in ("", "vanilla", "none"):
        return {"mc": mc, "loader": "vanilla", "label": "原版",
                "versions": [], "recommend": "", "cached": False}
    if not mc:
        raise RuntimeError("缺少 Minecraft 版本号")
    key = f"{loader}:{mc}"
    if not refresh:
        rows = _cache_get(key)
        if rows is not None:
            return {"mc": mc, "loader": loader, "label": label, "versions": rows[:120],
                    "recommend": rows[0]["version"] if rows else "", "cached": True}
    error = ""
    try:
        rows = _fetch_loader_versions(mc, loader)
    except Exception as e:  # noqa: BLE001
        rows = _cache_get(key, ttl=DISK_TTL) or []
        error = str(e)[:200]
        if not rows:
            raise RuntimeError(f"读取 {label} 版本列表失败：{error}")
    if not rows:
        return {"mc": mc, "loader": loader, "label": label, "versions": [],
                "recommend": "", "cached": False,
                "error": f"{mc} 没有可用的 {label} 版本"}
    _cache_put(key, rows)
    return {"mc": mc, "loader": loader, "label": label, "versions": rows[:120],
            "recommend": rows[0]["version"], "cached": False, "error": error}


def loader_summary(mc: str) -> dict:
    """一次问清 5 种加载器各自的最新版本（并发，任何一个失败都不影响别的）。"""
    out: dict[str, dict] = {
        "vanilla": {"label": "原版", "recommend": "", "count": 0, "ok": True},
    }
    lock = threading.Lock()

    def one(loader: str) -> None:
        try:
            data = loader_versions(mc, loader)
            item = {"label": LOADER_LABEL[loader], "recommend": data["recommend"],
                    "count": len(data["versions"]), "ok": bool(data["versions"])}
        except Exception as e:  # noqa: BLE001
            item = {"label": LOADER_LABEL[loader], "recommend": "", "count": 0,
                    "ok": False, "error": str(e)[:120]}
        with lock:
            out[loader] = item

    threads = [threading.Thread(target=one, args=(l,), daemon=True)
               for l in LOADER_ORDER if l != "vanilla"]
    for t in threads:
        t.start()
    for t in threads:
        t.join(22)
    return {"mc": mc, "loaders": out, "order": LOADER_ORDER}
