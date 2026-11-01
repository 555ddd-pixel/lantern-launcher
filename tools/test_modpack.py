# -*- coding: utf-8 -*-
"""整合包功能的离线自检：解析真实 .mrpack、Fabric 安装计划、实例目录落盘。

    python tools/test_modpack.py

不下载整套整合包（几百 MB），只验证解析与落盘逻辑；
用 monkeypatch 顶替「装原版 / 装 loader」这两步重活。
"""
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import modpack, net  # noqa: E402
from core.config import config  # noqa: E402
from core.meta import version_dir  # noqa: E402


def step(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="lantern-pack-"))
    print("临时目录:", work)
    # 整个自检跑在临时游戏目录里，不碰真实的 .minecraft
    config["game_dir"] = work / "mc"
    config["concurrent"] = 8
    print("临时游戏目录:", config.game_dir)

    # 1) 真实 .mrpack 解析
    step("1. 解析真实 Modrinth 整合包")
    rows = modpack.project_versions("fabulously-optimized")
    print(f"版本数: {len(rows)}，最新: {rows[0]['name'] if rows else '-'}")
    url = rows[0]["file"]["url"]
    mrpack = work / rows[0]["file"]["name"]
    net.download_file(url, mrpack, expected_sha1=rows[0]["file"]["sha1"] or None)
    info = modpack.read_zip_info(mrpack)
    print(f"包名: {info.name} / {info.version}")
    print(f"MC: {info.mc_version}  loader: {info.loader} {info.loader_version}")
    print(f"外部文件: {len(info.files)} 个，共 "
          f"{sum(f.size for f in info.files) / 1048576:.1f} MB")
    print("前 3 个:", [f.path for f in info.files[:3]])
    assert info.mc_version and info.loader, "解析失败：缺少 MC 版本或 loader"

    # 2) Fabric 安装描述 + 依赖计划（不下载）
    step("2. Fabric loader 计划")
    from core import installer
    profile = net.get_json(
        f"https://meta.fabricmc.net/v2/versions/loader/{info.mc_version}/"
        f"{info.loader_version}/profile/json")
    print("profile id:", profile.get("id"), "| inheritsFrom:", profile.get("inheritsFrom"))
    plan = installer.build_plan_from_meta(profile["id"], profile, include_assets=False,
                                          emit=lambda s: None)
    print(f"计划下载 {plan.total_files} 个依赖，共 {plan.total_bytes / 1048576:.1f} MB")

    # 3) 合成一个 mrpack，跑完整落盘流程（重活被 monkeypatch 掉）
    step("3. 合成 mrpack 落盘演练")
    fake_mc = "1.20.1"
    fake_loader_id = f"fabric-loader-0.15.11-{fake_mc}"
    vdir = version_dir(fake_loader_id)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{fake_loader_id}.json").write_text(json.dumps({
        "id": fake_loader_id, "inheritsFrom": fake_mc,
        "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
    }), encoding="utf-8")
    (version_dir(fake_mc)).mkdir(parents=True, exist_ok=True)
    (version_dir(fake_mc) / f"{fake_mc}.jar").write_bytes(b"fake")

    original_base, original_loader = modpack._ensure_base, modpack.ensure_loader
    modpack._ensure_base = lambda *a, **k: None
    modpack.ensure_loader = lambda mc, loader, lv, emit, cancel: fake_loader_id

    demo_url = info.files[0].url          # 用真实 CDN 地址，顺便验证下载链路
    pack = work / "demo.mrpack"
    with zipfile.ZipFile(pack, "w") as zf:
        zf.writestr("modrinth.index.json", json.dumps({
            "formatVersion": 1,
            "game": "minecraft",
            "versionId": "1.0.0",
            "name": "Lantern Demo Pack",
            "summary": "自检用",
            "files": [{
                "path": "mods/hello.txt",
                "hashes": {"sha1": "", "sha512": ""},
                "env": {"client": "required", "server": "unsupported"},
                "downloads": [demo_url],
                "fileSize": 0,
            }],
            "dependencies": {"minecraft": fake_mc, "fabric-loader": "0.15.11"},
        }, ensure_ascii=False))
        zf.writestr("overrides/config/demo.toml", "enabled = true\n")
        zf.writestr("overrides/resourcepacks/pack.mcmeta", "{}")

    logs: list[str] = []
    result = modpack.install_from_file(pack, emit=logs.append,
                                       progress=lambda p: None)
    for line in logs:
        print("  ", line)
    print("结果:", result)

    inst = modpack.instance_game_dir(result["version_id"])
    print("实例目录:", inst)
    assert inst and inst.exists(), "实例目录没有创建"
    for rel in ("config/demo.toml", "resourcepacks/pack.mcmeta", "mods/hello.txt"):
        p = inst / rel
        print(f"  {rel}: {'OK' if p.exists() else '缺失'}"
              f"{' ' + str(p.stat().st_size) + ' B' if p.exists() else ''}")
        assert p.exists(), f"缺少 {rel}"
    print("侧车:", json.loads((version_dir(result["version_id"]) /
                                ".lantern-instance.json").read_text(encoding="utf-8")))
    print("包标记:", modpack.load_pack_meta(result["version_id"]))

    # 4) 启动参数里 --gameDir 是否指向实例
    step("4. 启动参数使用实例目录")
    from core import launch
    from core import java as javamod
    fake_java = javamod.JavaRuntime(path=Path("fake/java.exe"), major=17)
    orig_pick, orig_inspect = javamod.pick, javamod.inspect
    javamod.pick = lambda major: fake_java
    javamod.inspect = lambda p: fake_java
    try:
        spec = launch.prepare(result["version_id"], emit=lambda s: None, auto_java=False)
    finally:
        javamod.pick, javamod.inspect = orig_pick, orig_inspect
    game_dir = next((spec.cmd[i + 1] for i, a in enumerate(spec.cmd) if a == "--gameDir"), "")
    print("--gameDir =", game_dir, "| cwd =", spec.cwd)
    assert str(inst) in game_dir, "启动参数没有指向实例目录"

    modpack._ensure_base, modpack.ensure_loader = original_base, original_loader
    print("\n全部通过 ✅")


if __name__ == "__main__":
    main()
