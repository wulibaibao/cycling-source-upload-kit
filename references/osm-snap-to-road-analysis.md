# OSM 路网偏差分析（识别「真 GPS 偏」vs「路网盲区」）

**适用场景：** FIT 坐标看起来正常（不在 50°N / 150°E 那种飞点区），但 Strava 地图上的路线**整体偏 100-500m** 或**局部段偏 200m+**，想判断**到底是 GPS 真偏了还是用户走了路网没覆盖的小路**。

**首次使用日期：** 2026-06-01（05-30 骑行复盘时首次实战）

---

## 核心思路

1. 从原始 FIT 提取所有 record 的 lat/lon（semicircles → degrees）
2. 用 **OSM Overpass API** 下载路线 bounding box 内所有 highway 类型的道路中心线
3. 用 **Shapely** 把路网 union 成 MultiLineString
4. 每个 GPS 点用 `line.interpolate(line.project(point))` 找最近路网点
5. **haversine 算距离**（不要用欧氏距离，经纬度算米数要 ×111000）
6. 画偏差分布（按时间分窗、看是不是连续偏还是孤立跳点）

---

## 完整脚本（已验证，05-30 案例跑通）

### 1. 装依赖（注意 venv 位置）

```bash
# 当前环境 venv 在 ~/hermes-agent/venv/
# 不能用系统的 pip，否则装到 ~/.local/ 找不到
python3 -m pip install shapely --quiet
# osmnx 下载极慢（依赖太多），不装。直接用 Overpass HTTP API
# matplotlib 装不上时改用 ASCII 热力图
```

### 2. 提取 FIT GPS 点

```python
import fitdecode

conv = 180 / (2**31)  # semicircles → degrees
points = []
with fitdecode.FitReader('/path/to/file.fit') as fit:
    for chunk in fit:
        if isinstance(chunk, fitdecode.FitMessage) and chunk.mesg_type.name == 'record':
            d = {f.name: f.value for f in chunk.fields}
            if d.get('position_lat') is not None:
                points.append({
                    'lat': d['position_lat'] * conv,
                    'lon': d['position_long'] * conv,
                    'timestamp': d.get('timestamp'),
                })
```

### 3. 下载 OSM 路网（Overpass API）

⚠️ **关键：overpass-api.de 返回 HTTP 406（请求头问题），用 overpass.kumi.systems 替代**

```python
import requests, json

bbox = f'{min(lat):.6f},{min(lon):.6f},{max(lat):.6f},{max(lon):.6f}'
# ⛔️ 'https://overpass-api.de/api/interpreter' → HTTP 406
# ✅ 'https://overpass.kumi.systems/api/interpreter' → HTTP 200

query = f'''[out:json][timeout:60];
(
  way["highway"~"^(primary|secondary|tertiary|residential|unclassified|track|path|cycleway|trunk|motorway|living_street|service)$"]({bbox});
);
out geom;'''

r = requests.post(server, data={'data': query}, timeout=120,
                  headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
data = r.json()
# 8.4 MB JSON（9992 个 way）属于正常规模
```

### 4. 算每点到最近路网的距离

```python
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
import math

roads = []
for el in data['elements']:
    if el.get('type') == 'way' and 'geometry' in el:
        coords = [(p['lon'], p['lat']) for p in el['geometry']]
        if len(coords) >= 2:
            roads.append(LineString(coords))

all_roads = unary_union(roads)  # 合并成 MultiLineString

# haversine（米）
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(a))

# 慢但准：每点投影一次（17948 点约 150 秒）
import time
t0 = time.time()
for p in points:
    pt = Point(p['lon'], p['lat'])
    nearest = all_roads.interpolate(all_roads.project(pt))
    p['offset_m'] = haversine(p['lat'], p['lon'], nearest.y, nearest.x)
```

### 5. 偏差分析

```python
import statistics
offs = [p['offset_m'] for p in points]
print(f'中位: {statistics.median(offs):.0f}m, P95: {sorted(offs)[int(len(offs)*0.95)]:.0f}m')

# 5分钟滑动窗（100点/秒记录频率）
WINDOW = 1500  # 5min ≈ 1500 点
THRESHOLD = 200
for i in range(0, len(points) - WINDOW, 100):
    chunk = points[i:i+WINDOW]
    m = statistics.mean(p['offset_m'] for p in chunk)
    if m > 150:
        print(f'[{i}~{i+WINDOW}] 5min均值 {m:.0f}m 持续偏')
```

