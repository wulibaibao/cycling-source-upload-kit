# FIT GPS 质量快速检查脚本

用于在上传 Strava 前检查 FIT 文件的 GPS 坐标质量，避免上传漂移文件。

## 使用方法

```bash
python3 ~/skills/productivity/strava-upload/references/check_fit_gps.py <file.fit>
```

## 脚本内容

```python
#!/usr/bin/env python3
"""快速检查 FIT 文件 GPS 坐标质量"""
import sys
import fitdecode.reader

def semicircles_to_deg(s):
    return s / (2**32 / 360)

def check_gps_quality(path):
    lats, lons, distances = [], [], []
    times = []
    
    with fitdecode.FitReader(path) as fit:
        for chunk in fit:
            if isinstance(chunk, fitdecode.FitDataMessage) and chunk.mesg_type.name == 'record':
                d = {f.name: f.value for f in chunk.fields}
                lat = d.get('position_lat')
                lon = d.get('position_long')
                if lat is not None and lon is not None:
                    lats.append(lat)
                    lons.append(lon)
                if 'timestamp' in d and d['timestamp'] is not None:
                    times.append(d['timestamp'])
                if 'distance' in d and d['distance'] is not None:
                    distances.append(d['distance'])
    
    if not lats:
        print('❌ 无 GPS 数据')
        return
    
    lat_deg = [semicircles_to_deg(x) for x in lats]
    lon_deg = [semicircles_to_deg(x) for x in lons]
    
    total_dist_km = max(distances) / 1000 if distances else 0
    lat_spread = max(lat_deg) - min(lat_deg)
    lon_spread = max(lon_deg) - min(lon_deg)
    
    print(f'📊 GPS 质量检查: {path}')
    print(f'   GPS 点数: {len(lats)}')
    print(f'   距离: {total_dist_km:.2f} km')
    print(f'   Lat: {min(lat_deg):.4f} ~ {max(lat_deg):.4f} (spread={lat_spread:.6f}°)')
    print(f'   Lon: {min(lon_deg):.4f} ~ {max(lon_deg):.4f} (spread={lon_spread:.6f}°)')
    
    # 判断标准
    xi_an_lat = (33.5, 35.5)
    xi_an_lon = (108.0, 110.0)
    anomalous = sum(1 for la, lo in zip(lat_deg, lon_deg) 
                    if not (xi_an_lat[0] <= la <= xi_an_lat[1] and xi_an_lon[0] <= lo <= xi_an_lon[1]))
    
    if anomalous > 0:
        print(f'   ❌ 漂移: {anomalous} 个异常点（不在西安城区）')
        return False
    elif max(lat_deg) > 50 or max(lon_deg) > 150:
        print(f'   ❌ 全失锁: 坐标飞到 ({max(lat_deg):.1f}, {max(lon_deg):.1f})')
        return False
    else:
        print(f'   ✅ 干净: 全部 {len(lats)} 个点在西安城区范围')
        return True

if __name__ == '__main__':
    ok = check_gps_quality(sys.argv[1])
    sys.exit(0 if ok else 1)
```

## 关键判断阈值

| 情况 | 判断条件 |
|------|---------|
| GPS 全失锁 | lat > 50° 或 lon > 150° |
| 漂移点 | 有点不在 (33.5°~35.5°N, 108°~110°E) |
| GPS 干净 | 所有点均在西安城区范围，且无异常聚类 |

## fitdecode 注意事项

- `fitdecode.FitFile` 不存在 → 用 `fitdecode.FitReader`
- `FitDataMessage.fields` 属性需用 `msg.fields`（字段对象列表），每个字段 `.name` 是字符串，`.value` 是原始值
- `position_lat/long` 是 **semicircles 整数值**，不是 degrees