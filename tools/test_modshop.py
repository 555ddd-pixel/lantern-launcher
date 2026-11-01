# -*- coding: utf-8 -*-
"""模组在线搜索 / 下载自检：搜索、版本列表、下载入库、换源。

    python tools/test_modshop.py

需要联网（走 Modrinth，官方连不上会自动换国内镜像）。
全部在临时游戏目录里跑，不碰真实的 .minecraft。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import content, modpack  # noqa: E402
from core.config import config  # noqa: E402
from core.meta import version_dir  # noqa: E402
import server  # noqa: E402  （server.py 在项目根目录）

PASS = 0
FAIL = 0
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机请求不走系统代理


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def step(title: str) -> None:
    print(f"\n=== {title} ===")


def call(port: int, path: str, body: dict | None = None, timeout: float = 90) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with OP.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="lantern-shop-"))
    print("临时目录:", work)
    config["game_dir"] = work / "mc"
    vid = "fabric-loader-0.16.9-1.20.1"
    vdir = version_dir(vid)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{vid}.json").write_text(json.dumps({
        "id": vid, "inheritsFrom": "1.20.1",
        "libraries": [{"name": "net.fabricmc:fabric-loader:0.16.9"}],
    }), encoding="utf-8")

    step("1. 从实例反推 MC 版本")
    check("fabric-loader-0.16.9-1.20.1 → 1.20.1", content.mc_version(vid) == "1.20.1",
          content.mc_version(vid))
    check("原版版本号直接返回", content.mc_version("1.21.1") == "1.21.1")
    check("state 里带上 mc_version", content.state(vid).get("mc_version") == "1.20.1")

    httpd, _ = server.start(0, False)
    port = httpd.server_address[1]
    try:
        step("2. 搜索接口")
        d = call(port, f"/api/mods/search?version={vid}&area=mods&q=sodium&limit=5")
        check("返回 hits", bool(d.get("hits")), str(d)[:160])
        first = (d.get("hits") or [{}])[0]
        check("命中有标题 / 图标字段", bool(first.get("title")) and "icon_mirror" in first)
        check("带了数据源标记", d.get("source") in ("auto", "official", "mirror"), str(d.get("source")))

        d2 = call(port, f"/api/mods/search?version={vid}&area=shaderpacks&q=bsl&limit=3")
        check("光影搜索可用", bool(d2.get("hits")), str(d2)[:160])

        d3 = call(port, f"/api/mods/search?version={vid}&area=resourcepacks&limit=3")
        check("资源包空关键词也有热门结果", bool(d3.get("hits")), str(d3)[:160])

        step("3. 版本列表（按 MC + 加载器过滤）")
        proj = first.get("slug") or first.get("id")
        v = call(port, f"/api/mods/versions?project={proj}&area=mods&version={vid}")
        rows = v.get("versions") or []
        check("拿到文件版本", bool(rows), str(v)[:200])
        row = rows[0] if rows else {}
        check("版本带 file.url / id", bool(row.get("id")) and bool((row.get("file") or {}).get("url")))
        check("版本兼容 1.20.1", "1.20.1" in (row.get("game_versions") or []), str(row.get("game_versions")))
        check("版本是 fabric", "fabric" in (row.get("loaders") or []), str(row.get("loaders")))

        step("4. 下载并入库")
        if row:
            r = call(port, "/api/mods/install", {
                "version": vid, "file_version": row["id"], "area": "mods",
                "title": first.get("title") or "test",
            })
            check("任务已启动", r.get("ok") is True, str(r)[:160])
            deadline = time.time() + 180
            snap = {}
            while time.time() < deadline:
                snap = call(port, "/api/status").get("task") or {}
                if not snap.get("running"):
                    break
                time.sleep(1.5)
            check("任务完成", snap.get("phase") == "完成",
                  f"{snap.get('phase')} / {snap.get('error')}")
            res = snap.get("result") or {}
            check("结果带文件名", bool(res.get("file")), str(res)[:160])
            target = Path((snap.get("result") or {}).get("dir") or work) / (res.get("file") or "x")
            check("文件真的落盘", target.exists(), str(target))
            st = content.state(vid)
            names = [i["name"] for i in st["areas"]["mods"]["items"]]
            check("出现在模组列表里", (res.get("file") in names) or bool(names), str(names))
            check("读到了模组元信息", any(i.get("title") for i in st["areas"]["mods"]["items"]))

        step("5. 换源与代理配置")
        saved = config["pack_source"]
        for mode in ("mirror", "official", "auto"):
            config["pack_source"] = mode
            bases = modpack.api_bases()
            if mode == "mirror":
                check("mirror 只走镜像", bases == [modpack.API_MIRROR], str(bases))
            elif mode == "official":
                check("official 只走官方", bases == [modpack.API_OFFICIAL], str(bases))
            else:
                check("auto 两个入口都在", set(bases) == {modpack.API_OFFICIAL, modpack.API_MIRROR}, str(bases))
        config["pack_source"] = saved
        check("下载地址能改写成镜像", modpack.to_mirror("https://cdn.modrinth.com/data/x/y.jar")
              == "https://mod.mcimirror.top/data/x/y.jar")
    finally:
        httpd.shutdown()

    print(f"\n结果：{PASS} 通过，{FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
