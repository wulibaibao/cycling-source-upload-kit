# FIT 文件重建：GPS 漂移修复方案

## 核心问题

MAGENE C706 等码表记录的原始 FIT 文件中，GPS 坐标已经是 WGS84 semicircles 格式，数值正确，但文件二进制结构存在压缩时间戳 header 导致 local_mesg_num 解析错误。直接重传会被 Strava 判定为重复文件拒绝处理。

## 解决方案：fitdecode 读 + garmin-fit-sdk Encoder 重建
## 解决方案：fitdecode 读 + garmin-fit-sdk Encoder 重建

```python
import fitdecode.reader

# 1. fitdecode 正确解析（注意用 FitReader，不是 FitFile）
with fitdecode.reader.FitReader(input_path) as fit:
    records = []
    for msg in fit:
        # 必须过滤：definition message 没有 fields，只有 data message 有
        if isinstance(msg, fitdecode.FitDataMessage) and msg.name == 'record':
            records.append(msg)
print('Record count:', len(records))
# 2. garmin_fit_sdk.Encoder 重建干净的 FIT 文件
from garmin_fit_sdk import Encoder
encoder = Encoder()
for frame in all_frames:
    if frame.frame_type != fitdecode.FIT_FRAME_DATA:
        continue
    # 提取字段值并写入 encoder...
data = encoder.close()
with open(output_path, 'wb') as f:
    f.write(data)
```

**常见错误**：
- `fitdecode.FitFile`（注意大写）不存在，应使用 `fitdecode.reader.FitReader`
- `FitDataMessage.fields` 属性在 definition message 上不存在，用 `isinstance(msg, fitdecode.FitDataMessage)` 过滤
- `get_value()` 的 `default` 参数不存在，字段不存在时直接返回 `None`

## 关键类型转换

| 字段 | fitdecode 原始值 | Encoder 需要值 |
|------|-----------------|---------------|
| position_lat/long | int (semicircles) | int (semicircles) |
| timestamp | datetime | int (FIT uint32 seconds since 1989-12-31 UTC) |
| altitude | float (meters) | int (sint16 with 0.2 scale, offset -500) |
| speed | float (m/s) | int (uint16 with 0.001 scale) |
| distance | float (km) | int (meters or as-is) |
| heart_rate | int | int |
| cadence | int | int |
| temperature | int | int |
| grade | float | int (×100) |

```python
def timestamp_to_fit_ts(dt):
    from datetime import datetime, timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    FIT_EPOCH = datetime(1989, 12, 31, 0, 0, 0, tzinfo=timezone.utc)
    return int((dt - FIT_EPOCH).total_seconds())

def altitude_to_sint16(meters):
    return int(round((meters + 500.0) / 0.2))

def speed_to_uint16(ms):
    return int(round(ms / 0.001))
```

## 只读字段类型（直接 try_int）

`type`, `manufacturer`, `product`, `serial_number`, `device_index`, `device_type`, `hardware_version`, `total_ascent`, `total_descent`, `total_calories`, `avg_heart_rate`, `max_heart_rate`, `sport`, `sub_sport`

## 跳过字段类型

- `monitoring` — 不支持，跳过
- `weather_conditions` — 不支持，跳过
- `field_description` / `developer_data_id` — 仅元数据，不需要
- `software.message_index` — 不要转 dict，直接 plain int
- `device_info.manufacturer` / `product` — 可能是字符串（如 "magene"），用 try_int 捕获

## 脚本路径

`~/convert_fit_encoder.py`

## venv 路径

`~/hermes-agent/venv/bin/python`

## 依赖安装

```bash
~/hermes-agent/venv/bin/pip install garmin-fit-sdk --break-system-packages
~/hermes-agent/venv/bin/pip install fitdecode --break-system-packages
```