**关键判断模式：**
- **滑动窗均值均匀 > 150m**（不是孤立跳点） → **整段路网对不上**，需查路网密度 / 走的是野路
- **孤立跳点（前后都 < 50m，中间突然 > 1km）** → **GPS 跳点，直接删**
- **中位数 < 50m 但 P95 高** → 局部跳点 + 大部分正常，按"两段式漂移"处理

---

## 05-30 案例关键数据

| 指标 | 值 | 含义 |
|------|-----|------|
| 中位偏差 | 72m | 整体 GPS 偏离路网约 200m |
| P95 偏差 | 430m | 1% 的点离路网超 1 公里 |
| 最大偏差 | 21.8km | 1 个孤立跳点 |
| >100m 偏差占比 | 60.7% | **超过一半的点都偏 100m+** |
| **5min 滑动窗正常段** | **22:34-23:30 唯一 56 min** | 骑行 1 小时后**全段持续偏** |
| **5min 滑动窗全偏段** | **00:13-04:34 共 260 min** | 4 个多小时所有窗均值 > 150m |

**结论：**
- 起点 1 小时 GPS 准 → 码表锁星成功
- 1 小时后码表漂移 / 用户走了 OSM 没覆盖的小路
- **必须用顽鹿运动（OnelapFit）App 同步星历**（每次骑行前）+ 或者用顽鹿 GPX 当基准替换

---

## ASCII 热力图（matplotlib 装不上时用）

```python
seg_n = 60
seg_size = len(points) // seg_n
for i in range(seg_n):
    chunk = points[i*seg_size:(i+1)*seg_size]
    offs = sorted([p['offset_m'] for p in chunk])
    median = offs[len(offs)//2]
    p95 = offs[int(len(offs)*0.95)]
    intensity = min(1.0, median / 500)
    bar = '█' * int(intensity * 30)
    print(f'段{i+1:>3} 起始 {chunk[0]["timestamp"][:19]} 中位{median:>5.0f}m P95{p95:>5.0f}m {bar}')
```

---

## 已知坑

### 1. `overpass-api.de` 返回 HTTP 406
直接换 `overpass.kumi.systems` 或 `overpass.osm.ch`，不要浪费时间调试请求头。

### 2. matplotlib 装不上
改用 ASCII 热力图（上面脚本），效果对调试足够。

### 3. scipy 装不上（KDTree 加速用）
17948 点 × 9992 路网段，shapely 直接 `unary_union + project` 150 秒可接受，不用非要 KDTree。

### 4. `unary_union` 后类型变 MultiLineString
不影响 `.interpolate(.project())` 用法，可以直接当 LineString 用。

### 5. 经纬度顺序：shapely 用 `(lon, lat)`，不是 `(lat, lon)`
路网 coords、Point 创建都按 `(x=lon, y=lat)`。

### 6. haversine 不要用 shapely 的 `.distance()`
shapely `.distance()` 返回**度**，不是米。在 (34, 109) 附近 1° ≈ 100km，差 1000 倍。

---

## 下一步行动建议

根据偏差模式选方法：

| 模式 | 行动 |
|------|------|
| 整段持续偏 + 用户可能走了野路 | 提示用户用顽鹿/佳速度等 App 录同一段路，对比 GPS |
| 整段持续偏 + 用户确认走的是大路 | 怀疑码表锁星失败，提示先同步星历重录 |
| 孤立跳点（< 1% 点位） | fitdecode 删除跳点 record 段，重建 FIT |
| 中位 < 50m 但局部有 200-500m 段 | 两段式漂移 → 用 `references/two-segment-gps-drift.md` 的 `correct_gps.py` |
| 路网密度 < 0.5 km/km² | 路线在偏远地区，OSM 覆盖不全，**正常现象**（不是 GPS 问题） |

**前置检查（避免无意义分析）：**
- 起点 / 终点 lat 是否在 30-40°（中国），lon 是否在 100-125°？不在 → 设备漂移，已失锁，直接重录
- spread（lat_max - lat_min）是否 < 1°、lon spread < 1°？是 → 路线长度正常，可以做路网分析
- 路线总距离 < 200 km？超出 → 路网 bbox 太大，Overpass 查询超时，需手动缩小范围
