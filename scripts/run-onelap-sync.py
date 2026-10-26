#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run-onelap-sync.py — 每日 Strava → 迈金（OneLap/顽鹿）同步的 cron 入口。

窗口：day-2 ~ day-0（前天+昨天+今天）。
  - day-2 起步：容忍源平台数据延迟入库
  - day-0 结尾：覆盖「今天早上」骑的（一天两练时 day-1 会漏一条）

靠 strava-to-onelap.py 的客户端去重闸门保证不重复 —— 迈金**没有服务端去重**，
重复上传会真的多出一条记录，所以这个闸门是硬要求。

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
SCRIPT = str(SKILL_SCRIPTS / "strava-to-onelap.py")
PY = sys.executable
LOG_DIR = HERMES_HOME / "onelap_sync" / "logs"


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = _acquire("onelap-sync", wait_s=180)
    if lock_fd is None:
        print(f"⚠️ Strava→迈金 本轮未执行：全局锁被占用（持锁: {_holder()}）")
        print(f"   下一个 cron 窗口会自动重试。")
        return 0

    try:
        now = datetime.datetime.now()
        d2 = (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        d0 = (now - datetime.timedelta(days=0)).strftime("%Y-%m-%d")
        cmd = [PY, SCRIPT, "--since", d2, "--until", d0, "--limit", "30"]

        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        out = (p.stdout or "") + (p.stderr or "")

        logf = LOG_DIR / f"sync-{now:%Y%m%d}.log"
        logf.write_text(out)

        uploaded = [l.strip() for l in out.splitlines() if l.strip().startswith("✅")]
        failed = [l.strip() for l in out.splitlines() if l.strip().startswith("❌")]
        stat = next((l.strip() for l in out.splitlines() if l.startswith("上传 ")), "")

        if not uploaded and not failed:
            skipped = [l.strip() for l in out.splitlines() if "跳过" in l]
            if skipped:
                print(f"🚴 Strava → 迈金 每日同步（{d2} ~ {d0}）：无新增")
                print(f"   {next((l.strip() for l in out.splitlines() if '迈金服务端索引' in l), '')}")
                for s in skipped[:5]:
                    print(f"   {s}")
                print(f"\n日志 {logf}")
            return 0

        print(f"🚴 Strava → 迈金 每日同步（{d2} ~ {d0}）")
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
