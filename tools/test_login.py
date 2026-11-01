# -*- coding: utf-8 -*-
"""正版登录自检：授权链接、回调解析、窗口回调抓取（用本地假页面模拟）。"""
from __future__ import annotations

import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import auth, mslogin  # noqa: E402

OK, FAIL = "✓", "✗"
fails = []


def check(name, cond, extra=""):
    print(f"  {OK if cond else FAIL} {name}{(' -> ' + str(extra)) if extra and not cond else ''}")
    if not cond:
        fails.append(name)


print("1) 授权链接")
url = auth.auth_url("state-abc")
q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
check("指向微软授权页", url.startswith("https://login.live.com/oauth20_authorize.srf?"))
check("client_id 用内置公共客户端", q["client_id"][0] == auth.CLIENT_ID, q.get("client_id"))
check("redirect_uri 正确", q["redirect_uri"][0] == "https://login.live.com/oauth20_desktop.srf")
check("scope 含 XboxLive+offline", "XboxLive.signin" in q["scope"][0]
      and "offline_access" in q["scope"][0])
check("带 state", q["state"][0] == "state-abc")
check("response_type=code", q["response_type"][0] == "code")

print("2) 回调解析")
cb = ("https://login.live.com/oauth20_desktop.srf?code=M.R3_BAY.2a1b3c4d-1111-2222-3333-"
      "444455556666&lc=1033&state=state-abc")
check("从完整链接取 code", auth.parse_code(cb) == "M.R3_BAY.2a1b3c4d-1111-2222-3333-444455556666")
check("裸 code 也能用", auth.parse_code("M.C509_BAY.abcdefghijklmnop") == "M.C509_BAY.abcdefghijklmnop")
check("URL 编码的 code 会解码", auth.parse_code("https://x/oauth20_desktop.srf?code=A%2BB%3D") == "A+B=")
try:
    auth.parse_code(cb)
    auth._check_state(cb, "other")
    check("state 不匹配会拒绝", False)
except RuntimeError:
    check("state 不匹配会拒绝", True)
try:
    auth.parse_code("https://login.live.com/oauth20_desktop.srf?error=access_denied")
    check("取消授权能识别", False)
except RuntimeError as e:
    check("取消授权能识别", "取消" in str(e), e)
try:
    auth.parse_code("随便一段文字")
    check("乱贴会报错", False)
except RuntimeError:
    check("乱贴会报错", True)

print("3) 登录窗口回调抓取（本地假页面，不碰微软）")


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/start"):
            body = ("<script>location.replace('/cb/oauth20_desktop.srf?code=SIMULATED123"
                    "&state=S1')</script>")
        else:
            body = "callback page"
        data = f"<!doctype html><meta charset=utf-8>{body}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
local = f"http://127.0.0.1:{srv.server_address[1]}/start"

win = mslogin.open_login_window(local)
check("能用 Chromium 打开登录窗口", bool(win), mslogin.find_browser())
if win:
    got = ""
    for _ in range(20):
        time.sleep(1)
        got = mslogin._callback_url(win["port"])
        if got:
            break
    check("读到窗口跳回的回调链接", "code=SIMULATED123" in got, got)
    mslogin._kill(win["proc"])

    # 完整走一遍 watcher：把换令牌那步换成假的，验证抓到的 code 能交给 auth
    captured = {}

    def fake_complete(code, state="", emit=print):
        captured["code"] = code
        auth._set(status="ok", stage="登录成功", name="TestUser", uuid="0" * 32)
        return {"name": "TestUser"}

    real = auth.complete_login
    auth.complete_login = fake_complete
    mslogin._active.update({"state": "S1", "proc": None, "port": 0})
    win2 = mslogin.open_login_window(local)
    mslogin._watch("S1", win2["proc"], win2["port"], print)
    auth.complete_login = real
    check("watcher 自动完成登录", auth.login_status()["status"] == "ok",
          auth.login_status())
    check("授权码正确传入", captured.get("code") == "SIMULATED123", captured)
    mslogin._kill(win2["proc"])

srv.shutdown()
auth.reset_state()
print()
print("全部通过" if not fails else f"失败 {len(fails)} 项：{fails}")
sys.exit(1 if fails else 0)
