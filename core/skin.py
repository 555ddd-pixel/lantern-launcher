# -*- coding: utf-8 -*-
"""皮肤：查看玩家皮肤 + 给正版账号换肤。

查皮肤（只读）::
    name → api.mojang.com/users/profiles/minecraft/{name}     拿 UUID
         → sessionserver.mojang.com/.../profile/{uuid}        拿 SKIN/CAPE 地址
    官方链路任何一步失败就退到 minotar / crafatar 这类只读镜像，
    再不行才报错 —— 查皮肤本身不该因为网络抖动失败。

换肤（要正版登录）::
    PUT  api.minecraftservices.com/minecraft/profile/skins   用图片链接换
    POST api.minecraftservices.com/minecraft/profile/skins   上传 PNG 换（multipart）
"""
from __future__ import annotations

import base64
import json
import struct
import urllib.error

from . import net
from .auth import current_account

PROFILE_API = "https://api.mojang.com/users/profiles/minecraft/{name}"
SESSION_API = "https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"
SET_SKIN_URL_API = "https://api.minecraftservices.com/minecraft/profile/skins"

FALLBACK_SKIN = "https://minotar.net/skin/{name}"
FALLBACK_SKIN_2 = "https://crafatar.com/skins/{uuid}"

VALID_SIZES = {(64, 64), (64, 32), (128, 128)}


# ---------------------------------------------------------------- 查询


def lookup(name: str) -> dict:
    """玩家名 → {uuid, name}。官方接口 404 视为玩家不存在。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("先填玩家名")
    try:
        d = net.get_json(PROFILE_API.format(name=name), timeout=10, retries=1)
        if d.get("id"):
            return {"uuid": d["id"], "name": d.get("name") or name}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(f"Mojang 账号库里没有 {name} 这个正版玩家") from None
        raise
    raise RuntimeError("Mojang 玩家查询接口没有返回 id")


def textures(uuid: str) -> dict:
    """UUID → {skin_url, cape_url, model}。没有皮肤返回 {}。"""
    try:
        d = net.get_json(SESSION_API.format(uuid=uuid), timeout=10, retries=1)
        props = d.get("properties") or []
        for p in props:
            if p.get("name") != "textures":
                continue
            raw = json.loads(base64.b64decode(p.get("value") or "{}").decode("utf-8", "replace"))
            tex = raw.get("textures") or {}
            skin = tex.get("SKIN") or {}
            cape = tex.get("CAPE") or {}
            model = ((skin.get("metadata") or {}).get("model")) or "classic"
            return {"skin_url": skin.get("url") or "", "cape_url": cape.get("url") or "",
                    "model": model}
    except Exception:  # noqa: BLE001 - 网络抖动不该打断查询
        pass
    return {}


def info(name: str = "") -> dict:
    """皮肤查询主入口。name 留空用当前账号名。"""
    name = (name or "").strip() or str((config_name()))
    try:
        p = lookup(name)
        tex = textures(p["uuid"])
        if tex.get("skin_url"):
            return {"name": p["name"], "uuid": p["uuid"], "source": "官方接口",
                    "live": True, **tex}
        # 有正版账号但没上传过皮肤：Mojang 返回默认 Steve
        return {"name": p["name"], "uuid": p["uuid"], "source": "官方接口",
                "live": True, "skin_url": "", "cape_url": "", "model": "classic"}
    except Exception as e:  # noqa: BLE001 - 官方链路挂了走只读镜像
        last_err = str(e)
    # 兜底 1：minotar 按名字解析（只对正版玩家有效）
    try:
        url = FALLBACK_SKIN.format(name=name)
        data = net.get_bytes(url, timeout=10, retries=1)
        _check_png(data)
        return {"name": name, "uuid": "", "skin_url": url, "cape_url": "",
                "model": "classic", "source": "minotar 镜像", "live": False}
    except Exception:  # noqa: BLE001
        pass
    # 兜底 2：crafatar 按离线 UUID（永远有图，只是多半是默认 Steve）
    from .auth import offline_uuid
    try:
        uuid = offline_uuid(name)
        url = FALLBACK_SKIN_2.format(uuid=uuid.replace("-", ""))
        data = net.get_bytes(url, timeout=10, retries=1)
        _check_png(data)
        return {"name": name, "uuid": uuid, "skin_url": url, "cape_url": "",
                "model": "classic", "source": "crafatar 镜像", "live": False,
                "note": "官方接口刚才连不上（" + last_err[:80] + "），显示的是镜像结果"}
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"查不到 {name} 的皮肤：官方接口失败（{last_err[:120]}），镜像也连不上") from e


def config_name() -> str:
    acc = current_account()
    return acc.get("name") or "Steve"


# ---------------------------------------------------------------- 换肤


def _check_png(data: bytes) -> tuple:
    """读 PNG IHDR 里的宽高；顺手确认这真的是张 PNG。"""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("这不是一张 PNG 图片")
    w, h = struct.unpack(">II", data[16:24])
    if (w, h) not in VALID_SIZES:
        raise ValueError(f"皮肤图片尺寸必须是 64x64 或 64x32（当前 {w}x{h}）")
    return (w, h)


def set_skin_by_url(url: str, variant: str = "classic") -> None:
    """用一条公网可访问的图片链接换肤。需要正版 access_token。"""
    token, name = _ms_account()
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("皮肤链接必须是 http(s) 地址")
    variant = _variant(variant)
    req = urllib.request.Request(
        SET_SKIN_URL_API, method="PUT",
        data=json.dumps({"variant": variant, "url": url}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}", "User-Agent": net.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(_skin_error(e)) from None


def set_skin_file(png: bytes, variant: str = "classic") -> None:
    """直接上传 PNG 换肤（ multipart，链接不通时用这个）。"""
    token, _ = _ms_account()
    _check_png(png)
    variant = _variant(variant)
    boundary = "LanternSkinBoundary7f3a2c"
    parts = []
    for field, value in (("variant", variant),):
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"\r\n\r\n{value}\r\n"
            .encode())
    parts.append(
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
         f"filename=\"skin.png\"\r\nContent-Type: image/png\r\n\r\n").encode())
    parts.append(png)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        SET_SKIN_URL_API, method="POST", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": f"Bearer {token}", "User-Agent": net.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(_skin_error(e)) from None


def _ms_account() -> tuple:
    acc = current_account()
    if acc.get("type") != "microsoft" or not acc.get("access_token") \
            or acc.get("access_token") == "0":
        raise ValueError("换肤需要微软正版账号，当前是离线模式；先在左下角用「微软正版」登录")
    return acc["access_token"], acc.get("name") or ""


def _variant(v: str) -> str:
    v = (v or "").strip().lower()
    if v in ("slim", "slim_arms", "3px"):
        return "slim"
    if v in ("classic", "wide", "4px", ""):
        return "classic"
    raise ValueError(f"手臂模型只能是 classic（宽）或 slim（细），收到的是 {v}")


def _skin_error(e: urllib.error.HTTPError) -> str:
    detail = ""
    try:
        detail = e.read().decode("utf-8", "replace")[:200]
    except Exception:  # noqa: BLE001
        pass
    if e.code == 401:
        return "登录状态过期了，左下角重新用微软正版登录一次再换"
    if e.code == 429:
        return "换得太频繁被官方限流了，过几分钟再试"
    if detail and "InvalidURL" in detail:
        return "官方要求皮肤链接必须是公网可访问的图片直链；本地文件请用「上传 PNG」"
    return f"官方接口返回 {e.code}：{detail or '换肤失败'}"
