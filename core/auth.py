# -*- coding: utf-8 -*-
"""账号：离线登录（生成离线 UUID）与微软正版登录（OAuth2 授权码流程）。

正版登录走的是微软官方「授权码」流程：
    授权页 → 用户登录并同意 → 跳回 oauth20_desktop.srf?code=...
拿到授权码后再依次换 Xbox Live → XSTS → Minecraft 令牌，最后取回正版档案。
客户端 ID 沿用 Minecraft 官方启动器的公共客户端，用户不需要自己申请 Azure 应用。
"""
from __future__ import annotations

import hashlib
import json
import re
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

# 微软 OAuth：Minecraft 官方启动器的公共客户端（公开、无需密钥）
CLIENT_ID = "00000000402b5328"
AUTHORIZE = "https://login.live.com/oauth20_authorize.srf"
REDIRECT = "https://login.live.com/oauth20_desktop.srf"
MS_TOKEN = "https://login.live.com/oauth20_token.srf"
SCOPE = "XboxLive.signin offline_access"

# 登录进度：界面上的登录弹层直接轮询它，不用去翻日志
_lock = threading.Lock()
_state: dict = {
    "status": "idle",        # idle | waiting | ok | error
    "stage": "",             # 当前进行到哪一步（给人看的中文）
    "error": "",
    "name": "", "uuid": "",
    "uri": "",               # 授权页链接（界面上「重新打开」用）
    "mode": "",              # window = 应用窗口，browser = 默认浏览器
    "started": 0.0,
}


def login_status() -> dict:
    with _lock:
        return dict(_state)


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def reset_state() -> None:
    _set(status="idle", stage="", error="", name="", uuid="",
         uri="", mode="", started=0.0)


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


# ------------------------------------------------------------ 微软授权


def auth_url(state: str, prompt: str = "select_account") -> str:
    """拼出微软授权页链接（state 用来校验回调确实是这次登录）。"""
    query = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT,
        "scope": SCOPE,
        "state": state,
    }
    if prompt:
        query["prompt"] = prompt
    return AUTHORIZE + "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)


def parse_code(text: str) -> str:
    """从回调链接里取出授权码；也容忍直接粘贴 code 本身。"""
    raw = (text or "").strip().strip('"').strip("'")
    if not raw:
        raise RuntimeError("还没有粘贴链接")

    low = raw.lower()
    if "error=access_denied" in low:
        raise RuntimeError("授权被取消，可以重新点「开始登录」")
    if re.search(r"[?&#]error=", raw):
        raise RuntimeError("微软返回了错误：请检查链接是否完整")

    m = re.search(r"[?&#]code=([^&\s#]+)", raw)
    if m:
        return urllib.parse.unquote(m.group(1))
    if re.fullmatch(r"[A-Za-z0-9._\-]{16,}", raw):
        return raw
    raise RuntimeError("链接里没有 code，请复制浏览器地址栏里的整条网址")


def _check_state(text: str, expect: str) -> None:
    m = re.search(r"[?&#]state=([^&\s#]+)", text or "")
    if not m or not expect:
        return
    if urllib.parse.unquote(m.group(1)) != expect:
        raise RuntimeError("授权回调与本次登录不匹配，请重新登录")


def exchange_code(code: str) -> dict:
    """授权码换微软令牌。"""
    return _post_form(MS_TOKEN, {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": REDIRECT,
        "scope": SCOPE,
    })


# ------------------------------------------------------------ 令牌链


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
        "Accept": "application/json",
        "User-Agent": net.USER_AGENT,
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"微软登录接口返回 {e.code}: {body[:300]}") from e


def login_with_token(token: dict, emit: Callable[[str], None] = print) -> dict:
    """微软令牌 → Xbox Live → XSTS → Minecraft 档案，成功后写入配置。"""
    ms_access = token.get("access_token") or ""
    if not ms_access:
        raise RuntimeError("微软没有返回访问令牌，请重试")

    _set(stage="正在验证 Xbox Live 账号…")
    emit("正在验证 Xbox Live 账号…")
    xbl = _post_json(XBL, {
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                       "RpsTicket": f"d={ms_access}"},
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
    })
    uhs = xbl["DisplayClaims"]["xui"][0]["uhs"]

    _set(stage="正在获取 XSTS 令牌…")
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
            raise RuntimeError("该微软账号还没有 Xbox 档案，请先在 xbox.com 创建") from e
        if "2148916238" in msg:
            raise RuntimeError("该账号是未成年账号，需要先加入家庭组") from e
        raise

    _set(stage="正在登录 Minecraft…")
    emit("正在登录 Minecraft…")
    mc = _post_json(MC_LOGIN, {"identityToken": f"XBL3.0 x={uhs};{xsts['Token']}"})
    access = mc["access_token"]

    req = urllib.request.Request(MC_PROFILE, headers={
        "Authorization": f"Bearer {access}", "User-Agent": net.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            profile = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError("这个微软账号没有购买 Minecraft（没查到游戏档案）") from e
        raise

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
    _set(status="ok", stage="登录成功", name=acc["name"], uuid=acc["uuid"], error="")
    emit(f"登录成功：{acc['name']}")
    return acc


def complete_login(code: str, state: str = "", emit: Callable[[str], None] = print) -> dict:
    """拿到授权码后的完整收尾（换令牌 + 令牌链）。"""
    _set(status="waiting", stage="已拿到授权，正在换取微软令牌…")
    emit("已拿到授权码，正在换取微软令牌…")
    token = exchange_code(code)
    return login_with_token(token, emit)
