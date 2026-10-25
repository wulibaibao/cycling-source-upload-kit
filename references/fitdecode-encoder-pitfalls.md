# fitdecode + garmin-fit-sdk Encoder 实操坑（2026-06-04 验证）

`references/gpx-intermediary-method.md` 是 5/30 验证的 GPX 中介法（手写完整流程）。
本文是 6/04 用 `correct_fit_gpx.py` 落地的精简版，**额外记录三个新坑**（5/30 没遇到）。

## 三个新坑（6-04 实战踩到）

### 坑 1：fitdecode 的 `f.value` 是 scaled 值，enum 类型是 string

fitdecode 默认 `apply_scale_and_offset=True` 把 raw int 转 float、enum 转 string。
对**坐标** (`position_lat`/`position_long` = sint32 semicircles) 没影响——scaled 仍是 int。
对**enum 字段**（`sport`、`sub_sport`）：`f.value` 是 `'cycling'` (str)，`f.raw_value` 是 `2` (int)。
garmin-fit-sdk Encoder **只接受 int enum**，给 string 会 ValueError。

```python
# ❌ 错：只读 f.value
v = try_int(f.value)  # 'cycling' → None
d['sport'] = v  # 字段被丢

# ✅ 对：value 优先，str/string 时回退 raw_value
v = try_int(f.value)
if v is not None:
    d[f.name] = v
elif isinstance(f.value, str) and f.raw_value is not None:
    rv = try_int(f.raw_value)
    if rv is not None:
        d[f.name] = rv
```

### 坑 2：`_MESG_NUM` 白名单漏 `sport` (mesg_num=12)

如果在白名单只写了 `file_id / user_profile / bike_profile / session / lap / record / activity`，
会**丢掉独立的 `sport` 消息**——而很多 GPS 码表（OnelapFit 验证、佳明）会发独立 sport 消息
标识活动类型。

后果：`strava-upload.py` 的 `parse_fit()` 只在 `name == "sport"` 分支填 `data["sport"]`，
缺消息时 `sport` 字段空 → `get_title()` 返回 `None`（else 分支兜底为空）→ Strava 标题变成**文件名**而不是 "Z2 脂肪燃烧区"。

**修复**：
```python
_MESG_NUM = {
    'file_id': 0, 'user_profile': 3, 'bike_profile': 6,
    'sport': 12,            # ← 别漏！OnelapFit / 佳明都有这消息
    'session': 18, 'lap': 19, 'record': 20,
    'activity': 34,
}
```

### 坑 3：garmin-fit-sdk Encoder 写 scaled float 丢精度

`fitdecode` 返回 `avg_speed = 9.088 m/s`（float），garmin-fit-sdk Encoder 写入时把它当原始 int
（`9.088` 不是 int）→ 字段被丢或置 0。修复后即使回退 `raw_value` 拿 unscaled int，
**round-trip 仍丢精度**——avg_speed=9088 (cm/s) 写进去，读出来是 9.0 m/s 而不是 9.088。

**影响范围**：session/lap 里的 scaled 字段——`avg_speed`、`max_speed`、`total_distance`、`avg_altitude` 等。
**不影响**：record 里的 lat/lon（sint32 已 OK）、heart_rate、cadence（int 字段）。

**症状**：纠偏上传后，Strava **主页面**地图/距离/速度都是它自己重算的所以正确（46.8 km/h、32.7 km/h），
但**活动 description 里**显示纠偏产物的 9.0 m/s → 32.4 km/h（实际更糟，会进一步四舍五入）。

**实测数字**（06-04 早 54.4 km 骑行）：

| 字段 | 原文件 | 纠偏产物 | 顽鹿 | Strava description |
|------|-------|---------|------|------|
| avg_speed | 9.088 m/s | 9.0 m/s | 32.6 km/h | **30.7 km/h** ❌ |
| max_speed | 12.962 m/s | 12.0 m/s | 46.7 km/h | **43.2 km/h** ❌ |
| total_distance | 54424.44 m | 54424.0 m | 54.42 km | **54.42 km** ✅ |

## ✅ 推荐 workaround：.orig 备份 + 自动 PATCH description

Encoder 丢精度问题不修 Encoder（要动 unscaled/raw_value 映射的逻辑容易引入别的 bug），
**改在 upload 完成后用 .orig 重新生成 description PATCH 活动**：

```python
# main() 末尾
activity_id = upload_fit(...)

# --correct-coords 路径会备份原文件成 .orig
orig_path = fit_path + '.orig'
if activity_id and args.correct_coords and os.path.exists(orig_path):
    d_orig = parse_fit(orig_path)        # 原始 .orig → 完整精度
    desc_orig = get_description(d_orig)  # 数字正确
    requests.patch(
        f"https://www.strava.com/api/v3/activities/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"description": desc_orig},
    )
```

`strava-upload.py` v1.4.1 已实现此逻辑（commit c5d0142）。`--correct-coords` 上传完成后自动 PATCH，
description 数字跟顽鹿一致（46.7 / 32.6 / 54.42 km）。

**为什么不直传（不上传纠偏版）**：直传版 description 数字更准（直接用 .orig parse_fit），
但**地图上路线偏 300-500m**（GCJ-02 vs Strava WGS-84）。所以：
- 想要地图准 → 必须纠偏上传（accept description 数字可能略偏）
- 想要 description 数字绝对准 → 上传前 .orig → parse_fit → 用这数字走 upload_fit
  传纠偏版 FIT 即可（产物精度差是 Encoder 限制，但 description 是我们生成的不是 FIT 里的）

## Python `__pycache__` 陷阱

修 `strava-upload.py` 后立即跑，Python 可能仍加载旧的 `.pyc`：
```
File "~/strava-upload.py", line 322, in correct_fit_file
    data_msg_size = 6 + num_fields * 3
TypeError: ...
```
即使源码已改，进程仍用 `__pycache__/strava-upload.cpython-311.pyc` 旧版本。

**修法**：
```bash
rm -rf ~/__pycache__/strava-upload*
```
或更彻底：
```bash
find ~ -maxdepth 4 -name "*.pyc" -path "*strava-upload*" -delete
```

⚠️ `importlib.util.spec_from_file_location` 加载源码路径**仍可能优先用 __pycache__**（取决于 sys.path）。
最稳是跑 `python3 strava-upload.py`（直接执行脚本）而不是 `python3 -c "import ..."`。

## 完整精简版 GPX 中介法（correct_fit_gpx.py 工作流）

`~/correct_fit_gpx.py`（已落地）流程：
1. `fitdecode.FitReader(input)` 遍历所有 frame
2. 只处理 `_HANDLED` 白名单消息（见坑 2）
3. 字段写入 dict，**用坑 1 的 enum 回退 raw_value 模式**
4. 坐标字段在 `_correct_coords_inplace()` 配对 (lat, lon) → `gcj2wgs()` → semicircles
5. `garmin_fit_sdk.Encoder.write_mesg(d)` 重建
6. 跳过写不进来的字段（try/except ValueError 静默）

输出 5988 records × 2 coords = 11976 个坐标成功纠偏（06-04 早 54.4 km 骑行实测）。
