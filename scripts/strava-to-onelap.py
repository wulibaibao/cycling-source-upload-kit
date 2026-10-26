#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strava-to-onelap.py — Strava → 迈金（OneLap/顽鹿）同步。

与 strava-to-xingzhe-sync.py 同构：拉 Strava → 客户端去重闸门 → 生成 FIT → 上传 → 回读校验。

迈金侧接口要点（2026-10-26 实测）：
  - 数据 host 用 https://www.onelap.cn（u.onelap.cn 是旧副本，total 少 2 条日常骑行）
  - 上传：POST {host}/api/otm/ride_record/upload/fit
          multipart/form-data，文件字段名固定为 **jilu0**
          返回 {code:200, data:{success_count, failed_count, ...}}
  - 认证：Authorization: <OneLap JWT> + Origin/Referer 头
  - ⚠ 迈金**没有服务端去重** —— 重复上传会真的多出一条记录。
    所以本脚本的客户端闸门是硬要求，不是优化项。
  - 删除（清理误传）：DELETE {host}/api/otm/ride_record/delete/{record_id}

用法：
  python3 strava-to-onelap.py --since 2026-10-01 --until 2026-10-26 --limit 30
  python3 strava-to-onelap.py --dry-run
"""
import argparse
import math
import base64
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests
from garmin_fit_sdk import Encoder
from garmin_fit_sdk.profile import Profile

Profile["messages"] = {int(k): v for k, v in Profile["messages"].items()}

HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
STRAVA_TOKEN_FILE = HERMES_HOME / "strava_token.json"
ONELAP_DIR = HERMES_HOME / "onelap_sync"
STATE_FILE = ONELAP_DIR / "state.json"
FIT_DIR = ONELAP_DIR / "fit"

STRAVA_API_BASE = "https://www.strava.com/api/v3"

# 迈金（OneLap）—— 数据 host 必须是 www（u. 是旧副本，见模块 docstring）
ONELAP_HOST = "https://www.onelap.cn"
ONELAP_LIST_API = f"{ONELAP_HOST}/api/otm/ride_record/list"
ONELAP_UPLOAD_API = f"{ONELAP_HOST}/api/otm/ride_record/upload/fit"
ONELAP_DELETE_API = f"{ONELAP_HOST}/api/otm/ride_record/delete/{{record_id}}"
ONELAP_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")

# 去重容差
TIME_TOL_SEC = 900      # 15 分钟
DIST_TOL_MIN = 30.0     # 米
DIST_TOL_RATIO = 0.005  # 0.5%

# 迈金 FIT 里没有的字段：type 用它自己的编码
# 13=竞技/比赛  15=快速燃脂  27=普通骑行  48=自由骑行  49=路线
ONELAP_TYPE_DEFAULT = 27

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)


def log(msg):
    print(msg, flush=True)


def log_err(msg):
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- 坐标纠偏
_GCJ_A = 6378245.0
_GCJ_EE = 0.00669342162296594323


def _tlat(x, y):
    r = -100.0 + 2.0*x + 3.0*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x))
    r += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi)) * 2.0/3.0
    r += (20.0*math.sin(y*math.pi) + 40.0*math.sin(y/3.0*math.pi)) * 2.0/3.0
    r += (160.0*math.sin(y/12.0*math.pi) + 320.0*math.sin(y*math.pi/30.0)) * 2.0/3.0
    return r


def _tlon(x, y):
    r = 300.0 + x + 2.0*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
    r += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi)) * 2.0/3.0
    r += (20.0*math.sin(x*math.pi) + 40.0*math.sin(x/3.0*math.pi)) * 2.0/3.0
    r += (150.0*math.sin(x/12.0*math.pi) + 300.0*math.sin(x/30.0*math.pi)) * 2.0/3.0
    return r


def in_china(lon, lat):
    return 72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271


def _gcj_delta(lat, lon):
    """GCJ-02 偏移量（度）。公式与 strava-upload.py 的 _gcj_delta 完全一致（已实战验证）。"""
    d_lat = _tlat(lon - 105.0, lat - 35.0)
    d_lon = _tlon(lon - 105.0, lat - 35.0)
    rad = lat / 180.0 * math.pi
    magic = math.sin(rad)
    magic = 1 - _GCJ_EE * magic * magic
    sq = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_GCJ_A * (1 - _GCJ_EE)) / (magic * sq) * math.pi)
    d_lon = (d_lon * 180.0) / ((_GCJ_A / sq * math.cos(rad)) * math.pi)
    return d_lat, d_lon


def wgs_to_gcj(lon, lat):
    """WGS-84 → GCJ-02（行者原生记录坐标系）。迭代 2 轮，精度 <1e-6 度。"""
    if not in_china(lon, lat):
        return lon, lat
    g_lat, g_lon = lat, lon
    for _ in range(2):
        d_lat, d_lon = _gcj_delta(g_lat, g_lon)
        g_lat = lat + d_lat
        g_lon = lon + d_lon
    return g_lon, g_lat


def gcj_to_wgs(lon, lat):
    """GCJ-02 → WGS-84。迭代至收敛。"""
    if not in_china(lon, lat):
        return lon, lat
    g_lat, g_lon = lat, lon
    for _ in range(30):
        d_lat, d_lon = _gcj_delta(g_lat, g_lon)
        new_lat = lat - d_lat
        new_lon = lon - d_lon
        if abs(new_lat - g_lat) < 1e-9 and abs(new_lon - g_lon) < 1e-9:
            g_lat, g_lon = new_lat, new_lon
            break
        g_lat, g_lon = new_lat, new_lon
    return g_lon, g_lat


# ---------------------------------------------------------------- 坐标系识别
# 借鉴 sizaif/ridefitsync 的 coord_fixer.dart：采样投票判断坐标系，而不是硬编码假设。
#
# 与该项目的差异（本项目的更正确之处）：
#   ridefitsync 的 _isSingleCoordLikelyWGS84 用 offsetIfWgs > offsetIfGcj 判定，
#   但「当作 WGS 时的偏移」总是 ≈「当作 GCJ 时的偏移」，且 WGS 方向恒略大，
#   导致判定方向整体反了（实测 10/10 全票判错）。
#   正确判定是：当作 WGS 时的偏移量 > 当作 GCJ 时的偏移量 ⇒ 其实是 GCJ。
#   本实现用 margin = (off_gcj - off_wgs) / max(off_wgs, off_gcj)，
#   margin > 0 ⇒ WGS-84，margin < 0 ⇒ GCJ-02。实测 1 个样本点即可判对。

_COORD_SAMPLE_MAX = 10


def _coord_offset_as_gcj(lon, lat):
    """把 (lon,lat) 当作 GCJ-02 反解成 WGS-84，返回位移量（度）"""
    if not in_china(lon, lat):
        return 0.0, 0.0
    w_lon, w_lat = gcj_to_wgs(lon, lat)
    return w_lon - lon, w_lat - lat


def _coord_offset_as_wgs(lon, lat):
    """把 (lon,lat) 当作 WGS-84 正向转成 GCJ-02，返回位移量（度）"""
    if not in_china(lon, lat):
        return 0.0, 0.0
    g_lon, g_lat = wgs_to_gcj(lon, lat)
    return g_lon - lon, g_lat - lat


def detect_coord_system(points):
    """采样投票判断 points 的坐标系。

    points: [(lat, lon), ...]
    返回 (is_wgs84: bool|None, detail: str)
      is_wgs84=True  → 原始就是 WGS-84，需要转成 GCJ-02
      is_wgs84=False → 原始就是 GCJ-02，无需转换
      is_wgs84=None  → 无法判定（境外 / 样本不足）
    """
    samples = []
    for lat, lon in points[:_COORD_SAMPLE_MAX]:
        if not in_china(lon, lat):
            continue                      # 境外：两向转换都是恒等，不参与投票
        samples.append((lat, lon))
    if not samples:
        return None, "全部样本在境外，无法判定"

    votes_wgs = votes_gcj = 0
    margins = []
    for lat, lon in samples:
        off_as_wgs = math.hypot(*_coord_offset_as_wgs(lon, lat))
        off_as_gcj = math.hypot(*_coord_offset_as_gcj(lon, lat))
        if off_as_gcj <= 0:
            continue
        margin = (off_as_gcj - off_as_wgs) / max(off_as_wgs, off_as_gcj)
        margins.append(margin)
        if margin > 0:
            votes_wgs += 1
        else:
            votes_gcj += 1

    if not margins:
        return None, "样本不足以计算偏移量"
    avg = sum(margins) / len(margins)
    detail = f"样本{len(margins)}点 wgs票={votes_wgs} gcj票={votes_gcj} margin={avg:+.2e}"
    if votes_wgs == votes_gcj:
        return None, detail + " 票数持平，无法判定"
    return votes_wgs > votes_gcj, detail


# 平台坐标系表（借鉴 ridefitsync 的 _gcjPlatforms）
# 注意：Strava 的坐标系**不固定** —— 取决于用户用什么 App/设备上传。
#   实测：MAGENE app 直接上传的 C706 活动 → Strava 里已是 GCJ-02（与行者原生 0.0m 差异）
#   实测：某些活动 → Strava 里是 WGS-84
# 所以 Strava 不写死，交给投票法判定；平台表仅在投票无法判定时兜底。
SOURCE_COORD_SYSTEM = {
    "strava": None,          # None = 不预设，依赖投票判定
    "onelap": "GCJ-02",
    "igp": "GCJ-02",
    "xingzhe": "GCJ-02",
    "giant": "GCJ-02",
    "edge_ride": "GCJ-02",
}
# 目标平台期望什么坐标系（行者原生 = GCJ-02）
TARGET_COORD_SYSTEM = {
    "xingzhe": "GCJ-02",
    "strava": "WGS-84",
}


# ---------------------------------------------------------------- 凭据 / token
def load_strava_token():
    if not STRAVA_TOKEN_FILE.exists():
        return None
    return json.loads(STRAVA_TOKEN_FILE.read_text())


def strava_get(path, params=None, token=None):
    token = token or (load_strava_token() or {}).get("access_token")
    if not token:
        raise RuntimeError("缺少 Strava token")
    r = requests.get(f"{STRAVA_API_BASE}{path}", headers={"Authorization": f"Bearer {token}"},
                     params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def onelap_auth():
    """复用 onelap_client 的登录态（JWT + cookies）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import onelap_client as ol
    auth = ol.load_auth()
    if not auth:
        raise RuntimeError("迈金未登录，请先运行 onelap_client.py login")
    if ol.token_expired(auth.get("token") or ""):
        ok, msg = ol.relogin()
        if not ok:
            raise RuntimeError(f"迈金重新登录失败: {msg}")
        auth = ol.load_auth()
    sess = ol.build_session(auth)
    token = auth.get("token") or auth.get("access_token")
    return auth, sess, token


