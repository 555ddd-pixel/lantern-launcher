# -*- coding: utf-8 -*-
"""自更新回退自检：发版时没传 update.json，也要能靠 GitHub API 升级。

    python tools/test_update_fallback.py

全程离线：把 net.get_json 换成假实现，只验证解析 / 回退 / 状态落库。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import updater  # noqa: E402
from core.version import VERSION  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(f"  {'✓' if cond else '✗'} {name}{'' if cond else ' -> ' + str(extra)[:200]}")
    if not cond:
        fails.append(name)


SANDBOX = Path(tempfile.mkdtemp(prefix="lantern-upd-"))
BASE_STATE = dict(updater.state)

RELEASE = {
    "tag_name": "v9.9.9",
    "name": "v9.9.9",
    "published_at": "2026-11-02T10:00:00Z",
    "body": "修了点什么\n第二行",
    "assets": [
        {"name": "notes.txt", "size": 10,
         "browser_download_url": "https://example.com/notes.txt"},
        {"name": "LanternLauncher.exe", "size": 20971520,
         "browser_download_url":
             "https://github.com/x/y/releases/download/v9.9.9/LanternLauncher.exe"},
    ],
}


def install_fake(api_payload=None, api_error=None, api_calls=None, other_error=None):
    """把 net.get_json 换成可控假实现。"""
    def fake(url, timeout=30.0, retries=3, use_proxy=False):
        if "api.github.com" in url:
            if api_calls is not None:
                api_calls.append(url)
            if api_error:
                raise api_error
            return api_payload
        if other_error:
            raise other_error
        raise RuntimeError("404 Not Found")
    updater.net.get_json = fake


def reset():
    updater.state.clear()
    updater.state.update(BASE_STATE)
    updater.STATE_CACHE = SANDBOX / "update_state.json"
    updater.config["update_url"] = "https://example.invalid/update.json"


print("1) 清单地址拿不到时，回退 GitHub API")
reset()
calls = []
install_fake(RELEASE, api_calls=calls)
snap = updater.check(force=True)
print("   ", {k: snap[k] for k in ("latest", "has_update", "size", "source")})
check("识别到远端版本", snap["latest"] == "9.9.9", snap)
check("判定有新版本", snap["has_update"] is True)
check("版本号去掉了 v 前缀", not snap["latest"].startswith("v"))
check("下载地址取自 exe 附件", snap["url"].endswith("/LanternLauncher.exe"), snap["url"])
check("没有误选别的附件", "notes.txt" not in snap["url"])
check("文件大小来自 API", snap["size"] == 20971520)
check("sha1 为空（API 不提供）", snap["sha1"] == "")
check("来源标记为 GitHub API", snap["source"] == updater.API_LABEL, snap["source"])
check("确实请求了 API", len(calls) == 1, calls)
check("更新说明取自 Release 正文", "第二行" in snap["notes"], snap["notes"])
check("发布日期取自 API", snap["published"] == "2026-11-02", snap["published"])
check("无 min_version 时不强制", snap["mandatory"] is False)
check("没有残留错误", snap["error"] == "", snap["error"])

print("2) 附件改名后仍能找到 exe")
reset()
renamed = dict(RELEASE)
renamed["assets"] = [{"name": "LanternLauncher-9.9.9.exe", "size": 1000,
                      "browser_download_url": "https://example.com/a.exe"}]
install_fake(renamed)
snap = updater.check(force=True)
check("按 .exe 后缀兜底", snap["url"] == "https://example.com/a.exe", snap)

print("3) tag 与当前版本一致（或更旧）时不算更新")
reset()
same = dict(RELEASE, tag_name=f"v{VERSION}")
install_fake(same)
snap = updater.check(force=True)
check("当前版本不提示更新", snap["has_update"] is False, snap["latest"])
check("latest 仍是远端 tag", snap["latest"] == VERSION)

reset()
older = dict(RELEASE, tag_name="v0.9.0")
install_fake(older)
snap = updater.check(force=True)
check("旧 tag 不提示更新", snap["has_update"] is False, snap["latest"])

print("4) 清单文件在时优先用清单，不去打扰 API")
reset()
local = SANDBOX / "update.json"
local.write_text("""
{"version": "9.8.0", "notes": "本地清单",
 "files": {"all": {"url": "https://example.com/m.exe", "size": 7, "sha1": "abc"}}}
""", encoding="utf-8")
updater.config["update_url"] = str(local)
calls = []
install_fake(RELEASE, api_calls=calls)
snap = updater.check(force=True)
check("用了清单里的版本", snap["latest"] == "9.8.0", snap["latest"])
check("清单地址作为来源", snap["source"] == "update.json", snap["source"])
check("sha1 来自清单", snap["sha1"] == "abc")
check("没有请求 API", calls == [], calls)

print("5) API 报错时给出可读原因，不抛异常")
reset()
install_fake(api_error=RuntimeError("403 rate limit exceeded"))
try:
    snap = updater.check(force=True)
    crashed = False
except Exception as e:  # noqa: BLE001
    snap, crashed = {}, repr(e)
check("check 不抛异常", not crashed, crashed)
check("error 里有失败原因", "rate limit" in snap.get("error", ""), snap.get("error"))
check("失败时 has_update 为假", snap.get("has_update") is False)

print("6) Release 里没有 exe 附件")
reset()
noexe = dict(RELEASE, assets=[{"name": "src.zip", "size": 1,
                              "browser_download_url": "https://example.com/s.zip"}])
install_fake(noexe)
snap = updater.check(force=True)
check("提示没有附件", "exe" in snap.get("error", ""), snap.get("error"))

print("7) API 返回限流消息（没有 tag_name）")
reset()
install_fake({"message": "API rate limit exceeded for 1.2.3.4"})
snap = updater.check(force=True)
check("把 message 报出来", "rate limit" in snap.get("error", ""), snap.get("error"))

print("8) 缓存落盘后能读回来")
reset()
install_fake(RELEASE)
updater.check(force=True)
cached = updater.STATE_CACHE.exists() and updater.STATE_CACHE.read_text("utf-8")
check("状态写进了缓存文件", bool(cached))
updater.state.update(latest="", has_update=False, checked_at=0)
updater._load_cache()
check("读回后仍记得远端版本", updater.state["latest"] == "9.9.9", updater.state["latest"])

# 还原，别影响真实运行时的状态
updater.state.clear()
updater.state.update(BASE_STATE)

print()
if fails:
    print(f"失败 {len(fails)} 项：" + "; ".join(fails))
    sys.exit(1)
print("全部通过")
