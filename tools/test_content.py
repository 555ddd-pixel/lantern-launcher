# -*- coding: utf-8 -*-
"""模组 / 光影管理自检：开关、批量、导入归类、回收站、实例隔离。

    python tools/test_content.py

全部在临时游戏目录里跑，不碰真实的 .minecraft；加载器版本列表需要联网，
连不上只提示、不算失败。
"""
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import content  # noqa: E402
from core.config import config  # noqa: E402
from core.meta import version_dir  # noqa: E402

PASS = 0
FAIL = 0


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


def make_jar(path: Path, kind: str, name: str = "", version: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        if kind == "fabric":
            zf.writestr("fabric.mod.json", json.dumps({
                "id": "sodium", "name": name or "Sodium", "version": version or "0.5.8",
                "description": "现代渲染优化，显著提升帧数。"}))
        elif kind == "forge":
            zf.writestr("META-INF/mods.toml", "\n".join([
                'modLoader="javafml"', 'loaderVersion="[47,)"', 'license="MIT"',
                "[[mods]]",
                f'modId="jei"',
                f'version="{version or "15.2.0.27"}"',
                f'displayName="{name or "Just Enough Items"}"',
                'description="物品配方查询"',
                "", "[[dependencies.jei]]", 'modId="forge"',
            ]))
        elif kind == "neoforge":
            zf.writestr("META-INF/neoforge.mods.toml", "\n".join([
                "[[mods]]", 'modId="create"', 'version="0.5.1"',
                'displayName="Create"', 'description="机械动力"',
            ]))
        else:
            zf.writestr("README.txt", "no metadata")
    return path


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="lantern-content-"))
    print("临时目录:", work)
    config["game_dir"] = work / "mc"
    game = config.game_dir
    vid = "fabric-loader-0.16.9-1.20.1"
    vdir = version_dir(vid)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{vid}.json").write_text(json.dumps({
        "id": vid, "inheritsFrom": "1.20.1",
        "libraries": [{"name": "net.fabricmc:fabric-loader:0.16.9"}],
    }), encoding="utf-8")

    # 1) 加载器识别 + 初始状态
    step("1. 识别加载器与目录")
    check("识别为 fabric", content.detect_loader(vid) == "fabric")
    st = content.state(vid)
    check("默认走共享目录", st["game_dir"] == str(game), st["game_dir"])
    check("未隔离标记正确", st["isolated"] is False)
    check("三个分类都在", set(st["areas"]) == {"mods", "shaderpacks", "resourcepacks"})

    # 2) 模组元信息解析
    step("2. 读取 jar 内的模组信息")
    mods = game / "mods"
    make_jar(mods / "sodium-fabric-0.5.8.jar", "fabric")
    make_jar(mods / "jei-1.20.1-forge-15.2.0.27.jar", "forge")
    make_jar(mods / "create-1.20.1-0.5.1.jar", "neoforge")
    make_jar(mods / "SodiumExtra-0.5.3.jar", "none")
    st = content.state(vid)
    items = {i["name"]: i for i in st["areas"]["mods"]["items"]}
    check("列出 4 个模组", len(items) == 4, str(list(items)))
    sod = items["sodium-fabric-0.5.8.jar"]
    check("Fabric 名称/版本", sod["title"] == "Sodium" and sod["version"] == "0.5.8",
          f"{sod['title']} {sod['version']}")
    check("Fabric 加载器标记", sod["mod_loader"] == "fabric")
    jei = items["jei-1.20.1-forge-15.2.0.27.jar"]
    check("Forge 名称/版本", jei["title"] == "Just Enough Items" and jei["version"] == "15.2.0.27",
          f"{jei['title']} {jei['version']}")
    nf = items["create-1.20.1-0.5.1.jar"]
    check("NeoForge 识别", nf["mod_loader"] == "neoforge" and nf["title"] == "Create")
    plain = items["SodiumExtra-0.5.3.jar"]
    check("无元信息时用文件名兜底", plain["title"].lower().startswith("sodiumextra"),
          plain["title"])

    # 3) 单个开关
    step("3. 单个启用 / 停用")
    r = content.toggle(vid, "mods", "SodiumExtra-0.5.3.jar", False)
    check("停用后改名 .disabled", r["file"].endswith(".disabled"), r["file"])
    st = content.state(vid)
    check("统计已启用 3 个", st["areas"]["mods"]["enabled"] == 3,
          str(st["areas"]["mods"]["enabled"]))
    r = content.toggle(vid, "mods", r["file"], True)
    check("再启用恢复原名", r["file"] == "SodiumExtra-0.5.3.jar", r["file"])
    check("重复启用不报错", content.toggle(vid, "mods", "SodiumExtra-0.5.3.jar", True)["changed"] is False)

    # 4) 一键开关
    step("4. 一键全部停用 / 全部启用")
    r = content.toggle_all(vid, "mods", False)
    check("全部停用 4 个", r["changed"] == 4, str(r["changed"]))
    check("文件都带 .disabled", all(i["file"].endswith(".disabled")
                                for i in content.state(vid)["areas"]["mods"]["items"]))
    r = content.toggle_all(vid, "mods", True)
    check("全部启用 4 个", r["changed"] == 4, str(r["changed"]))
    check("没有 .disabled 残留", all(not i["file"].endswith(".disabled")
                                 for i in content.state(vid)["areas"]["mods"]["items"]))

    # 5) 导入归类
    step("5. 导入光影 / 资源包 / 模组")
    shader = work / "BSL_v8.2.zip"
    with zipfile.ZipFile(shader, "w") as zf:
        zf.writestr("shaders/final.fsh", "// shader")
        zf.writestr("shaders/shaders.properties", "profile.LOW=BSL")
    info = content.place(vid, shader, "auto", filename="BSL_v8.2.zip")
    check("识别为光影", info["area"] == "shaderpacks", info["area"])
    rp = work / "Faithful.zip"
    with zipfile.ZipFile(rp, "w") as zf:
        zf.writestr("pack.mcmeta", json.dumps({"pack": {"pack_format": 15}}))
        zf.writestr("assets/minecraft/textures/a.png", b"x")
    info = content.place(vid, rp, "auto", filename="Faithful.zip")
    check("识别为资源包", info["area"] == "resourcepacks", info["area"])
    extra = make_jar(work / "iris-fabric-1.7.0.jar", "fabric", "Iris", "1.7.0")
    info = content.place(vid, extra, "auto", filename="iris-fabric-1.7.0.jar")
    check("识别为模组", info["area"] == "mods", info["area"])
    check("模组信息带回来", info.get("title") == "Iris", str(info.get("title")))
    again = make_jar(work / "iris-fabric-1.7.0.jar", "fabric", "Iris", "1.7.0")
    info = content.place(vid, again, "auto", filename="iris-fabric-1.7.0.jar")
    check("重名自动加后缀", info["file"] == "iris-fabric-1.7.0-2.jar", info["file"])

    # 6) 删除进回收站
    step("6. 删除 → 回收站")
    r = content.remove(vid, "mods", "iris-fabric-1.7.0-2.jar")
    check("回收站里能找到", Path(r["trash"]).exists())
    check("列表里已消失", "iris-fabric-1.7.0-2.jar" not in
          [i["file"] for i in content.state(vid)["areas"]["mods"]["items"]])

    # 7) 实例隔离
    step("7. 独立游戏目录")
    inst = content.ensure_instance(vid)
    check("实例目录在 instances 下", "instances" in str(inst).replace("\\", "/"), str(inst))
    check("侧车文件已写", (vdir / ".lantern-instance.json").exists())
    st = content.state(vid)
    check("状态变为已隔离", st["isolated"] is True)
    check("目录已切换", st["game_dir"] == str(inst), st["game_dir"])
    check("模组目录跟着走", st["areas"]["mods"]["dir"].startswith(str(inst)))
    check("重复调用不报错", content.ensure_instance(vid) == inst)

    # 8) 路径越界
    step("8. 非法文件名拦截")
    for bad in ("../evil.jar", "..\\evil.jar", "sub/evil.jar", ".."):
        try:
            content.toggle(vid, "mods", bad, False)
            check(f"拒绝 {bad}", False)
        except Exception:
            check(f"拒绝 {bad}", True)

    # 9) 加载器版本目录（联网可选）
    step("9. 加载器版本列表（联网）")
    for mc, real in (("1.20.1", "fabric"), ("1.20.1", "forge"), ("1.21.1", "neoforge")):
        try:
            data = content.loader_versions(mc, real)
            rows = data["versions"]
            print(f"  {mc} {real}: {len(rows)} 个版本，推荐 {data['recommend']}")
            check(f"{real} 有版本", bool(rows))
            if rows:
                check(f"{real} 首项是最新", rows[0]["version"] == data["recommend"])
        except Exception as e:  # noqa: BLE001
            print(f"  {real}: 取不到（网络问题，跳过）{e}")
    # 1.20.1 的 NeoForge 用的是另一套坐标，这里应为空而不是报错
    neo120 = content.loader_versions("1.20.1", "neoforge")
    check("1.20.1 无 NeoForge（提示改用 Forge）", neo120["versions"] == [])
    try:
        data = content.loader_versions("1.20.1", "vanilla")
        check("原版返回空列表", data["versions"] == [] and data["recommend"] == "")
    except Exception as e:  # noqa: BLE001
        check("原版返回空列表", False, str(e))

    # 10) ensure_loader 链路（打桩，不真下载）
    step("10. ensure_loader 返回可启动版本")
    from core import installer, modpack
    orig = installer.install_from_meta
    seen = {}

    def fake_install(vid, raw, **kw):
        seen["vid"] = vid
        seen["raw_id"] = raw.get("id")
        return None

    installer.install_from_meta = fake_install
    try:
        out = modpack.ensure_loader("1.20.1", "fabric", "0.16.9", lambda *_: None, None)
        check("返回 fabric 版本 ID", out.startswith("fabric-loader-0.16.9"), out)
        check("版本 JSON 已落地", (config.versions_dir / out / f"{out}.json").exists())
        check("调用了安装器", seen.get("vid") == out, str(seen))
        inst = content.ensure_instance(out)
        check("loader 版本也能建独立目录", inst.exists() and "instances" in str(inst).replace("\\", "/"))
    except Exception as e:  # noqa: BLE001
        check("ensure_loader 链路", False, str(e))
    finally:
        installer.install_from_meta = orig

    print(f"\n结果：{PASS} 通过，{FAIL} 失败")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
