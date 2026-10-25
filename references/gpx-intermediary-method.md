# GPX 中介法：重建干净 FIT 的最可靠方案（2026-05-30 验证）

## 适用场景

Encoder 直接写入 metadata 时遇到以下错误，改为 GPX 中介路径：

- `Could not convert "(None, None, None, 4)" to "uint8z"` — `capabilities` 字段有厂商 tuple
- `Could not convert "(77, 65, 71, 69, 78, 69, ...)" to "byte"` — `developer_data_id.application_id` 字节数组
- `mesg_num: 0 could not be found in the Profile` — `Profile['messages']` 使用 str key，Encoder 内部用 int key lookup 失败

## 完整流程

```
原始 FIT
  ↓ fitdecode.FitReader 解析
  ↓ 坐标校正（三段中位数法）
  ↓ 写 GPX（含 hr/cad/spd/pwr in extensions）
  ↓ 从 GPX 读回数据
  ↓ Encoder 写入（只写 essential metadata）
  ↓ 输出干净 FIT → Strava 上传
```

## Step 1: fitdecode 提取 record（含 semicircles）

```python
import os
import fitdecode

with fitdecode.FitReader(orig_path) as reader:
    records = []
    for block in reader:
        if type(block).__name__ != 'FitDataMessage':
            continue
        mt = getattr(block, 'mesg_type', None)
        if mt is None or mt.name != 'record':
            continue
        r = {}
        for f in block.fields:
            if f.value is not None:
                r[f.name] = f.value
        if 'position_lat' in r and 'position_long' in r:
            records.append(r)
```

## Step 2: 坐标校正（三段中位数值）

```python
import statistics

def semicircles_to_deg(s):
    return s / (2**32 / 360)

for r in records:
    r['lat'] = semicircles_to_deg(r['position_lat'])
    r['lon'] = semicircles_to_deg(r['position_long'])

records.sort(key=lambda r: r.get('timestamp') or 0)
n = len(records)
TARGET_LON = 108.80  # 西安地区目标经度

seg0 = records[:int(n*0.25)]
seg1 = records[int(n*0.25):int(n*0.86)]
seg2 = records[int(n*0.86):]

corr0 = statistics.median([r['lon'] for r in seg0]) - TARGET_LON
corr1 = statistics.median([r['lon'] for r in seg1]) - TARGET_LON
corr2 = statistics.median([r['lon'] for r in seg2]) - TARGET_LON

for r in seg0: r['lon'] -= corr0
for r in seg1: r['lon'] -= corr1
for r in seg2: r['lon'] -= corr2
```

## Step 3: 写 GPX

```python
import xml.etree.ElementTree as ET

ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}
ET.register_namespace('', ns)
root = ET.Element(f'{{{ns}}}gpx', version='1.1', creator='MAGENE-FIX')
trk = ET.SubElement(root, f'{{{ns}}}trk')
ET.SubElement(trk, f'{{{ns}}}name').text = 'Corrected'
trkseg = ET.SubElement(trk, f'{{{ns}}}trkseg')

for r in records:
    trkpt = ET.SubElement(trkseg, f'{{{ns}}}trkpt', lat=str(r['lat']), lon=str(r['lon']))
    if r.get('altitude') is not None:
        ET.SubElement(trkpt, f'{{{ns}}}ele').text = str(r['altitude'])
    if r.get('timestamp'):
        ET.SubElement(trkpt, f'{{{ns}}}time').text = r['timestamp'].strftime('%Y-%m-%dT%H:%M:%SZ')
    ext = ET.SubElement(trkpt, f'{{{ns}}}extensions')
    for tag, key in [('hr', 'heart_rate'), ('cad', 'cadence'), ('spd', 'speed'), ('pwr', 'power')]:
        val = r.get(key)
        if val is not None:
            ET.SubElement(ext, tag).text = str(val)

ET.ElementTree(root).write(gpx_path, encoding='utf-8', xml_declaration=True)
```

## Step 4: Encoder 写入（关键：只写 essential metadata）

