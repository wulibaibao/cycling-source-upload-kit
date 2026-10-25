# FIT 文件修复方法选择决策树（2026-06-01 整合）

收到一份有问题的 FIT 文件，需要在**三种修复方法**中选择。本文档对比各方法适用场景、优缺点、复杂度。

## 快速决策表

| 偏移类型 | 推荐方法 | 工具 | 难度 | 时间 |
|---------|---------|------|------|------|
| **GCJ-02 偏移**（路线恒定方向偏 300-500m） | ❌ 不要用 `--correct-coords`（06-04 废）→ 用 GPX 中介法 | `scripts/correct_fit_gpx.py` + `garmin_fit_sdk` | ⭐⭐ | 10 分钟 |
| **两段式漂移**（前段正常，后段偏 0.1-0.5°） | GPX 中介法 | `fitdecode` + `garmin_fit_sdk` | ⭐⭐⭐ | 30 分钟 |
| **GPS 完全失锁**（坐标飞掉 68°N/217°E） | 顽鹿 GPX 替换 + 重建 | `convert_fit_encoder.py` | ⭐⭐⭐⭐ | 1 小时+ |
| **GPS 跳点**（孤立点飞 1km+） | 简单删除 | `fitdecode` 过滤 | ⭐⭐ | 10 分钟 |
| **Strava 地图匹配小偏移**（30-100m） | ❌ 不需要修 | — | — | — |

## 三种修复方法详解

### 方法 1：Binary patch（最快，依赖 `--correct-coords`）

**原理**：直接修改 FIT 二进制文件中的 4 字节 sint32 坐标字段，重新计算 CRC-16，写回。

**实现**：`scripts/correct_gps.py` + `strava-upload.py` 的 `--correct-coords` 参数。

**适用**：
- ✅ GCJ-02 偏移（最常见）
- ✅ 整体均匀偏移（任意方向、任意距离）

**不适用**：
- ❌ GPS 完全失锁（要重建新 record，binary patch 改不了）
- ❌ 两段式漂移（需要分段校正，binary patch 处理不了复杂逻辑）
- ❌ 跳点删除（需要重新组织 record 流）

**优点**：
- ⚡ 极快（< 1 分钟，包括上传）
- 📦 保留所有原始元数据（device_info, capabilities, field_description 等厂商字段全保留）
- 🚫 不需要 garmin_fit_sdk Profile 字典

**缺点**：
- 🔒 只能改坐标，不能删除/插入 record
- 🔒 需要手算 semicircles（FIT 用 sint32 二进制存储坐标）
- 🐛 CRC-16 算错会损坏文件（`scripts/correct_gps.py` 已实现查表法）

