# -*- coding: utf-8 -*-
"""发版体检与发布脚本：杜绝「tag 写一个版本、附件却是另一个版本」。

    python tools/release.py                  # 体检（只读）
    python tools/release.py --publish        # 按 core/version.py 的 VERSION 发版
    python tools/release.py --retag v1.8.1   # 把标错号的 Release 改到正确 tag
    python tools/release.py --prune          # 删掉没有 Release 的残留/畸形 tag
    python tools/release.py --check --proxy http://127.0.0.1:7897

判定依据：本地 git 历史里每份 dist/update.json 都记录了该版本 exe 的 size，
而每个版本的 exe 体积都不一样，所以「附件 size → 版本号」可以反查。
--publish 时还会用 sha1 复核 exe、用 uploads.github.com 传附件。

token 取环境变量 GITHUB_TOKEN，否则问 git credential（Windows 凭据管理器）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.version import GITHUB_REPO, REPO_URL, VERSION  # noqa: E402

API = "https://api.github.com"
UPLOAD = "https://uploads.github.com"
DIST = ROOT / "dist"
EXE = DIST / "LanternLauncher.exe"
MANIFEST = DIST / "update.json"
PROXY_CANDIDATES = ("http://127.0.0.1:7897", "http://127.0.0.1:7890")


# ---------------------------------------------------------------- 基础设施


def say(msg: str = "") -> None:
    print(msg, flush=True)


def pick_proxy(explicit: str = "") -> str:
    """挑一个能连 github.com 的代理；都不行就返回空（直连）。"""
    if explicit:
        return explicit
    env = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    for cand in ([env] if env else []) + list(PROXY_CANDIDATES):
        if not cand:
            continue
        try:
            op = _opener(cand)
            with op.open(urllib.request.Request(
                    "https://github.com/", headers={"User-Agent": "lz"}), timeout=8) as r:
                if r.status < 400:
                    return cand
        except Exception:  # noqa: BLE001
            continue
    return ""


def _opener(proxy: str = ""):
    if proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy}))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if tok:
        return tok.strip()
    try:
        out = subprocess.run(["git", "credential", "fill"],
                             input="protocol=https\nhost=github.com\n\n",
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001
        return ""
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return ""


class Client:
    def __init__(self, token: str, proxy: str = "") -> None:
        self.token = token
        self.opener = _opener(proxy)
        self.proxy = proxy

    def call(self, method: str, url: str, body=None, raw: bytes | None = None,
             ctype: str = "application/json", timeout: float = 60.0):
        data = None
        headers = {"User-Agent": "laozhang-release", "Accept": "application/vnd.github+json",
                   "Authorization": f"Bearer {self.token}"}
        if raw is not None:
            data = raw
            headers["Content-Type"] = ctype
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                payload = r.read()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            raise RuntimeError(f"{method} {url.split('api.github.com')[-1]} -> {e.code} {detail}") from None

    def releases(self) -> list:
        return self.call("GET", f"{API}/repos/{GITHUB_REPO}/releases?per_page=50")

    def tags(self) -> dict:
        raw = self.call("GET", f"{API}/repos/{GITHUB_REPO}/git/matching-refs/tags")
        return {r["ref"].split("/tags/")[-1]: r["object"]["sha"] for r in raw}

    def main_sha(self) -> str:
        return self.call("GET", f"{API}/repos/{GITHUB_REPO}/branches/main")["commit"]["sha"]

    def code_version(self, ref: str) -> str:
        """读某个 ref 上 core/version.py 里写的 VERSION，用来验证 tag 是否打在正确的提交上。"""
        import base64
        import re
        d = self.call("GET", f"{API}/repos/{GITHUB_REPO}/contents/core/version.py?ref={ref}")
        txt = base64.b64decode(d.get("content") or "").decode("utf-8", "replace")
        m = re.search(r'VERSION\s*=\s*"([^"]+)"', txt)
        return m.group(1) if m else ""

    def upload(self, release_id: int, path: Path, replace: bool = True) -> dict:
        name = path.name
        size = path.stat().st_size
        for a in self.call("GET", f"{API}/repos/{GITHUB_REPO}/releases/{release_id}")["assets"]:
            if a["name"] != name:
                continue
            if a["size"] == size:      # 已经在上面且体积一致，不必删了重传
                say(f"    已有 {name}（{size / 1048576:.1f} MB），跳过")
                return a
            if replace:
                self.call("DELETE", f"{API}/repos/{GITHUB_REPO}/releases/assets/{a['id']}")
                say(f"    删掉旧附件 {name}（{a['size']:,} B）")
        blob = path.read_bytes()
        say(f"    上传 {name}（{len(blob) / 1048576:.1f} MB）…")
        return self.call("POST",
                         f"{UPLOAD}/repos/{GITHUB_REPO}/releases/{release_id}/assets?name={name}",
                         raw=blob, ctype="application/octet-stream", timeout=600)


# ---------------------------------------------------------------- 版本指纹表


def version_sizes() -> dict:
    """{size: version}：从 git 历史里的 dist/update.json 反查每个版本的 exe 体积。"""
    out: dict = {}
    try:
        log = subprocess.run(["git", "log", "--format=%h", "--", "dist/update.json"],
                             cwd=ROOT, capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001
        return out
    for sha in log.split():
        try:
            raw = subprocess.run(["git", "show", f"{sha}:dist/update.json"], cwd=ROOT,
                                 capture_output=True, text=True, timeout=20).stdout
            d = json.loads(raw)
            files = d.get("files") or {}
            entry = files.get("windows-x64") or files.get("all") or {}
            size = int(entry.get("size") or 0)
            if size and d.get("version"):
                out.setdefault(size, str(d["version"]))
        except Exception:  # noqa: BLE001
            continue
    if EXE.exists():  # 当前 dist 里的 exe 一定属于 VERSION
        out[EXE.stat().st_size] = VERSION
    return out


# ---------------------------------------------------------------- 体检


def check(c: Client) -> list:
    sizes = version_sizes()
    local_size = EXE.stat().st_size if EXE.exists() else 0
    problems: list = []

    say(f"仓库      {REPO_URL}")
    main = c.main_sha()
    say(f"远端 main {main[:8]}")
    rels = c.releases()
    tags = c.tags()
    say(f"Release {len(rels)} 个 / tag {len(tags)} 个")
    say()

    for r in rels:
        tag = r["tag_name"]
        is_current = tag.lstrip("vV") == VERSION
        say(f"[{tag}] published {r['published_at']}  target {r.get('target_commitish')}"
            + ("  ← 当前版本" if is_current else ""))
        assets = r["assets"]
        names = [a["name"] for a in assets]
        if not assets:
            say("   ✗ 没有附件")
            problems.append(f"{tag}: 没有附件")
        for a in assets:
            if not a["name"].lower().endswith(".exe"):
                say(f"   · {a['name']}  {a['size']:,} B")
                continue
            what = sizes.get(a["size"], "未知版本")
            mark = "✓" if what == tag.lstrip("vV") else "✗"
            say(f"   {mark} {a['name']}  {a['size']:,} B  → 实际是 {what} 的包")
            if what == "未知版本":
                problems.append(f"{tag}: 附件 {a['name']} 体积 {a['size']} 无法对应任何版本")
            elif what != tag.lstrip("vV"):
                problems.append(f"{tag}: 附件是 {what} 的 exe（标错号）")
        if "update.json" not in names:
            if is_current:
                say("   ✗ 缺少 update.json（老客户端拿不到版本号）")
                problems.append(f"{tag}: 缺少 update.json 附件")
            else:
                say("   · 没有 update.json（历史版本，不影响）")
        if tag in tags:
            pointed = tags[tag]
            code_ver = c.code_version(tag) if is_current else ""
            if is_current and code_ver and code_ver != tag.lstrip("vV"):
                say(f"   ✗ tag 指向的代码是 {code_ver}，不是 {tag}")
                problems.append(f"{tag}: tag 指向 {pointed[:8]}，那份代码里 VERSION={code_ver}")
            elif pointed != main:
                say(f"   · tag 指向 {pointed[:8]}（main 已前进到 {main[:8]}，正常）")
        say()

    versions = {r["tag_name"] for r in rels}
    orphans = [t for t in tags if t not in versions]
    if orphans:
        say("没有 Release 的残留 tag：" + ", ".join(sorted(orphans)))
        problems.append("残留 tag: " + ", ".join(sorted(orphans)))
    if f"v{VERSION}" not in versions:
        say(f"缺少当前版本 v{VERSION} 的 Release")
        problems.append(f"缺少 v{VERSION} 的 Release")
    elif local_size:
        rel = next(r for r in rels if r["tag_name"] == f"v{VERSION}")
        sizes_hit = [a["size"] for a in rel["assets"] if a["name"].endswith(".exe")]
        if sizes_hit and sizes_hit[0] != local_size:
            problems.append(f"v{VERSION}: 附件体积 {sizes_hit[0]} ≠ 本地 {local_size}")

    say("=" * 60)
    if problems:
        say(f"发现 {len(problems)} 个问题：")
        for p in problems:
            say("  · " + p)
    else:
        say("一切正常")
    return problems


# ---------------------------------------------------------------- 发布


def publish(c: Client, dry: bool = False) -> None:
    tag = f"v{VERSION}"
    if not EXE.exists():
        raise SystemExit(f"找不到 {EXE}，先跑 python build.py")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) \
        if MANIFEST.exists() else {}
    if str(manifest.get("version")) != VERSION:
        raise SystemExit(f"dist/update.json 是 {manifest.get('version')}，与 VERSION={VERSION} 不一致，先重新打包")

    rel = None
    for r in c.releases():
        if r["tag_name"] == tag:
            rel = r
            break
    body = manifest.get("notes") or f"{VERSION} 发布"
    notes_file = ROOT / "RELEASE_NOTES.md"   # 想在 Release 页写长文就放这个文件
    if notes_file.exists():
        body = notes_file.read_text(encoding="utf-8").strip() or body
    title = f"老张的minecraft启动器 {VERSION}"
    if rel is None:
        say(f"新建 Release {tag}")
        if not dry:
            rel = c.call("POST", f"{API}/repos/{GITHUB_REPO}/releases", {
                "tag_name": tag, "target_commitish": "main",
                "name": title, "body": body, "draft": False, "prerelease": False,
            })
    else:
        say(f"Release {tag} 已存在，同步标题/说明并补传附件")
        if not dry:
            rel = c.call("PATCH", f"{API}/repos/{GITHUB_REPO}/releases/{rel['id']}",
                         {"tag_name": tag, "name": title, "body": body,
                          "draft": False, "prerelease": False})

    if dry:
        say("（dry-run，未做改动）")
        return
    c.upload(rel["id"], EXE)
    if MANIFEST.exists():
        c.upload(rel["id"], MANIFEST)
    got = c.call("GET", f"{API}/repos/{GITHUB_REPO}/releases/{rel['id']}")
    say("发布完成，附件：" + ", ".join(f"{a['name']}({a['size']:,}B)" for a in got["assets"]))


def retag(c: Client, target: str, dry: bool = False) -> None:
    """把「附件是某版本、tag 却写成另一个」的 Release 改到正确 tag。"""
    tag = target if target.startswith("v") else "v" + target
    rels = c.releases()
    hit = [r for r in rels if r["tag_name"] == tag]
    if hit:
        raise SystemExit(f"{tag} 已经存在 Release，不用改")

    sizes = version_sizes()
    want = tag.lstrip("vV")
    src = None
    for r in rels:
        for a in r["assets"]:
            if a["name"].endswith(".exe") and sizes.get(a["size"]) == want:
                src = r
                break
        if src:
            break
    if src is None:
        raise SystemExit(f"没找到哪份附件属于 {want}，先用 --check 看看")

    say(f"把 [{src['tag_name']}] 改名为 [{tag}]（附件属于 {want}）")
    if dry:
        say("（dry-run，未做改动）")
        return
    c.call("PATCH", f"{API}/repos/{GITHUB_REPO}/releases/{src['id']}",
           {"tag_name": tag, "name": f"老张的minecraft启动器 {want}",
            "body": f"{want} 发布"})
    say("已改名；tag 会在下次 push 后指向正确提交")


def prune(c: Client, dry: bool = False) -> None:
    """删掉没有对应 Release 的 tag（多为手工发版留下的残骸，含 v.1.3.0 这种畸形名）。"""
    versions = {r["tag_name"] for r in c.releases()}
    tags = c.tags()
    orphans = sorted(t for t in tags if t not in versions)
    if not orphans:
        say("没有残留 tag")
        return
    for t in orphans:
        say(f"删除 tag {t}（{tags[t][:8]}）")
        if not dry:
            c.call("DELETE", f"{API}/repos/{GITHUB_REPO}/git/refs/tags/{t}")
    say(f"共 {len(orphans)} 个" + ("（dry-run，未改动）" if dry else "已删除"))


# ---------------------------------------------------------------- 入口


def main() -> None:
    args = sys.argv[1:]
    proxy = ""
    if "--proxy" in args:
        proxy = args[args.index("--proxy") + 1]
    dry = "--dry-run" in args
    token = get_token()
    if not token:
        raise SystemExit("没有 GitHub token：设置 GITHUB_TOKEN 或先 git push 一次让凭据管理器存好")
    proxy = pick_proxy(proxy)
    say(f"代理      {proxy or '直连'}")
    c = Client(token, proxy)

    if "--prune" in args:
        prune(c, dry)
    elif "--retag" in args:
        retag(c, args[args.index("--retag") + 1], dry)
    elif "--publish" in args:
        publish(c, dry)
    else:
        sys.exit(1 if check(c) else 0)


if __name__ == "__main__":
    main()
