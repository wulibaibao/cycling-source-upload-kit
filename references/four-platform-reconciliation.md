# 四平台一致性盘点（2026-10-26 实测）

用于「让几个平台数据保持一致」这类需求：把 Strava / 迈金 / IGPSPORT / 行者 的活动全量拉下来，
按「日期 + 距离」匹配，找出每个平台缺什么。

## 各平台列表接口 + 正确分页参数

| 平台 | 接口 | 分页参数 | 坑 |
|---|---|---|---|
| 行者 | `GET https://www.imxingzhe.com/api/v1/pgworkout/` | **`offset` / `limit` / `sport=3`** | 用 `page`/`page_size` 会只返回 20 条，制造「缺几百条」的假象 |
| IGPSPORT | `queryMyActivity` | `pageNo` / `pageSize=100` / `sort=1` / `reqType=0`，读到 `totalPage` | `rideDistance` 单位是米 |
| 迈金 | `POST https://www.onelap.cn/api/otm/ride_record/list` | `page` / `limit=20`，读到 `pagination.total_pages` | host 必须是 `www.onelap.cn`（`u.onelap.cn` 是旧副本，缺日常骑行） |
| Strava | `GET https://www.strava.com/api/v3/athlete/activities` | `after` / `per_page=100` / `page` | `start_date_local` 是本地时间，别做时区偏移 |

## 匹配口径

同一天 + 距离误差 ≤ `max(0.3km, 3%)`。必须允许误差 —— 迈金/IGPS/行者 的距离字段舍入不同。

**一天两次骑行**要靠距离区分：2026-08-22 有 244.97km 和 221.51km 两次，是两次不同的骑行，不是同一条。

## 行者的两个坑

### 1. md5 闸门假阳性

`strava-to-xingzhe-sync.py` 有两道去重闸门：
- 闸门 1：服务端活动列表里有「相同时间+距离」→ 判 `exists_server` 跳过。
- 闸门 2：FIT 的 md5 已在行者**上传记录**里 → 判 `exists_md5` 跳过。

**闸门 2 会假阳性**：`#20537327496`（2026-10-06, 19.95km）md5 在上传记录里，但活动列表里查不到 ——
说明当时上传接口收下了，后台又把它当重复数据过滤掉了（当天 07:48 已有 180.93km 的记录）。

**解法**：对这种条目用 `--force-id <strava_id>` 强制重传。实测 `#20537327496` → workout `225884086`，回读校验通过。

### 2. 状态文件位置

`strava_state.json`（**不是** `state.json`），在 `~/.hermes/xingzhe_sync/` 下。
状态值：`uploaded` / `exists_server` / `exists_md5` / `failed` / `duplicate_9006`。

## 补传的正确做法

```bash
# 1) 先 dry-run，看脚本自己的闸门判什么
python3 strava-to-xingzhe-sync.py --since 2026-09-26 --until 2026-10-26 --dry-run

# 2) 正常传（不带 force-id，脚本自带服务端去重）
python3 strava-to-xingzhe-sync.py --since 2026-09-26 --until 2026-10-26

# 3) 只有「md5 假阳性」的条目才单独强制
python3 strava-to-xingzhe-sync.py --since 2026-10-06 --until 2026-10-07 --force-id 20537327496
```

**不要用 `--ignore-gate`** —— 那会真的产生重复记录。

## 边界

**只补近 30 天的真实缺口。** 2024–2025 的活动本来是逐年手工同步的，
批量灌进去会污染数据（Strava 361 条里有 260+ 条是 2024–2025 的老活动）。

## 结果（2026-10-26）

近 30 天缺口 14 条 → 补齐后**零缺口，四平台（迈金/IGPSPORT/行者）2026-10 起各 20 条**。


---

用户要求「几个平台数据保持一致」时，脚本本身**只跑 D2–D0 窗口**，历史缺口要单独补。

**盘点脚本逻辑**（四平台全量拉取 → 按「日期 + 距离±3%」匹配）：
- 行者活动列表：私有接口 `GET /api/v1/pgworkout/`，参数 **`offset`/`limit`/`sport=3`**（**不是** `page`/`page_size`！用错分页参数只会拿到 20 条假象）。
- IGPSPORT：`queryMyActivity`，`pageNo`/`pageSize`/`sort=1`/`reqType=0`，读到 `totalPage`。
- 迈金：`POST https://www.onelap.cn/api/otm/ride_record/list`，`page`/`limit=20`，读到 `pagination.total_pages`。
- Strava：`GET /api/v3/athlete/activities`，`after`/`per_page=100`。

**补传**：`strava-to-xingzhe-sync.py --since <起> --until <止>`（不带 `--force-id` 时脚本自带服务端去重闸门，会把「已有」判成 `exists_server` 跳过；`--force-id` 只给**单条**用）。

**行者的两个坑**：
1. **md5 闸门会假阳性**：`#20537327496` 的 md5 已在行者上传记录里，但活动列表里查不到（当时上传接口收下了，后台把它当重复数据过滤掉）。这种要 `--force-id` 强制重传。
2. **强制重传会覆盖 state**：传完后 `strava_state.json` 里该条从 `exists_md5` 变成 `uploaded`，状态一致。

**结果（2026-10-26）**：近 30 天 14 条缺口全部补齐（12 条正常 + 2 条被服务端已有判跳过），补后 **近 30 天零缺口，四平台各 20 条**。

详见 `references/four-platform-reconciliation.md`（各平台列表接口 + 正确分页参数 + 匹配口径 + 行者两个坑）。

**历史老活动不要批量补** —— 2024-2025 的活动本来是逐年手工同步的，批量灌进去反而污染数据。只补「近 30 天」这种真实缺口。
