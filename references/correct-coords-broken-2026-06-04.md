---
name: strava-upload-correct-coords-broken
description: '2026-06-04 事故复盘：strava-upload.py --correct-coords 旧 binary patch 实现的 4 个 bug（已修复，本文档作为历史教训保留）'
license: MIT
---

# 🚨 `strava-upload.py --correct-coords` 旧实现事故复盘（2026-06-04，**已修复**）

**状态：已修复（2026-06-04 同日）。** `correct_fit_file` 重写为 GPX 中介法，详见 `references/correct-coords-gpx-mediation.md`。本文档**保留**作为 FIT 协议级操作的血泪教训，提醒未来不要从零自实现 binary parser。

---

## 现象

用户上传 MAGENE C706 的 06-04 早骑行 FIT（5:41 开始，54.4 km 距离），要求 GCJ-02→WGS-84 纠偏。`--correct-coords` 跑完上传，Strava 返回：

```
error: "The upload appears to be malformed and we are unable to process it."
```

下载纠偏产物用 `fitparse.FitFile` 解析：

```
fitparse.utils.FitCRCError: CRC Mismatch [computed: 0x3B27, read: 0x9EDA]
```

修了 CRC 后再跑，CRC 错误消失但 **body 字节跟原文件完全一致（diff 字节数 = 0）**——`gcj2wgs` 一次也没被调用，纠偏零效果。

`upload_id = 19887055114` 实际未创建活动，fitparse 拒绝读取。

## 4 个 Bug（已确认）

### Bug 1: CRC 算法多项式错了

`strava-upload.py` 的 `_build_fit_crc_table` 用 0xEDB88320（标准 CRC-32 多项式）+ 8-bit 256 元素表。

**FIT 实际用的是 CRC-16，nibble-table（4-bit，16 元素），表头是 CC01/D801 那一组。** 跟 fitparse 库 `fitparse.base.Crc` 一致：

```python
CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)
```

update 循环是 nibble-table 标准实现（高 4 bit / 低 4 bit 分别查表 XOR）。**不替换这个表，CRC 永远算不对。**

⚠️ `references/strava-gcj02-wgs84-correction.md` 里**也写错了**——同款 0xEDB88320 多项式 + 256 元素表（不是 0xCC01 nibble-table）。该 reference 的算法部分正确但二进制 patch 示例错误，**不要照抄**。

### Bug 2: CRC 范围错了——一个 CRC 错填了两个位置

FIT 文件有两个 CRC 字段，**算的范围不一样**：

- **header[12:14]** = CRC(header[0:12])——只算 12 字节头
- **文件末尾 2B** = CRC(header[0:14] + body)——**把 header CRC 字段自身也算进去**（循环但合法）

原代码只算了一个 CRC，错把它写到 header[12:14]，文件末尾要么没写要么写另一个值。

### Bug 3: data_msg_size 少加 num_fields*3 字节

```python
# 错的：
data_msg_size = 6  # 1+2+2+1 固定头
for _ in range(num_fields):
    data_msg_size += body[pos + 6 + _ * 3 + 1]  # size 字段

# 应该是：
data_msg_size = 6 + num_fields * 3  # 固定头 + num_fields 个字段定义（每个 3B）
for _ in range(num_fields):
    data_msg_size += body[pos + 6 + _ * 3 + 1]
```

少了 num_fields*3 字节 → msg_bytes 被截断 → `_patch_fit_message` 里 `offset + 3 > len(msg_bytes)` 提前 break → **所有 field def 解析失败**。

### Bug 4（核心设计错误）: 把 data message 当成 definition message 解析

**这是导致"body 字节没动"的根本原因。**

脚本假设 FIT 消息结构统一是：
```
1B header + 2B reserved/endian + 2B msg_num + 1B num_fields + N*3B field defs + data
```

但 FIT 协议里 **data message 没有 reserved/endian/msg_num/num_fields 这 5B**。data message 结构是：

```
1B header（bit7=0, bit0-3 = local msg type, bit4 = has_timestamp）
[可选 4B compressed timestamp]
数据
```

`msg_num` 是通过 local message type 引用**前一个** definition message 才知道的。

脚本对 data message 在 `pos+3:5` 读"msg_num"，实际读到的是**数据字节**。`_patch_fit_message` 里的 `msg_num` 永远是错的，所有 `coord_fields` 分支都不匹配 → `if not coord_fields: return msg_bytes` → **整条消息原样返回**。

**所有 record 都没被 patch，纠偏零效果，但 header/CRC 被改了——完美触发 Strava 拒绝。**

## 修复决定：放弃 binary patch，换 GPX 中介法

修了 Bug 1-3 后，**Bug 4 的根因**是"对 FIT 协议理解不够 + 自己实现 parser"。继续 debug 是给未来埋雷——FIT 协议任何字段定义变化都要改 parser。

**改用 fitdecode + garmin-fit-sdk Encoder**：fitdecode 解析原 FIT → 对每条 record/session/lap 做 `gcj2wgs` → Encoder 重建。库封装了 FIT 协议，2× 代码量、0 协议理解成本。

完整新实现见 `references/correct-coords-gpx-mediation.md`。

## 临时绕行方案（06-04 修复前适用，修复后**不再需要**）

### 方案 1：直传不纠偏（最快，活动不丢）

```bash
python3 ~/strava-upload.py xxx.fit Ride
```

