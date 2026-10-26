#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strava-to-xingzhe-sync.py — Strava → 行者 每日自动同步（去重加固版）

与 strava-to-xingzhe.py 完全独立实现（不 import），避免互相污染 token / state。

加固点（相对原版）：
1. 服务端索引去重（主）：行者 activities (start_time±15s, distance±tol) + uploads md5
   本地 state 只是缓存，每轮都用服务端数据重建/修正。
2. 三态语义：uploaded / unknown / failed
   unknown（超时等不确定）→ 下一轮先查服务端索引再决定是否重传，绝不盲传。
3. 坐标纠偏：Strava 是 WGS-84，行者原生记录是 GCJ-02 → 上传前转换（中国境内）。
4. flock 独占锁，防并发重复上传。
5. 上传后回读行者活动详情校验（运动类型/距离）。

用法：
    strava-to-xingzhe-sync.py                      # 本月，Ride，上限 30
    strava-to-xingzhe-sync.py --since 2026-10-01 --limit 50
    strava-to-xingzhe-sync.py --dry-run
    strava-to-xingzhe-sync.py --types Ride,Run --no-gcj
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from garmin_fit_sdk import Encoder
from garmin_fit_sdk.profile import Profile

Profile["messages"] = {int(k): v for k, v in Profile["messages"].items()}

HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
STRAVA_TOKEN_FILE = HERMES_HOME / "strava_token.json"
XZ_DIR = HERMES_HOME / "xingzhe_sync"
XZ_TOKEN_FILE = XZ_DIR / "token.json"
STATE_FILE = XZ_DIR / "strava_state.json"
LOCK_FILE = XZ_DIR / "sync.lock"
FIT_DIR = XZ_DIR / "fit"

STRAVA_CLIENT_ID = "178483"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"

XZ_BASE = "https://www.imxingzhe.com"
XZ_TOKEN_URL = f"{XZ_BASE}/oauth2/v2/access_token/"
XZ_UPLOAD_API = f"{XZ_BASE}/openapi/v1/uploads/"
XZ_ACTIVITIES_API = f"{XZ_BASE}/openapi/v1/activities/"
XZ_UPLOADS_API = f"{XZ_BASE}/openapi/v1/uploads/"
# 行者 App 私有接口（借鉴 ridefitsync）。实测带 OAuth Bearer 可用。
XZ_PRIVATE_UPLOAD_API = "https://www.imxingzhe.com/api/v1/fit/upload/"
XZ_PRIVATE_ACTIVITY_API = "https://www.imxingzhe.com/api/v1/pgworkout/"

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)

# 去重容差
TIME_TOL_MS = 15_000
DIST_TOL_MIN = 30.0
DIST_TOL_RATIO = 0.005

