# -*- coding: utf-8 -*-
"""自检：验证版本清单、元数据解析、库/资源展开是否正常（不实际下载大文件）。"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import installer, meta, net  # noqa: E402
from core.manifest import list_versions  # noqa: E402


def main() -> None:
    mirror = net.resolve_mirror("auto")
    print(f"[1] 下载源测速结果：{mirror}")

    versions = list_versions()
    counts = Counter(v.type for v in versions)
    print(f"[2] 版本总数：{len(versions)}  {dict(counts)}")
    print(f"    最新正式版：{next((v.id for v in versions if v.type == 'release'), '?')}")
    print(f"    最老版本：{versions[-1].id}（{versions[-1].type_label}）")

    samples = []
    for vid in ("1.21.8", "1.20.1", "1.12.2", "1.7.10", "b1.7.3", "rd-132211"):
        match = [v for v in versions if v.id == vid]
        if match:
            samples.append(match[0])
    # 再补一个最新快照
    snap = next((v for v in versions if v.type == "snapshot"), None)
    if snap and all(s.id != snap.id for s in samples):
        samples.append(snap)

    for v in samples:
        try:
            plan = installer.build_plan(v.id, v.url, include_assets=True)
            libs, natives = meta.resolve_libraries(plan.meta)
            cp = len(libs) + (1 if plan.jar else 0)
            print(f"[3] {v.id:<12} 类型={v.type:<9} Java>={(plan.meta.get('javaVersion') or {}).get('majorVersion', '?'):<3} "
                  f"文件={plan.total_files:<5} 体积={plan.total_bytes/1048576:8.1f}MB "
                  f"classpath={cp:<3} natives={len(natives)} assets={plan.assets_index_id}"
                  f"{' virtual' if plan.virtual_assets else ''}")
        except Exception as e:  # noqa: BLE001
            print(f"[3] {v.id:<12} 解析失败：{e}")

    print("[4] 下载源改写测试：")
    for u in ("https://piston-meta.mojang.com/v1/x.json",
              "https://libraries.minecraft.net/org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1.jar",
              "https://resources.download.minecraft.net/ab/abcdef"):
        print("    ", u, "->", net.rewrite(u, "bmclapi"))


if __name__ == "__main__":
    main()
