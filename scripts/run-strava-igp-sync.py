#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run-strava-igp-sync.py — 每日 Strava → IGPSPORT 同步的 cron 入口。

窗口：day-2 ~ day-0（前天+昨天+今天）。
IGPSPORT 服务端**有去重**，所以重复跑不会产生重复活动。

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
from _serial_lock import acquire as _acquire, release as _release, holder as _holder

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
SKILL_SCRIPTS = HERMES_HOME / "skills" / "cycling-data-upload" / "scripts"
SCRIPT = str(SKILL_SCRIPTS / "strava-to-igp.py")
PY = sys.executable
LOG_DIR = HERMES_HOME / "igp_sync" / "logs"


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = _acquire("strava-igp-sync", wait_s=180)
    if lock_fd is None:
        print(f"⚠️ Strava→IGPSPORT 本轮未执行：全局锁被占用（持锁: {_holder()}）")
        print(f"   下一个 cron 窗口会自动重试。")
        return 0

    try:
        now = datetime.datetime.now()
        d2 = (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        d0 = (now - datetime.timedelta(days=0)).strftime("%Y-%m-%d")
        cmd = [PY, SCRIPT, "--since", d2, "--until", d0, "--limit", "30"]

        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        out = (p.stdout or "") + (p.stderr or "")

        logf = LOG_DIR / f"strava-igp-{now:%Y%m%d}.log"
        logf.write_text(out)

        uploaded = [l.strip() for l in out.splitlines() if l.strip().startswith("✅")]
        failed = [l.strip() for l in out.splitlines() if l.strip().startswith("❌")]
        stat = next((l.strip() for l in out.splitlines() if l.startswith("上传 ")), "")

        if not uploaded and not failed:
            skipped = [l.strip() for l in out.splitlines() if "跳过" in l]
            if skipped:
                print(f"🚴 Strava → IGPSPORT 每日同步（{d2} ~ {d0}）：无新增")
                print(f"   {next((l.strip() for l in out.splitlines() if 'IGPSPORT 服务端索引' in l), '')}")
                for s in skipped[:5]:
                    print(f"   {s}")
                print(f"\n日志 {logf}")
            return 0

        print(f"🚴 Strava → IGPSPORT 每日同步（{d2} ~ {d0}）")
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