def onelap_session(token, cookies):
    s = requests.Session()
    s.headers.update({
        "User-Agent": ONELAP_USER_AGENT,
        "Authorization": str(token),
        "Origin": ONELAP_HOST,
        "Referer": f"{ONELAP_HOST}/analysis",
    })
    s.cookies.update(cookies or {})
    return s


# ---------------------------------------------------------------- 迈金服务端索引
def fetch_onelap_index(sess):
    """拉全量迈金活动 → [(date_str, distance_km, record_id, name)]"""
    import onelap_client as ol
    out = []
    page = 1
    while True:
        payload = {"page": page, "limit": 20}
        headers = ol.sign_headers(payload)
        r = sess.post(ONELAP_LIST_API, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        pd = (r.json().get("data") or {})
        rows = pd.get("list") or []
        if not rows:
            break
        for x in rows:
            out.append((str(x.get("start_riding_time") or "")[:19],
                        float(x.get("distance_km") or 0.0),
                        x.get("id"),
                        x.get("name") or ""))
        if (pd.get("pagination") or {}).get("total_pages", 0) <= page:
            break
        page += 1
    return out


def onelap_has(index, day, dist, time_str):
    """迈金已有相同活动（日期+距离+时刻容差）"""
    for dstr, dkm, rid, name in index:
        if dstr[:10] != day:
            continue
        if abs(dkm - dist) > max(DIST_TOL_MIN / 1000.0, dist * DIST_TOL_RATIO):
            continue
        # 同日同距离 → 视为已存在（迈金一天很少有两趟几乎相同距离的骑行）
        return rid
    return None


# ---------------------------------------------------------------- state
def load_state():
    if not STATE_FILE.exists():
        return {"synced": {}}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"synced": {}}


