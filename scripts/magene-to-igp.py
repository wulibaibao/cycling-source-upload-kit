#!/usr/bin/env python3
"""迈金(顽鹿/OneLap) → IGPSPORT 同步。

数据流:
  迈金码表 → 迈金 App → 顽鹿云端 (u.onelap.cn) → 拉原始 FIT → 上传 IGPSPORT

坐标系: 迈金 FIT 本身是 GCJ-02，IGPSPORT 也按 GCJ-02 存
        （实测：迈金 App 传到 IGPSPORT 的记录与云端原始 FIT 坐标差 0.0 m）
        ⇒ 不做坐标转换。

去重: IGPSPORT 活动列表（日期 + 距离容差）为主，MD5 为辅。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import igp_client as ig
import onelap_client as ol

# 状态/缓存/日志目录随 HERMES_HOME 走（不写死个人路径）
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
STATE_DIR = HERMES_HOME / "igp_sync"
STATE_FILE = STATE_DIR / "state.json"
FIT_DIR = STATE_DIR / "fit"
LOG_DIR = HERMES_HOME / "xingzhe_sync" / "logs"

# 去重容差
TIME_TOL_SEC = 300          # 5 分钟（迈金 start_riding_time 与 IGPSPORT 记录时间有分钟级差）
DIST_TOL_MIN = 50.0         # 米
DIST_TOL_RATIO = 0.005      # 0.5%


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"synced": {}}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def igp_activity_detail(ride_id):
    """IGPSPORT 活动详情（含完整开始时间）。"""
    url = (f"https://prod.zh.igpsport.com/service/web-gateway/web-analyze/activity/"
           f"queryActivityDetail/{ride_id}")
    r = ig.requests.get(url, headers=ig._plain_headers(ig.load_token()), timeout=45)
    r.raise_for_status()
    return (r.json().get("data") or {})


def build_igp_index(token):
    """拉 IGPSPORT 活动索引 → [(date, dist_m, rideId, title)]

    begin_time=None → 拉全量（最早到 2023-06）。用 begin_time 会漏掉更早的记录，
    导致历史补传时产生重复。
    """
    acts = ig.list_activities(token=token, begin_time=None)
    out = []
    for a in acts:
        st = a.get("startTime") or ""
        m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", st)
        date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        out.append((date, a.get("rideDistance") or 0.0, a.get("rideId"), a.get("title") or ""))
    return out


def igp_has_activity(index, date, dist_m):
    """按 日期 + 距离 容差查 IGPSPORT 是否已有该活动。返回 rideId 或 None"""
    if not date:
        return None
    tol = max(DIST_TOL_MIN, dist_m * DIST_TOL_RATIO)
    for d, dd, rid, title in index:
        if d == date and abs(dd - dist_m) <= tol:
            return rid
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="起始日期 YYYY-MM-DD（默认前天）")
    ap.add_argument("--until", help="结束日期 YYYY-MM-DD（默认前天）")
    ap.add_argument("--days", type=int, default=1, help="回看天数")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略去重，强制上传")
    ap.add_argument("--no-gcj", action="store_true", help="强制不转坐标（默认就不转）")
    args = ap.parse_args()

    today = datetime.now().date()
    if args.since and args.until:
        since = datetime.fromisoformat(args.since).date()
        until = datetime.fromisoformat(args.until).date()
    else:
        until = today - timedelta(days=2)
        since = until - timedelta(days=max(0, args.days - 1))

    log(f"窗口 {since} → {until}")
    FIT_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()
    synced = state.setdefault("synced", {})

    # --- 登录检查 + 自动续期 ---
    ol_auth = ol.load_auth()
    if not ol_auth:
        log("❌ 顽鹿未登录，请先运行: python3 onelap_client.py login <account> <password>")
        return 1
    # 顽鹿 token 过期 → 自动重新登录（需 ONELAP_PASSWORD 或凭据文件）
    if ol.token_expired(ol_auth.get("token"), margin=3600) is True:
        log("顽鹿 token 即将过期，尝试自动重新登录...")
        ok_re, msg_re = ol.relogin()
        log(f"  {msg_re}")
        if ok_re:
            ol_auth = ol.load_auth()
        else:
            log(f"  ⚠️ {msg_re} —— 继续用旧 token 试一次")
    igp_token = ig.load_token()
    if not igp_token:
        log("❌ IGPSPORT 未登录，请先运行: python3 igp_client.py sms <phone> 然后 login")
        return 1
    # IGPSPORT token 过期 → 自动刷新（refresh_token）
    # 刷新端点要求 Bearer 仍有效，所以必须提前刷（margin 6 小时足够：每天跑一次）
    if ig.token_expired(igp_token, margin=21600) is True:
        log("IGPSPORT token 即将过期，尝试自动刷新...")
        ok_re, msg_re = ig.refresh_access_token()
        log(f"  {msg_re}")
        if ok_re:
            igp_token = ig.load_token()
        else:
            # 刷新失败 = 认证即将彻底失效，必须让用户知道（否则 cron 静默失败几天）
            phone = ""
            try:
                phone = json.loads(ig.TOKEN_FILE.read_text()).get("phone") or ""
            except Exception:
                pass
            log(f"  ⚠️ 刷新失败（{msg_re}），需重新登录：python3 igp_client.py sms {phone}")
            return 1

    ol_sess = ol.build_session(ol_auth)

    # --- 拉 OneLap 活动（按窗口需要翻页）---
    all_items, ok, msg = ol.list_rides(ol_sess, 1, 20)
    if not ok:
        log(f"❌ 顽鹿拉取失败: {msg}")
        return 1
    log(f"顽鹿活动 {len(all_items)} 条")

    # 翻页：拉到 limit 用尽或没有更多
    page = 2
    while len(all_items) < args.limit:
        items, ok, msg = ol.list_rides(ol_sess, page, 20)
        if not ok or not items:
            break
        all_items.extend(items)
        page += 1
        if len(items) < 20:
            break
    log(f"顽鹿活动累计 {len(all_items)} 条（翻 {page-1} 页）")

    # --- 筛选窗口内 ---
    candidates = []
    for it in all_items:
        st = it.get("start_riding_time") or ""
        try:
            d = datetime.fromisoformat(st.replace(" ", "T")).date()
        except Exception:
            continue
        if since <= d <= until:
            candidates.append(it)
    log(f"窗口内候选 {len(candidates)} 条")

    # --- IGPSPORT 索引（去重真相） ---
    igp_idx = build_igp_index(igp_token)
    log(f"IGPSPORT 索引 {len(igp_idx)} 条")

    uploaded, skipped, failed = [], [], []
    for it in candidates:
        rid = it.get("id")
        st = it.get("start_riding_time")
        dist_m = (it.get("distance_km") or 0) * 1000
        name = it.get("name") or ""
        date = st[:10] if st else None

        # 取详情拿 fitUrl
        try:
            detail = ol.fetch_detail(ol_sess, rid)
        except Exception as e:
            log(f"  #{rid} 详情失败: {e}")
            failed.append(rid)
            continue
        rr = (detail.get("data") or {}).get("ridingRecord") or {}
        fit_url = rr.get("fitUrl") or ""
        if not fit_url:
            log(f"  #{rid} 无 fitUrl，跳过")
            skipped.append(rid)
            continue

        key = f"{rid}:{fit_url}"

        # 闸门 1：本地 state
        if key in synced and not args.force:
            log(f"  #{rid} 跳过：本地已同步 {synced[key].get('status')}")
            skipped.append(rid)
            continue

        # 闸门 2：IGPSPORT 服务端（日期+距离）
        if not args.force:
            hit = igp_has_activity(igp_idx, date, dist_m)
            if hit:
                log(f"  #{rid} 跳过：IGPSPORT 已有相同活动 (rideId={hit})")
                synced[key] = {"status": "exists_server", "rideId": hit, "at": int(time.time())}
                skipped.append(rid)
                continue

        # 下载 FIT
        data, err = ol.download_fit(ol_sess, fit_url)
        if not data:
            log(f"  #{rid} FIT 下载失败: {err}")
            failed.append(rid)
            continue
        md5 = hashlib.md5(data).hexdigest()
        fit_path = FIT_DIR / f"{rid}.fit"
        fit_path.write_bytes(data)
        log(f"  #{rid} FIT {len(data)} bytes md5={md5[:10]} {fit_url[:50]}")

        if args.dry_run:
            log(f"  #{rid} [dry-run] 将上传: {name} {dist_m/1000:.2f} km")
            continue

        # 上传 IGPSPORT（文件名保持原始，便于服务端识别）
        fname = fit_url.rsplit("/", 1)[-1]
        if not fname.lower().endswith(".fit"):
            fname = fname.replace(".st", ".fit")
        ok_up, msg_up = ig.upload_fit(data, fname)
        if ok_up:
            log(f"  #{rid} ✅ 上传成功: {msg_up}")
            uploaded.append((rid, name, fname))
            synced[key] = {"status": "uploaded", "md5": md5, "at": int(time.time()),
                           "name": name, "file": fname}
        else:
            log(f"  #{rid} ❌ 上传失败: {msg_up}")
            failed.append(rid)
        save_state(state)

    save_state(state)
    print("----")
    print(f"上传 {len(uploaded)} / 跳过 {len(skipped)} / 失败 {len(failed)}")
    for rid, name, fname in uploaded:
        print(f"  ✅ #{rid} {name} → {fname}")
    for rid in failed:
        print(f"  ❌ #{rid}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
