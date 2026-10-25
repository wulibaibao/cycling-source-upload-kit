#!/usr/bin/env python3
"""全局串行锁 —— 所有定时任务共用同一把锁，保证「一个个进行」。

为什么需要：
  博客的 AI 文章抓取（3-9 分钟）、Strava/IGPSPORT 同步、Trending 刷新
  原本分散在 02:00 / 04:00 / 07:30 / 08:00 / 09:00。挪到同一个上午后，
  只错开几分钟仍可能撞车 —— 抓取脚本跑超 9 分钟就会压到下一个。
  串行锁是唯一能真正保证不并发、且不积压的做法。

用法：
    from _serial_lock import acquire, release
    fd = acquire("task-name")
    if fd is None:
        sys.exit(0)      # 别的任务在跑，本轮静默放弃（不排队、不堆叠）
    try:
        do_work()
    finally:
        release(fd)
"""
import fcntl
import os
import time

HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
LOCK_PATH = os.path.join(HERMES_HOME, "run", "serial.lock")


def acquire(task: str):
    """抢全局锁。成功返回 fd（必须持有到 release），失败返回 None。"""
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    try:
        os.ftruncate(fd, 0)
        os.write(
            fd,
            f"{task} {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()}\n".encode(),
        )
    except OSError:
        pass
    return fd


def release(fd) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def holder() -> str:
    """当前持锁者描述（诊断用，不持锁）。"""
    try:
        with open(LOCK_PATH, "r") as f:
            return f.read().strip() or "(空)"
    except OSError:
        return "(无锁文件)"
