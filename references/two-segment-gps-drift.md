# 两段式 GPS 漂移修复

## 识别特征

MAGENE C706 的 FIT 文件中，GPS 数据出现两个明显聚类：
- 干净段：lon ≈ 108.9°（西安城区正常范围）
- 偏移段：lon ≈ 109.2°（向东偏移 ~0.3° ≈ 33 km）
- 两段之间**无过渡**，泾渭分明
- 偏移量是**恒定的**（GPS 重锁到错误位置后固定漂移）

## 关键判断：spread 对比法（区分两段式漂移 vs 路线本身延伸更远）

**永远先用 spread 对比，再决定修复策略：**
```python
spread = max(lons) - min(lons)
# 对比同一个人、同时段参考路线的 spread
# spread 接近（<2×）→ 两段式漂移，对偏移段单独处理
# spread 差异大（>3×）→ 路线本身延伸更远，不是漂移，均匀偏移修所有点即可
```

**案例对比（05-30 vs 05-15）：**
- 05-30 spread（0.57°）是 05-15（0.088°）的 **6.5×**
- 结论：05-30 路线本身向东延伸更远（不是均匀漂移），校正后 lon 108.57°~108.95°

| 类型 | spread 对比 | 修复策略 |
|------|------------|---------|
| 两段式漂移 | spread 接近（<2×参考路线） | 对偏移段单独减去偏移量 |
| 路线本身更远 | spread 差异大（>3×） | 对所有点减去**均匀偏移**（修起点对齐） |

## ✅ 推荐方案：GPX 中介法（见 `references/gpx-intermediary-method.md`）

**直接 Decoder→Encoder 重建法（已过时）：** garmin_fit_sdk Decoder 的 `mesg_listener` 无法可靠透传所有非 record 元数据，导致重建文件缺少关键字段，Strava 报 "There was an error processing your activity"。改用 GPX 中介法。

## 旧方法：garmin-fit-sdk Decoder→Encoder（已不推荐）

```python
from garmin_fit_sdk import Decoder, Encoder
from garmin_fit_sdk.stream import Stream

stream = Stream.from_file(IN_PATH)
decoder = Decoder(stream)
encoder = Encoder()

def on_message(mesg_num, mesg):
    if mesg_num == 20:  # RECORD
        lat = mesg.get('position_lat')
        lon = mesg.get('position_long')
        if lat is not None:
            mesg['position_lat'] = lat + OFFSET_LAT_SC
        if lon is not None:
            mesg['position_long'] = lon + OFFSET_LON_SC
    encoder.on_mesg(mesg_num, mesg)

decoder.read(mesg_listener=on_message)
data = encoder.close()
```

⚠️ **注意**：OFFSET 要用 `int(deg * (2**32 / 360))` 转换，不是 `(2**31/180)`。