---
name: strava-xingzhe-sync
description: Use when syncing Strava activities to 行者 (imxingzhe.com), scheduling the daily Strava→行者 upload, fixing duplicate 行者 activities, or debugging FIT coordinate/offset issues. Covers Strava API limits, 行者 OpenAPI endpoints, GCJ-02 vs WGS-84, and the dedupe gate design.
---

# Strava → 行者 自动同步

## 关键事实（实测，勿再重复验证）

| 项 | 结论 |
|---|---|
| Strava 原始文件下载 | **做不了**。`GET /api/v3/activities/{id}/download` 对用户所有活动都 404 `Record Not Found`，因为账号 `premium=False, summit=False`。原始下载是付费功能，API 一并锁。 |
| 唯一可自动化路径 | `GET /api/v3/activities/{id}/streams?keys=latlng,time,heartrate,watts,cadence,altitude,distance&key_by_type=true` → 用 `garmin_fit_sdk` 重建 FIT → `POST https://www.imxingzhe.com/openapi/v1/uploads/` |
| 坐标系 | **Strava 的不固定**，取决于用什么 App 上传（实测：MAGENE app 上传的 C706 → Strava 里已是 GCJ-02；其他活动 → WGS-84）。行者原生 = GCJ-02。转换后实测偏差平均 0.039 m / 最大 0.071 m。**用投票法判定，不要写死** —— 见下方「坐标系投票法」。 |
| 行者上传幂等 | **服务端不按 MD5 幂等**。同一文件重复提交会生成新 workout_id → 重复活动。必须自己做闸门。 |
| 行者删除 | OpenAPI 没有（`DELETE` → 405），但**私有接口可以**：`DELETE /api/v1/pgworkout/{id}/` → 200，立即生效。 |
| 行者 sport | 1徒步 2跑步 3骑行。FIT 里 `sport=2(cycling) + sub_sport=1` 是非法组合（treadmill），会被判成跑步 → `sub_sport` 必须用 0。 |
| 行者上传字段 | `title`(≤32) `detail`(≤1500) `fit_filename` `file_type=fit` `md5`(32位小写，服务端会校验) + multipart 文件字段名 `fit_file`。 |
| 行者读取轨迹 | `POST /openapi/v1/workout/{id}/stream/`（**必须 POST**，GET 返回 405）。返回 `data.location` = `[[lng, lat], ...]`。 |
| 行者活动列表 | `GET /openapi/v1/activities/?limit=20&offset=N&start_timestamp=<ms>&end_timestamp=<ms>` |
| 行者上传列表 | `GET /openapi/v1/uploads/?limit=20&offset=N` → 含 `md5` 字段，可用于去重 |

## 脚本

- `~/strava-to-xingzhe-sync.py` — 主同步脚本（独立实现，不 import 其他脚本）
- `~/scripts/run-xingzhe-sync.py` — cron 入口，输出报告给 QQ，无新活动时零输出
- `~/strava-to-xingzhe.py` — 旧版，**不要用**（去重靠本地 state，已实证会重复上传）
- cron job_id `3670c2356238`，每天 07:30，`no_agent=true`

## 同步窗口

**用户需求：每天早上上传「前天」(day-2) 当天的数据。**

cron 入口用 `--since <前天> --until <前天>` 固定成单日窗口（本地时区 00:00:00~23:59:59）。

不要改成"最近 N 天"——虽然去重闸门能防重复，但需求明确是 day-2 单日。已验证：
- 单日多条（2026-10-06 → 2 条）✅
- 无活动日（2026-10-13 → 0 条）✅
- 跨月（2026-09-30 → 0 条）✅

主脚本 `--until` 语义：`until` 当天的 23:59:59，Strava 查询用 `before = until + 86400`。

## 行者私有接口（重要，比 OpenAPI 强）

| 接口 | OpenAPI | 私有接口 |
|---|---|---|
| 活动列表 | `GET /openapi/v1/activities/`（索引有**分钟级延迟**） | `GET /api/v1/pgworkout/?sport=3`（**实时**，需翻页） |
| 删除活动 | ❌ `DELETE` → 405 | ✅ `DELETE /api/v1/pgworkout/{id}/` → 200，立即生效 |
| 上传 | `POST /openapi/v1/uploads/` | `POST /api/v1/fit/upload/`（有 9006「已上传」判定） |

**私有接口带 OAuth Bearer 即可用**（实测 200，无需 sessionid cookie）。

用私有接口做活动索引 —— 避免 OpenAPI 延迟导致已删除的记录仍被命中。

## 9006 判重的真相

**OpenAPI 上传对相同 MD5 不返回 9006**，而是直接「上传成功」并创建新活动
（实测：`upload_id 38532699`, `workout_id 225818351`）。
9006 只在行者的**私有上传接口**上出现。

⇒ 所以 MD5 索引闸门是必需的，不能依赖 9006。

## 健康检查（判坏记录）

`xz_record_healthy(access, workout_id, expect)`：
- 有 `expect`（Strava 首点）→ 比对偏差 >2 km 判坏（最准，能抓经纬度互换）
- 无 `expect` → 启发式：`lat == lon` 或 `90<|lat|<140 且 20<|lon|<50` 判坏

判坏后**自动删除**（私有接口）再重传。

## 坐标系投票法

投票法（借鉴 ridefitsync）判定方向要反：
- ridefitsync 用 `offsetIfWgs > offsetIfGcj` → **判定方向整体反了**（实测 10/10 全票判错）
- 正确：`margin = (off_gcj - off_wgs) / max(...)`，`margin > 0` ⇒ WGS-84