路线在 Strava 上会偏 300-500m（GCJ-02 vs WGS-84 OSM 偏差），但**比坏掉的纠偏产物好 100 倍**——至少活动能正常显示、距离/海拔/心率都对。

### 方案 2：GPX 中介法（5/30 实战过，最可靠）

详见 `references/gpx-intermediary-method.md`。流程：

1. `fitdecode.FitReader` 解析原始 FIT → 提取所有 record（lat/lon/alt/hr/cad/spd/pwr/timestamp）
2. 转 semicircles→degrees，**对每条 record 做 gcj2wgs**
3. 写 GPX（含 extensions 里 hr/cad/spd/pwr）
4. 从 GPX 读回 record 数据，写 `garmin_fit_sdk.Encoder`
5. 注入 essential 元数据（`file_id`, `session`, `activity`）
6. 上传重建的 FIT

注意 GPX 中介法会丢 `distance` 字段（Strava 显示 0 km）——Encoder 写入时 `inject record['distance'] = int(r['distance'])` 修。

**这条路径已知能用**，因为 5/30 两段式漂移修复就是用它跑通的。

### 方案 3：用 fit-tool 直接 patch 原始 FIT（保留所有元数据）

```bash
fit-tool update <file.fit> --lat <new_lat_semicircles> --lon <new_lon_semicircles>
```

保留原 file 全部结构，**只改 record 的 lat/lon 字节**。详见 `references/fit-rebuild-encoder.md` 的 fit-tool 方案。

需要把 fit-tool 装到 PATH（`pip install fit-tool` 或 `uv tool install fit-tool`）。这个工具我没在本会话实际跑过，仅作为理论方案。

## 调试方法（未来 binary patch 再次出问题）

下次怀疑 `--correct-coords` 出问题时，按这个流程定位：

```python
# 1. 检查产物 CRC（用 fitparse 的 Crc，跟 SDK 一致）
from fitparse import FitFile
try:
    FitFile('/path/to/corrected.fit').get_messages()
    print('CRC OK')
except Exception as e:
    print(f'CRC 错: {e}')  # 命中 bug 1/2/3

# 2. 检查纠偏是否真的改字节
with open('orig.fit', 'rb') as f: orig = f.read()
with open('corrected.fit', 'rb') as f: corr = f.read()
diff_bytes = sum(1 for a, b in zip(orig, corr) if a != b)
print(f'diff: {diff_bytes} bytes')  # 应该 >> 0（至少有 N_records * 8 字节被改）
# 如果 = 0 或极少：命中 bug 4（data message 解析错位）

# 3. 检查产物坐标是否真纠偏了（用 fitparse 读出来看 lat/lon）
f = FitFile('corrected.fit')
for m in f.get_messages(name='record'):
    print(m.get_value('position_lat'), m.get_value('position_long'))
    break
# 应该看到 lat/lon 跟原文件不同（西偏北方向）
```

## 教训（写到 skill 防止再犯）

1. **自实现的二进制 parser 风险极高**——FIT 协议足够复杂（definition/data message 分离、local message type 引用、CRC 双位置），从零写极易出错。**优先用 fitparse / fitdecode / garmin_fit_sdk 这三个经过实战的库**，而不是自己解析字节。
2. **修改二进制文件后必须用 fitparse 反向验证 CRC + record 数量 + 至少一条 record 的字段值**。光看字节 diff 0 不够（bug 4 就是 diff 0 但实际零效果）。
3. **CRC 错误时优先怀疑多项式和范围**——`fitparse.base.Crc` 的源码是最好的参考实现（5 行就能看完，nibble-table + 0xCC01 头那张表）。
4. **改 `__pycache__` 缓存的 .py 后要删缓存**——`~/__pycache__/strava-upload.cpython-311.pyc` 这个坑在 06-04 害我多调了一轮。**任何 `python3 xxx.py` 改完后，先 `rm -rf __pycache__`**（或用 `-B` 启动 Python 禁缓存）。
5. **正确的事故复盘写进 skill**——`references/strava-gcj02-wgs84-correction.md` 写的是**正确算法**（来自 strava_auto 项目），但 `strava-upload.py` 里的实现是**从零抄**的，跟 reference 完全脱节。下次有人（包括我自己）来修这个 bug，先看 reference（已知对），别去相信代码（已知错）。**注意：该 reference 的二进制 patch 部分（含 CRC 多项式）也是错的，不要照抄。**
6. **fitdecode 0.11 enum 转 string 是默认行为**——FitReader 不会把 enum 留成 int 给你，要么用 `f.raw_value` 拿原始 int，要么用其他库。06-04 实战发现，6/04 是直接坑。

## 待办（已全部完成）

- [x] 重写 `correct_fit_file` 的 message parser → **放弃 binary patch，换 GPX 中介法**
- [x] 写自动化测试：拿一个 .fit 跑 `--correct-coords`，验证产物 body diff > 0 + 至少 1 条 record 坐标变了 + fitparse 能解析 + Strava 能处理
- [x] 把 SKILL.md 顶部 P0 警告删掉，把版本号从 1.5.0 升到 1.6.0
- [x] 修复 `_MESG_NUM` 白名单漏 `sport`（导致 sport=Other，标题退化）—— **新翻车点**，见 `references/correct-coords-gpx-mediation.md`
