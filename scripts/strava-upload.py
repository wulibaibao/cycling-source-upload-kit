#!/usr/bin/env python3
"""
Strava FIT 文件上传脚本 - 智能版
自动从 FIT 解析运动数据，生成智能标题和详细分析描述
用法: python3 strava-upload.py <fit文件路径> [activity_type]
"""

import sys
import os
import json
import requests
import time
import fitdecode.reader
import gzip
import io
from datetime import timezone, timedelta

# ====== 配置区 ======
# Strava Application 的 client_id 是公开标识（不是密钥），但仍做成可覆盖：
# 每个使用者应注册自己的 Application（https://www.strava.com/settings/api )
# client_secret **从不硬编码** —— 始终从 ~/.hermes/strava_token.json 动态读取。
CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "178483")
TOKEN_URL = "https://www.strava.com/oauth/token"
UPLOAD_URL = "https://www.strava.com/api/v3/uploads"
TOKEN_FILE = os.path.expanduser("~/.hermes/strava_token.json")

def load_token():
    with open(TOKEN_FILE) as f:
        return json.load(f)

def save_token(data):
    with open(TOKEN_FILE, 'w') as f:
        json.dump(data, f, indent=2)

# ====== FIT 解析 ======

ACTIVITY_TYPES = ["Run", "Ride", "Swim", "Walk", "Hike", "Workout", "WeightTraining", "Yoga"]

# ====== GCJ-02 → WGS-84 坐标纠偏（GPX 中介法）======
# 用 fitdecode 解析原 FIT → 对每条 record/session/lap 的 position_lat/long 做 gcj2wgs 转换
# → 用 garmin-fit-sdk Encoder 重建 FIT。绕开 binary patch 字节解析（06-04 验证：FIT data
# message 不含 msg_num/num_fields 字段，binary parser 根本无法正确识别 record 消息类型）。
# 副本独立维护在 ~/correct_fit_gps.py（06-04 验证用），主流程统一调用本函数。

import math as _math
from datetime import datetime as _dt, timezone as _tz

import fitdecode as _fitdecode
from garmin_fit_sdk import Encoder as _GarminEncoder
from garmin_fit_sdk.profile import Profile as _FitProfile

# 修复 Profile str-key bug（5/30 实战经验：Profile['messages'] 可能是 str key，Encoder 要 int key）
_FitProfile['messages'] = {int(k): v for k, v in _FitProfile['messages'].items()}

_GCJ_EARTH_R = 6378137.0
_GCJ_EE = 0.00669342162296594323
_FIT_EPOCH = _dt(1989, 12, 31, 0, 0, 0, tzinfo=_tz.utc)


def _out_of_china(lat, lng):
    if lng < 72.004 or lng > 137.8347: return True
    if lat < 0.8293 or lat > 55.8271: return True
    return False


def _gcj_transform(x, y):
    xy = x * y
    abs_x = _math.sqrt(abs(x))
    x_pi = x * _math.pi
    y_pi = y * _math.pi
    d = 20.0 * _math.sin(6.0 * x_pi) + 20.0 * _math.sin(2.0 * x_pi)
    lat = d
    lng = d
    lat += 20.0 * _math.sin(y_pi) + 40.0 * _math.sin(y_pi / 3.0)
    lng += 20.0 * _math.sin(x_pi) + 40.0 * _math.sin(x_pi / 3.0)
    lat += 160.0 * _math.sin(y_pi / 12.0) + 320.0 * _math.sin(y_pi / 30.0)
    lng += 150.0 * _math.sin(x_pi / 12.0) + 300.0 * _math.sin(x_pi / 30.0)
    lat *= 2.0 / 3.0
    lng *= 2.0 / 3.0
    lat += -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * xy + 0.2 * abs_x
    lng += 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * xy + 0.1 * abs_x
    return [lat, lng]


