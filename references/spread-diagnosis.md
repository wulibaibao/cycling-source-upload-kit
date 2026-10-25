# Spread 诊断：真实长路线 vs GPS 漂移（2026-06-10 整合）

**为什么需要这个 reference：** 旧 SKILL 误判案例：05-23 骑行 spread lon=0.43°（约 40 km）被标为「严重漂移（坐标飞掉）」「需 Encoder 重建」，但实测**它是真实 38 km 往返路线**。仅看 spread 大小不能区分「漂移」和「真实长路线」。

## 核心判断

| 表现 | 结论 | 处理方式 |
|------|------|---------|
| spread 正常（lon < 0.1°，约 <11 km） | 城区短路线，正常 | 直接上传 |
| spread 大（lon > 0.3°，约 >30 km），**chunk 中位单向往返**（先涨到 peak 再降回起点附近） | 真实长距离路线 | 直接上传（路线就这样） |
| spread 大（lon > 0.3°），**chunk 中位有显著「断层」（前段恒定、后段恒定、跳变 >0.05°）** | 真漂移（两段式 / 设备漂移） | 用 `scripts/correct_gps.py` 修复偏移段或 GPX 中介法 |
| FIT 坐标 > 50° lat 或 > 150° lon | 设备完全失锁 | Encoder 重建 |

## 关键诊断：滚动 chunk 中位对比

**做法：** 把 records 分成 N 个等长 chunk（推荐 500 records/chunk），看每个 chunk 的 lon 中位变化模式：

```python
def rolling_chunk_median(records, field, chunk_size=500):
    n = len(records)
    out = []
    for i in range(0, n, chunk_size):
        chunk = records[i:i+chunk_size]
        vals = [semicircles_to_deg(r[field]) for r in chunk if field in r]
        if vals:
            out.append((i, min(vals), max(vals), sorted(vals)[len(vals)//2]))
    return out
```

## 案例对比

### 案例 1：05-23（真实长路线，**旧版误判**）

```
records: 14846, distance: 106.8 km
Lat range: 34.2451 ~ 34.3336  (spread 0.0885°)
Lon range: 108.8482 ~ 109.2813 (spread 0.4332°)
```

滚动 chunk 中位（每 500 records）：

```
[    0-  500] med 108.86   ← 起点（西安）
[ 4500- 5000] med 109.16   ← 单向往东 33 km
[ 8500- 9000] med 109.28   ← peak（最远 38 km）
[13500-14000] med 108.93   ← 返程回到原点附近
[14500-15000] med 108.86   ← 终点（西安）
```

**模式：** 单调上升到 peak，然后单调下降到起点（**经典往返路线**）→ **真实长距离路线**

### 案例 2：05-30（两段式漂移，已用 GPX 中介法修）

```
records: 11960, distance: 144.8 km
Lat range: 34.20 ~ 34.43   (spread 0.23°)
Lon range: 108.55 ~ 109.12 (spread 0.57°)
```

滚动 chunk 中位（简略）：

```
前 79% records: lon 范围 108.55~109.12（已偏东 0.22°~0.48°）
后 21% records: lon 范围 108.85~109.00（回到正常）
```

**模式：** 前段恒定偏移 + 后段突然回到正常（**段间跳变，无过渡**）→ **两段式漂移**

### 案例 3：05-15 / 06-01~06-10（正常短路线）

```
records: ~6000
Lon spread: 0.057° ~ 0.089°（约 6-10 km，全在西安城区）
```

**模式：** chunk 中位在小范围内波动，无单调趋势 → **正常**

## 决策算法

```python
def diagnose_route(records, chunk_size=500):
    rolling = rolling_chunk_median(records, 'position_long', chunk_size)
    if not rolling: return 'unknown', 0
    
    lon_meds = [c[3] for c in rolling]
    lon_spread = max(lon_meds) - min(lon_meds)
    
    if lon_spread < 0.1:
        return 'normal', lon_spread
    
    peak_idx = lon_meds.index(max(lon_meds))
    pre = lon_meds[:peak_idx]
    post = lon_meds[peak_idx+1:]
    
    # 判断「先涨到 peak 再降回来」模式（容许 1-2 步抖动）
    def mostly_increasing(seq):
        return sum(1 for i in range(len(seq)-1) if seq[i] <= seq[i+1]) >= len(seq) * 0.7
    
    def mostly_decreasing(seq):
        return sum(1 for i in range(len(seq)-1) if seq[i] >= seq[i+1]) >= len(seq) * 0.7
    
    going_up = mostly_increasing(pre) if pre else True
    coming_back = mostly_decreasing(post) if post else True
    
    if going_up and coming_back and lon_spread > 0.3:
        return 'real_long_route', lon_spread
    else:
        return 'drift', lon_spread
```

**05-23 实测：** `('real_long_route', 0.4202)` ✅

## 与既有 spread 对比的关系

`references/two-segment-gps-drift.md` 已经有 spread 对比法（spread > 3× 参考路线 = 路线本身远）。这是**另一个维度**的判断：

- **既有 spread 对比**：spread 与"同一个人、同时段参考路线"对比（需要历史 baseline）
- **滚动 chunk 中位**（本文）：只看自己这条记录的 chunk 中位趋势（不需要 baseline）

两者结论应一致，但**滚动 chunk 方法更普适**（不需要先有参考数据）。

## 速查表

| 场景 | chunk 中位模式 | spread | 结论 |
|------|---------------|--------|------|
| 西安城区 50 km 短路线 | 波动 < 0.1° | 小 | 正常 |
| 西安 → 临潼 100 km 往返 | 单调上升 → peak → 单调下降 | 大 (0.4°+) | 真实长路线 |
| 半路 GPS 失锁重锁 | 前段恒定 + 后段恒定 + 段间跳变 | 大 | 两段式漂移 |
| 全程信号差 | chunk 中位乱跳 | 中-大 | 设备漂移 |

## 工具

- `scripts/rolling_chunk_diagnose.py` —— 一键诊断脚本
- `scripts/detect_gcj02_offset.py` —— OSM 路网偏差分析（确诊 GCJ-02 用，对 spread 大但路线正常的情况是过度诊断，不需要跑）
