# -*- coding: utf-8 -*-
"""整合包网络自检：数据源切换、线路测速、CDN 改写、代理配置。

    python tools/test_net.py

需要联网；官方接口在部分网络下会超时，此时只要求镜像可达。
"""
import json
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402
from core import modpack  # noqa: E402
from core.config import config  # noqa: E402

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    with opener.open(req, timeout=60) as r:
        return json.loads(r.read().decode())


fails = []


def check(name, cond, extra=""):
    print(f"  {'✓' if cond else '✗'} {name}{'' if cond else ' -> ' + str(extra)[:200]}")
    if not cond:
        fails.append(name)


try:
    print("1) 连通性测试接口")
    probe = call("/api/net/probe")
    print("   ", json.dumps(probe, ensure_ascii=False)[:260])
    reach = [k for k in ("official", "mirror") if probe["results"][k]["ok"]]
    print("    可达线路:", reach or "无")
    check("至少一条线路可达", bool(reach))
    check("给出建议线路", probe["best"] in ("official", "mirror"))

    print("2) 三种数据源各自的搜索")
    for mode in ("mirror", "official", "auto"):
        call("/api/config", {"config": {"pack_source": mode}})
        t0 = time.time()
        d = call("/api/modpack/search?q=sodium&limit=3")
        hits = d.get("hits") or []
        used = modpack._live_api
        print(f"    {mode:9s} {len(hits)} 条  {(time.time()-t0)*1000:.0f} ms  "
              f"入口={'镜像' if used == modpack.API_MIRROR else '官方'}")
        check(f"{mode} 能搜到结果", len(hits) > 0, d)
        if hits:
            check(f"{mode} 带镜像图标地址", bool(hits[0].get("icon_mirror")), hits[0])

    print("3) 镜像模式下版本列表 + 下载地址改写")
    call("/api/config", {"config": {"pack_source": "mirror"}})
    pid = (call("/api/modpack/search?q=optimized&limit=1")["hits"] or [{}])[0].get("slug", "")
    vers = call(f"/api/modpack/versions?project={pid}")["versions"]
    print(f"    {pid}: {len(vers)} 个版本")
    check("镜像模式下能取版本", len(vers) > 0, vers)
    cdn = "https://cdn.modrinth.com/data/ApfI7pvI/versions/x/y.mrpack"
    check("CDN 改写正确",
          modpack.to_mirror(cdn) == "https://mod.mcimirror.top/data/ApfI7pvI/versions/x/y.mrpack",
          modpack.to_mirror(cdn))
    check("镜像模式只走镜像", modpack.file_candidates(cdn)[0].startswith("https://mod.mcimirror.top"))
    call("/api/config", {"config": {"pack_source": "auto"}})
    check("自动模式官方优先", modpack.file_candidates(cdn)[0] == cdn,
          modpack.file_candidates(cdn))
    check("自动模式保留镜像兜底", len(modpack.file_candidates(cdn)) == 2)

    print("4) 代理配置能落到网络层")
    call("/api/config", {"config": {"net_proxy": "http://127.0.0.1:7890"}})
    from core import net
    check("代理已生效", net.proxy_url() == "http://127.0.0.1:7890", net.proxy_url())
    call("/api/config", {"config": {"net_proxy": ""}})
    check("代理可清空", net.proxy_url() == "", net.proxy_url())
finally:
    config.update({"pack_source": "auto", "net_proxy": ""})
    httpd.shutdown()

print()
print("全部通过" if not fails else f"失败 {len(fails)} 项：{fails}")
sys.exit(1 if fails else 0)
