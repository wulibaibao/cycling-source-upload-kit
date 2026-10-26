#!/usr/bin/env python3
"""三副本一致性校验 —— 保证「生产脚本 / skill 副本 / GitHub 导出」不会漂移。

背景（2026-10-25 用户提问「定时任务为什么跑在 git 仓库的代码里」引出）：
  定时任务**不跑在 git 仓库里** —— cron 执行的是 ~/.hermes/scripts/ 下的真文件，
  那些目录不是 git 仓库。但同一份代码存在三处拷贝：
    1. ~/.hermes/scripts/*.py                    ← 生产（cron 实际执行）
    2. ~/.hermes/skills/<skill>/scripts/*.py     ← skill 副本（导出源）
    3. GitHub 仓库                              ← 公开副本（别人 clone）
  改了一处忘了同步 = 漂移，公开仓库里的示例代码会误导别人。

本脚本校验 1↔2 是否一致（本地，秒级）。3 由 export-skill.sh 在推送前校验。

用法：
    python3 verify-no-drift.py            # 只报告，不改
    python3 verify-no-drift.py --fix     # 把 skill 副本同步成生产版
退出码：0 = 一致；1 = 有漂移（--fix 时为修完仍不一致）
"""
import hashlib
import os
import shutil
import sys
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
SCRIPTS = HERMES_HOME / "scripts"
SKILL_SCRIPTS = HERMES_HOME / "skills" / "cycling-data-upload" / "scripts"

# 只校验「生产侧有、且 skill 也应该同步」的文件。
# run-github-trending-refresh.py 只服务博客，不属于骑行 skill —— 故意不同步。
FILES = [
    "_serial_lock.py",
    "run-xingzhe-sync.py",
    "run-magene-igp-sync.py",
    "run-onelap-sync.py",
    "run-strava-igp-sync.py",
    "run-ai-article-scrape.py",
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    fix = "--fix" in sys.argv
    drifted = []

    print(f"生产: {SCRIPTS}")
    print(f"副本: {SKILL_SCRIPTS}\n")

    for name in FILES:
        a = SCRIPTS / name
        b = SKILL_SCRIPTS / name
        if not a.exists():
            print(f"  ⚠️  {name:36} 生产侧缺失")
            continue
        if not b.exists():
            drifted.append(name)
            print(f"  ❌ {name:36} skill 副本缺失")
            if fix:
                shutil.copy2(a, b)
                print(f"       → 已从生产侧复制")
            continue
        if sha(a) != sha(b):
            drifted.append(name)
            print(f"  ❌ {name:36} 漂移（生产 {sha(a)[:8]} vs 副本 {sha(b)[:8]}）")
            if fix:
                shutil.copy2(a, b)
                print(f"       → 已同步为生产版")
        else:
            print(f"  ✅ {name:36} 一致")

    print()
    if drifted:
        if fix:
            print(f"✅ 已修复 {len(drifted)} 个漂移: {drifted}")
            return 0
        print(f"❌ {len(drifted)} 个文件漂移 —— 跑 `python3 {sys.argv[0]} --fix` 修复")
        return 1
    print("✅ 全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