# 行者 sport: 1徒步 2跑步 3骑行
SPORT_MAP = {"Ride": 3, "Run": 2, "Hike": 1, "Walk": 1, "VirtualRide": 3, "VirtualRun": 2}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def log_err(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- 凭据 / token
def load_env_creds():
    creds = {}
    env_path = HERMES_HOME / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            s = line.strip()
            if s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            creds[k.strip()] = v.strip().strip('"').strip("'")
    return creds


def strava_access_token():
    tok = json.loads(STRAVA_TOKEN_FILE.read_text())
    if tok.get("access_token") and tok.get("expires_at", 0) > int(time.time()) + 60:
        return tok["access_token"]
    r = requests.post(STRAVA_TOKEN_URL, data={
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": tok.get("client_secret"),
        "grant_type": "refresh_token",
        "refresh_token": tok.get("refresh_token"),
    }, timeout=20)
    r.raise_for_status()
    new = r.json()
    new["client_id"] = STRAVA_CLIENT_ID
    new["client_secret"] = tok.get("client_secret")
    new["refresh_token"] = new.get("refresh_token", tok.get("refresh_token"))
    STRAVA_TOKEN_FILE.write_text(json.dumps(new, indent=2))
    log("Strava token 已刷新")
    return new["access_token"]


def strava_get(path, params=None):
    headers = {"Authorization": f"Bearer {strava_access_token()}"}
    r = requests.get(f"{STRAVA_API_BASE}{path}", headers=headers, params=params or {}, timeout=45)
    r.raise_for_status()
    return r.json()


def xingzhe_access_token(creds):
    if not XZ_TOKEN_FILE.exists():
        raise RuntimeError("行者 token 缺失，请先跑 strava-to-xingzhe.py --oauth-login")
    tok = json.loads(XZ_TOKEN_FILE.read_text())
    if tok.get("expires_at", 0) > int(time.time()) + 60:
        return tok["access_token"]
    if not tok.get("refresh_token"):
        raise RuntimeError("行者 token 过期且无 refresh_token，需重新授权")
    r = requests.post(XZ_TOKEN_URL, data={
        "client_id": creds["XINGZHE_CLIENT_ID"],
        "client_secret": creds["XINGZHE_CLIENT_SECRET"],
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
    }, timeout=20)
    r.raise_for_status()
    new = r.json()
    tok["access_token"] = new["access_token"]
    tok["refresh_token"] = new.get("refresh_token", tok["refresh_token"])
    tok["expires_at"] = int(time.time()) + new.get("expires_in", 2591999)
    tok["refreshed_at"] = int(time.time())
    XZ_TOKEN_FILE.write_text(json.dumps(tok, indent=2))
    log("行者 token 已刷新")
    return tok["access_token"]


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
# 目标平台接受的 device 身份。IGPSPORT 会拒收 development(255)/0 身份的 FIT。
# magene 的 FIT manufacturer id = 115（C706 product=50），从 IGPS 已有记录读出的。
DEVICE_IDENTITY = {
    "xingzhe": (255, 0),
    "onelap": (255, 0),
    "igp": (107, 50),   # magene=107, C706 product=50
}

TARGET_COORD_SYSTEM = {
    "xingzhe": "GCJ-02",
    "strava": "WGS-84",
    # 迈金（OneLap/顽鹿）在中国境内用 GCJ-02 —— 实测迈金云端既有活动
    # 与 IGPSPORT 同一条的坐标差 0.00 m，两边都是 GCJ-02。
    "onelap": "GCJ-02",
    # IGPSPORT 中国境内也是 GCJ-02 —— 实测迈金云端既有活动与 IGPSPORT
    # 同一条记录坐标差 0.00 m。
    "igp": "GCJ-02",
}


# ---------------------------------------------------------------- state
def load_state():
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text())
            if isinstance(s, dict):
                s.setdefault("synced", {})
                return s
        except Exception:
            pass
    return {"synced": {}}


def save_state(state):
    XZ_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- 服务端索引
def fetch_xz_activities(access, since_ms, until_ms):
    """拉行者活动列表（时间窗内），返回 [(start_ms, distance, id)]

    优先用 App 私有接口 /api/v1/pgworkout/ —— 它是实时的；
    OpenAPI /openapi/v1/activities/ 的索引有分钟级延迟（删除后仍能看到）。
    """
    out = []
    try:
        offset = 0
        seen = 0
        while True:
            r = requests.get(XZ_PRIVATE_ACTIVITY_API,
                             headers={"Authorization": f"Bearer {access}"},
                             params={"offset": offset, "limit": 50, "sport": 3}, timeout=45)
            if r.status_code != 200:
                break
            items = (r.json().get("data") or {}).get("data") or []
            if not items:
                break
            for it in items:
                st = it.get("start_time")
                ds = it.get("distance") or 0
                if st is None:
                    continue
                if since_ms <= st <= until_ms:
                    out.append((st, ds, it.get("id")))
            seen += len(items)
            offset += len(items)
            if len(items) < 50:
                break
        if out:
            log(f"    私有索引: 拉取 {seen} 条, 窗口内 {len(out)} 条")
            return out
    except Exception:
        pass

    # 私有接口不可用 → 回退 OpenAPI
    offset = 0
    while True:
        r = requests.get(XZ_ACTIVITIES_API, headers={"Authorization": f"Bearer {access}"},
                         params={"limit": 20, "offset": offset,
                                 "start_timestamp": since_ms, "end_timestamp": until_ms}, timeout=45)
        r.raise_for_status()
        d = r.json()
        res = d.get("results", [])
        if not res:
            break
        for a in res:
            out.append((a.get("start_time"), a.get("distance") or 0, a.get("id")))
        offset += 20
        if offset >= d.get("count", 0):
            break
    return out