def save_state(st):
    ONELAP_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- 上传 / 校验
def upload_fit(session, fit_path, title):
    """上传 FIT 到迈金。字段名固定 jilu0。"""
    with open(fit_path, "rb") as fh:
        r = session.post(ONELAP_UPLOAD_API,
                         files={"jilu0": (Path(fit_path).name, fh, "application/octet-stream")},
                         timeout=180)
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"上传返回非 JSON: HTTP {r.status_code} {r.text[:200]}")
    if r.status_code != 200 or data.get("code") != 200:
        raise RuntimeError(f"上传失败: HTTP {r.status_code} {str(data)[:200]}")
    payload = data.get("data") or {}
    failed = int(payload.get("failed_count") or 0)
    success = int(payload.get("success_count") or 0)
    if failed > 0 or success <= 0:
        raise RuntimeError(f"上传未成功入队: {json.dumps(data, ensure_ascii=False)[:200]}")
    return payload


def verify_onelap(index, day, dist):
    """回读校验：迈金列表里能看到刚上传的那条"""
    for dstr, dkm, rid, name in index:
        if dstr[:10] == day and abs(dkm - dist) <= max(DIST_TOL_MIN / 1000.0, dist * DIST_TOL_RATIO):
            return rid
    return None


def delete_onelap(sess, record_id):
    import onelap_client as ol
    payload = {"page": 1, "limit": 5}
    r = sess.delete(ONELAP_DELETE_API.format(record_id=record_id),
                    headers=ol.sign_headers(payload), timeout=30)
    return r.status_code == 200 and (r.json().get("code") == 200)