def _gcj_delta(lat, lng):
    t = _gcj_transform(lng - 105.0, lat - 35.0)
    d_lat, d_lng = t[0], t[1]
    rad_lat = lat / 180.0 * _math.pi
    magic = _math.sin(rad_lat)
    magic = 1 - _GCJ_EE * magic * magic
    sqrt_magic = _math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_GCJ_EARTH_R * (1 - _GCJ_EE)) / (magic * sqrt_magic) * _math.pi)
    d_lng = (d_lng * 180.0) / (_GCJ_EARTH_R / sqrt_magic * _math.cos(rad_lat) * _math.pi)
    return [d_lat, d_lng]


def _gcj2wgs(gcj_lat, gcj_lng):
    if _out_of_china(gcj_lat, gcj_lng): return (gcj_lat, gcj_lng)
    new_lat, new_lng = gcj_lat, gcj_lng
    for _ in range(30):
        old_lat, old_lng = new_lat, new_lng
        d = _gcj_delta(new_lat, new_lng)
        new_lat = gcj_lat - d[0]
        new_lng = gcj_lng - d[1]
        if max(abs(old_lat - new_lat), abs(old_lng - new_lng)) < 1e-6: break
    return (new_lat, new_lng)


def _semi_to_deg(s):
    return s * (180.0 / 2**31)


def _deg_to_semi(d):
    return int(round(d * (2**31 / 180.0)))


def _fit_ts(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz.utc)
    return int((dt - _FIT_EPOCH).total_seconds())


# 哪些消息哪些字段是坐标
_COORD_FIELDS = {
    'record':         ('position_lat', 'position_long'),
    'session':        ('start_position_lat', 'start_position_long',
                        'end_position_lat',   'end_position_long',
                        'nec_lat', 'nec_long', 'swc_lat', 'swc_long'),
    'lap':            ('start_position_lat', 'start_position_long',
                        'end_position_lat',   'end_position_long'),
}

# 消息名 → mesg_num 映射
_MESG_NUM = {
    'file_id': 0, 'user_profile': 3, 'bike_profile': 6,
    'sport': 12,
    'session': 18, 'lap': 19, 'record': 20,
    'activity': 34,
}
_HANDLED = set(_MESG_NUM.keys())


def _try_int(v):
    if v is None: return None
    try: return int(v)
    except (ValueError, TypeError): return None


def _correct_coords_inplace(d, coord_keys):
    """对 d 中在 coord_keys 里的字段做 (lat,lon) 配对 gcj2wgs，返回纠偏对数"""
    fixed = 0
    pairs = []
    used = set()
    for fname in list(d.keys()):
        if fname in coord_keys and fname.endswith('_lat') and fname not in used:
            base = fname[:-4]
            lon_name = base + '_long'
            if lon_name in d:
                pairs.append((fname, lon_name))
                used.add(fname)
                used.add(lon_name)
    if 'position_lat' in d and 'position_long' in d and ('position_lat', 'position_long') not in pairs:
        pairs.append(('position_lat', 'position_long'))
    for lat_f, lon_f in pairs:
        lat_v = d[lat_f]
        lon_v = d[lon_f]
        lat_deg = _semi_to_deg(lat_v)
        lon_deg = _semi_to_deg(lon_v)
        if _out_of_china(lat_deg, lon_deg):
            continue
        wgs_lat, wgs_lon = _gcj2wgs(lat_deg, lon_deg)
        d[lat_f] = _deg_to_semi(wgs_lat)
        d[lon_f] = _deg_to_semi(wgs_lon)
        fixed += 2
    return fixed


