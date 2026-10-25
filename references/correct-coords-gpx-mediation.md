---
name: strava-upload-correct-coords-gpx-mediation
description: '2026-06-04 实现：--correct-coords 改为 GPX 中介法（fitdecode → gcj2wgs → garmin-fit-sdk Encoder），替代坏掉的 binary patch'
license: MIT
---

# `--correct-coords` GPX 中介法（2026-06-04 实现）

`strava-upload.py` 里的 `correct_fit_file` 06-04 重写为 GPX 中介法。**旧 binary patch 实现已废弃，4 个 bug 详情见 `correct-coords-broken-2026-06-04.md`。**

## 为什么放弃 binary patch

旧实现尝试自己解析 FIT 字节流、只 patch 坐标字节、原地重写。FIT 协议的关键陷阱：

- **data message 没有 msg_num/num_fields 字段**——通过 local message type 引用前一个 definition message 才知道消息类型
- **CRC 双位置**：header[12:14] 和文件末尾 2B 算的范围**不同**
- **scaled/unscaled 字段值**：distance 字段 `f.value` 是 m，`f.raw_value` 是 cm

任何一处写错就触发 Strava 拒绝（"malformed"）或纠偏零效果。从零自实现 binary parser 风险太高，**改用 fitparse/fitdecode/garmin-fit-sdk 这三个经过实战的库做"fitdecode→纠偏→Encoder 重建"**。

## 实现流程

```
原始 FIT (GCJ-02)
  ↓ fitdecode.FitReader 解析（保留所有 metadata）
  ↓ 对 record/session/lap 里的 position_lat/long 做 gcj2wgs
  ↓ garmin-fit-sdk Encoder 重建（重写所有 metadata + record）
  ↓ 产物 FIT (WGS-84) → gzip 压缩 → Strava 上传
```

## 白名单（必须包含的 8 种消息）

**`sport` 消息漏了 = 产物 sport=Other，标题退化为文件名。** 06-04 实战翻车：

```python
HANDLED = {
    'file_id',      # mesg_num=0   必备：Strava 识别文件类型
    'user_profile', # mesg_num=3   用户身高/体重
    'bike_profile', # mesg_num=6   车架名
    'sport',        # mesg_num=12  ⚠️ 必含！漏了→sport=Other
    'session',      # mesg_num=18  运动摘要（HR/power/calories）
    'lap',          # mesg_num=19  圈数据
    'record',       # mesg_num=20  轨迹点
    'activity',     # mesg_num=34  顶层活动
}
```

其他消息（device_info / monitoring / event / hrv / weather_conditions）**静默跳过**——Strava 仍能正确解析。GPX 中介法只重建这 8 种，丢了什么 device_info 是可接受的（这些是次要元数据）。

## 坐标字段映射

```python
COORD_FIELDS = {
    'record':  ('position_lat', 'position_long'),
    'session': ('start_position_lat', 'start_position_long',
                'end_position_lat', 'end_position_long',
                'nec_lat', 'nec_long', 'swc_lat', 'swc_long'),
    'lap':     ('start_position_lat', 'start_position_long',
                'end_position_lat', 'end_position_long'),
}
```

`correct_coords_inplace(d, coord_keys)` 对每个 `(lat, long)` 配对调 `gcj2wgs`，半圆→度→WGS-84→半圆，再写回 dict。

## fitdecode 0.11 enum / scaled 字段处理（核心陷阱）

fitdecode 0.11 `FitReader` **没有** `convert_types_to_strings` 参数（高版本才有）。enum 转 string 是 fitdecode **内部默认行为**，绕不开。**正确处理**：

```python
def _try_int(v):
    if v is None: return None
    try: return int(v)
    except (ValueError, TypeError): return None

for f in frame.fields:
    if f.value is None:
        continue
    # 数字字段用 value（scaled，e.g. distance=54424.44m）
    v = _try_int(f.value)
    if v is not None:
        d[f.name] = v
    # enum 字符串（fitdecode 默认 'cycling'）回退 raw_value（= 2）
    elif isinstance(f.value, str) and f.raw_value is not None:
        rv = _try_int(f.raw_value)
        if rv is not None:
            d[f.name] = rv
```

| 字段 | `f.value` | `f.raw_value` | Encoder 用 |
|------|-----------|---------------|----------|
| sport | `'cycling'` (str) | `2` (int) | `raw_value` (2) |
| total_distance | `54424.44` (m) | `5442444` (cm) | `value` (54424.44) |
| avg_speed | `9.088` (m/s) | `9088` (cm/s) | `value` (9.088) |
| total_elapsed_time | `6832.0` (s) | `6832000` (ms) | `value` (6832) |
| avg_heart_rate | `125` | `125` | 都行 |
| timestamp | `datetime(2026,6,3,21,41,55+UTC)` | `int` | 转 FIT uint32 |

