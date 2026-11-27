# -*- coding: utf-8 -*-
"""通过 GitHub API（api.github.com）推送本地提交——git 协议被墙时的替代通道。

    python tools/api_push.py           # 把本地领先 origin/main 的提交推上去
    python tools/api_push.py --check   # 只看差异，不推送

原理：Git Data API（blobs → trees → commits → refs）按提交逐个重放。
tree/blob 是内容寻址的，只要 author/committer/message 字节一致，
API 建出来的 commit sha 就和本地完全相同，推完本地远端零分叉。
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"
REPO = "555ddd-pixel/lantern-launcher"


def say(m: str = "") -> None:
    print(m, flush=True)


def token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if t:
        return t.strip()
    out = subprocess.run(["git", "credential", "fill"],
                         input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True, timeout=20,
                         cwd=ROOT).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("没有 GitHub token（设 GITHUB_TOKEN 或让凭据管理器保存一次）")


class Api:
    def __init__(self, tok: str) -> None:
        self.op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method: str, path: str, body=None, timeout: float = 60):
        req = urllib.request.Request(
            f"{API}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"User-Agent": "laozhang-apipush", "Accept": "application/vnd.github+json",
                     "Authorization": "Bearer " + token(),
                     "Content-Type": "application/json"})
        try:
            with self.op.open(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read()[:200].decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} -> {e.code} {detail}") from None


def git(*args, binary=False):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("git " + " ".join(args) + " 失败：" + r.stderr.decode("utf-8", "replace")[:200])
    return r.stdout if binary else r.stdout.decode("utf-8", "replace").strip()


def sha_remote_head(api: Api) -> str:
    return api.call("GET", f"/repos/{REPO}/branches/main")["commit"]["sha"]


def parse_commit(sha: str) -> dict:
    """读本地 commit 对象原始字节，拆出 author/committer/message。"""
    raw = git("cat-file", "commit", sha, binary=True).decode("utf-8", "replace")
    head, _, message = raw.partition("\n\n")
    lines = head.splitlines()
    parent = tree = ""
    author = committer = ""
    for ln in lines:
        if ln.startswith("tree "):
            tree = ln[5:]
        elif ln.startswith("parent "):
            parent = ln[7:]
        elif ln.startswith("author "):
            author = ln[7:]
        elif ln.startswith("committer "):
            committer = ln[10:]
    m = re.match(r"^(.*?) <(.*?)> (\d+) ([+-]\d{4})$", author)
    c = re.match(r"^(.*?) <(.*?)> (\d+) ([+-]\d{4})$", committer)
    if not (m and c):
        raise RuntimeError(f"解析 {sha} 的 author/committer 失败")
    def iso(ts: str, tz: str) -> str:
        import datetime
        dt = datetime.datetime.fromtimestamp(int(ts), datetime.timezone(
            datetime.timedelta(hours=int(tz[:3]), minutes=int(tz[0] + tz[3:5]))))
        return dt.isoformat(timespec="seconds")
    return {
        "tree": tree,
        "parent": parent,
        "message": message,
        "author": {"name": m.group(1), "email": m.group(2),
                   "date": iso(m.group(3), m.group(4))},
        "committer": {"name": c.group(1), "email": c.group(2),
                      "date": iso(c.group(3), c.group(4))},
    }


def upload_diff(api: Api, parent: str, commit: str) -> dict:
    """把 parent→commit 的差异传成 API tree。返回 tree sha（与本地必须一致）。"""
    base_tree = git("rev-parse", f"{parent}^{{tree}}")
    entries = []
    out = git("diff-tree", "-r", "--no-renames", "--no-commit-id", parent, commit)
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        cols = meta.lstrip(":").split(" ")
        old_mode, new_mode, old_sha, new_sha, status = cols[0], cols[1], cols[2], cols[3], cols[4][0]
        if new_mode == "160000":
            raise RuntimeError(f"{commit} 含子模块 {path}，API 通道不支持，请手动 push")
        if status == "D":
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        blob_sha = new_sha
        raw = git("cat-file", "blob", blob_sha, binary=True)
        made = api.call("POST", f"/repos/{REPO}/git/blobs",
                        {"content": base64.b64encode(raw).decode(),
                         "encoding": "base64"})
        if made["sha"] != blob_sha:
            raise RuntimeError(f"blob sha 不一致：{path} 本地 {blob_sha} 远端 {made['sha']}")
        entries.append({"path": path, "mode": new_mode or "100644",
                        "type": "blob", "sha": blob_sha})
    tree = api.call("POST", f"/repos/{REPO}/git/trees",
                    {"base_tree": base_tree, "tree": entries})
    local_tree = git("rev-parse", f"{commit}^{{tree}}")
    if tree["sha"] != local_tree:
        raise RuntimeError(f"tree sha 不一致：本地 {local_tree} 远端 {tree['sha']}")
    return tree["sha"]


def push() -> None:
    api = Api(token())
    remote = sha_remote_head(api)
    local = git("rev-parse", "HEAD")
    say(f"远端 main {remote[:9]} / 本地 HEAD {local[:9]}")
    if remote == local:
        say("已经同步，无事可做")
        return
    try:
        git("cat-file", "-e", f"{remote}^{{commit}}")
    except Exception:
        raise SystemExit("远端 main 的提交本地没有（历史分叉），API 通道无法处理，请手动 push")
    todo = git("rev-list", "--reverse", f"{remote}..HEAD").split()
    say(f"待推提交 {len(todo)} 个：")
    for c in todo:
        say(f"  {c[:9]} {git('log', '-1', '--format=%s', c)}")

    cursor = remote
    for c in todo:
        info = parse_commit(c)
        tree_sha = upload_diff(api, info["parent"], c)
        made = None
        for msg in (info["message"], info["message"].rstrip("\n")):
            made = api.call("POST", f"/repos/{REPO}/git/commits", {
                "message": msg, "tree": tree_sha,
                "parents": [info["parent"]] if info["parent"] else [],
                "author": info["author"], "committer": info["committer"],
            })
            if made["sha"] == c:
                break
        if made["sha"] != c:
            raise SystemExit(
                f"commit sha 对不上（本地 {c[:9]} / 远端 {made['sha'][:9]}）。\n"
                "refs 未改动，远端无副作用；把这段输出发我看一眼就能修。")
        cursor = made["sha"]
        say(f"  ✓ {c[:9]} -> {made['sha'][:9]} {git('log', '-1', '--format=%s', c)}")

    api.call("PATCH", f"/repos/{REPO}/git/refs/heads/main",
             {"sha": cursor, "force": False})
    git("update-ref", "refs/remotes/origin/main", cursor)
    say(f"推送完成：main -> {cursor[:9]}（本地 origin/main 引用已同步）")


def check() -> None:
    api = Api(token())
    remote = sha_remote_head(api)
    local = git("rev-parse", "HEAD")
    say(f"远端 main {remote[:9]} / 本地 HEAD {local[:9]}")
    if remote == local:
        say("已同步")
        return
    git("cat-file", "-e", f"{remote}^{{commit}}")
    todo = git("rev-list", "--reverse", f"{remote}..HEAD").split()
    say(f"本地领先 {len(todo)} 个提交：")
    for c in todo:
        say(f"  {c[:9]} {git('log', '-1', '--format=%s', c)}")


if __name__ == "__main__":
    try:
        say("通道    api.github.com（git 协议被墙时的替代）")
        check() if "--check" in sys.argv else push()
    except Exception as e:  # noqa: BLE001
        say(f"失败：{e}")
        sys.exit(1)