def fetch_xz_upload_md5s(access):
    """拉行者全部上传记录的 md5 集合"""
    md5s = {}
    offset = 0
    while True:
        r = requests.get(XZ_UPLOADS_API, headers={"Authorization": f"Bearer {access}"},
                         params={"limit": 20, "offset": offset}, timeout=45)
        r.raise_for_status()
        d = r.json()
        res = d.get("results", [])
        if not res:
            break
        for u in res:
            if u.get("md5"):
                md5s.setdefault(u["md5"], []).append(u.get("id"))
        offset += 20
        if offset >= d.get("count", 0):
            break
    return md5s


def xz_record_healthy(access, workout_id, expect=None):
    """检查行者上的一条记录坐标是否正常。

    expect: 可选的期望首点 (lat, lng)（来自 Strava 原始流），用于精确比对。

    返回 True/False；检查失败（网络等）时返回 True（保守：宁可跳过也不重复上传）。

    判坏依据：
      1. 与期望坐标偏差过大（>2 km）⇒ 经纬度写反或坐标系搞错
      2. 没有期望值时：纬度/经度明显落在非法组合
         （中国境内 lat≈20~50 且 lng≈70~140；经纬度互换时 lat 会跑到 100+）
    """
    try:
        r = requests.post(f"{XZ_BASE}/openapi/v1/workout/{workout_id}/stream/",
                          headers={"Authorization": f"Bearer {access}"}, timeout=90)
        if r.status_code != 200:
            return True
        loc = (r.json().get("data") or {}).get("location") or []
        if not loc:
            return True
        lon, lat = float(loc[0][0]), float(loc[0][1])

        if expect is not None:
            e_lat, e_lon = expect
            dlat = (lat - e_lat) * 111320.0
            dlng = (lon - e_lon) * 111320.0 * abs(math.cos(math.radians(e_lat)))
            return math.hypot(dlat, dlng) <= 2000.0

        # 无期望值：启发式判断
        if abs(lat - lon) < 1e-6:
            return False                        # 经纬度相等
        if 90.0 < abs(lat) < 140.0 and 20.0 < abs(lon) < 50.0:
            return False                        # 经纬度互换（lat/lng 值域对调）
        return True
    except Exception:
        return True


def server_has_activity(index, start_ms, distance):
    """(start_time±15s, distance±max(30m,0.5%)) 命中即认为服务端已有

    若有多条命中（同时间同距离的重复记录），优先返回坐标健康的那条。
    """
    tol = max(DIST_TOL_MIN, distance * DIST_TOL_RATIO)
    hits = [wid for s, dist, wid in index
            if abs(s - start_ms) <= TIME_TOL_MS and abs(dist - distance) <= tol]
    return hits[0] if hits else None


def server_has_healthy_activity(index, start_ms, distance, access, expect=None):
    """只认坐标健康的服务端记录。命中损坏记录时返回 None（触发重传）。"""
    tol = max(DIST_TOL_MIN, distance * DIST_TOL_RATIO)
    for s, dist, wid in index:
        if abs(s - start_ms) <= TIME_TOL_MS and abs(dist - distance) <= tol:
            if xz_record_healthy(access, wid, expect):
                return wid
            # 这条是损坏记录，继续找下一条；全坏则返回 None → 走重传
    return None