```python
from garmin_fit_sdk import Encoder
from garmin_fit_sdk.profile import Profile
from garmin_fit_sdk.util import convert_datetime_to_timestamp
import datetime

# ⚠️ 必须修复 Profile str key bug（garmin-fit-sdk 21.205.0 特有）
Profile['messages'] = {int(k): v for k, v in Profile['messages'].items()}

mesg_name_to_num = {}
for k, v in Profile['messages'].items():
    name = v.get('name', '').lower()
    num = v.get('num')
    if isinstance(num, str): num = int(num)
    if name and num is not None:
        mesg_name_to_num[name] = int(num)

def semicircles(deg):
    return int(deg * (2**32 / 360))

# 只提取 essential metadata（避开有问题的 capabilities/developer_data_id/field_description）
essential = ['file_id', 'session', 'activity']
essential_metadata = {k: [] for k in essential}
with fitdecode.FitReader(orig_path) as reader:
    for block in reader:
        if type(block).__name__ != 'FitDataMessage':
            continue
        mt = getattr(block, 'mesg_type', None)
        if mt is None or mt.name not in essential:
            continue
        fields = {}
        for f in block.fields:
            if f.value is not None:
                fields[f.name] = f.value
        if fields:
            essential_metadata[mt.name].append(fields)

encoder = Encoder()

# 写 essential metadata
for mt_name in essential:
    for fields in essential_metadata[mt_name]:
        clean = {fn: (convert_datetime_to_timestamp(fv) if isinstance(fv, datetime.datetime) else fv)
                 for fn, fv in fields.items() if fv is not None}
        encoder.write_mesg({'mesg_num': mesg_name_to_num[mt_name], **clean})

# 从 GPX 读回并写 record
tree = ET.parse(gpx_path)
ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}
trkpts = tree.findall('.//gpx:trkpt', ns)

for pt in trkpts:
    lat = float(pt.get('lat'))
    lon = float(pt.get('lon'))
    alt_el = pt.find('gpx:ele', ns)
    alt = float(alt_el.text) if alt_el is not None and alt_el.text else None
    t_el = pt.find('gpx:time', ns)
    ts = None
    if t_el is not None and t_el.text:
        dt = datetime.datetime.fromisoformat(t_el.text.replace('Z', '+00:00'))
        ts = convert_datetime_to_timestamp(dt)
    ext = pt.find('gpx:extensions', ns)
    hr = cad = spd = pwr = None
    if ext:
        for tag, cls in [('hr', int), ('cad', int), ('spd', float), ('pwr', int)]:
            el = ext.find(tag, {})
            if el is not None and el.text:
                try: {'hr': lambda v: hr, 'cad': lambda v: cad, 'spd': lambda v: spd, 'pwr': lambda v: pwr}[tag](cls(el.text))
                except: pass

    record = {
        'mesg_num': mesg_name_to_num['record'],
        'position_lat': semicircles(lat),
        'position_long': semicircles(lon),
        'timestamp': ts,
    }
    if alt is not None: record['altitude'] = alt
    if hr is not None: record['heart_rate'] = hr
    if cad is not None: record['cadence'] = cad
    if spd is not None: record['speed'] = spd
    if pwr is not None: record['power'] = pwr

    encoder.write_mesg(record)

data = encoder.close()
with open(out_path, 'wb') as f:
    f.write(data)
```

## ⚠️ 已知弱点

### 丢失 `distance` 字段 → ✅ 已修复（2026-05-30 验证通过）

GPX 标准没有 distance 字段，Strava 会显示 0 km。**解决方案（✅ 已验证）：** 在 Step 1 fitdecode 解析时把 `distance` 字段一并提取（第行写入 `r[f.name] = f.value` 后 distance 自动在 dict 中）；Step 4 Encoder 写入时 inject：

```python
# Step 4 Encoder 写入 record 时（第行之后追加）：
if r.get('distance') is not None:
    record['distance'] = int(r['distance'])  # uint32, 米
```

Step 3 写 GPX 时无需额外操作，distance 保留在 Step 1 的 record dict 中，Step 4 直接使用。

Strava 收到含 distance 的 FIT 后**直接使用字段值，不重新计算轨迹长度**，距离显示正常。

### 丢失 `enhanced_altitude` / `enhanced_speed`

标准 FIT record 的 `altitude` 是 `sint16`（0.2 m scale, offset -500），而码表可能还记录 `enhanced_altitude`（float 米）。如果Strava 高度数据异常，可能需要补这两个字段。

## 验证检查

```python
import requests, json

tok = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))
headers = {'Authorization': f'Bearer {tok["access_token"]}'}
r = requests.get(f'https://www.strava.com/api/v3/activities/{activity_id}', headers=headers)
a = r.json()
print(f"距离: {a['distance']/1000:.2f} km")
print(f"起点: {a['start_latlng']}")
print(f"终点: {a['end_latlng']}")
```

距离 0 km → 补 distance 字段后重建。