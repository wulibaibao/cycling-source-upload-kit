#!/usr/bin/env python3
"""
滚动 chunk 中位诊断：区分「真实长距离路线」vs「GPS 漂移」（2026-06-10 新增）

用法：
    python3 rolling_chunk_diagnose.py <file.fit> [--chunk-size N]

输出：
    - records 总数、距离、lon/lat 范围与 spread
    - 每 chunk 的 lon min/max/median 表格
    - 诊断结论：normal / real_long_route / drift
    - 决策建议

参考：references/spread-diagnosis.md
"""
import sys
import argparse
from fitdecode.reader import FitReader


def semicircles_to_deg(s):
    return s / (2**32 / 360)


def parse_records(fit_path):
    records = []
    with FitReader(fit_path) as fit:
        for block in fit:
            if type(block).__name__ == 'FitDataMessage' and block.mesg_type.name == 'record':
                r = {f.name: f.value for f in block.fields}
                records.append(r)
    return records


def rolling_chunk(records, field, chunk_size=500):
    n = len(records)
    out = []
    for i in range(0, n, chunk_size):
        chunk = records[i:i+chunk_size]
        vals = [semicircles_to_deg(r[field]) for r in chunk if field in r]
        if vals:
            out.append((i, i+len(chunk), min(vals), max(vals), sorted(vals)[len(vals)//2]))
    return out


def mostly_increasing(seq, threshold=0.7):
    if len(seq) < 2: return True
    return sum(1 for i in range(len(seq)-1) if seq[i] <= seq[i+1]) >= (len(seq) - 1) * threshold


def mostly_decreasing(seq, threshold=0.7):
    if len(seq) < 2: return True
    return sum(1 for i in range(len(seq)-1) if seq[i] >= seq[i+1]) >= (len(seq) - 1) * threshold


def diagnose(records, chunk_size=500):
    lons_rolling = rolling_chunk(records, 'position_long', chunk_size)
    lats_rolling = rolling_chunk(records, 'position_lat', chunk_size)
    if not lons_rolling:
        return 'unknown', 'no position data'

    lon_meds = [c[4] for c in lons_rolling]
    lat_meds = [c[4] for c in lats_rolling]
    lon_spread = max(lon_meds) - min(lon_meds)
    lat_spread = max(lat_meds) - min(lat_meds)

    if lon_spread < 0.1:
        return 'normal', f'spread {lon_spread:.4f}° < 0.1°，城区短路线'

    peak_idx = lon_meds.index(max(lon_meds))
    pre = lon_meds[:peak_idx]
    post = lon_meds[peak_idx+1:]

    going_up = mostly_increasing(pre) if pre else True
    coming_back = mostly_decreasing(post) if post else True

    if going_up and coming_back and lon_spread > 0.3:
        return 'real_long_route', (
            f'spread {lon_spread:.4f}° (约 {lon_spread*111:.0f} km)，'
            f'peak @ chunk {peak_idx}，前段单调上升+后段单调下降 → 真实长距离路线'
        )
    elif not going_up or not coming_back:
        return 'drift', (
            f'spread {lon_spread:.4f}°，前段 going_up={going_up} 后段 coming_back={coming_back} '
            f'→ 可能是两段式漂移或设备漂移'
        )
    else:
        return 'normal_or_short', f'spread {lon_spread:.4f}° 在 0.1-0.3° 之间，建议结合具体场景判断'


def main():
    parser = argparse.ArgumentParser(description='滚动 chunk 中位诊断：真实长路线 vs GPS 漂移')
    parser.add_argument('fit_path', help='.fit 文件路径')
    parser.add_argument('--chunk-size', type=int, default=500, help='每 chunk records 数（默认 500）')
    args = parser.parse_args()

    print(f'Reading {args.fit_path} ...')
    records = parse_records(args.fit_path)
    print(f'  {len(records)} records')

    if not records:
        print('无 record 数据，退出')
        sys.exit(1)

    distances = [r.get('distance', 0) for r in records if r.get('distance')]
    if distances:
        print(f'  距离: {max(distances)/1000:.1f} km')

    lons_rolling = rolling_chunk(records, 'position_long', args.chunk_size)
    lats_rolling = rolling_chunk(records, 'position_lat', args.chunk_size)

    lon_meds = [c[4] for c in lons_rolling]
    lat_meds = [c[4] for c in lats_rolling]
    print(f'  Lon spread: {max(lon_meds)-min(lon_meds):.4f}° ({(max(lon_meds)-min(lon_meds))*111:.1f} km)')
    print(f'  Lat spread: {max(lat_meds)-min(lat_meds):.4f}° ({(max(lat_meds)-min(lat_meds))*111:.1f} km)')
    print()

    print(f'滚动 chunk 中位（chunk_size={args.chunk_size}）：')
    print(f'  {"chunk":<14} {"lon min":<10} {"lon max":<10} {"lon med":<10} {"lat med":<10}')
    for i, (start, end, lon_min, lon_max, lon_med) in enumerate(lons_rolling):
        lat_med = lats_rolling[i][4] if i < len(lats_rolling) else 0
        marker = ' ← peak' if lon_med == max(lon_meds) else ''
        print(f'  [{start:5d}-{end:5d}] {lon_min:.4f}    {lon_max:.4f}    {lon_med:.4f}    {lat_med:.4f}{marker}')

    print()
    diagnosis, reason = diagnose(records, args.chunk_size)
    print(f'诊断结论: {diagnosis}')
    print(f'  {reason}')
    print()
    if diagnosis == 'real_long_route':
        print('建议: 直接上传（路线就这样，不是漂移）')
    elif diagnosis == 'drift':
        print('建议: 用 scripts/correct_gps.py 修复偏移段，或上 GPX 中介法')
        print('       参考 references/two-segment-gps-drift.md + references/fit-method-comparison.md')
    elif diagnosis == 'normal':
        print('建议: 直接上传')
    else:
        print('建议: 进一步人工判断（spread 边界值）')


if __name__ == '__main__':
    main()