# -*- coding: utf-8 -*-
"""启动器自身的版本信息。改版本号只需要改这里。"""

APP_NAME = "老张的minecraft启动器"
VERSION = "1.8.1"
BUILD = "2026-11-01"
CHANNEL = "stable"

# ---------------------------------------------------------------- 发布地址
# 改这里就能把自动更新指向别的仓库/分支，不用再翻 build.py 和 config.py
GITHUB_REPO = "555ddd-pixel/lantern-launcher"
REPO_URL = f"https://github.com/{GITHUB_REPO}"

# 更新清单：始终指向「最新 Release」里的 update.json
MANIFEST_URL = f"{REPO_URL}/releases/latest/download/update.json"

# 清单附件缺失时的兜底：直接问 GitHub API 最新 Release 是哪个 tag，
# 再用附件名拼出下载地址。这样发版只需要传 exe 一个文件。
API_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASE_ASSET_NAME = "LanternLauncher.exe"   # Release 里那个 exe 附件的名字


def release_asset_url(name: str, tag: str = "") -> str:
    """某个 Release 里附件的下载地址。tag 省略时用当前版本 v{VERSION}。"""
    tag = tag or f"v{VERSION}"
    return f"{REPO_URL}/releases/download/{tag}/{name}"


def version_tuple(text: str) -> tuple:
    """把 "1.2.3" / "v1.2.3-beta" 变成可比较的元组。"""
    text = str(text).strip().lstrip("vV")
    core = text.split("-")[0].split("+")[0]
    parts = []
    for piece in core.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])