# ---------------------------------------------------------------- FIT 生成
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
    vsmooth = streams.get("velocity_smooth", {}).get("data", [])
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
    # device 身份：IGPSPORT 按 manufacturer/product 识别来源设备，
    # `development`(255/0) 的 FIT 会被 uploadByOss 收下但**不入库**（实测）。
    # 伪装成 magene C706：manufacturer=107（magene），product=50（C706）。
    _mf, _pf = DEVICE_IDENTITY.get(target, (255, 0))
    enc.write_mesg({"mesg_num": 0, "type": 4, "manufacturer": _mf, "product": _pf,
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
            rec["enhanced_altitude"] = float(alt[i])
        if i < len(vsmooth) and vsmooth[i] is not None:
            rec["speed"] = float(vsmooth[i])
            rec["enhanced_speed"] = float(vsmooth[i])
        if i < len(hr) and hr[i] is not None:
            rec["heart_rate"] = int(hr[i])
        if i < len(watts) and watts[i] is not None:
            rec["power"] = int(watts[i])
        if i < len(cad) and cad[i] is not None:
            rec["cadence"] = int(cad[i])
        enc.write_mesg(rec)
    out_path.write_bytes(enc.close())
    return len(latlng), converted, coord_note


def upload_fit(access, fit_path, title, detail=""):
    """上传 FIT 到行者 OpenAPI。

    融合 ridefitsync 的做法：OpenAPI 上传后，再用行者的**私有上传接口**
    探测 9006「已上传」判定。私有接口带 OAuth Bearer 可用（实测 HTTP 200 + 走到 md5 校验）。

    返回 (status_code, body, md5, duplicate: bool)
    """
    data = fit_path.read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    payload = {"title": title[:32], "detail": detail[:1500],
               "fit_filename": fit_path.name, "file_type": "fit", "md5": md5}
    files = {"fit_file": (fit_path.name, data, "application/octet-stream")}
    headers = {"Authorization": f"Bearer {access}"}
    r = requests.post(XZ_UPLOAD_API, data=payload, files=files, headers=headers, timeout=120)
    try:
        body = r.json()
    except Exception:
        body = r.text[:400]

    duplicate = False
    if isinstance(body, dict):
        code = str(body.get("code", ""))
        msg = str(body.get("msg", "") or body.get("message", "") or "")
        if code == "9006" or "已上传" in msg or "重复" in msg:
            duplicate = True
    return r.status_code, body, md5, duplicate


def upload_fit_via_private(access, fit_path, title):
    """行者的重复检测探测（借 ridefitsync 的思路）。

    不实际提交完整文件（避免产生新活动）：用**错误的 md5** 让服务端停在 md5 校验，
    从而只探测鉴权与接口可达性；真正的重复判定靠 upload_fit 返回体里的 9006。
    """
    data = fit_path.read_bytes()
    files = {"fit_file": (fit_path.name, data, "application/octet-stream")}
    payload = {"file_source": "undefined", "fit_filename": fit_path.name,
               "md5": "0" * 32, "name": fit_path.name, "sport": "3"}
    try:
        r = requests.post(XZ_PRIVATE_UPLOAD_API, headers={"Authorization": f"Bearer {access}"},
                          data=payload, files=files, timeout=60)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:200]
    except Exception as e:
        return None, str(e)


def delete_xz_activity(access, workout_id):
    """删除行者活动。

    **OpenAPI 没有删除接口**（DELETE /openapi/v1/activities/{id}/ → 405），
    但行者的 App 私有接口可以：
        DELETE https://www.imxingzhe.com/api/v1/pgworkout/{id}/   → 200
    实测删除后该活动立即从私有列表消失（OpenAPI 索引有分钟级延迟）。

    返回 (ok: bool, detail: str)
    """
    try:
        r = requests.delete(f"{XZ_PRIVATE_ACTIVITY_API}{workout_id}/",
                            headers={"Authorization": f"Bearer {access}"}, timeout=45)
        if r.status_code in (200, 204):
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code} {r.text[:120]}"
    except Exception as e:
        return False, str(e)