**参考**：
- `references/strava-gcj02-wgs84-correction.md` — 完整算法和二进制实现
- 复制自 [xqdoo00o/strava_auto](https://github.com/xqdoo00o/strava_auto)

---

### 方法 2：fit-tool patch（最简，保留元数据）

**原理**：用 `fit-tool` 库的 `update` 子命令原地修改 FIT 坐标，自动处理 CRC。

**实现**：
```bash
fit-tool update <file.fit> --lat <new_lat_semicircles> --lon <new_lon_semicircles>
```

**适用**：
- ✅ 与 binary patch 类似的场景（GCJ-02 偏移、整体偏移）
- ✅ 需要原地修改、不想重建整个文件

**不适用**：
- ❌ 大部分复杂场景（fit-tool 只能 update，不能 delete/insert）

**优点**：
- ⚡ 极简（一条命令）
- 📦 保留所有原始元数据
- ✅ 自动 CRC 处理（不需要手算）

**缺点**：
- 🔒 一次只能 update 一条 record，不支持批量（要写循环）
- 🔧 需要安装 fit-tool（`pip install fit-tool`）

**状态（2026-06-01）**：
- ⚠️ 本次会话**未实际使用** fit-tool（用户 06-01 的 FIT 在室内记录，没有真实 GPS 偏移，binary patch 也不需要）
- ✅ 06-01 SKILL.md 主文档已记录此方案作为备选

---

### 方法 3：GPX 中介法 + Encoder 重建（最强大）

**原理**：把 FIT → GPX → FIT，中间用 GPX 统一格式过滤坏数据，最后用 garmin_fit_sdk Encoder 重建。

**实现**：
1. `fitdecode.FitReader` 解析原始 FIT → 提取 record（lat/lon/alt/hr/cad/spd/pwr/timestamp/distance）
2. semicircles→degrees，做坐标校正（GCJ-02→WGS-84、分段偏移修复等）
3. 写 GPX 文件（含 extensions 里 hr/cad/spd/pwr）
4. 从 GPX 读回，写 `garmin_fit_sdk.Encoder`，只保留 essential 元数据（file_id, session, activity），丢弃其他
5. 上传重建的 FIT

**适用**：
- ✅ 两段式漂移（最常用）—— 2026-05-30 验证通过
- ✅ GPS 完全失锁（用顽鹿运动 App 导出的 GPX 当基准替换原 FIT GPS 点）
- ✅ 跳点删除（GPX 阶段就过滤掉）
- ✅ 任何需要修改 record 数量/顺序的场景

**不适用**：
- ❌ 简单的 GCJ-02 偏移（杀鸡用牛刀，binary patch 1 分钟搞定）

**优点**：
- 💪 最强大（任何修改都能做）
- 🎯 坐标校正逻辑全部在 Python 字典里，调试容易
- 📦 GPX 中间格式通用，可读

**缺点**：
- 🐌 慢（30 分钟+）
- 🐛 复杂（5 步流程，每步都可能出错）
- ⚠️ **核心陷阱**：Encoder 重建会丢失大量原始元数据
  - `device_info`、`capabilities`、`field_description` 等厂商特定字段**不会写入**
  - 05-30 实测：直接 Encoder 输出会触发 Strava "There was an error processing your activity"
  - **必须用 GPX 中介法 + 手动只保留 essential 元数据**（file_id, session, activity）

**参考**：
- `references/gpx-intermediary-method.md` — 完整流程（推荐读这个）
- `references/fit-rebuild-encoder.md` — 原始 Encoder 方案（含元数据保留细节）

---

## 决策流程

```
用户反馈"路线不对"
    ↓
跑 `scripts/detect_gcj02_offset.py xxx.fit` 做 OSM 双向验证（5-10 分钟）
    ↓
确诊后选择方法：
    ↓
├── GCJ-02 偏移 → ❌ 不要用 `--correct-coords`（已坏，06-04 事故）
│              → ✅ 用 GPX 中介法 + 纠偏（`scripts/correct_fit_gpx.py` + garmin_fit_sdk）
├── 两段式漂移 → 方法 3 GPX 中介法（5/30 验证）
├── GPS 完全失锁 → 方法 3 + 顽鹿 GPX 替换
├── 跳点 → 方法 3 简化版（GPX 阶段删除）
└── Strava 地图匹配 → 不修
```

> ⚠️ 06-04 更新：方法 1（binary patch / `--correct-coords`）的 `strava-upload.py` 实现已废（4 个 bug，详见 `references/correct-coords-broken-2026-06-04.md`）。GCJ-02 偏移场景**改走方法 3（GPX 中介法）**。

## 关键经验

1. **GPX 中介法会丢 `distance` 字段** → Step 4 Encoder 写入时 inject `record['distance'] = int(r['distance'])` 即可
2. **`garmin_fit_sdk` 的 Profile 字典 key 是字符串**（不是 int）→ 需要 `{int(k): v for k, v in ...}` 修复
3. **fit-tool 一次只能 update 一条 record** → 大批量改坐标时写 Python 循环
4. **Encoder 重建后所有时间戳要保留** → 否则 Strava 认为是新活动（不影响上传但会改变 `start_date`）

## 与方法选择相关的"坑"

| 坑 | 后果 | 规避 |
|----|------|------|
| 用 binary patch 试图删除跳点 | CRC 算错，FIT 损坏 | 改用 GPX 中介法 |
| 用 Encoder 直接重建不保留元数据 | Strava "error processing" | 用 GPX 中介法 |
| 用 fit-tool update 不算 CRC | FIT 损坏 | fit-tool 内部处理 CRC，不需要手算 |
| GPX 中介法忘记 inject `distance` | Strava 显示 0 km | Encoder 写入时显式给 distance |
| 用 `--correct-coords` 处理两段式漂移 | 偏移方向距离不恒定，纠偏后反而更偏 | 改用 GPX 中介法手动分段 |

## ⚠️ 2026-06-04 重大修订：`strava-upload.py` 的 `--correct-coords`（binary patch 路径）整条废掉

**实测发现**（用 06-04 早的 MAGENE C706 FIT 测试）：
- `--correct-coords` 跑完，产物 FIT 的 **record 字节完全没改**（diff 跟原文件 0 字节差异，仅 header/尾部 CRC 改）
- 上传 Strava 返回 `malformed` 错误
- 即使把 data_msg_size 和 CRC 算法都修了，body 仍然没动

**根本原因**（双重 bug 堆叠）：

### Bug A：`_patch_fit_message` 假设 data message 跟 definition message 同结构（错的）

`strava-upload.py` 假设 FIT data message 结构是 `1+2+2+1+N*3+data`（跟 definition message 一样），但 **FIT data message 根本没有 reserved/endian/msg_num/num_fields 这 5B**：
- **Definition message**（bit7=1）：`header(1) + reserved(2) + msg_num(2) + num_fields(1) + N*3(field defs) + 0字节数据`
- **Data message**（bit7=0）：`header(1) + 数据`（msg_num 通过 local message type 引用前一个 definition message，**字段定义不在 data message 里**）

脚本里 `_patch_fit_message` 读 `msg_num = body[pos+3:pos+5]`——对 data message 来说这是**数据字节**而不是 msg_num，导致 `coord_fields` 永远是空，整条消息被原样返回。

### Bug B：CRC 算法 + 范围都错了（即使 patch 成功也写不出正确 CRC）

发现 3 个独立 CRC 问题：
1. **算法错了**：`0xEDB88320` 反射表是 256 元素 byte-level 错版；正确是 nibble-table（4-bit）+ CC01/D801 那张 16 元素表
2. **范围错了**：原代码 `new_crc = _fit_crc_update(0, data[:14])`——把 header[12:14]（CRC 字段自身）也喂入
3. **写错位置**：原代码把同一个 CRC 同时写到 header[12:14] 和文件末尾，但这两个 CRC **必须不同**：
   - `header_crc = CRC(header[0:12])` → 写 header[12:14]
   - `file_tail_crc = CRC(header[0:14] + body)`（**header CRC 字段自身也算进去**）→ 写文件末尾

### ✅ 推荐替代：GPX 中介法（已验证 06-04 可用）

**写新脚本 `correct_fit_gpx.py`（已实测通过）**：
1. `fitdecode.FitReader` 读原 FIT 所有 frame
2. 遍历 record/session/lap 消息，对 (lat, lon) 配对调 `gcj2wgs(deg_lat, deg_lon)`
3. 用 `garmin_fit_sdk.Encoder.write_mesg(dict)` 重建——只写 7 种核心消息：`file_id / user_profile / bike_profile / session / lap / record / activity`（其他全跳）
4. 产物 5988 records × 2 coords = 11976 个坐标成功纠偏
5. fitparse 验证产物 6004 messages ✅
6. Strava 上传成功（需先删原直传版避免 duplicate）

**脚本路径**：`~/correct_fit_gpx.py`（已落地，可复用）

### 决策流程更新（06-04 修订）

```
用户反馈"路线不对" / 默认 --correct-coords
    ↓
--correct-coords 上传 → Strava 返回 malformed（binary patch 路径当前已废）
    ↓
改用 GPX 中介法（correct_fit_gpx.py）
    ↓
上传成功 ✅
```

**未来再遇 GCJ-02 偏移**：直接走 `correct_fit_gpx.py`，不要碰 `--correct-coords`。要么修 binary patch 路径（修 3 个 CRC bug + 重写 data message 解析），要么明确弃用 `--correct-coords` 参数。