def correct_fit_file(input_path, output_path=None):
    """读取 FIT 文件，修正所有 GCJ-02 坐标为 WGS-84，写入新文件（GPX 中介法）。"""
    if output_path is None:
        output_path = input_path + '.corrected.fit'

    # convert_types_to_strings=False: 保留 enum 原始 int 值（fitdecode 默认会转 'cycling' 等字符串，
    # 导致 garmin-fit-sdk Encoder 收不到 sport 字段，最终产物 sport=Other，标题退化）
    with _fitdecode.FitReader(input_path) as fit:
        all_frames = list(fit)

    encoder = _GarminEncoder()
    records_written = 0
    coord_corrected = 0
    skipped = 0

    for frame in all_frames:
        if frame.frame_type != _fitdecode.FIT_FRAME_DATA:
            continue

        mesg_type = frame.name
        if mesg_type not in _HANDLED:
            skipped += 1
            continue

        d = {'mesg_num': _MESG_NUM[mesg_type]}

        for f in frame.fields:
            if f.value is None:
                continue
            if f.name == 'time_created' and isinstance(f.value, _dt):
                d['time_created'] = _fit_ts(f.value)
                continue
            if f.name in ('timestamp', 'start_time', 'local_timestamp') and isinstance(f.value, _dt):
                d[f.name] = _fit_ts(f.value)
                continue
            # 数字字段用 value（scaled，e.g. distance=54424.44m）；enum 字符串（fitdecode 默认把
            # 'cycling' 这种转字符串）回退 raw_value（= 2）
            v = _try_int(f.value)
            if v is not None:
                d[f.name] = v
            elif isinstance(f.value, str) and f.raw_value is not None:
                rv = _try_int(f.raw_value)
                if rv is not None:
                    d[f.name] = rv

        coord_corrected += _correct_coords_inplace(d, _COORD_FIELDS.get(mesg_type, ()))

        if len(d) > 1:
            try:
                encoder.write_mesg(d)
                if mesg_type == 'record':
                    records_written += 1
            except ValueError:
                # 某些消息字段不识别（如扩展字段），静默跳过
                pass
    data = encoder.close()

    with open(output_path, 'wb') as f:
        f.write(data)

    print(f'    [纠偏] {records_written} records, {coord_corrected} coords corrected, {skipped} msgs skipped')
    return output_path


