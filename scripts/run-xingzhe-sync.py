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
from _serial_lock import acquire as _acquire, release as _release, holder as _holder

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
    # wait_s=180：cron 间隔 5 分钟，但 AI 抓取要 3-9 分钟。
    # 抢不到就等最多 3 分钟 —— 前一个任务结束后接上跑完。
    # 仍抢不到 → 明确报告（不再静默），否则用户无法区分
    # 「这轮没跑」和「跑了但没新数据」。
    lock_fd = _acquire("xingzhe-sync", wait_s=180)
    if lock_fd is None:
        print(f"⚠️ Strava→行者 本轮未执行：全局锁被占用（持锁: {_holder()}）")
        print(f"   下一个 cron 窗口会自动重试。")
        return 0

    try:
        now = datetime.datetime.now()
        # 2026-10-26 修复：窗口从「只有 day-2」改成「day-2 ~ day-1」。
        # 原先 --since day --until day（前天单日），导致：
        #   11:05 跑同步前天 → 昨天(10-25)骑的活动当天完全扫不到，
        #   要等到后天 11:05 才会被扫到（用户感知「同步没生效」）。
        # 现在覆盖前天+昨天两条链路，服务端去重闸门保证不会重复上传。
        d2 = (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        d1 = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        cmd = [PY, SCRIPT, "--since", d2, "--until", d1, "--limit", "30"]

        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        out = (p.stdout or "") + (p.stderr or "")

        logf = LOG_DIR / f"sync-{now:%Y%m%d}.log"
        logf.write_text(out)

        uploaded = [l.strip() for l in out.splitlines() if l.strip().startswith("✅")]
        failed = [l.strip() for l in out.splitlines() if l.strip().startswith("❌")]
        stat = next((l.strip() for l in out.splitlines() if l.startswith("新增上传")), "")

        if not uploaded and not failed:
            # 静默前先自检：0 候选 vs 候选被跳过，是两回事。
            # 之前「候选 0 条」和「全部已同步」都返回 0，用户看到的是同一个
            # 「没动静」，无法区分「今天没骑行」和「过滤器把活动挡掉了」。
            # 2026-10-26 用户反馈「同步没生效」时，就是这种情况（VirtualRide
            # 被 --types 默认值静默过滤），而 cron 一个字都没说。
            cand = next((l.strip() for l in out.splitlines() if "Strava 候选" in l), "")
            skipped = [l.strip() for l in out.splitlines() if "跳过" in l]
            if skipped:
                print(f"🚴 Strava → 行者 每日同步（{d2} ~ {d1}）：无新增")
                print(f"   {cand}")
                for s in skipped[:5]:
                    print(f"   {s}")
                print(f"\n日志 {logf}")
                return 0
            # 候选 0 条 → 可能真的没骑行，也可能窗口/types 有问题。
            # 只在「窗口内本应有活动」时才提示；无法判断时保持静默。
            return 0

        print(f"🚴 Strava → 行者 每日同步（{d2} ~ {d1}）")
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