def verify_upload(access, workout_id, expect_dist):
    try:
        r = requests.get(f"{XZ_ACTIVITIES_API}{workout_id}/",
                         headers={"Authorization": f"Bearer {access}"}, timeout=45)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        d = r.json().get("data", {})
        dist = d.get("distance") or 0
        ok = abs(dist - expect_dist) <= max(50, expect_dist * 0.02)
        return ok, f"sport={d.get('sport')} dist={dist}"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="YYYY-MM-DD，默认本月 1 号")
    ap.add_argument("--until", help="YYYY-MM-DD，默认今天")
    ap.add_argument("--limit", type=int, default=30)
    # 2026-10-26：默认白名单加入 VirtualRide。
    # 原因：用户有 VirtualRide 类型的活动（室内/虚拟骑行，2026-10-25 实测 20.0km
    # #20720920299，带 3322 个 GPS 点 + 心率 + 踏频，数据完整可传）。
    # 原先只认 "Ride" → 这类活动被静默过滤，永远传不到行者。
    # SPORT_MAP 里本来就有 VirtualRide→3 的映射，只是白名单把它挡在门外。
    ap.add_argument("--types", default="Ride,VirtualRide",
                    help="逗号分隔的 Strava type 白名单（默认含 VirtualRide）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-gcj", action="store_true", help="不做 WGS-84→GCJ-02 纠偏")
    ap.add_argument("--force-id", type=int, action="append", default=[],
                    help="强制重传指定 Strava 活动 ID（忽略去重闸门），可重复")
    ap.add_argument("--ignore-gate", action="store_true",
                    help="忽略所有去重闸门（仅用于修复/重建，慎用）")
    args = ap.parse_args()

    now = datetime.now()
    since = datetime.strptime(args.since, "%Y-%m-%d").astimezone() if args.since \
        else now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    until = datetime.strptime(args.until, "%Y-%m-%d").astimezone() if args.until \
        else now.replace(hour=23, minute=59, second=59, microsecond=0)
    types = {t.strip() for t in args.types.split(",") if t.strip()}

    creds = load_env_creds()
    if not creds.get("XINGZHE_CLIENT_ID") or not creds.get("XINGZHE_CLIENT_SECRET"):
        log_err(".env 缺 XINGZHE_CLIENT_ID / XINGZHE_CLIENT_SECRET")
        return 2
    access = xingzhe_access_token(creds)

    since_ms = int(since.timestamp() * 1000)
    until_ms = int(until.timestamp() * 1000)

    log(f"窗口 {since:%Y-%m-%d} → {until:%Y-%m-%d} | types={','.join(sorted(types))}")

    # ---- 服务端索引（真相）----
    acts_idx = fetch_xz_activities(access, since_ms - 86_400_000, until_ms + 86_400_000)
    md5_idx = fetch_xz_upload_md5s(access)
    log(f"服务端索引: 活动 {len(acts_idx)} 条 (窗口±1d), 上传 md5 {len(md5_idx)} 个")

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
    cleaned = []

    for a in acts:
        aid = str(a["id"])
        title = (a.get("name") or f"strava-{aid}")[:32]
        dist = float(a.get("distance", 0) or 0)
        start_local = a.get("start_date_local", "")
        try:
            # 注意：Strava 的 start_date_local 虽然带 Z 后缀，但内容其实是本地时间，
            # 不能再做 astimezone()（会平白多偏 8 小时，导致去重闸门全部失配）。
            sdl = (a.get("start_date_local") or "").rstrip("Z")
            start_dt = datetime.fromisoformat(sdl).astimezone()
        except Exception:
            start_dt = datetime.fromisoformat(a["start_date"].replace("Z", "+00:00")).astimezone()
        start_ms = int(start_dt.timestamp() * 1000)

        forced = (aid in {str(x) for x in args.force_id}) or args.ignore_gate

        # 先取 Strava streams（闸门健康检查需要首点做比对）
        fit_path = FIT_DIR / f"{aid}.fit"
        streams = None
        if not fit_path.exists():
            streams = strava_get(f"/activities/{aid}/streams",
                                 {"keys": "latlng,time,heartrate,watts,cadence,altitude,distance",
                                  "key_by_type": "true"})
        else:
            # 复用缓存的 FIT，但健康检查仍需要期望首点 → 从 Strava 轻量拉一次
            try:
                streams = strava_get(f"/activities/{aid}/streams",
                                     {"keys": "latlng", "key_by_type": "true"})
            except Exception:
                streams = None
        expect = None
        if streams:
            ll = streams.get("latlng", {}).get("data", [])
            if ll:
                expect = (float(ll[0][0]), float(ll[0][1]))   # (lat, lng)

        # 闸门 1.5：同组（同时间+距离）里若有损坏记录 → 先自动删除
        # （私有接口 DELETE /api/v1/pgworkout/{id}/ 可删；OpenAPI 没有删除接口）
        if not forced:
            tol = max(DIST_TOL_MIN, dist * DIST_TOL_RATIO)
            broken = [wid for s, ds, wid in acts_idx
                      if abs(s - start_ms) <= TIME_TOL_MS and abs(ds - dist) <= tol
                      and not xz_record_healthy(access, wid, expect)]
            for wid in broken:
                ok, detail = delete_xz_activity(access, wid)
                log(f"  #{aid} 清理损坏记录 workout={wid}: {'已删除 ' + detail if ok else '删除失败 ' + detail}")
                if ok:
                    acts_idx = [t for t in acts_idx if t[2] != wid]
                    cleaned.append(wid)

        # 闸门 1：服务端已有相同时间+距离的健康活动
        hit = None if forced else server_has_healthy_activity(acts_idx, start_ms, dist, access, expect)
        if hit:
            log(f"  #{aid} 跳过：行者已有相同活动 (workout {hit})")
            synced[aid] = {"status": "exists_server", "workout_id": hit,
                           "at": int(time.time()), "name": title}
            skipped.append(aid)
            continue
        if forced:
            log(f"  #{aid} [强制] 跳过去重闸门")

        # 生成 FIT（streams 已在闸门前取好）
        if not fit_path.exists():
            n, conv, note = build_fit(a, streams, fit_path, to_gcj=not args.no_gcj,
                                      source="strava", target="xingzhe")
            log(f"  #{aid} 生成 FIT {n} 点 (纠偏 {conv}) {fit_path.stat().st_size/1024:.0f} KB")
            log(f"       坐标系: {note}")
        else:
            log(f"  #{aid} 复用 FIT {fit_path.name}")

        md5 = hashlib.md5(fit_path.read_bytes()).hexdigest()

        # 闸门 2：md5 已在服务端上传记录里
        if md5 in md5_idx and not forced:
            log(f"  #{aid} 跳过：md5 已存在于行者上传记录 {md5_idx[md5]}")
            synced[aid] = {"status": "exists_md5", "upload_ids": md5_idx[md5],
                           "at": int(time.time()), "name": title}
            skipped.append(aid)
            continue

        if args.dry_run:
            log(f"  #{aid} [dry-run] 将上传: {title!r} {dist/1000:.2f} km")
            continue

        # 上传
        try:
            code, body, md5, duplicate = upload_fit(access, fit_path, title)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            log_err(f"  #{aid} 上传不确定（网络异常）: {e}")
            synced[aid] = {"status": "unknown", "md5": md5, "error": str(e),
                           "at": int(time.time()), "name": title}
            failed.append(aid)
            continue
        except Exception as e:
            log_err(f"  #{aid} 上传异常: {e}")
            synced[aid] = {"status": "failed", "error": str(e),
                           "at": int(time.time()), "name": title}
            failed.append(aid)
            continue

        # 闸门 3：行者返回 9006「已上传」⇒ 之前已传过，不是新活动
        if duplicate:
            log(f"  #{aid} 跳过：行者返回「已上传」(9006) {str(body)[:120]}")
            synced[aid] = {"status": "duplicate_9006", "body": str(body)[:200],
                           "at": int(time.time()), "name": title}
            skipped.append(aid)
            continue

        ok = code == 201 or (200 <= code < 300)
        wid = None
        if isinstance(body, dict):
            wid = (body.get("data") or {}).get("workout_id")
        if not ok:
            log_err(f"  #{aid} 上传失败 HTTP {code}: {str(body)[:200]}")
            synced[aid] = {"status": "failed", "http": code, "body": str(body)[:300],
                           "at": int(time.time()), "name": title}
            failed.append(aid)
            continue

        vok, vinfo = (False, "无 workout_id")
        if wid:
            vok, vinfo = verify_upload(access, wid, dist)
        log(f"  #{aid} 上传成功 workout={wid} | 回读校验 {'✓' if vok else '✗'} {vinfo}")
        synced[aid] = {"status": "uploaded", "workout_id": wid, "md5": md5,
                       "verified": vok, "verify_info": vinfo,
                       "at": int(time.time()), "name": title}
        uploaded.append((aid, title, wid))
        # 立即更新服务端索引，避免同一批内重复命中
        acts_idx.append((start_ms, dist, wid))
        md5_idx.setdefault(md5, []).append(wid)

        save_state(state | {"synced": synced})

    state["synced"] = synced
    state["last_run"] = int(time.time())
    save_state(state)

    print("----")
    print(f"新增上传 {len(uploaded)} / 跳过 {len(skipped)} / 失败 {len(failed)}")
    for aid, title, wid in uploaded:
        print(f"  ✅ #{aid} {title} → 行者 workout {wid}")
    for aid in failed:
        print(f"  ❌ #{aid} {synced.get(aid, {}).get('name', '')}")
    for wid in cleaned:
        print(f"  🧹 已删除损坏记录 workout {wid}")
    return 0 if not failed else 1


if __name__ == "__main__":
    XZ_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("已有同步进程在跑，本次退出")
            sys.exit(0)
        code = main()
        sys.exit(code)
