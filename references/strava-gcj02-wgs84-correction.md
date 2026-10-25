---
name: strava-upload-gcj02-wgs84-correction
description: 'GCJ-02 → WGS-84 坐标纠偏算法 + FIT 文件 patch 参考。算法部分（来自 strava_auto）正确；FIT 二进制 patch 部分有错误（2026-06-04 修复时确认），不要再按这里的 patch 代码实现'
license: MIT
---

# GCJ-02 → WGS-84 坐标纠偏 + FIT 文件 patch 参考

> ⚠️ **2026-06-04 重要修正**：本文档**算法部分**（GCJ-02 转换）正确；**FIT 二进制 patch 部分**有 3 处错误（CRC 多项式、CRC 范围、data message 结构理解）。**不要按这里的 patch 代码实现**——已用 GPX 中介法替代，详见 `references/correct-coords-gpx-mediation.md`。

## 背景

[xqdoo00o/strava_auto](https://github.com/xqdoo00o/strava_auto) 的核心功能：GPS 记录为 GCJ-02（中国国测局偏移坐标系）而非 WGS-84，Strava/OpenStreetMap 用 WGS-84 导致路线整体偏移。

本参考复刻其算法部分，并简要说明 FIT 二进制 patch 的设计（**仅供理解，实际实现已废弃**）。

---

## 算法部分：GCJ-02 → WGS-84（精确迭代法）✅ 正确

```python
import math

EARTH_R = 6378137.0
EE = 0.00669342162296594323  # 第一偏心率平方 (a²-b²)/a²

def outOfChina(lat, lng):
    """判断坐标是否在中国境外"""
    if lng < 72.004 or lng > 137.8347: return True
    if lat < 0.8293 or lat > 55.8271: return True
    return False

def _transform(x, y):
    """计算 GCJ→WGS 偏移量的辅助函数（多项式展开）"""
    xy = x * y
    absX = math.sqrt(abs(x))
    xPi = x * math.pi
    yPi = y * math.pi
    d = 20.0 * math.sin(6.0 * xPi) + 20.0 * math.sin(2.0 * xPi)
    lat = d
    lng = d
    lat += 20.0 * math.sin(yPi) + 40.0 * math.sin(yPi / 3.0)
    lng += 20.0 * math.sin(xPi) + 40.0 * math.sin(xPi / 3.0)
    lat += 160.0 * math.sin(yPi / 12.0) + 320.0 * math.sin(yPi / 30.0)
    lng += 150.0 * math.sin(xPi / 12.0) + 300.0 * math.sin(xPi / 30.0)
    lat *= 2.0 / 3.0
    lng *= 2.0 / 3.0
    lat += -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * xy + 0.2 * absX
    lng += 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * xy + 0.1 * absX
    return [lat, lng]

def _delta(lat, lng):
    """计算经纬度差值"""
    t = _transform(lng - 105.0, lat - 35.0)
    dLat = t[0]
    dLng = t[1]
    radLat = lat / 180.0 * math.pi
    magic = math.sin(radLat)
    magic = 1 - EE * magic * magic
    sqrtMagic = math.sqrt(magic)
    dLat = (dLat * 180.0) / ((EARTH_R * (1 - EE)) / (magic * sqrtMagic) * math.pi)
    dLng = (dLng * 180.0) / (EARTH_R / sqrtMagic * math.cos(radLat) * math.pi)
    return [dLat, dLng]

def gcj2wgs(gcjLat, gjclng):
    """GCJ-02 → WGS-84（迭代法，阈值 1e-6，最多 30 次）"""
    if outOfChina(gcjLat, gjclng): return [gcjLat, gjclng]
    newLat, newLng = gcjLat, gjclng
    for _ in range(30):
        oldLat, oldLng = newLat, newLng
        d = _delta(newLat, newLng)
        newLat = gcjLat - d[0]
        newLng = gjclng - d[1]
        if max(abs(oldLat - newLat), abs(oldLng - newLng)) < 1e-6: break
    return [newLat, newLng]
```

## FIT 坐标字段换算

```python
def semicircles_to_degrees(s):
    """FIT 半圆坐标 → 十进制度数"""
    return s * (180.0 / 2**31)

def degrees_to_semicircles(d):
    """十进制度数 → FIT 半圆坐标"""
    return int(d * (2**31 / 180.0))
```

---

## ❌ FIT 二进制 patch（**已废弃，2026-06-04**）

> **不要按这一节实现。** 06-04 实战证明自己实现 binary parser 风险极高。3 处错误详情见 `correct-coords-broken-2026-06-04.md`，本节仅供理解 FIT 文件结构。

### FIT 文件结构

```
14B header | data body (data_size bytes) | 2B CRC-16
```

Header 结构：
- `data[0]` — header_size (=14)
- `data[1]` — protocol version
- `data[2:4]` — profile version (little endian)
- `data[4:8]` — data size (little endian)
- `data[8:12]` — signature ".FIT"
- `data[12:14]` — header CRC (CRC of header[0:12])

### ❌ 错误：CRC 多项式

下面这段代码用的是 **0xEDB88320 + 256 元素 8-bit 表**——**这是错的**。FIT 用的是 **CRC-16 nibble-table**（4-bit，16 元素，表头 0xCC01）：

```python
# ❌ 错的（不要用）：
def _build_fit_crc_table():
    table = [0] * 256
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
        table[i] = crc
    return table
```

✅ **正确实现**（fitparse `fitparse.base.Crc`）：

```python
CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)

def fit_crc16(byte_arr, crc=0):
    for byte in byte_arr:
        tmp = CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ CRC_TABLE[byte & 0xF]
        tmp = CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ CRC_TABLE[(byte >> 4) & 0xF]
    return crc
```

### ❌ 错误：data message 结构理解

本节原描述说 "Data message 结构：1B header + 2B reserved + endian + 2B msg_num + 1B num_fields + N*3B field defs + Data"——**这是错的**。

data message 实际结构：
```
1B header（bit7=0，bit0-3 = local msg type，bit4 = has_timestamp）
[可选 4B compressed timestamp（如果 has_timestamp=1）]
数据（直接是字段值，没有 msg_num/num_fields 头）
```

`msg_num` 通过 local message type 引用**前一个** definition message 才知道。

错误地按 6B 头解析 data message 会**永远识别不出消息类型**，`_patch_fit_message` 的所有 `coord_fields` 分支都不匹配 → `if not coord_fields: return msg_bytes` → **整条消息原样返回，纠偏零效果**。

### ❌ 错误：CRC 范围混淆

CRC 双位置算的范围**不同**：
- `header[12:14]` = CRC(header[0:12])
- 文件末尾 2B = CRC(header[0:14] + body)——**header CRC 字段自身也算进去**（循环但合法）

很多实现把同一 CRC 写到两个位置，fitparse 会报 `CRC Mismatch`。

### 坐标字段映射（这部分正确，仅供参考）

| 消息类型 | 消息号 | 字段 def_num | 说明 |
|---------|-------|-------------|------|
| Record | 20 | 0=position_lat, 1=position_long | track point |
| Lap | 19 | 0/1=start_lat/lon, 2/3=end_lat/lon | 圈数据 |
| Session | 2 | 0/1=start, 4/5=nec, 6/7=swc | session |
| SegmentLap | 34 | 0/1/2/3 | segment lap |
| CoursePoint | 32 | 0/1 | 导航点 |
| SegmentPoint | 33 | 0/1 | segment point |

---

## gzip 压缩上传

strava_auto 用 gzip 压缩 FIT 后上传：
- Content-Type: `application/gzip`
- data_type: `fit.gz`
- 文件名：`<原名>.fit.gz`

```python
import gzip

with open(fit_path, 'rb') as f:
    raw_bytes = f.read()
compressed = gzip.compress(raw_bytes, compresslevel=6)

files = {"file": (gz_name, compressed, "application/gzip")}
data = {"name": title, "description": desc, "activity_type": at, "data_type": "fit.gz"}
resp = requests.post(UPLOAD_URL, headers=headers, data=data, files=files, timeout=60)
```

## 触发条件

| 情况 | 触发 | 实现 |
|------|------|------|
| Strava 路线偏 50-300m（非漂移） | 判断 outOfChina | 不需要修，是 Strava 地图匹配 |
| FIT 坐标明显 GCJ-02（lon 差 0.1°+）| 用 `--correct-coords` | **`correct_fit_file` (GPX 中介法，06-04 实现)** |
| GPS 彻底失锁（坐标飞到 68°N） | 需 Encoder 重建 | 见 fit-rebuild-encoder.md |

## 使用方式

```bash
# 仅解析（不上传）
python3 strava-upload.py --parse-only xxx.fit

# 上传（自动生成标题/描述，06-04 起默认加 --correct-coords）
python3 strava-upload.py xxx.fit --correct-coords

# 上传不纠偏（路线在 Strava 上偏 300-500m，特殊情况下用）
python3 strava-upload.py xxx.fit
```

## 与 MAGENE C706 的关系

**⚠️ 重要更正（2026-06-01 推翻旧结论）**：

~~MAGENE C706 固件输出 WGS84，不需要 GCJ-02 转换~~ —— **这是错的**。

实测（05-30 骑行 OSM 双向验证）证明 **MAGENE C706 固件输出 GCJ-02**，路线 vs WGS-84 路网中位偏 72m，方向恒东偏南；转 GCJ-02 路网后骤降到 4.8m。详见 `references/magene-c706-gcj02-offset.md`。

因此 MAGENE C706 用户**应当**使用 `--correct-coords` 参数上传，否则 Strava 上路线会偏 300-500m。

本算法适用于所有国产码表（华为 GT/Runner、小米 Watch、佳明中国大陆固件版本等），判断标准统一为 OSM 双向验证：`scripts/detect_gcj02_offset.py` 工具 5-10 分钟出结论。
