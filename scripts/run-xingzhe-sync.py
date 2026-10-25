#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run-xingzhe-sync.py — 每日 Strava → 行者 同步的 cron 入口。

需求：每天早上把「前天」(day-2) 当天在 Strava 的骑行数据上传到行者。

窗口固定为前天的 00:00:00 ~ 23:59:59（本地时区）。
靠 strava-to-xingzhe-sync.py 的服务端去重闸门保证不会重复上传。

输出规则：
- 有新同步 / 有失败 → 输出报告（cron 投递到 QQ）
- 没有新活动且无失败 → 零字节输出（cron 不投递，静默）
"""
import datetime
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _serial_lock import acquire as _acquire, release as _release

# 主脚本本体在 skill 目录（单一真相）。cron 入口本身必须是
# ~/.hermes/scripts/ 下的真文件 —— cron 会 resolve() 软链接并要求路径
# 落在 scripts 目录内，软链出去会被守卫拦下（#resolves outside the scripts directory）。
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
SKILL_SCRIPTS = HERMES_HOME / "skills" / "cycling-data-upload" / "scripts"
SCRIPT = str(SKILL_SCRIPTS / "strava-to-xingzhe-sync.py")
PY = sys.executable
LOG_DIR = HERMES_HOME / "xingzhe_sync" / "logs"


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # 全局串行锁：所有定时任务共用一把，保证「一个个进行」。
    # 抢不到锁 → 别的任务正在跑，本轮静默放弃（不排队、不堆叠）。
    lock_fd = _acquire("xingzhe-sync")
    if lock_fd is None:
        return 0

    try:
        now = datetime.datetime.now()
        day = (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d")   # 前天
        cmd = [PY, SCRIPT, "--since", day, "--until", day, "--limit", "30"]

        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        out = (p.stdout or "") + (p.stderr or "")

        logf = LOG_DIR / f"sync-{now:%Y%m%d}.log"
        logf.write_text(out)

        uploaded = [l.strip() for l in out.splitlines() if l.strip().startswith("✅")]
        failed = [l.strip() for l in out.splitlines() if l.strip().startswith("❌")]
        stat = next((l.strip() for l in out.splitlines() if l.startswith("新增上传")), "")

        if not uploaded and not failed:
            return 0   # 零字节输出 → cron 不投递，静默

        print(f"🚴 Strava → 行者 每日同步（{day}）")
        if stat:
            print(stat)
        for u in uploaded:
            print(u)
        for f in failed:
            print(f)
        print(f"\n日志 {logf}")
        return 0
    finally:
        _release(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
