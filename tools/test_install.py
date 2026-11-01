# -*- coding: utf-8 -*-
"""端到端实测：真实下载安装一个版本，然后尝试启动。用法：
    python tools/test_install.py 1.21.8 [--launch] [--seconds 25]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import installer, launch as launcher  # noqa: E402
from core.manifest import list_versions  # noqa: E402

vid = sys.argv[1] if len(sys.argv) > 1 else "1.21.8"
do_launch = "--launch" in sys.argv
seconds = 25
if "--seconds" in sys.argv:
    seconds = int(sys.argv[sys.argv.index("--seconds") + 1])

match = [v for v in list_versions() if v.id == vid]
if not match:
    print("版本不存在:", vid)
    sys.exit(1)
url = match[0].url

last = [0.0]


def prog(s: dict) -> None:
    now = time.time()
    if now - last[0] < 1.0:
        return
    last[0] = now
    print(f"  {s['ratio']*100:5.1f}%  {s['done_files']}/{s['total_files']} 文件  "
          f"{s['done_bytes']/1048576:7.1f}/{s['total_bytes']/1048576:7.1f} MB  "
          f"{s['speed']/1048576:5.2f} MB/s  {s['current'][:40]}", flush=True)


t0 = time.time()
print(f"=== 安装 {vid} ===", flush=True)
plan = installer.install(vid, url, progress=prog, emit=lambda s: print(" " + s, flush=True))
print(f"耗时 {time.time()-t0:.1f}s，文件 {plan.total_files}，体积 {plan.total_bytes/1048576:.1f} MB")

# 校验关键文件
vdir = ROOT / ".minecraft" / "versions" / vid
for p in sorted(vdir.glob("*")):
    print("  ", p.relative_to(vdir), p.stat().st_size if p.is_file() else "<dir>")
print("  natives:", len(list((vdir / 'natives').glob('*'))) if (vdir / 'natives').exists() else 0)

if not do_launch:
    sys.exit(0)

print("\n=== 启动 ===", flush=True)
spec = launcher.prepare(vid, emit=lambda s: print("  " + s, flush=True))
print("命令长度:", sum(len(a) + 1 for a in spec.cmd))
gp = launcher.launch(spec, on_line=lambda l: print("  | " + l[:150], flush=True) if ("Exception" in l or "Error" in l or "Setting" in l or "Loading" in l or "Started" in l) else None)
deadline = time.time() + seconds
while time.time() < deadline:
    if not gp.running:
        print("进程已退出，退出码:", gp.proc.returncode)
        break
    time.sleep(1)
else:
    print("游戏运行中（未崩溃），强制结束")
    gp.proc.terminate()
print("日志文件:", spec.log_file)
