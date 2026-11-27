# -*- coding: utf-8 -*-
"""内存优化 + 崩溃诊断自检（含 HTTP 接口，测试后自动还原配置）。"""
from __future__ import annotations

import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import diagnose, sysinfo           # noqa: E402
from core.config import config               # noqa: E402
import server                                # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def main() -> int:
    backup = dict(config.data)
    print("1) 内存推荐算法")
    for total in (2048, 4096, 8192, 16384, 65536):
        rec = sysinfo.recommend_all(total)
        check(f"{total}MB 三档均合法",
              all(min(sysinfo.PROFILES[k]["min"], sysinfo.safe_max(total)) <= v
                  <= sysinfo.PROFILES[k]["max"]
                  and v <= sysinfo.safe_max(total) and v % 128 == 0
                  for k, v in rec.items()), str(rec))
        check(f"{total}MB 三档递增", rec["vanilla"] <= rec["light"] <= rec["heavy"], str(rec))
    check("极小内存不崩", sysinfo.recommend(256, "heavy") >= 1024)
    check("safe_max 留系统余量", sysinfo.safe_max(8192) <= 8192 - 1024)
    check("min_heap 不超过 Xmx", sysinfo.min_heap(1024) <= 1024)
    check("文本解析 8G", sysinfo.parse_memory_text("8G") == 8192)
    check("文本解析 4096M", sysinfo.parse_memory_text("4096M") == 4096)
    check("文本解析非法值", sysinfo.parse_memory_text("abc") is None)

    print("2) 崩溃日志诊断")
    cases = [
        ("java.lang.UnsupportedClassVersionError: bad class file version", "java_version"),
        ("Exception in java.lang.OutOfMemoryError: Java heap space", "out_of_memory"),
        ("Caused by: ModLoadingException: mod foo failed to load", "mod_conflict"),
        ("MissingDependenciesException: requires mod architectury", "missing_dependency"),
        ("NoClassDefFoundError: net/minecraft/client/Main", "class_missing"),
    ]
    for text, expected in cases:
        d = diagnose.analyze(text)
        check(f"识别 {expected}", d["count"] > 0 and d["issues"][0]["id"] == expected,
              str(d["issues"])[:120])
    check("空日志不误报", diagnose.analyze("")["count"] == 0)
    check("正常日志不误报", diagnose.analyze("[INFO] Starting server")["count"] == 0)
    multi = diagnose.analyze("OutOfMemoryError\nUnsupportedClassVersionError\nmod loading failed")
    check("多问题按优先级排序", multi["count"] >= 2 and multi["issues"][0]["id"] == "java_version")
    check("提取 Caused by", bool(diagnose.analyze("Caused by: java.lang.RuntimeException: boom")["cause"]))

    print("3) HTTP 接口")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path: str) -> dict:
        return json.load(op.open(f"http://127.0.0.1:{port}{path}", timeout=30))

    def post(path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        return json.load(op.open(req, timeout=40))

    try:
        info = get("/api/sysinfo")
        check("/api/sysinfo 返回物理内存", info["total_mb"] > 0)
        check("/api/sysinfo 返回三档推荐", set(info["recommend"]) == set(sysinfo.PROFILES))

        before = config["memory_mb"]
        r = post("/api/config/optimize", {"profile": "heavy"})
        check("/api/config/optimize 生效", r["ok"] and r["applied_mb"] == config["memory_mb"],
              str(r)[:120])
        check("预设被记录", config["memory_profile"] == "heavy")
        check("Xms 不超过 Xmx", config["min_memory_mb"] <= config["memory_mb"])
        check("手动指定值被采纳",
              post("/api/config/optimize", {"profile": "light", "memory_mb": 4096})["applied_mb"] == 4096)
        check("非法预设被拒绝（保持原值）",
              post("/api/config/optimize", {"profile": "unknown"})["config"]["memory_profile"] == "light")

        d = post("/api/logs/diagnose", {"text": "OutOfMemoryError: Java heap space"})
        check("/api/logs/diagnose 出结果", d["count"] >= 1 and d["issues"][0]["id"] == "out_of_memory")
        d2 = post("/api/logs/diagnose", {})
        check("/api/logs/diagnose 空 body 不崩", d2["ok"] and "count" in d2)
        _ = before
    finally:
        httpd.shutdown()

    # 还原配置，避免测试污染真实设置
    config._data.clear()
    config._data.update(backup)
    config.save()

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
