#!/usr/bin/env python3
"""cron 入口：AI 文章抓取发布（v4）。

每天 11:20 触发：抓 RSS → MiniMax 扩写 → 发布到博客 → 写入 digest 队列。

关键点：
- **持锁跑完全程**。scrape 脚本要跑 3-9 分钟，必须持有全局串行锁直到结束，
  否则中途释放会让后面的同步任务并发进来。
- 前台执行 + 内部用 setsid 无关 —— 这里直接前台等，cron 调度器给足超时即可。
- 输出摘要（成功/失败/篇数）；无产出时静默。

全局串行锁：与其它定时任务共用一把，保证「一个个进行」。
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))
from _serial_lock import acquire as _acquire, release as _release, holder as _holder

# 博客项目根目录：可用 BLOG_DIR 环境变量覆盖，默认 ~/wulibaibao-blog
# （不用绝对个人路径，公开仓库里不含个人路径）
BLOG_DIR = Path(
    os.environ.get("BLOG_DIR")
    or (Path.home() / "wulibaibao-blog")
)
SCRAPE = BLOG_DIR / "scripts" / "scrape_ai_articles_v4.py"
LOG_DIR = BLOG_DIR / "logs"
TIMEOUT = 1500  # 25 分钟上限（实测整轮 3-9 分钟，棒 21 最长 9:08）


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 全局串行锁：抢不到 → 别的任务在跑，本轮静默放弃（不排队、不堆叠）。
    # wait_s=240：AI 抓取本身要 3-9 分钟，是最长的一条，11:20 那轮
    # 前面若还有任务在跑，很容易被挡。给 4 分钟等待窗口接上。
    lock_fd = _acquire("ai-article-scrape", wait_s=240)
    if lock_fd is None:
        print(f"⚠️ AI 文章抓取 本轮未执行：全局锁被占用（持锁: {_holder()}）")
        print(f"   下一个 cron 窗口会自动重试。")
        return 0

    try:
        # 加载 .env（MINIMAX_API_KEY / GITHUB_TOKEN 等）
        env = dict(os.environ)
        envfile = BLOG_DIR / ".env"
        if envfile.exists():
            for line in envfile.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))

        try:
            p = subprocess.run(
                [sys.executable, str(SCRAPE)],
                capture_output=True, text=True, timeout=TIMEOUT,
                cwd=str(BLOG_DIR), env=env,
            )
            out, err, rc = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired:
            out, err, rc = "", f"超时（>{TIMEOUT}s）", 124
        except Exception as e:
            out, err, rc = "", f"执行异常: {e}", 1

        import datetime
        logf = LOG_DIR / f"scrape-{datetime.datetime.now():%Y%m%d}.log"
        with open(logf, "a") as f:
            f.write(f"\n===== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} rc={rc} =====\n")
            f.write(out)
            if err:
                f.write("STDERR:\n" + err)

        lines = [l for l in out.splitlines() if l.strip()]
        # 关键指标：抓取数 / 发布数 / 入队数
        n_fetched = next((l for l in lines if "共获得" in l and "篇原始文章" in l), "")
        n_published = next((l for l in lines if "成功发布" in l), "")
        n_queued = next((l for l in lines if "今日队列已保存" in l), "")
        # 只统计真正的失败（❌ / 「发布失败」），排除「⏭ 跳过入队（撞库）」
        # —— 撞库跳过是正常去重机制，不该报成失败。
        n_failed = len([l for l in lines if "❌" in l or "发布失败" in l])

        if rc != 0:
            print(f"❌ AI 文章抓取发布失败 rc={rc}\n{(err or out)[-800:]}")
            print(f"日志 {logf}")
            return 0

        if not (n_fetched or n_published or n_queued):
            # 没有任何产出（源全死等情况）→ 静默，不打扰用户
            return 0

        print("📝 AI 文章抓取发布完成")
        if n_fetched:
            print("  " + n_fetched.strip())
        if n_published:
            print("  " + n_published.strip())
        if n_queued:
            print("  " + n_queued.strip())
        if n_failed:
            print(f"  ⚠️ 失败/异常行数: {n_failed}")
        print(f"日志 {logf}")
        return 0
    finally:
        _release(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
