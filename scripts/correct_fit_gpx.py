#!/usr/bin/env python3
"""
GPX 中介法（2026-06-04 实测通过）：fitdecode 读原 FIT → 纠偏 GCJ-02 坐标 → garmin-fit-sdk Encoder 写新 FIT。
只处理 file_id / user_profile / bike_profile / record / lap / session / activity 7 种核心消息。
其他消息类型（device_info / monitoring / event / hrv / weather_conditions 等）全部跳过——Strava 仍能正确解析。

用法: python3 correct_fit_gpx.py <input.fit> <output.fit>

实测（06-04 早的 MAGENE C706 FIT）：
- 输入 223386 字节（12028 messages），输出 210799 字节（6004 messages）
- 5988 records × 2 coords = 11976 坐标成功纠偏
- fitparse 验证通过，Strava 上传成功
"""
import sys
import math
from datetime import datetime, timezone

import fitdecode
from garmin_fit_sdk import Encoder
from garmin_fit_sdk.profile import Profile

# 修复 Profile str-key bug（5/30 实战经验）
Profile['messages'] = {int(k): v for k, v in Profile['messages'].items()}

EARTH_R = 6378137.0
EE = 0.00669342162296594323


def out_of_china(lat, lng):
    if lng < 72.004 or lng > 137.8347: return True
    if lat < 0.8293 or lat > 55.8271: return True
    return False


def _transform(x, y):
    xy = x * y
    abs_x = math.sqrt(abs(x))
    x_pi = x * math.pi
    y_pi = y * math.pi
    d = 20.0 * math.sin(6.0 * x_pi) + 20.0 * math.sin(2.0 * x_pi)
    lat = d
    lng = d
    lat += 20.0 * math.sin(y_pi) + 40.0 * math.sin(y_pi / 3.0)
    lng += 20.0 * math.sin(x_pi) + 40.0 * math.sin(x_pi / 3.0)
    lat += 160.0 * math.sin(y_pi / 12.0) + 320.0 * math.sin(y_pi / 30.0)
    lng += 150.0 * math.sin(x_pi / 12.0) + 300.0 * math.sin(x_pi / 30.0)
    lat *= 2.0 / 3.0
    lng *= 2.0 / 3.0
    lat += -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * xy + 0.2 * abs_x
    lng += 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * xy + 0.1 * abs_x
    return [lat, lng]


def _delta(lat, lng):
    t = _transform(lng - 105.0, lat - 35.0)
    d_lat = t[0]
    d_lng = t[1]
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((EARTH_R * (1 - EE)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (EARTH_R / sqrt_magic * math.cos(rad_lat) * math.pi)
    return [d_lat, d_lng]


def gcj2wgs(gcj_lat, gcj_lng):
    if out_of_china(gcj_lat, gcj_lng): return (gcj_lat, gcj_lng)
    new_lat, new_lng = gcj_lat, gcj_lng
    for _ in range(30):
        old_lat, old_lng = new_lat, new_lng
        d = _delta(new_lat, new_lng)
        new_lat = gcj_lat - d[0]
        new_lng = gcj_lng - d[1]
        if max(abs(old_lat - new_lat), abs(old_lng - new_lng)) < 1e-6: break
    return (new_lat, new_lng)


def semi_to_deg(s):
    return s * (180.0 / 2**31)


def deg_to_semi(d):
    return int(round(d * (2**31 / 180.0)))


FIT_EPOCH = datetime(1989, 12, 31, 0, 0, 0, tzinfo=timezone.utc)


def fit_ts(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int((dt - FIT_EPOCH).total_seconds())


# 哪些消息哪些字段是坐标
COORD_FIELDS = {
    'record':         ('position_lat', 'position_long'),
    'session':        ('start_position_lat', 'start_position_long',
                        'end_position_lat',   'end_position_long',
                        'nec_lat', 'nec_long', 'swc_lat', 'swc_long'),
    'lap':            ('start_position_lat', 'start_position_long',
                        'end_position_lat',   'end_position_long'),
}


# 消息名 → mesg_num 映射
MESG_NUM = {
    'file_id': 0, 'user_profile': 3, 'bike_profile': 6,
    'session': 18, 'lap': 19, 'record': 20,
    'activity': 34,
}

HANDLED = set(MESG_NUM.keys())


def try_int(v):
    if v is None: return None
    try: return int(v)
    except (ValueError, TypeError): return None


def correct_coords_inplace(d, coord_keys):
    """对 d 中在 coord_keys 里的字段做 (lat,lon) 配对 gcj2wgs，返回纠偏对数"""
    fixed = 0
    pairs = []
    used = set()
    for fname in list(d.keys()):
        if fname in coord_keys and fname.endswith('_lat') and fname not in used:
            base = fname[:-4]
            lon_name = base + '_long'
            if lon_name in d:
                pairs.append((fname, lon_name, base))
                used.add(fname)
                used.add(lon_name)
    if 'position_lat' in d and 'position_long' in d and ('position_lat', 'position_long', 'position') not in pairs:
        pairs.append(('position_lat', 'position_long', 'position'))
    for lat_f, lon_f, _ in pairs:
        lat_v = d[lat_f]
        lon_v = d[lon_f]
        lat_deg = semi_to_deg(lat_v)
        lon_deg = semi_to_deg(lon_v)
        if out_of_china(lat_deg, lon_deg):
            continue
        wgs_lat, wgs_lon = gcj2wgs(lat_deg, lon_deg)
        d[lat_f] = deg_to_semi(wgs_lat)
        d[lon_f] = deg_to_semi(wgs_lon)
        fixed += 2
    return fixed


def main():
    if len(sys.argv) < 3:
        print('用法: python3 correct_fit_gpx.py <input.fit> <output.fit>')
        sys.exit(1)
    in_path, out_path = sys.argv[1], sys.argv[2]

    print(f'Reading {in_path} ...')
    with fitdecode.FitReader(in_path) as fit:
        all_frames = list(fit)

    encoder = Encoder()
    records_written = 0
    coord_corrected = 0
    skipped = 0
    FIT_FRAME_DATA = fitdecode.FIT_FRAME_DATA

    for frame in all_frames:
        if frame.frame_type != FIT_FRAME_DATA:
            continue

        mesg_type = frame.name
        if mesg_type not in HANDLED:
            skipped += 1
            continue

        d = {'mesg_num': MESG_NUM[mesg_type]}

        for f in frame.fields:
            if f.value is None:
                continue

            if f.name == 'time_created' and isinstance(f.value, datetime):
                d['time_created'] = fit_ts(f.value)
                continue

            if f.name in ('timestamp', 'start_time', 'local_timestamp') and isinstance(f.value, datetime):
                d[f.name] = fit_ts(f.value)
                continue

            v = try_int(f.value)
            if v is not None:
                d[f.name] = v

        coord_corrected += correct_coords_inplace(d, COORD_FIELDS.get(mesg_type, ()))

        if len(d) > 1:
            try:
                encoder.write_mesg(d)
                if mesg_type == 'record':
                    records_written += 1
            except ValueError as e:
                print(f'  跳过 {mesg_type}（字段不识别）: {e}')

    data = encoder.close()

    with open(out_path, 'wb') as f:
        f.write(data)

    print(f'Done. Wrote {records_written} records, corrected {coord_corrected} coords, skipped {skipped} other msgs')
    print(f'-> {out_path} ({len(data)} bytes)')


if __name__ == '__main__':
    main()