**06-04 实战翻车**：如果统一用 `f.value`，`sport='cycling'` 字符串写不进 Encoder → 产物 `parse_fit` 返回 `sport=Other` → 标题退化为文件名。

## datetime → FIT uint32 时间戳

```python
FIT_EPOCH = datetime(1989, 12, 31, 0, 0, 0, tzinfo=timezone.utc)

def fit_ts(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int((dt - FIT_EPOCH).total_seconds())
```

应用在 `time_created` / `timestamp` / `start_time` / `local_timestamp` 字段上。

## garmin-fit-sdk Profile str-key bug

```python
from garmin_fit_sdk.profile import Profile
Profile['messages'] = {int(k): v for k, v in Profile['messages'].items()}
```

不修会报 `mesg_num: X could not be found in the Profile`。5/30 实战发现，6/04 仍要修（库版本没变）。

## Encoder 写入错误的处理

某些消息字段不识别（厂商特定字段）→ `encoder.write_mesg(d)` 抛 `ValueError`。**静默跳过单条消息**比整脚本崩溃好：

```python
try:
    encoder.write_mesg(d)
except ValueError:
    pass  # 跳过单条消息，继续处理下一条
```

## 验证清单（上传前必跑）

```python
# 1) 产物能 fitparse 解析（无 CRC 错）
from fitparse import FitFile
f = FitFile(out_path)
n = sum(1 for _ in f.get_messages())
print(f'parse: {n} messages')
# 预期 ~6000（5988 records + file_id/sport/session/lap 等）

# 2) sport 字段正确（不是 Other）
import fitdecode
FIT_FRAME_DATA = fitdecode.FIT_FRAME_DATA
with fitdecode.FitReader(out_path) as fr:
    for f in fr:
        if f.frame_type == FIT_FRAME_DATA and f.name == 'sport':
            print(f'sport: {f.get_value("sport")}')  # 预期 'cycling' 字符串
            break

# 3) parse_fit 返回 sport='Ride'（不是 Other）
d = su.parse_fit(out_path)
print(f'sport: {d["sport"]}')  # 预期 'Ride'
print(f'get_title: {su.get_title(d)}')  # 预期 'Z2 脂肪燃烧区'
```

## 实测数据（06-04 早 MAGENE C706 FIT）

```
输入:  223386 bytes, 12028 messages (file_id/sport/record/lap/session/...)
输出:  210799 bytes, 6004 messages
纠偏:  5988 records × 2 coords = 11976 坐标成功
验证:  fitparse 通过 / sport='cycling' / parse_fit sport='Ride' / get_title='Z2 脂肪燃烧区'
上传:  Strava 接受，路线偏西 ~400m（GCJ-02→WGS-84 预期方向）
```

## 与旧 binary patch 的对比

| 维度 | 旧 binary patch | GPX 中介法 |
|------|----------------|-----------|
| 代码行数 | ~400 行（含 CRC 库、patch 逻辑、消息解析） | ~200 行（直接用 fitdecode/Encoder） |
| FIT 协议理解要求 | 全部自实现 | 库封装 |
| 风险点 | data message 结构差异（4 个 bug 都源于此） | 白名单 + enum 处理（2 个易踩点） |
| 元数据保留 | 完整保留 | 只保留白名单 8 种 |
| 产物大小 | 接近原文件 | 通常略小（丢次要 metadata） |
| 速度 | 快（原地 patch） | 慢（重建整个文件） |
| 维护成本 | 高（FIT 协议变了要改） | 低（库升级自动兼容） |

**结论：FIT 协议级操作永远用库，不要自实现 parser。** GPX 中介法虽然丢了 device_info 等次要 metadata，但 Strava 显示完全正常，且代码 5× 短。

## 替代方案

| 方案 | 适用 | 已知问题 |
|------|------|---------|
| **fit-tool update** | 只改 1 条坐标（手工测试） | 没在 06-04 实战过；需要装 fit-tool CLI |
| **garmin-fit-sdk Decoder 完整保留** | 想要保留所有元数据 | Decoder/Encoder API 易用性差，listener 形态不固定 |
| **GPX 中介（本方法）** | 通用、可靠 | 丢 device_info 等次要字段（无影响） |