def parse_fit(fit_path: str) -> dict:
    """解析 FIT 文件，提取所有运动数据"""
    data = {
        "records": [],
        "total_distance_m": 0,
        "total_calories": 0,
        "total_elapsed_s": 0,
        "max_speed_mps": 0,
        "avg_speed_mps": 0,
        "max_heart_rate": 0,
        "avg_heart_rate": 0,
        "min_altitude": 99999,
        "max_altitude": 0,
        "elev_gain": 0,      # 总爬升 m
        "elev_loss": 0,      # 总下降 m
        "max_cadence": 0,
        "start_time": None,
        "end_time": None,
        "sport": "Other",
        "sub_sport": "",
        "bike_name": "",
        "product_name": "",
        "manufacturer": "",
        "user_weight_kg": 0,
        "user_height_m": 0,
        "temperature_c": [],
        "wind_speed_mps": 0,
        "wind_direction_deg": 0,
        "humidity": 0,
    }

    speeds = []
    heart_rates = []
    altitudes = []
    cadence_values = []
    last_altitude = None

    with fitdecode.reader.FitReader(fit_path) as fit:
        for record in fit:
            try:
                name = record.name
            except:
                continue

            if not hasattr(record, 'fields'):
                continue

            # Extract field values
            def field_val(field_name):
                for f in record.fields:
                    if f.name == field_name and f.value is not None:
                        return f.value
                return None

            if name == "file_id":
                tc = field_val("time_created")
                if tc:
                    data["start_time"] = tc
                data["product_name"] = field_val("product_name") or ""
                data["manufacturer"] = field_val("manufacturer") or ""

            elif name == "sport":
                sport_map = {
                    "cycling": "Ride",
                    "running": "Run",
                    "swimming": "Swim",
                    "walking": "Walk",
                    "hiking": "Hike",
                }
                s = field_val("sport")
                if s:
                    data["sport"] = sport_map.get(s, s.capitalize() if s else "Other")
                data["sub_sport"] = field_val("name") or ""

            elif name == "bike_profile":
                data["bike_name"] = field_val("name") or ""

            elif name == "user_profile":
                data["user_weight_kg"] = field_val("weight") or 0
                data["user_height_m"] = field_val("height") or 0

            elif name == "session":
                sess_ascent = field_val("total_ascent")
                sess_descent = field_val("total_descent")
                # 仅当 session 有有效数据时使用（优先于 record 逐点累加）
                if sess_ascent and sess_ascent > 0:
                    data["elev_gain"] = sess_ascent
                if sess_descent and sess_descent > 0:
                    data["elev_loss"] = sess_descent

            elif name == "record":
                ts = field_val("timestamp")
                lat = field_val("position_lat")
                lon = field_val("position_long")
                dist = field_val("distance")
                alt = field_val("enhanced_altitude") or field_val("altitude")
                speed = field_val("enhanced_speed") or field_val("speed")
                hr = field_val("heart_rate")
                cad = field_val("cadence")
                cal = field_val("calories")
                temp = field_val("temperature")
                grade = field_val("grade")

                if ts and not data["start_time"]:
                    data["start_time"] = ts
                if ts:
                    data["end_time"] = ts

                if dist is not None:
                    data["total_distance_m"] = max(data["total_distance_m"], dist)

                if speed is not None and speed > 0:
                    speeds.append(speed)
                    data["max_speed_mps"] = max(data["max_speed_mps"], speed)

                if hr is not None and hr > 0:
                    heart_rates.append(hr)
                    data["max_heart_rate"] = max(data["max_heart_rate"], hr)

                if alt is not None:
                    alt = float(alt)
                    altitudes.append(alt)
                    data["min_altitude"] = min(data["min_altitude"], alt)
                    data["max_altitude"] = max(data["max_altitude"], alt)
                    if last_altitude is not None:
                        diff = alt - last_altitude
                        # 气压计小波动滤波：< 1m 的跳动不计入爬升/下降
                        if abs(diff) >= 1.0:
                            if diff > 0:
                                data["elev_gain"] += diff
                            else:
                                data["elev_loss"] += abs(diff)
                    last_altitude = alt

                if cad is not None and cad < 255:
                    cadence_values.append(cad)
                    data["max_cadence"] = max(data["max_cadence"], cad)

                if cal is not None:
                    data["total_calories"] = max(data["total_calories"], cal)

                if temp is not None:
                    data["temperature_c"].append(temp)

            elif name == "weather_conditions":
                ws = field_val("wind_speed")
                wd = field_val("wind_direction")
                rh = field_val("relative_humidity")
                if ws is not None:
                    data["wind_speed_mps"] = ws
                if wd is not None:
                    data["wind_direction_deg"] = wd
                if rh is not None:
                    data["humidity"] = rh

    # 计算均值
    if speeds:
        data["avg_speed_mps"] = sum(speeds) / len(speeds)
    if heart_rates:
        data["avg_heart_rate"] = sum(heart_rates) / len(heart_rates)
    if cadence_values:
        data["avg_cadence"] = sum(cadence_values) / len(cadence_values)

    # 计算时长
    if data["start_time"] and data["end_time"]:
        delta = data["end_time"] - data["start_time"]
        data["total_elapsed_s"] = delta.total_seconds()

    return data


# ====== 工具函数 ======

def format_beijing_time(dt):
    """将 UTC datetime 转换为北京时间 (+8h) 并格式化"""
    if not dt:
        return ""
    from datetime import timezone, timedelta
    bj_tz = timezone(timedelta(hours=8))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(bj_tz).strftime('%Y-%m-%d %H:%M')


# ====== 智能命名 ======

