#!/usr/bin/env python3
"""
OSM 双向验证：GPS 是 WGS-84 还是 GCJ-02？

用法：
    python3 detect_gcj02_offset.py <file.fit>

输出：
    - GPS 当 WGS-84 vs WGS-84 路网中位偏差
    - GPS 当 GCJ-02 vs GCJ-02 路网中位偏差
    - 结论：GPS 是 WGS-84 / GCJ-02 / 漂移

原理：下载路线 bbox 内所有 highway 路网 → 同时生成 WGS-84 和 GCJ-02 两个版本 →
把所有 GPS 点跟两个版本各投影一次 → 如果"GPS 当 GCJ-02"偏差骤降到 < 10m → 确诊。

依赖：
    pip install shapely requests --break-system-packages
    不需要 osmnx / matplotlib
"""
import sys, math, json, requests, statistics
from shapely.geometry import LineString, Point
import shapely.ops as so

PI = math.pi; A = 6378245.0; EE = 0.00669342162296594323


def transform_lat(x, y):
    ret = -100.0 + 2.0*x + 3.0*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x))
    ret += (20.0*math.sin(6.0*x*PI) + 20.0*math.sin(2.0*x*PI)) * 2.0/3.0
    ret += (20.0*math.sin(y*PI) + 40.0*math.sin(y/3.0*PI)) * 2.0/3.0
    ret += (160.0*math.sin(y/12.0*PI) + 320*math.sin(y*PI/30.0)) * 2.0/3.0
    return ret


def transform_lng(x, y):
    ret = 300.0 + x + 2.0*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
    ret += (20.0*math.sin(6.0*x*PI) + 20.0*math.sin(2.0*x*PI)) * 2.0/3.0
    ret += (20.0*math.sin(x*PI) + 40.0*math.sin(x/3.0*PI)) * 2.0/3.0
    ret += (150.0*math.sin(x/12.0*PI) + 300.0*math.sin(x/30.0*PI)) * 2.0/3.0
    return ret


def gcj02_to_wgs84(lng, lat):
    if lng < 72.004 or lng > 137.8347 or lat < 0.8293 or lat > 55.8271:
        return lng, lat
    dlat = transform_lat(lng-105.0, lat-35.0)
    dlng = transform_lng(lng-105.0, lat-35.0)
    radlat = lat/180.0*PI
    magic = math.sin(radlat); magic = 1 - EE*magic*magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat*180.0)/((A*(1-EE))/sqrtmagic*PI)
    dlng = (dlng*180.0)/((A/sqrtmagic)*math.cos(radlat)*PI)
    return lng - dlng, lat - dlat


def wgs84_to_gcj02(lng, lat):
    if lng < 72.004 or lng > 137.8347 or lat < 0.8293 or lat > 55.8271:
        return lng, lat
    dlat = transform_lat(lng-105.0, lat-35.0)
    dlng = transform_lng(lng-105.0, lat-35.0)
    radlat = lat/180.0*PI
    magic = math.sin(radlat); magic = 1 - EE*magic*magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat*180.0)/((A*(1-EE))/sqrtmagic*PI)
    dlng = (dlng*180.0)/((A/sqrtmagic)*math.cos(radlat)*PI)
    return lng + dlng, lat + dlat


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1); dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(a))


def extract_points(path):
    import fitdecode
    conv = 180/(2**31)
    pts = []
    with fitdecode.FitReader(path) as fit:
        for chunk in fit:
            if isinstance(chunk, fitdecode.FitDataMessage) and chunk.mesg_type.name == 'record':
                d = {f.name: f.value for f in chunk.fields}
                if d.get('position_lat') is not None and d.get('position_long') is not None:
                    pts.append((d['position_lat']*conv, d['position_long']*conv))
    return pts