def build_fit(activity, streams, out_path, to_gcj=True, source="strava", target="xingzhe"):
    """从 Strava streams 重建 FIT。

    坐标系处理采用「平台信任 + 投票校验」双重机制（融合 ridefitsync 的两个设计）：
      1. 平台坐标系表给出预期（SOURCE_COORD_SYSTEM / TARGET_COORD_SYSTEM）
      2. 对实际数据采样投票，判断真实坐标系
      3. 两者一致 → 采信；不一致 → 采信投票结果但记警告
         （投票法实测 1 点即可判对，比平台表更贴近真实数据）

    to_gcj=True 表示「目标要 GCJ-02 且原始不是 GCJ-02 时需要转换」。
    """
    start = datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00"))
    latlng = streams.get("latlng", {}).get("data", [])
    times = streams.get("time", {}).get("data", [])
    hr = streams.get("heartrate", {}).get("data", [])
    watts = streams.get("watts", {}).get("data", [])
    cad = streams.get("cadence", {}).get("data", [])
    alt = streams.get("altitude", {}).get("data", [])
    dist = streams.get("distance", {}).get("data", [])
    if not latlng or not times:
        raise RuntimeError(f"活动 {activity['id']} 没有 streams")

    # ---- 决定是否需要坐标转换 ----
    need_convert = False
    coord_note = ""
    sample_pts = [(float(ll[0]), float(ll[1])) for ll in latlng[:_COORD_SAMPLE_MAX]]
    detected, detail = detect_coord_system(sample_pts)

    src_expect = SOURCE_COORD_SYSTEM.get(source)       # 可能是 None（不预设）
    tgt_expect = TARGET_COORD_SYSTEM.get(target, "GCJ-02")

    if detected is None:
        # 无法判定 → 采信平台表（若源平台未预设，则按「已是目标坐标系」处理，不转换）
        if src_expect is None:
            need_convert = False
            coord_note = f"投票无法判定（{detail}），源平台坐标系未预设 → 不转换"
        else:
            need_convert = (src_expect != tgt_expect)
            coord_note = f"投票无法判定（{detail}），采信平台表({src_expect})"
    else:
        detected_name = "WGS-84" if detected else "GCJ-02"
        # 投票判定是权威：源坐标系 != 目标坐标系时才转换
        need_convert = (detected_name != tgt_expect)
        if src_expect is None:
            coord_note = f"投票判定={detected_name}（源平台未预设，以投票为准，{detail}）"
        elif detected_name == src_expect:
            coord_note = f"投票判定={detected_name}，与平台表一致（{detail}）"
        else:
            coord_note = (f"⚠️ 投票判定={detected_name} 与平台表({src_expect})冲突，"
                          f"以投票为准（{detail}）")

    if to_gcj is False:
        need_convert = False
        coord_note += " | --no-gcj 强制不转换"

    enc = Encoder()
    enc.write_mesg({"mesg_num": 0, "type": 4, "manufacturer": 255, "product": 0,
                    "serial_number": 0,
                    "time_created": int((start - FIT_EPOCH).total_seconds())})
    enc.write_mesg({
        "mesg_num": 18,
        "start_time": int((start - FIT_EPOCH).total_seconds()),
        "total_elapsed_time": float(activity.get("elapsed_time", 0) or 0),
        "total_distance": float(activity.get("distance", 0) or 0),
        "sport": 2,          # cycling
        "sub_sport": 0,      # generic（cycling 下 1=treadmill 非法，会被误判为跑步）
    })
    converted = 0
    for i in range(len(latlng)):
        ll = latlng[i]
        lat, lon = float(ll[0]), float(ll[1])
        if need_convert:
            lon2, lat2 = wgs_to_gcj(lon, lat)
            if abs(lat2 - lat) > 1e-9 or abs(lon2 - lon) > 1e-9:
                converted += 1
            lat, lon = lat2, lon2          # 注意顺序：lat2 是纬度，lon2 是经度
        ts = start + timedelta(seconds=int(times[i]))
        rec = {"mesg_num": 20,
               "timestamp": int((ts - FIT_EPOCH).total_seconds()),
               "position_lat": int(lat * (2 ** 31) / 180),
               "position_long": int(lon * (2 ** 31) / 180)}
        if i < len(dist) and dist[i] is not None:
            rec["distance"] = float(dist[i])
        if i < len(alt) and alt[i] is not None:
            rec["altitude"] = float(alt[i])
        if i < len(hr) and hr[i] is not None:
            rec["heart_rate"] = int(hr[i])
        if i < len(watts) and watts[i] is not None:
            rec["power"] = int(watts[i])
        if i < len(cad) and cad[i] is not None:
            rec["cadence"] = int(cad[i])
        enc.write_mesg(rec)
    out_path.write_bytes(enc.close())
    return len(latlng), converted, coord_note


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="YYYY-MM-DD，默认本月 1 号")
    ap.add_argument("--until", help="YYYY-MM-DD，默认今天")
    ap.add_argument("--types", default="Ride,VirtualRide")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-id", action="append", default=[],
                    help="强制上传某个 Strava activity id（跳过去重闸门）")
    ap.add_argument("--ignore-gate", action="store_true", help="忽略所有去重闸门（慎用）")
    args = ap.parse_args()

    now = datetime.now()
    since = datetime.strptime(args.since, "%Y-%m-%d").astimezone() if args.since \
        else now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    until = datetime.strptime(args.until, "%Y-%m-%d").astimezone() if args.until \
        else now.replace(hour=23, minute=59, second=59, microsecond=0)
    types = {t.strip() for t in args.types.split(",") if t.strip()}

    log(f"窗口 {since:%Y-%m-%d} → {until:%Y-%m-%d} | types={','.join(sorted(types))}")

    auth, sess, token = onelap_auth()
    osession = onelap_session(token, sess.cookies)

    # ---- 迈金服务端索引（真相）----
    index = fetch_onelap_index(sess)
    log(f"迈金服务端索引: {len(index)} 条活动")

    state = load_state()
    synced = state.get("synced", {})

    # ---- 拉 Strava ----
    after = int(since.timestamp())
    before = int(until.timestamp()) + 86400
    acts = []
    page = 1
    while len(acts) < args.limit:
        batch = strava_get("/athlete/activities",
                           {"after": after, "before": before, "per_page": 50, "page": page})
        if not batch:
            break
        acts += batch
        if len(batch) < 50:
            break
        page += 1
    acts = [a for a in acts if a.get("type") in types][: args.limit]
    log(f"Strava 候选 {len(acts)} 条")

    FIT_DIR.mkdir(parents=True, exist_ok=True)
    uploaded, skipped, failed = [], [], []

    for a in acts:
        aid = str(a["id"])
        title = (a.get("name") or f"strava-{aid}")[:32]
        dist = float(a.get("distance", 0) or 0) / 1000.0        # km
        sdl = (a.get("start_date_local") or "")[:19]
        day = sdl[:10]

        forced = (aid in {str(x) for x in args.force_id}) or args.ignore_gate

        # ---- 闸门 1：迈金已有相同活动 ----
        if not forced:
            hit = onelap_has(index, day, dist, sdl)
            if hit:
                log(f"  #{aid} 跳过：迈金已有相同活动 ({hit})")
                synced[aid] = {"status": "exists_server", "record_id": hit,
                               "at": int(time.time()), "name": title}
                skipped.append(aid)
                continue

        # ---- 拉 streams + 生成 FIT ----
        fit_path = FIT_DIR / f"{aid}.fit"
        streams = strava_get(f"/activities/{aid}/streams",
                             {"keys": "latlng,time,heartrate,watts,cadence,altitude,distance",
                              "key_by_type": "true"})
        if not fit_path.exists():
            try:
                n, conv, note = build_fit(a, streams, fit_path, to_gcj=True,
                                           source="strava", target="onelap")
                log(f"  #{aid} 生成 FIT {n} 点 (纠偏 {conv}) {fit_path.stat().st_size/1024:.0f} KB")
                log(f"       坐标系: {note}")
            except Exception as e:
                log_err(f"  #{aid} FIT 生成失败: {e}")
                failed.append(aid)
                continue
        else:
            log(f"  #{aid} 复用 FIT {fit_path.name}")

        if args.dry_run:
            log(f"  #{aid} [dry-run] 将上传: {title!r} {dist:.2f} km")
            continue

        # ---- 上传 ----
        try:
            payload = upload_fit(osession, fit_path, title)
        except Exception as e:
            log_err(f"  #{aid} 上传失败: {e}")
            failed.append(aid)
            synced[aid] = {"status": "failed", "error": str(e),
                           "at": int(time.time()), "name": title}
            continue

        # ---- 回读校验（迈金是异步入库，等一下再查）----
        rid = None
        for _ in range(12):
            time.sleep(5)
            index = fetch_onelap_index(sess)
            rid = verify_onelap(index, day, dist)
            if rid:
                break
        if not rid:
            log_err(f"  #{aid} 上传返回成功但 60s 内回读不到（迈金异步入库可能更慢）")
            failed.append(aid)
            synced[aid] = {"status": "uploaded_unverified", "at": int(time.time()), "name": title}
            continue

        log(f"  #{aid} 上传成功 record_id={rid}")
        uploaded.append(aid)
        synced[aid] = {"status": "synced", "record_id": rid,
                       "at": int(time.time()), "name": title, "dist_km": dist}

    state["synced"] = synced
    save_state(state)

    print("----")
    print(f"上传 {len(uploaded)} / 跳过 {len(skipped)} / 失败 {len(failed)}")
    for aid in uploaded:
        print(f"  ✅ #{aid} {synced.get(aid, {}).get('name', '')} → record_id={synced.get(aid, {}).get('record_id')}")
    for aid in failed:
        print(f"  ❌ #{aid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