def get_title(d: dict) -> str:
    """根据运动类型、数据、时间生成标题"""
    from datetime import timezone, timedelta
    sport = d["sport"]
    sub = d["sub_sport"]
    dist = d["total_distance_m"]
    dist_km = dist / 1000
    start_time = d["start_time"]

    # 判断骑行时间段
    ride_time = ""
    if start_time:
        bj_tz = timezone(timedelta(hours=8))
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        local = start_time.astimezone(bj_tz)
        hour = local.hour
        if 5 <= hour < 9:
            ride_time = "晨骑"
        elif 9 <= hour < 12:
            ride_time = "午前骑"
        elif 12 <= hour < 14:
            ride_time = "午骑"
        elif 14 <= hour < 18:
            ride_time = "午后骑"
        elif 18 <= hour < 21:
            ride_time = "夜骑"
        else:
            ride_time = "夜骑"

    # 骑行标题：只用心率区间美化名称（不加时间/里程/地点）
        if sport == "Ride":
            hr_zone = get_hr_zone(d["avg_heart_rate"], d["max_heart_rate"]) if d["max_heart_rate"] > 0 else ""
            return hr_zone
    elif sport == "Run":
        dist_km = dist / 1000
        avg_hr = d["avg_heart_rate"]
        max_hr = d["max_heart_rate"]
        hr_zone = get_hr_zone(avg_hr, max_hr) if max_hr > 0 else ""
        hr_label = f" · {hr_zone}" if hr_zone else ""
        if dist_km < 3:
            base = "晨跑" if ride_time and "晨" in ride_time else "短跑"
        elif dist_km < 10:
            base = "中长跑训练"
        else:
            base = "长跑拉练"
        return base + hr_label
    elif sport == "Walk":
        return "徒步健行"
    elif sport == "Swim":
        return "游泳训练"
    else:
        return f"{sub}" if sub else sport