def get_osm(bbox):
    """用 overpass.kumi.systems（overpass-api.de 经常 406）"""
    query = f'''[out:json][timeout:60];
(
  way["highway"~"^(primary|secondary|tertiary|residential|unclassified|track|path|cycleway|trunk|motorway|living_street|service)$"]({bbox});
);
out geom;'''
    for server in ['https://overpass.kumi.systems/api/interpreter',
                   'https://overpass.osm.ch/api/interpreter']:
        try:
            r = requests.post(server, data={'data': query}, timeout=120,
                              headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
            if r.status_code == 200: return r.json()
        except Exception as e:
            print(f'  {server} 失败: {e}', file=sys.stderr)
    return None


def main():
    if len(sys.argv) < 2:
        print('用法: python3 detect_gcj02_offset.py <file.fit>')
        sys.exit(1)
    fit_path = sys.argv[1]
    print(f'📂 {fit_path}')

    print('提取 GPS 点...')
    pts = extract_points(fit_path)
    if not pts:
        print('❌ 无 GPS 数据')
        sys.exit(1)
    print(f'  {len(pts)} 个点')

    lats = [p[0] for p in pts]; lons = [p[1] for p in pts]
    print(f'  Lat: {min(lats):.4f} ~ {max(lats):.4f}')
    print(f'  Lon: {min(lons):.4f} ~ {max(lons):.4f}')

    # 设备漂移检查
    if max(lats) > 50 or min(lats) < -50 or max(lons) > 180 or min(lons) < -180:
        print('❌ 设备漂移（坐标异常），不属于 GCJ-02 问题')
        sys.exit(1)

    bbox = f'{min(lats):.6f},{min(lons):.6f},{max(lats):.6f},{max(lons):.6f}'
    print(f'下载 OSM 路网 (bbox {bbox})...')
    data = get_osm(bbox)
    if not data:
        print('❌ OSM 下载失败')
        sys.exit(1)
    roads_wgs = []
    for el in data['elements']:
        if el.get('type') == 'way' and 'geometry' in el and len(el['geometry']) >= 2:
            coords = [(p['lon'], p['lat']) for p in el['geometry']]
            roads_wgs.append(LineString(coords))
    print(f'  {len(roads_wgs)} 个 way')

    # 同步生成 GCJ-02 版本路网
    roads_gcj = [LineString([wgs84_to_gcj02(x, y) for x, y in r.coords]) for r in roads_wgs]
    all_wgs = so.unary_union(roads_wgs)
    all_gcj = so.unary_union(roads_gcj)

    # 抽样测试（每 10 个点取 1 个加速）
    sample = pts[::10]
    offs_wgs = []; offs_gcj = []
    for lat, lon in sample:
        pt = Point(lon, lat)
        n1 = all_wgs.interpolate(all_wgs.project(pt))
        offs_wgs.append(haversine(lat, lon, n1.y, n1.x))
        n2 = all_gcj.interpolate(all_gcj.project(pt))
        offs_gcj.append(haversine(lat, lon, n2.y, n2.x))

    med_wgs = statistics.median(offs_wgs)
    med_gcj = statistics.median(offs_gcj)
    p95_wgs = sorted(offs_wgs)[int(len(offs_wgs)*0.95)]
    p95_gcj = sorted(offs_gcj)[int(len(offs_gcj)*0.95)]

    print()
    print('=' * 60)
    print(f'GPS 当 WGS-84 vs WGS-84 路网:  中位 {med_wgs:.1f}m, P95 {p95_wgs:.0f}m')
    print(f'GPS 当 GCJ-02 vs GCJ-02 路网:  中位 {med_gcj:.1f}m, P95 {p95_gcj:.0f}m')
    print('=' * 60)

    if med_gcj < 10 and med_wgs > 30:
        print('🟢 确诊: GPS 输出 GCJ-02（中国国测局火星坐标）')
        print(f'   偏移量 {med_wgs:.0f}m，方向大致东偏南')
        print('   修复: 用 --correct-coords 参数上传，或用 fit-tool 重建 FIT')
    elif med_wgs < 30:
        print('🟢 GPS 输出 WGS-84，路网偏差正常')
        if med_wgs < 10:
            print('   几乎完全贴路网，无需修复')
    else:
        # 双向都不好
        ratio = med_wgs / med_gcj if med_gcj > 0 else 1
        if ratio > 3:
            print(f'🟡 可能 GCJ-02 偏移（双向比率 {ratio:.1f}x）但不完全典型')
            print('   建议做更精细分析（滑动窗检查是否全程均匀偏）')
        else:
            print('🟡 双向偏差都较大，可能不是简单的坐标系问题')
            print('   建议检查 5min 滑动窗、孤立跳点、是否走野路')

    # 5min 滑动窗检查（粗筛码表漂移）
    print()
    print('5min 滑动窗偏差分布（粗筛漂移/跳点）:')
    WINDOW = 1500
    bad_windows = 0
    for i in range(0, len(pts) - WINDOW, 100):
        chunk = pts[i:i+WINDOW]
        m = statistics.mean(haversine(*p, *p) for p in chunk)  # 占位
        # 重算窗口内所有点到 WGS-84 路网距离
        offs = []
        for lat, lon in chunk:
            pt = Point(lon, lat)
            n = all_wgs.interpolate(all_wgs.project(pt))
            offs.append(haversine(lat, lon, n.y, n.x))
        m = statistics.mean(offs)
        flag = '⚠️' if m > 150 else '  '
        if m > 150: bad_windows += 1
        if i % 1500 == 0:  # 每 5min 打印一次
            print(f'  [{i:>5}] 5min 均值 {m:>5.0f}m {flag}')

    if bad_windows > 0:
        print(f'  ⚠️ {bad_windows} 个 5min 窗口均值 > 150m → 存在持续偏/漂移')


if __name__ == '__main__':
    main()
