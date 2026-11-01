# -*- coding: utf-8 -*-
"""一键打包成单文件 exe。

    python build.py            打包（默认）
    python build.py --clean    先清掉旧产物再打包

产物：
    dist/LanternLauncher.exe   单文件可执行程序（免安装，绿色版）
    dist/update.json           更新清单（和 exe 放同一个目录即可提供更新）
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.version import (APP_NAME, BUILD, REPO_URL, VERSION,  # noqa: E402
                          release_asset_url)

DIST = ROOT / "dist"
WORK = ROOT / "build" / "pyinstaller"
ICON = ROOT / "assets" / "lantern.ico"
EXE_NAME = "LanternLauncher"

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({v0}, {v1}, {v2}, 0),
    prodvers=({v0}, {v1}, {v2}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', '{name}'),
        StringStruct('FileDescription', '{name} · Minecraft 启动器'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', '{exe}'),
        StringStruct('LegalCopyright', 'MIT / 二次创作，非官方产品'),
        StringStruct('OriginalFilename', '{exe}.exe'),
        StringStruct('ProductName', '{name}'),
        StringStruct('ProductVersion', '{version}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_icon() -> None:
    if ICON.exists():
        return
    print("· 生成图标…")
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)


def write_version_info() -> Path:
    parts = (VERSION.split(".") + ["0", "0", "0"])[:3]
    nums = [int("".join(ch for ch in p if ch.isdigit()) or 0) for p in parts]
    path = ROOT / "build" / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        VERSION_INFO.format(v0=nums[0], v1=nums[1], v2=nums[2], version=VERSION,
                            name=APP_NAME, exe=EXE_NAME),
        encoding="utf-8",
    )
    return path


def run_pyinstaller() -> Path:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", EXE_NAME,
        "--distpath", str(DIST),
        "--workpath", str(WORK),
        "--specpath", str(ROOT / "build"),
        "--add-data", f"{ROOT / 'web'};web",
        "--add-data", f"{ROOT / 'assets'};assets",
        "--icon", str(ICON),
        "--version-file", str(write_version_info()),
    ]
    # 桌面窗口层（pywebview/WebView2 + 托盘）存在才打包进去
    try:
        import webview  # noqa: F401
    except ImportError:
        print("· 未安装 pywebview，打包为浏览器模式（pip install pywebview pythonnet pystray 可补）")
    else:
        cmd += [
            "--collect-all", "webview",
            "--collect-all", "pythonnet",
            "--collect-all", "clr_loader",
            "--collect-all", "pystray",
            "--hidden-import", "webview.platforms.edgechromium",
            "--hidden-import", "webview.platforms.winforms",
            "--hidden-import", "clr",
        ]
    cmd.append(str(ROOT / "launcher.py"))
    print("· 运行 PyInstaller（首次约 1~2 分钟）…")
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    exe = DIST / f"{EXE_NAME}.exe"
    if not exe.exists():
        raise SystemExit("打包失败：没有生成 exe")
    return exe


def write_manifest(exe: Path) -> Path:
    """生成更新清单。

    url 默认写成 GitHub Release 的绝对地址（tag = v{VERSION}），这样清单
    传到 Release 之后，所有已安装的客户端都能直接更新；
    传 --local-url 则退回到相对路径（U 盘/离线分发的场景）。
    """
    local = "--local-url" in sys.argv
    manifest = {
        "version": VERSION,
        "published": BUILD,
        "notes": "新增「整合包」页：实时搜索 Modrinth 整合包、一键安装；"
                 "支持把 .mrpack / CurseForge .zip 直接拖进窗口导入；"
                 "登录窗口改为「微软正版 / 离线」分支选择，设备码直接显示在弹层里。",
        "min_version": "1.0.0",
        "files": {
            "windows-x64": {
                "url": exe.name if local else release_asset_url(exe.name),
                "size": exe.stat().st_size,
                "sha1": sha1_of(exe),
            }
        },
    }
    path = DIST / "update.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    clean = "--clean" in sys.argv
    t0 = time.time()
    if clean:
        for p in (DIST, WORK, ROOT / "build" / f"{EXE_NAME}.spec"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("缺少 PyInstaller，先执行：python -m pip install pyinstaller")
        raise SystemExit(1)

    ensure_icon()
    exe = run_pyinstaller()
    manifest = write_manifest(exe)

    size_mb = exe.stat().st_size / 1048576
    print("\n打包完成")
    print(f"  程序    {exe}  ({size_mb:.1f} MB)")
    print(f"  清单    {manifest}")
    print(f"  版本    {VERSION} (build {BUILD})")
    print(f"  耗时    {time.time() - t0:.0f}s")
    print("\n分发：")
    print(f"  1) 到 {REPO_URL}/releases/new 建 Release，Tag 填 v{VERSION}")
    print("  2) 上传 dist/LanternLauncher.exe 和 dist/update.json 两个附件")
    print("  3) 发布后，所有客户端（update_url 指向 latest/download/update.json）即可自动更新")


if __name__ == "__main__":
    main()
