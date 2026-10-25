#!/usr/bin/env python3
"""cron 入口：迈金(顽鹿) → IGPSPORT 每日同步。

每天 11:05 同步「前天」(day-2) 当天的数据。
无新活动 → 零输出；有新同步或失败 → 输出报告。

全局串行锁：与其它定时任务共用一把，保证「一个个进行」。
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))
from _serial_lock import acquire as _acquire, release as _release

# 主脚本本体在 skill 目录（单一真相）。cron 入口本身必须是
# ~/.hermes/scripts/ 下的真文件 —— cron 会 resolve() 软链接并要求路径
# 落在 scripts 目录内，软链出去会被守卫拦下。
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
SKILL_SCRIPTS = HERMES_HOME / "skills" / "cycling-data-upload" / "scripts"


def main() -> int:
    TARGET = (datetime.now().date() - timedelta(days=2)).isoformat()
    LOG_DIR = HERMES_HOME / "xingzhe_sync" / "logs"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG = LOG_DIR / f"magene-igp-{datetime.now():%Y%m%d}.log"

    # 全局串行锁：抢不到 → 别的任务在跑，本轮静默放弃。
    lock_fd = _acquire("magene-igp-sync")
    if lock_fd is None:
        return 0

    try:
        cmd = [sys.executable, str(SKILL_SCRIPTS / "magene-to-igp.py"),
               "--since", TARGET, "--until", TARGET, "--limit", "30"]

        env = dict(os.environ)
        env["PYTHONPATH"] = str(SKILL_SCRIPTS) + ":" + env.get("PYTHONPATH", "")

        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, env=env)
            out, err, rc = p.stdout, p.stderr, p.returncode
        except Exception as e:
            out, err, rc = "", f"执行异常: {e}", 1

        with open(LOG, "a") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} (target {TARGET}) rc={rc} =====\n")
            f.write(out)
            if err:
                f.write("STDERR:\n" + err)

        lines = [l for l in out.splitlines() if l.strip()]
        if not lines:
            if rc != 0:
                print(f"❌ 迈金→IGPSPORT 同步失败\n{(err or out)[:800]}")
            return 0

        # 提取摘要
        summary = [l for l in lines if l.startswith("上传 ")]
        uploaded = [l for l in lines if "✅ 上传成功" in l]
        failed = [l for l in lines if "❌" in l or "⚠️" in l]

        if not uploaded and not failed:
            # 无新活动 → 静默
            return 0

        print(f"🚴 迈金→IGPSPORT 同步完成 ({TARGET})")
        if summary:
            print(summary[-1])
        for l in uploaded:
            print("  " + l.strip())
        for l in failed:
            print("  " + l.strip())
        return 0
    finally:
        _release(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
