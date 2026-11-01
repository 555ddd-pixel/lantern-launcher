# -*- coding: utf-8 -*-
"""账号：离线登录（生成离线 UUID）与微软正版登录（设备码流程，可选）。"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as uuidlib
from typing import Callable, Optional

from . import net
from .config import config

XBL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE = "https://api.minecraftservices.com/minecraft/profile"
DEVICE_CODE = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
TOKEN = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
SCOPE = "XboxLive.signin offline_access"

# 设备码登录的实时状态：界面上的「登录窗口」直接读它，不必去翻日志
_lock = threading.Lock()
device: dict = {
    "status": "idle",        # idle | waiting | ok | error
    "code": "", "uri": "", "uri_complete": "",
    "expires_in": 0, "started": 0.0,
    "stage": "", "error": "", "name": "", "uuid": "",
}


def device_state() -> dict:
    with _lock:
        return dict(device)


def _set_device(**kw) -> None:
    with _lock:
        device.update(kw)


def offline_uuid(name: str) -> str:
    """Java 的 UUID.nameUUIDFromBytes("OfflinePlayer:"+name) 等价实现。"""
    digest = hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest()
    return str(uuidlib.UUID(bytes=digest, version=3))


def offline_account(name: str) -> dict:
    return {
        "type": "offline",
        "name": name,
        "uuid": offline_uuid(name),
        "access_token": "0",
        "user_type": "legacy",
    }


def current_account() -> dict:
    acc = dict(config["account"])
    if not acc.get("name"):
        acc["name"] = config["player_name"] or "Steve"
    if acc.get("type") != "microsoft":
        return offline_account(acc["name"])
    if not acc.get("access_token") or not acc.get("uuid"):
        return offline_account(acc["name"])
    acc.setdefault("user_type", "msa")
    return acc


# ------------------------------------------------------------ 微软登录


def _post_json(url: str, payload: dict, headers: Optional[dict] = None) -> dict:
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", "Accept": "application/json",
         "User-Agent": net.USER_AGENT}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"{url} 返回 {e.code}: {body[:300]}") from e


def _post_form(url: str, payload: dict) -> dict:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": net.USER_AGENT,
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"登录接口返回 {e.code}: {body[:300]}") from e


def microsoft_login(
    client_id: str,
    emit: Callable[[str], None] = print,
    wait: bool = True,
) -> dict:
    """设备码登录。emit 里会给出用户需要访问的网址与代码。"""
    if not client_id:
        raise RuntimeError("需要先在设置里填写 Azure 应用（公共客户端）ID")

    _set_device(status="waiting", code="", uri="", uri_complete="", name="", uuid="",
                error="", started=time.time(), stage="正在向微软申请设备码…")

    dc = _post_form(DEVICE_CODE, {"client_id": client_id, "scope": SCOPE})
    _set_device(code=dc["user_code"], uri=dc["verification_uri"],
                uri_complete=dc.get("verification_uri_complete", dc["verification_uri"]),
                expires_in=int(dc.get("expires_in", 900)),
                stage="等待你在浏览器里输入代码")
    emit(f"请在浏览器打开 {dc['verification_uri']} 并输入代码：{dc['user_code']}")
    emit(f"链接：{dc.get('verification_uri_complete', dc['verification_uri'])}")
    if not wait:
        return {"device_code": dc["device_code"], "user_code": dc["user_code"],
                "verification_uri": dc["verification_uri"],
                "expires_in": dc.get("expires_in", 900)}

    interval = int(dc.get("interval", 5))
    deadline = time.time() + int(dc.get("expires_in", 900))
    token = None
    while time.time() < deadline:
        time.sleep(interval)
        try:
            token = _post_form(TOKEN, {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": dc["device_code"],
            })
            break
        except RuntimeError as e:
            msg = str(e)
            if "authorization_pending" in msg:
                continue
            if "slow_down" in msg:
                interval += 5
                continue
            raise
    if not token:
        raise RuntimeError("登录超时，请重试")

    _set_device(stage="已验证微软账号，正在验证 Xbox Live…")
    emit("正在验证 Xbox Live 账号…")
    xbl = _post_json(XBL, {
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                       "RpsTicket": f"d={token['access_token']}"},
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
    })
    uhs = xbl["DisplayClaims"]["xui"][0]["uhs"]

    _set_device(stage="正在获取 XSTS 令牌…")
    emit("正在获取 XSTS 令牌…")
    try:
        xsts = _post_json(XSTS, {
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl["Token"]]},
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        })
    except RuntimeError as e:
        msg = str(e)
        if "2148916233" in msg:
            raise RuntimeError("该微软账号还没有 Xbox 档案，请先创建") from e
        if "2148916238" in msg:
            raise RuntimeError("该账号是未成年账号，需要加入家庭组") from e
        raise

    _set_device(stage="正在登录 Minecraft…")
    emit("正在登录 Minecraft…")
    mc = _post_json(MC_LOGIN, {"identityToken": f"XBL3.0 x={uhs};{xsts['Token']}"})
    access = mc["access_token"]

    req = urllib.request.Request(MC_PROFILE, headers={
        "Authorization": f"Bearer {access}", "User-Agent": net.USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        profile = json.loads(r.read().decode("utf-8"))

    acc = {
        "type": "microsoft",
        "name": profile["name"],
        "uuid": profile["id"],
        "access_token": access,
        "refresh_token": token.get("refresh_token", ""),
        "user_type": "msa",
        "login_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    config.update({"account": acc, "player_name": acc["name"]})
    _set_device(status="ok", stage="登录成功", name=acc["name"], uuid=acc["uuid"],
                error="")
    emit(f"登录成功：{acc['name']}")
    return acc


def ms_login_async(client_id: str, emit: Callable[[str], None],
                   done: Callable[[Optional[dict], Optional[str]], None]) -> None:
    def run() -> None:
        try:
            acc = microsoft_login(client_id, emit)
            done(acc, None)
        except Exception as e:  # noqa: BLE001
            _set_device(status="error", stage="登录失败", error=str(e))
            done(None, str(e))

    threading.Thread(target=run, daemon=True).start()
