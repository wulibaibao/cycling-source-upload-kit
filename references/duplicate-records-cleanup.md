# 历史重复记录的识别与清理（2026-10-26）

四个平台的历史数据都检查过，重复情况**不一样**：

| 平台 | 总数 | 重复 | 能否自助清理 |
|---|---|---|---|
| 迈金 / OneLap | 456 | **0** | — |
| 行者 | 308 → **301** | 7 组 | ✅ `DELETE /api/v1/pgworkout/{id}` |
| IGPSPORT | 519 | **74** | ❌ 无删除 API（2026-10-26 穷尽排查确认） |
| Strava | 361 | 1 组（0.00km 空活动） | ✅ `DELETE /api/v3/activities/{id}` |

## 1. 行者：可以删

```http
DELETE https://www.imxingzhe.com/api/v1/pgworkout/{workout_id}
Authorization: Bearer <access_token>
→ 200 OK
```

`openapi/v1/activities/` 只有 GET（405），删除必须走**私有接口** `api/v1/pgworkout/`。

### 识别影子副本：看 `credits`

同一活动被重复上传时，行者会建两条：
- `credits > 0` → 正式记录（正常显示、有积分）
- `credits == 0` → 影子副本（重复上传产生的）

实测：行者 308 条里有 7 条 `credits=0`，每条都有一条同日期同距离的 `credits>0` 副本。
删掉 7 条影子副本后 → 301 条，同日期同距离组数 **0**。

### 清理脚本（一次性，慎用）

```python
import requests, datetime
H = {"Authorization": f"Bearer {access_token}"}

def key(a):
    t = datetime.datetime.fromtimestamp(a.get("start_time", 0) / 1000)
    return (t.strftime("%Y-%m-%d"), round((a.get("distance") or 0) / 1000, 2))

for a in [x for x in xz if x.get("credits") == 0]:
    partners = [x for x in xz if key(x) == key(a) and x.get("id") != a["id"] and x.get("credits")]
    if not partners:
        print(f"❌ {a['id']} 没有配对记录，跳过"); continue
    r = requests.delete(f"https://www.imxingzhe.com/api/v1/pgworkout/{a['id']}", headers=H, timeout=25)
    print(f"{'✅' if r.status_code == 200 else '❌'} 删 {a['id']} → 保留 {partners[0]['id']}")
```

**必须先验证配对存在**（同日期 + 同距离 + `credits>0`），否则会删掉唯一的一条。

### 根因

`openapi/v1/uploads/` 上传记录里同一 md5 出现 2–3 次（实测 198 条记录里 18 组重复），
来源是 **2026-07 第一版 Strava→行者脚本还没做去重闸门**的时候。
现在的三层闸门（服务端活动 / md5 / state）已能挡住。

## 2. 迈金：本来就干净

`www.onelap.cn` 456 条，同日期同距离组数 **0**。原生数据源，没有历史导入问题。

迈金**有**删除接口：`DELETE /api/otm/ride_record/delete/{id}` ✅（实测有效，
清理过一条探测产生的重复 `6adee216b48d416dc20c9a94`）。

## 3. IGPSPORT：74 组真重复，但删不掉

### 3.1 ⚠️ `dataStatus=4` 不是「影子副本」标记（2026-10-26 修正）

早期文档写「`dataStatus=4` = 影子副本」是**错的**。实测 519 条的状态分布：

| `dataStatus` | 条数 | `analysisStatus` | `errorType` | 性质 |
|---|---|---|---|---|
| `1` | 438 | 全 1 | 0/缺 | 正常条目 |
| `4` | 75 | **全 1（分析已完成）** | 0/缺 | **正常分析完的活动**，只是被判重留下的 |
| `0` | 6 | 1/0 | **1** | ⚠️ **这才是真正的错误记录**（含 1 条 0.0m 空活动） |

`dataStatus=4` 的记录：功率/踏频/心率/爬升**数据完整**，不是残缺条目。
`dataStatus=0` 的 6 条才带 `errorType=1`（分析失败）—— 其中 `57995529` 距离 0.0m。

### 3.2 74 组重复的判定口径（用容差，不要用字符串相等）

早期统计出「72 条缺 3 条」是**精度误报** —— 迈金 65.12km vs IGPS 65116.7m 直接比字符串。
**必须用容差匹配**：

```python
# 同日期 + 距离差 ≤ 0.5m + 时长完全相同 → 真重复
if abs(a["rideDistance"] - b["rideDistance"]) <= 0.5 and a["recordTime"] == b["recordTime"]:
```

改用容差后：**74 组严格重复**（距离完全相等、时长完全相等，零误差）。

### 3.3 两组的来源不同

| | title 格式 | 示例 |
|---|---|---|
| 正式条目 | `MATCH_<uid>-YYYY-MM-DD-HH-MM-SS-log` | `MATCH_470592-2026-02-25-20-48-…` |
| 重复副本 | 纯数字 `.fit` | `18615857830.fit` |

副本 title 里的数字**不是 Strava activity ID**（逐个查 Strava API 全部 404），
是迈金云端自己的 FIT 编号。副本详情里有 `dataSyncStravaStatus=99`。

涉及日期集中在 **2025-09 ~ 2025-11** 和 **2026-01 ~ 2026-02** 两段。

### 3.4 删除接口：2026-10-26 穷尽排查，确认没有

**候选端点**（`deleteActivity` / `delete` / `remove` / `delActivity` / `removeActivity` /
`batchDelete` / `deleteById` / `deleteRecord` / `deleteFile` / `deleteUpload` /
`deleteUserActivity` / `deleteUserSport` / `deleteMyActivity` / `cancelActivity` 等）
全部返回 `{"code":1003,"message":"服务器异常"}`（带正确签名也一样）。

**排查覆盖**：
- Web App 全部 50 条路径 —— 无删除端点
- GitHub 上 4 个成熟第三方实现（`NenoSann/igpsport_sync`、`Dunky-Z/FitSync`、
  `Anathleticbicyclist/sports-data-sync-multiplatform`、`fooooxxxx/igpsport-export-fit-files`）
  —— 都只有查询/详情/下载/上传
- 盲扫 **1600+** 个「前缀 × 方法名」组合 —— 全无命中

**⚠️ 排查方法论见 `references/igpsport-api-probe.md`** —— 里面有可靠的
「端点存在 / 不存在」判据（别再被 `Allow: DELETE` 误导），以及从 JS bundle /
GitHub 搜索提取端点路径的正确姿势。

唯一半成品是 `mergeActivities`（合并活动），参数 `{"rideIds": [...]}`：
对重复副本必然返回「合并失败，存在时间重叠」。

### 3.5 用户无感

**✅ 已由用户确认（2026-10-26）**：IGPSPORT App 的活动列表里，同一天同距离的骑行**没有出现两次**。
App 端只展示 `dataStatus=1`。

- **接口层** 519 条 = 438 条 `dataStatus=1` + 75 条 `dataStatus=4` + 6 条 `dataStatus=0`
- **App 端**只展示 `dataStatus=1`，用户无感
- **服务端删不掉**，但也不需要删 —— 不占用户视图、不干扰同步
- 列表接口 `totalRows` 恒定且不提供 `dataStatus` 过滤参数 → 副本只在 API 层可见

**不影响同步**：去重闸门比对的是 `dataStatus=1` 的记录，副本不会干扰匹配。

## 4. Strava

361 条里只有 1 组重复：`2025-04-27 0.00km`（`14301402123` / `14301402306`）—— 两条空活动。
可删：`DELETE https://www.strava.com/api/v3/activities/{id}`。
