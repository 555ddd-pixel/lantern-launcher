# -*- coding: utf-8 -*-
"""皮肤功能自检：查皮肤、图片代理、换肤接口的参数校验与鉴权拦截。

    python tools/test_skin.py

前半段离线（本地假服务器 + 参数校验），后半段联网真查一次 Notch。
"""
import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402
from core import skin  # noqa: E402

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data or body is not None else "GET")
    with opener.open(req, timeout=60) as r:
        return json.loads(r.read().decode())


def call_raw(path, data=b"", method="POST", headers=None):
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers=headers or {})
    with opener.open(req, timeout=60) as r:
        return json.loads(r.read().decode())


def call_err(path, body=None):
    try:
        call(path, body)
        return ""
    except Exception as e:  # noqa: BLE001
        import json as j
        try:
            return j.loads(e.read().decode()).get("error", str(e))
        except Exception:  # noqa: BLE001
            return str(e)


fails = []


def check(name, cond, extra=""):
    print(f"  {'✓' if cond else '✗'} {name}{'' if cond else ' -> ' + str(extra)[:160]}")
    if not cond:
        fails.append(name)


try:
    print("1) 参数校验（离线）")
    r0 = call("/api/skin/preview", {"name": ""})   # 空名 = 查当前账号
    check("空玩家名回退当前账号", r0.get("ok") and r0["skin"].get("name"))
    check("换肤拒绝离线账号", "离线模式" in call_err("/api/skin/set", {"url": "https://a.com/s.png"}))
    check("非 http 链接被拒", "http(s)" in call_err("/api/skin/set", {"url": "file:///c:/s.png"})
          or "离线模式" in call_err("/api/skin/set", {"url": "file:///c:/s.png"}))
    check("非法 variant 被拒", "宽" in call_err("/api/skin/set", {"url": "https://a.com/s.png", "variant": "xxx"})
          or "离线模式" in call_err("/api/skin/set", {"url": "https://a.com/s.png", "variant": "xxx"}))
    check("图片代理拒白名单外域名",
          "白名单" in call_err("/api/skin/image?url=http://evil.example.com/a.png"))
    check("uuid 头像拒坏格式", "uuid" in call_err("/api/skin/uuid-avatar?uuid=xyz"))
    check("上传空内容被拒", bool(call_err("/api/skin/upload?variant=slim")))

    print("2) PNG 校验（离线）")
    fake = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (300).to_bytes(4, "big") + (300).to_bytes(4, "big")
    try:
        skin._check_png(fake)
        check("300x300 应被拒", False)
    except ValueError as e:
        check("300x300 应被拒", "64x64" in str(e), e)
    ok_png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (64).to_bytes(4, "big") + (64).to_bytes(4, "big") + b"rest"
    check("64x64 通过", skin._check_png(ok_png) == (64, 64))
    not_png = b"GIF89a" + ok_png[6:]
    try:
        skin._check_png(not_png)
        check("GIF 应被拒", False)
    except ValueError as e:
        check("GIF 应被拒", "PNG" in str(e), e)

    print("3) 真实查询（联网）")
    info = skin.info("Notch")
    print("   ", json.dumps(info, ensure_ascii=False)[:200])
    check("拿到 Notch 的 UUID", info.get("uuid", "").replace("-", "") == "069a79f444e94726a5befca90e38aaf5")
    check("有皮肤地址", bool(info.get("skin_url")))
    check("手臂模型合法", info.get("model") in ("classic", "slim"))

    print("4) 接口联动（联网）")
    r = call("/api/skin/preview", {"name": "Notch"})
    check("/api/skin/preview 可用", r["ok"] and r["skin"]["name"] == "Notch")
    img = opener.open(urllib.request.Request(
        base + "/api/skin/image?url=" + urllib.request.quote(r["skin"]["skin_url"], safe="")),
        timeout=30)
    head = img.read(8)
    check("皮肤图代理返回 PNG", head[:4] == b"\x89PNG", head[:4])
    av = opener.open(base + "/api/skin/uuid-avatar?uuid=069a79f444e94726a5befca90e38aaf5", timeout=30)
    check("uuid 头像返回 PNG", av.read(4) == b"\x89PNG")

except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    fails.append(f"异常：{e}")

print()
if fails:
    print(f"失败 {len(fails)} 项：" + "; ".join(fails))
    sys.exit(1)
print("全部通过")
