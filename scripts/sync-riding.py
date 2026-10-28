#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync-riding.py — 手动骑行同步（用户说一声才跑）。

一次同步全部平台：
  迈金 → IGPSPORT     （IGPSPORT 的数据源只有迈金）
  Strava → 行者
  Strava → 迈金

用法
-----
  # 默认窗口：今天往前 3 天（day-2 ~ day-0）
  python3 sync-riding.py

  # 指定窗口
  python3 sync-riding.py --since 2026-10-20 --until 2026-10-26

  # 只同步某条链路
  python3 sync-riding.py --only igp
  python3 sync-riding.py --only xingzhe onelap

  # 试跑，不真传（看会传什么）
  python3 sync-riding.py --dry-run

  # 往前追更久
  python3 sync-riding.py --days 14

设计要点
--------
* **不用定时**。原来 3 条 cron（11:05/11:10/11:15）已全部暂停，
  改由用户在对话里说一句「同步骑行」触发。
* 每条链路独立执行，**一条失败不影响其它**。
* 复用现有的三层去重闸门（服务端索引 / md5 / state），
  所以重复跑是幂等的 —— 不会重复上传。
* 全局串行锁（`_serial_lock.py`）—— 和其它 cron 任务共用，
  防止和 GitHub Trending / AI 抓取抢资源。
* 走 `~/.hermes/skills/cycling-data-upload/scripts/` 下的主脚本
  （生产副本），不直接调 cron 入口 —— cron 入口会吞掉输出。
"""
import argparse
import datetime
import os
import subprocess
import sys
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
SKILL_SCRIPTS = HERMES_HOME / "skills" / "cycling-data-upload" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

try:
    from _serial_lock import acquire as _acquire, release as _release, holder as _holder
except Exception:  # 锁模块缺失时不阻断同步
    _acquire = _release = _holder = None

# 链路定义：(key, 显示名, 主脚本, 额外参数)
LINKS = [
    ("igp",     "迈金 → IGPSPORT", "magene-to-igp.py",        []),
    ("xingzhe", "Strava → 行者",   "strava-to-xingzhe-sync.py", []),
    ("onelap",  "Strava → 迈金",   "strava-to-onelap.py",      []),
]


def run_link(key: str, label: str, script: str, extra: list, since: str, until: str,
             limit: int, dry_run: bool) -> tuple[bool, str]:
    """跑一条链路。返回 (是否成功, 输出文本)"""
    path = SKILL_SCRIPTS / script
    if not path.exists():
        return False, f"脚本不存在: {path}"

    cmd = [sys.executable, str(path), "--since", since, "--until", until, "--limit", str(limit)]
    cmd += extra
    if dry_run:
        cmd.append("--dry-run")

    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=2400)
    except subprocess.TimeoutExpired:
        return False, "超时（2400s）"

    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode == 0, out


def summarize(text: str) -> list[str]:
    """从脚本输出里抽出关键行"""
    keep = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if (s.startswith("✅") or s.startswith("❌") or s.startswith("⚠")
                or "上传 " in s or "新增上传" in s or "跳过" in s or "候选" in s
                or "索引" in s or "失败" in s):
            keep.append(s)
    return keep


def main() -> int:
    ap = argparse.ArgumentParser(description="手动骑行同步（全平台）")
    ap.add_argument("--since", help="起始日期 YYYY-MM-DD（默认 = until 往前 days-1 天）")
    ap.add_argument("--until", help="结束日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--days", type=int, default=3, help="窗口天数（默认 3，即 day-2 ~ day-0）")
    ap.add_argument("--limit", type=int, default=30, help="单次最多处理多少条（默认 30）")
    ap.add_argument("--only", nargs="+", choices=[k for k, *_ in LINKS],
                    help="只同步指定链路（igp / xingzhe / onelap）")
    ap.add_argument("--dry-run", action="store_true", help="试跑，不真正上传")
    args = ap.parse_args()

    now = datetime.datetime.now()
    until = args.until or now.strftime("%Y-%m-%d")
    if args.since:
        since = args.since
    else:
        since = (now - datetime.timedelta(days=max(1, args.days) - 1)).strftime("%Y-%m-%d")

    targets = [l for l in LINKS if not args.only or l[0] in args.only]

    print(f"🚴 骑行同步（{since} ~ {until}）")
    if args.dry_run:
        print("   ⚠️ 试跑模式，不会真的上传")
    print(f"   链路 {len(targets)} 条: {', '.join(l[1] for l in targets)}")

    # 全局串行锁 —— 和其它 cron 任务共用
    lock_fd = None
    if _acquire:
        lock_fd = _acquire("riding-sync", wait_s=180)
        if lock_fd is None:
            print(f"⚠️ 未执行：全局锁被占用（持锁: {_holder()}）")
            print("   过一会儿再说一次即可。")
            return 0

    results = []
    try:
        for key, label, script, extra in targets:
            print(f"\n{'='*56}\n▶ {label}")
            ok, out = run_link(key, label, script, extra, since, until, args.limit, args.dry_run)
            lines = summarize(out)
            for l in lines[:40]:
                print("   " + l)
            if not lines:
                tail = (out or "").strip().splitlines()[-3:]
                for t in tail:
                    print("   " + t)
            results.append((key, label, ok, lines))
    finally:
        if lock_fd is not None:
            _release(lock_fd)

    print(f"\n{'='*56}")
    bad = [(k, l) for k, l, ok, _ in results if not ok]
    if not bad:
        print("✅ 全部链路完成")
        return 0
    for k, l in bad:
        print(f"❌ {l} 失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