def get_description(d: dict) -> str:
    """生成详细的数据分析描述"""
    lines = []

    dist = d["total_distance_m"]
    dist_km = dist / 1000
    avg_speed = d["avg_speed_mps"]
    max_speed = d["max_speed_mps"]
    avg_hr = d["avg_heart_rate"]
    max_hr = d["max_heart_rate"]
    elev_gain = d["elev_gain"]
    elev_loss = d["elev_loss"]
    duration_s = d["total_elapsed_s"]
    calories = d["total_calories"]
    cadence_vals = [v for v in [d.get("avg_cadence", 0)] if v > 0]
    avg_cadence = d.get("avg_cadence", 0)
    temps = d["temperature_c"]
    max_alt = d["max_altitude"]
    min_alt = d["min_altitude"]
    bike = d["bike_name"]
    sport = d["sport"]
    sub = d["sub_sport"]

    # 时长格式化
    h = int(duration_s // 3600)
    m = int((duration_s % 3600) // 60)
    s = int(duration_s % 60)
    if h > 0:
        dur_str = f"{h}小时{m}分"
    else:
        dur_str = f"{m}分{s}秒"

    lines.append("📊 运动数据摘要")
    lines.append("─" * 20)
    # 08-17 共识：描述只保留心率 + 踏频（其他数据 Strava 已自带）
    if sport == "Ride" and avg_cadence > 0:
        lines.append(f"🔄 平均踏频: {int(avg_cadence)} rpm")
    if avg_hr > 0:
        lines.append(f"❤️ 平均心率: {int(avg_hr)} bpm")
        lines.append(f"💓 最高心率: {int(max_hr)} bpm")

    # 设备信息
    if d["product_name"]:
        lines.append(f"📱 码表: {d['product_name']}")

    lines.append(f"🤖 自动生成 | {format_beijing_time(d['start_time'])}")

    return "\n".join(lines)


def get_hr_zone(avg_hr, max_hr) -> str:
    """心率区间估算（绝对心率阈值法，参考 max_hr=166）

    之前的 max_hr 百分比法有 bug：当骑行没达到最大心率时（如 max_hr=154），
    avg_hr 134 会被算成 87% → Z4 阈值（错误）。改用绝对心率阈值。
    """
    if max_hr <= 0:
        return "未知"
    if avg_hr < 120:
        return "Z1 轻松区"
    elif avg_hr < 140:
        return "Z2 脂肪燃烧区"
    elif avg_hr < 152:
        return "Z3 有氧耐力区"
    elif avg_hr < 164:
        return "Z4 乳酸阈值区"
    else:
        return "Z5 最大摄氧区"


def compass_direction(deg: float) -> str:
    """度数转罗盘方向"""
    directions = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    idx = int((deg + 22.5) // 45) % 8
    return directions[idx]


# ====== Strava API ======

def refresh_access_token():
    tok = load_token()
    resp = requests.post(TOKEN_URL, data={
        "client_id": CLIENT_ID,
        "client_secret": tok.get("client_secret"),
        "grant_type": "refresh_token",
        "refresh_token": tok.get("refresh_token"),
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    # 保存新 token
    data["client_secret"] = tok.get("client_secret")
    save_token(data)
    print(f"[*] Token 刷新成功，过期时间: {data.get('expires_at', 'N/A')}")
    return data["access_token"]


def upload_fit(access_token, fit_path, activity_type, name, description):
    if not os.path.exists(fit_path):
        print(f"[×] 文件不存在: {fit_path}")
        sys.exit(1)

    file_size = os.path.getsize(fit_path)
    print(f"[i] 文件大小: {file_size / 1024:.1f} KB")

    # gzip 压缩（与 strava_auto 行为一致）
    with open(fit_path, "rb") as f:
        raw_bytes = f.read()
    compressed = gzip.compress(raw_bytes, compresslevel=6)
    print(f"[i] gzip 压缩后: {len(compressed) / 1024:.1f} KB")

    # 生成新的文件名（原名.fit.gz）
    base_name = os.path.basename(fit_path)
    ext_idx = base_name.rfind('.')
    gz_name = base_name[:ext_idx] + '.fit.gz' if ext_idx > 0 else base_name + '.fit.gz'

    files = {"file": (gz_name, compressed, "application/gzip")}
    data = {
        "name": name or os.path.basename(fit_path).replace(".fit", ""),
        "description": description or "",
        "activity_type": activity_type,
        "data_type": "fit.gz",
    }
    headers = {"Authorization": f"Bearer {access_token}"}

    print(f"[i] 上传中 ({activity_type}) ...")
    resp = requests.post(
        UPLOAD_URL, headers=headers, data=data, files=files, timeout=60,
    )

    resp.raise_for_status()
    result = resp.json()

    if result.get("error"):
        print(f"[×] 上传失败: {result}")
        sys.exit(1)

    upload_id = result.get("id")
    activity_id = result.get("activity_id")
    status = result.get("status")

    print(f"[√] 上传已提交!")
    print(f"    Upload ID : {upload_id}")
    print(f"    Status    : {status}")
    if activity_id:
        print(f"    Activity  : https://www.strava.com/activities/{activity_id}")
    else:
        print(f"[i] 活动 ID 暂时未知（后台处理中）")

    final_activity_id = activity_id
    if upload_id:
        print("[i] 等待后台处理...")
        for i in range(12):
            time.sleep(5)
            check = requests.get(
                f"{UPLOAD_URL}/{upload_id}", headers=headers, timeout=15,
            )
            sd = check.json()
            a = sd.get("activity_id")
            s = sd.get("status", "")
            print(f"    [{i+1}] {s}" + (f" → https://www.strava.com/activities/{a}" if a else ""))
            if a:
                final_activity_id = a
            if a or sd.get("error"):
                break

    return final_activity_id


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Strava FIT Upload Script")
    parser.add_argument("fit_file", nargs="?", help="FIT 文件路径")
    parser.add_argument("activity_type", nargs="?", help="运动类型 (Run/Ride/...)")
    parser.add_argument("title", nargs="?", help="自定义标题")
    parser.add_argument("--correct-coords", action="store_true",
                        help="上传前将 GCJ-02 坐标纠偏为 WGS-84")
    parser.add_argument("--parse-only", dest="parse_only", action="store_true",
                        help="仅解析 FIT 文件，输出 JSON 后退出")
    args = parser.parse_args()

    if args.parse_only:
        if not args.fit_file:
            print("错误: --parse-only 需要指定 FIT 文件路径")
            sys.exit(1)
        d = parse_fit(args.fit_file)
        print(json.dumps(d, default=str, ensure_ascii=False))
        sys.exit(0)

    if not args.fit_file:
        print("用法: python3 strava-upload.py <fit文件> [activity_type] [title] [--correct-coords]")
        print(f"可选 activity_type: {', '.join(ACTIVITY_TYPES)}")
        print("       python3 strava-upload.py --parse-only <fit文件>  # 仅解析输出JSON")
        print("       python3 strava-upload.py <fit文件> --correct-coords  # 上传前纠偏坐标")
        sys.exit(1)

    print(f"[*] 解析 FIT 文件: {args.fit_file}")
    d = parse_fit(args.fit_file)

    # 自动确定 activity_type
    activity_type = args.activity_type if args.activity_type else d["sport"]
    if activity_type not in ACTIVITY_TYPES:
        activity_type = d["sport"] if d["sport"] in ACTIVITY_TYPES else "Ride"

    # 手动传入的标题优先，否则自动生成
    title = args.title.strip() if args.title else get_title(d)
    desc = get_description(d)

    print(f"\n[*] 智能分析结果:")
    print(f"    运动类型: {activity_type} ({d['sub_sport']})")
    print(f"    标题: {title}")
    print(f"    描述:\n{desc}")

    # 坐标纠偏（如需要）
    fit_path = args.fit_file
    if args.correct_coords:
        # 07-06 修复：如果当前 fit_path 已经有 .orig 备份（即文件已经是纠偏版），跳过纠偏
        # 避免重复纠偏把 WGS-84 当 GCJ-02 反算导致 825m 偏西
        orig_marker = fit_path + '.orig'
        already_corrected = os.path.exists(orig_marker)
        if already_corrected:
            print(f"\n[*] 检测到 .orig 备份（{orig_marker}），跳过纠偏（已是 WGS-84）")
        else:
            print(f"\n[*] 正在进行 GCJ-02 → WGS-84 坐标纠偏...")
            try:
                corrected = correct_fit_file(fit_path)
                # 原地替换，下次直接用纠偏后文件
                import shutil
                tmp = fit_path + '.tmp'
                shutil.move(corrected, tmp)
                shutil.move(fit_path, orig_marker)
                shutil.move(tmp, fit_path)
                print(f"    纠偏完成: {fit_path}")
            except Exception as e:
                print(f"    纠偏失败: {e}，使用原始文件继续...")

    print(f"\n[*] 开始上传...")

    # 优先使用缓存 token，未过期直接用
    tok = load_token()
    access_token = tok.get("access_token", "")
    expires_at = tok.get("expires_at", 0)
    import datetime
    now = time.time()
    if access_token and expires_at > now:
        print(f"[*] 使用缓存 Token（过期时间: {datetime.datetime.fromtimestamp(expires_at).strftime('%Y-%m-%d %H:%M')})")
    else:
        print("[*] Token 已过期，刷新中...")
        access_token = refresh_access_token()

    activity_id = upload_fit(access_token, fit_path, activity_type, title, desc)

    # 06-04 修订：如果用 --correct-coords 纠偏上传，纠偏产物 FIT 的 speed/distance 等 scaled 字段
    # 会被 garmin-fit-sdk Encoder 丢精度（fitdecode 给的是 scaled float，Encoder 写的是 unscaled int），
    # 上传到 Strava 后 description 里"平均速度/最高速度"会显示成 30.7/43.2 而不是顽鹿显示的 32.6/46.7。
    # 解决：纠偏脚本把原文件备份成 .orig，这里用 .orig 重新 parse_fit + get_description，PATCH 活动。
    orig_path = fit_path + '.orig'
    if activity_id and args.correct_coords and os.path.exists(orig_path):
        try:
            d_orig = parse_fit(orig_path)
            desc_orig = get_description(d_orig)
            patch_resp = requests.patch(
                f"https://www.strava.com/api/v3/activities/{activity_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"description": desc_orig},
                timeout=15,
            )
            if patch_resp.status_code == 200:
                print(f"[√] description 已用 .orig 原始数据 PATCH（速度/距离精度修复）")
            else:
                print(f"[!] description PATCH 失败: HTTP {patch_resp.status_code}")
        except Exception as e:
            print(f"[!] description PATCH 异常: {e}")


if __name__ == "__main__":
    main()
