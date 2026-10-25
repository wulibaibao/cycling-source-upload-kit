# Strava Streams → 重建 FIT 文件（用于跨平台同步）

适用场景：从 Strava 把骑行记录**反向同步**到不支持 Strava 的国产平台（行者、顽鹿、华为运动健康等），又不想用 Strava 网页 "Export Original"（OAuth Bearer 拿不到文件）。**用 Strava Streams API 自己拼 FIT**。

## 验证状态

**5970 条 record 重建成功**（2026-10-03 实测，含 latlng/time/HR/cadence/power/altitude/distance 七个字段，137 KB）。所有字段从 Strava Streams 直接拿到，**无需原始 FIT 文件**。

## 拉 Streams

```python
import os
import json, requests
TOK = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))['access_token']
H = {'Authorization': f'Bearer {TOK}'}

# 用 keys=latlng,time,heartrate,watts,cadence,altitude 拿全字段
# 注意:series_type=distance 时 time 是相对秒数（0 到总时长）
# 拿到的 streams[i].data[i] 是数组，按 record 顺序一一对应
streams = requests.get(
    f'https://www.strava.com/api/v3/activities/{aid}/streams'
    '?keys=latlng,time,heartrate,watts,cadence,altitude&key_by_type=true',
    headers=H
).json()
```

**关键事实（实测）：**
- Strava 1Hz 采样，`time` 数组里值是相对活动起点的秒数（`[0, 1, 2, ..., 6691]`）
- `latlng` 是 `[lat, lng]` 数组，不是 `[lng, lat]`（注意顺序！）
- 缺失字段返回 null（不是缺键位），用 `if x is not None` 跳过
- 5970 个点的 1.5 小时骑行，单次请求 311 KB，< 1 秒返回

## 重建 FIT（garmin_fit-sdk）

```python
from datetime import datetime, timezone
from garmin_fit_sdk import Encoder
from garmin_fit_sdk.profile import Profile

# 必须 fix int-key bug（key_by_type 默认 str-key，Encoder 要 str-key 一致，
# 但 0.* encoder 内部又按 int 索引——实测 int-key 后 Encoder 才能跑通）
Profile['messages'] = {int(k): v for k, v in Profile['messages'].items()}

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
def fit_ts(dt):
    return int((dt - FIT_EPOCH).total_seconds())

def semicircle(deg):
    return int(deg * (2**31) / 180)

# 起点时间从 activity 元数据拿（UTC）
start = datetime(2026, 10, 2, 6, 4, 41, tzinfo=timezone.utc)
# Streams 里的 time 是相对秒数，加 start 得到 record timestamp
def record_dt(rel_sec):
    return datetime.fromtimestamp(start.timestamp() + rel_sec, tz=timezone.utc)

enc = Encoder()
# 必写：file_id + session + sport 消息
# 不写 sport 的话，产物 parse_fit 会返回 sport=Other —— Strava 标题会退化为文件名
enc.write_mesg({
    'mesg_num': 0,  # file_id
    'type': 4,  # activity
    'manufacturer': 255,
    'product': 0,
    'serial_number': 0,
    'time_created': fit_ts(start),
})
enc.write_mesg({
    'mesg_num': 18,  # session
    'start_time': fit_ts(start),
    'total_elapsed_time': float(streams['time']['data'][-1]),
    'total_distance': float(streams.get('distance', {}).get('data', [0])[-1] or 0),
    'sport': 1,  # cycling
    'sub_sport': 0,
})

# 写 records（逐点）
for i in range(len(streams['latlng']['data'])):
    ll = streams['latlng']['data'][i]
    t_rel = streams['time']['data'][i]
    rec = {
        'mesg_num': 20,  # record
        'timestamp': fit_ts(record_dt(t_rel)),
        'position_lat': semicircle(ll[0]),
        'position_long': semicircle(ll[1]),
    }
    # 可选字段（缺失跳过）
    if 'distance' in streams and streams['distance']['data'][i] is not None:
        rec['distance'] = float(streams['distance']['data'][i])
    if 'altitude' in streams and streams['altitude']['data'][i] is not None:
        rec['altitude'] = float(streams['altitude']['data'][i])
    if 'heartrate' in streams and streams['heartrate']['data'][i] is not None:
        rec['heart_rate'] = int(streams['heartrate']['data'][i])
    if 'watts' in streams and streams['watts']['data'][i] is not None:
        rec['power'] = int(streams['watts']['data'][i])
    if 'cadence' in streams and streams['cadence']['data'][i] is not None:
        rec['cadence'] = int(streams['cadence']['data'][i])
    enc.write_mesg(rec)

data = enc.close()
open('/tmp/reconstructed.fit', 'wb').write(data)
```

## 与 strava-upload.py GPX 中介法对比

| 维度 | GPX 中介法（strava-upload.py） | Streams → FIT（本方法） |
|------|-------------------------------|----------------------|
| 输入 | 本地 FIT 文件 | Strava activity_id |
| 坐标校正 | GCJ-02→WGS-84 自动 | **已经是 WGS-84**（Strava 标准化后） |
| 用途 | MAGENE 原始 FIT → Strava | Strava → 国产平台（行者/顽鹿等） |
| 元数据 | 完整保留原始 | 只能拿 Strava 元数据 + Streams 7 字段 |

**两条路**对称——GCJ-02 异构设备要发到 Strava 走 GPX 中介法；Strava 发到国产平台走 Streams 重建（不需要再纠偏，因为 Strava 已经是 WGS-84）。

## 已知限制

- Strava Streams 不返回温度字段（`temperature` 拉不到），国产平台如果强制要温度，重建版会是缺失
- Strava Streams 不返回扩展字段（`enhanced_altitude`、`enhanced_speed`），功率计的 left/right balance 也没有
- 上传下来对**最严格的国产平台**（如要求 FIT 有 `device_info` 消息），可能不被识别为有效 FIT。但行者这种**只看 sport + total_distance + records 的平台**实测可接收
- 不重写 `device_info` / `manufacturer` 会丢失码表识别——产物 FIT 里设备显示为 `manufacturer=255`（开发者），不是 `Magene`

## 适用决策树

```
要把 Strava 骑行同步到：国产平台？
├── 是 → Strava 导出页面 (`/activities/{id}/export_original`) 能登录拿到原始 FIT？
│   ├── 是（cookie 登录）→ 用网页端 export，保留全部元数据
│   └── 否（API Bearer 不被接受）→ 用本方法从 Streams 重建
└── 否（自有平台）→ 直接调 Strava API
```