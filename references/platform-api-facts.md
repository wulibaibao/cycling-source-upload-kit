# 各平台接口事实（2026-10-26 整理）

### 迈金/顽鹿（OneLap 中国版）

**迈金中国版 App 就是「顽鹿运动」**，云端 = `u.onelap.cn`。用户 uid = `<DEVICE_UID>`（= C706 码表序列号）。

| 操作 | 端点 | 关键点 |
|---|---|---|
| 登录 | `POST www.onelap.cn/api/login` | body `{account, password: md5hex(密码)}` → `data[0].token` |
| 列表 | `POST u.onelap.cn/api/otm/ride_record/list` | body `{page, limit}` + 签名头 → `data.list[]`（`id`/`start_riding_time`/`distance_km`/`name`） |
| 详情 | `GET u.onelap.cn/api/otm/ride_record/analysis/{id}` | → `data.ridingRecord.fitUrl` |
| FIT 下载 | `GET .../fit_content/{base64(fit_key)}` | **必须 GET，fit_key 要 base64**。POST → 405 |

**签名**：`md5("&".join(f"{k}={v}" for k in sorted(params ∪ {nonce,timestamp})) + "&key=<ONELAP_SIGN_KEY>")`
nonce = 16 位随机字母数字，timestamp = unix 秒。**body 不参与签名。**

**两种 fitUrl 格式**（都要支持）：
- `MATCH_<DEVICE_UID>-YYYY-MM-DD-HH-MM-SS-log.st` — 迈金 App 直接传给 IGPSPORT 的记录
- `geo/YYYYMMDD/Magene_C706_<epoch>_<uid>_<epoch>.fit` — 迈金云端存的原始 FIT

上传时把 `.st` 改回 `.fit` 作为 fileName。

### IGPSPORT

| 操作 | 端点 | 关键点 |
|---|---|---|
| 短信登录 | `POST prod.zh.igpsport.com/service/auth/account/login/phone` | body `{appId:"igpsport-web", phone, code}` |
| 刷新 token | `GET /service/auth/token/refresh?refreshToken=***` | ⚠️ **驼峰参数名**；⚠️ **必须带有效 Bearer**；⚠️ **refresh_token 每次刷新后轮换，必须存新值** |
| 活动列表 | `GET /service/web-gateway/web-analyze/activity/queryMyActivity` | `data.rows[]`；`startTime` **只有日期**（`2026.10.25`） |
| 活动详情 | `GET .../queryActivityDetail/{rideId}` | 含完整 `startTime`（`2026-10-14 07:21:08`） |
| 下载 FIT | `GET .../getDownloadUrl/{rideId}` | → OSS 直链，无需鉴权 |

**上传 = 三步 OSS 流程**（不是单次 POST）：
```
1. GET  /service/sportg/third-party-server/oss/getSignedUrl?fileExtension=.fit
        → {signedUrl, ossId}
2. PUT  signedUrl                    → 阿里云 OSS（igp-zh.oss-cn-hangzhou.aliyuncs.com）
3. POST /service/web-gateway/web-analyze/activity/uploadByOss
        body {fileName, ossName}
```

**签名**：`base64(HMAC-SHA256(key=<IGP_HMAC_KEY>, sign_str))`
`sign_str = method\npath(含query)\ntimestamp\nnonce\nsha256hex(body)`
Header：`x-signature` `x-timestamp` `x-nonce` `x-access-key` `x-platform: web` + `Authorization: Bearer *`

token 有效期 7 天（`expires_in=604800`），refresh_token 是 64 字符随机串（不是 JWT）。

### 行者（XOSS / imxingzhe.com）

| 操作 | OpenAPI | 私有接口（更强） |
|---|---|---|
| 活动列表 | `GET /openapi/v1/activities/`（**分钟级延迟**） | `GET /api/v1/pgworkout/?sport=3`（**实时**，需翻页） |
| 删除活动 | ❌ `DELETE` → 405 | ✅ `DELETE /api/v1/pgworkout/{id}/` → 200，立即生效 |
| 上传 | `POST /openapi/v1/uploads/` | `POST /api/v1/fit/upload/`（有 9006 判定） |

**私有接口带 OAuth Bearer 即可用**（实测 200，无需 sessionid cookie）。用私有接口做活动索引 —— 避免 OpenAPI 延迟导致已删记录仍被命中。

- 行者 sport：1徒步 2跑步 3骑行。FIT 里 `sport=2(cycling) + sub_sport=1` 是非法组合（treadmill）→ 会被判成跑步 → **`sub_sport` 必须用 0**
- 上传字段：`title`(≤32) `detail`(≤1500) `fit_filename` `file_type=fit` `md5`(32位小写) + multipart 字段名 `fit_file`
- 读轨迹：`POST /openapi/v1/workout/{id}/stream/`（**必须 POST**，GET → 405），返回 `data.location` = `[[lng, lat], ...]`
- **9006 的真相**：OpenAPI 对相同 MD5 **不返回 9006**，而是直接创建新活动。9006 只在私有接口出现 ⇒ **MD5 索引闸门是必需的**

### Strava

| 项 | 结论 |
|---|---|
| 原始文件下载 | **做不了**。`GET /api/v3/activities/{id}/download` 对所有活动都 404，因为账号 `premium=False, summit=False`（付费功能，API 一并锁） |
| 唯一可自动化路径 | `GET /api/v3/activities/{id}/streams?keys=latlng,time,heartrate,watts,cadence,altitude,distance&key_by_type=true` → `garmin_fit_sdk` 重建 FIT |
| 2026-07 政策 | API 曾对非 Premium 全 403 `Inactive`。**07-02 实测：切换到新 Application（client_id 217315→178483）即可恢复，不必订阅 Premium** |
| token | `~/.hermes/strava_token.json`，access_token 必须 40 字符。**OAuth code 一次性**，client_id/secret/code 任一改就得重新授权 |

**链路 A 只能靠 streams 重建 FIT**，拿不到原始文件。