实测 1 个样本点即可判对，混入境外点/重复停站点均不影响。

**Strava 的坐标系不固定**！取决于用户用什么 App 上传：
- MAGENE app 上传的 C706 → Strava 里已是 GCJ-02
- 其他活动 → WGS-84

⇒ 平台表不能写死 Strava，必须靠投票判定。`SOURCE_COORD_SYSTEM["strava"] = None`。

## 去重设计（核心）

服务端索引是真相，本地 state 只是缓存。三层闸门按顺序：

1. **服务端活动索引**命中 → 跳过。键 = `(start_time ± 15s, distance ± max(30m, 0.5%))`
   用 `server_has_healthy_activity()`，**不是** `server_has_activity()` —— 见下方「健康检查」。
2. **服务端 uploads 的 MD5** 命中 → 跳过
3. 都没有 → 才上传

三态语义（防"超时后重传导致重复"）：
- `uploaded` → 跳过
- `unknown`（超时/连接中断）→ 下一轮**先查服务端索引**，查到就补记 `uploaded`，查不到才重传。绝不盲传。
- `failed` → 下一轮重试（服务端索引仍作前置闸门）

**本地 state 清空后重跑必须仍是 0 新增** —— 这是验证去重不依赖本地文件的标准测试。

## 健康检查（防坏记录永久挡住）

`xz_record_healthy(access, workout_id, expect)`：拉 `POST /openapi/v1/workout/{id}/stream/` 取首点。
- 有 `expect`（Strava 原始首点）→ 偏差 > 2 km 判坏（最准，能抓经纬度互换）
- 无 `expect` → 启发式：`lat == lon` 或 `90 < |lat| < 140 且 20 < |lon| < 50` 判坏

原因：2026-10-20 调试时写反过一次经纬度，产生一条轨迹塌成直线的记录。
它和服务端正常记录同时间同距离，会被闸门命中并永久挡住正确数据。

**判坏后自动删除（私有接口）再重传**，日志会打 `🧹 已删除损坏记录 workout <id>`。
不要再依赖用户手动删。

注意：`server_has_healthy_activity()` 会跳过坏记录继续找下一条；
若日志出现「跳过：行者已有相同活动」但那条其实坐标有问题，说明健康检查没生效，排查 `expect` 是否传了。

## 时区基准

行者 `start_time`（ms）与 Strava `start_date_local` 去掉 `Z` 后当本地时间解析，
两者**同为 UTC epoch，差 0 秒**。已验证。写测试时别自己算时间戳，容易错 8 小时。

## 踩过的坑（都实际发生过）

1. **经纬度写反**：
   ```python
   lon2, lat2 = wgs_to_gcj(lon, lat)
   lat, lon = lat2, lon2   # ✅ 顺序不能反
   ```
   写反的话 FIT 里 lat==lon，轨迹塌成直线。

2. **Strava `start_date_local` 带 Z 后缀但内容是本地时间**：
   ```python
   sdl = (a.get("start_date_local") or "").rstrip("Z")
   start_dt = datetime.fromisoformat(sdl).astimezone()
   ```
   直接 `fromisoformat(x).astimezone()` 会多偏 8 小时 → 去重闸门全部失配 → 全部重复上传。

3. **GCJ 公式分母漏 `a` 因子** → 算出 29047° 的经度。必须与 `strava-upload.py` 的 `_gcj_delta` 同源。

4. **FIT `sub_sport`**：cycling 下用 1(treadmill) 会被判成跑步。

## 验证方法

```bash
# 幂等：连跑两次，第二次必须 0 新增
python3 ~/strava-to-xingzhe-sync.py --since 2026-10-01 --limit 30

# 强制重传（忽略所有闸门，修复/重建用）
python3 ~/strava-to-xingzhe-sync.py --force-id <strava_id>
```

标准回归（清空 state + FIT 缓存后必须 0 新增）：
```bash
rm -f ~/.hermes/xingzhe_sync/fit/*.fit
python3 -c "import json,os; p=os.path.expanduser('~/.hermes/xingzhe_sync/strava_state.json'); s=json.load(open(p)); s['synced']={}; json.dump(s,open(p,'w'),indent=2)"
python3 ~/strava-to-xingzhe-sync.py --since 2026-10-01 --until 2026-10-20 --limit 30
```

上传后**回读校验**：`GET /openapi/v1/activities/{workout_id}/` 检查 `sport=3` 和 `distance` 误差 < 2%。

## 2026-10-25 融合 ridefitsync 的三个设计

从 `https://github.com/sizaif/ridefitsync`（Flutter App，手动点同步，无定时）吸收：

1. **平台坐标系表** → `SOURCE_COORD_SYSTEM` / `TARGET_COORD_SYSTEM`
2. **投票法自动识别坐标系** → `detect_coord_system()`
3. **行者重复检测** → 9006 识别（但见上方「9006 判重的真相」，实际靠 MD5 索引）

**注意**：ridefitsync 的 Strava 只是「上传目标」不是「数据源」——
它的 `strava_service.dart` 只有 `uploadFitFile`，没有任何下载/拉取活动的方法。
它解决 Strava 无原始 FIT 的方式是**换源平台**（顽鹿/iGPSPORT/行者）。
所以它不直接解决本项目的问题，只能借鉴设计。

另外从该项目的思路延伸挖到的（ridefitsync 本身没提）：
- 行者私有接口 `DELETE /api/v1/pgworkout/{id}/` → 可删活动 ⇒ 自动清理坏记录
- 行者私有接口 `GET /api/v1/pgworkout/` → 实时索引（OpenAPI 有分钟级延迟）
