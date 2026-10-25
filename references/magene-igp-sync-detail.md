---
name: magene-igp-sync
description: Use when syncing 迈金/Magene (顽鹿/OneLap) ride FIT files to IGPSPORT, debugging the OneLap cloud API reverse-engineering, the IGPSPORT OSS upload flow, or deduping IGPSPORT activities. Covers the u.onelap.cn signed API and the prod.zh.igpsport.com 3-step OSS upload.
---

# 迈金 (Magene/OneLap) → IGPSPORT 同步

## 关键事实（实测，勿再重复验证）

| 项 | 结论 |
|---|---|
| 迈金云端 = 顽鹿运动 | **迈金中国版 App 就是「顽鹿运动」**，云端 = `u.onelap.cn`。用户顽鹿 uid = `<DEVICE_UID>`（= C706 码表序列号）。所以迈金 FIT 就是 OneLap 的 `onelap` 源 —— ridefitsync 第一个源平台。 |
| 顽鹿登录 | `POST https://www.onelap.cn/api/login`，body `{"account": <账号>, "password": md5hex(密码)}` → `data[0].token` |
| 顽鹿列表 | `POST https://u.onelap.cn/api/otm/ride_record/list`，body `{page, limit}` + 签名头 → `data.list[]`（字段 `id`/`start_riding_time`/`distance_km`/`name`） |
| 顽鹿详情 | `GET https://u.onelap.cn/api/otm/ride_record/analysis/{id}` → `data.ridingRecord.fitUrl` |
| 顽鹿 FIT 下载 | `GET https://u.onelap.cn/api/otm/ride_record/analysis/fit_content/{base64(fit_key)}` —— **必须是 GET，且 fit_key 要 base64 编码**。用 POST 会 405。 |
| 顽鹿签名 | `md5("&".join(f"{k}={v}" for k in sorted(params ∪ {nonce,timestamp})) + "&key=<ONELAP_SIGN_KEY>")`，nonce=16 位随机字母数字，timestamp=unix 秒。**body 不参与签名。** |
| IGPSPORT 登录 | `POST https://prod.zh.igpsport.com/service/auth/account/login`，body `{appId:"igpsport-web", username, password}` → `data.access_token`；短信验证码：`/auth/account/SendVerificationCode` + `/auth/account/login/phone` |
| IGPSPORT 刷新 | `GET /service/auth/token/refresh?refreshToken=***` + `Authorization: Bearer <access_token>` → 新 access_token + **新 refresh_token**。<br>⚠️ 参数名是驼峰 `refreshToken`（用 `refresh_token` 报 Parameter is required）<br>⚠️ **必须带有效 Bearer** —— 无 Bearer 时返回 HTML 错误页（非 JSON）⇒ 只能在 access_token 仍有效时刷新<br>⚠️ **refresh_token 每次刷新后轮换**，必须保存新值，否则下次 403 |
| IGPSPORT 有效期 | access_token 7 天（`expires_in=604800`），refresh_token 64 字符随机串（不是 JWT） |
| IGPSPORT 上传 | **三步 OSS 流程**（不是 OpenAPI 简单 POST）：<br>1. `GET /service/sportg/third-party-server/oss/getSignedUrl?fileExtension=.fit` → `{signedUrl, ossId}`<br>2. `PUT signedUrl` → 阿里云 OSS（`igp-zh.oss-cn-hangzhou.aliyuncs.com`）<br>3. `POST /service/web-gateway/web-analyze/activity/uploadByOss` body `{fileName, ossName}` |
| IGPSPORT 签名 | `base64(HMAC-SHA256(key=<IGP_HMAC_KEY>, sign_str))`，`sign_str = method\npath(含query)\ntimestamp\nnonce\nsha256hex(body)`。Header `x-signature` `x-timestamp` `x-nonce` `x-access-key` `x-platform: web` + `Authorization: Bearer *` |
| **坐标系** | **不需要转换**。迈金 FIT 本身是 GCJ-02，IGPSPORT 也按 GCJ-02 原样存。实测上传后 IGPSPORT 存的 FIT 与源 FIT 坐标差 **0.00 m**（最大 0.00 m）。这是与「迈金→行者/Strava」的**关键区别** —— 那边要转，这边不要转。 |
| IGPSPORT 活动列表 | `GET /service/web-gateway/web-analyze/activity/queryMyActivity?pageNo=&pageSize=&sort=1&reqType=0` → `data.rows[]`，字段 `rideId`/`title`/`startTime`（只有日期 `2026.10.25`）/`rideDistance`/`dataStatus` |
| IGPSPORT 活动详情 | `GET /service/web-gateway/web-analyze/activity/queryActivityDetail/{rideId}` → 含完整 `startTime`(`2026-10-14 07:21:08`)/`rideDistance`/`totalMovingTime`/`avgSpeed`/`totalAscent` |
| IGPSPORT 下载 FIT | `GET /service/web-gateway/web-analyze/activity/getDownloadUrl/{rideId}` → `data` = OSS 直链，无需鉴权 |

## 脚本

- `~/onelap_client.py` — 顽鹿/迈金 API 客户端（登录/列表/详情/FIT 下载/签名）
- `~/igp_client.py` — IGPSPORT API 客户端（短信登录/列表/三步 OSS 上传/签名）
- `~/magene-to-igp.py` — 主同步脚本
- `~/scripts/run-magene-igp-sync.py` — cron 入口（前天单日窗口，无新活动零输出）
- cron job_id `3e3e0194d61b`，每天 07:30，`no_agent=true`
- 凭据：`~/.hermes/igp_sync/token.json`（IGPSPORT）、`onelap_token.json`（顽鹿），均 0600

## 两种 fitUrl 格式（都要支持）

| 格式 | 含义 | 例子 |
|---|---|---|
| `MATCH_<DEVICE_UID>-YYYY-MM-DD-HH-MM-SS-log.st` | 迈金 App 直接传给 IGPSPORT 的记录（IGPSPORT 里已有） | `MATCH_<DEVICE_UID>-2026-10-25-07-11-07-log.st` |
| `geo/YYYYMMDD/Magene_C706_<epoch>_<uid>_<epoch>.fit` | 迈金云端自己存的原始 FIT | `geo/20261006/Magene_C706_1791240682_<DEVICE_UID>_1791243785951.fit` |

上传时把 `.st` 改回 `.fit` 作为 fileName。

## 去重设计

**IGPSPORT 服务端索引是真相**，本地 state 只是缓存。

1. **本地 state**（`.hermes/igp_sync/state.json`，key = `{record_id}:{fitUrl}`）命中 → 跳过
2. **IGPSPORT 活动列表**按 `日期 + 距离容差` 命中 → 跳过
   - 容差：`max(50m, 距离 × 0.5%)`
   - 用**日期**而不是精确时间 —— OneLap 的 `start_riding_time`（FIT 开始时间）和 IGPSPORT 的记录时间（文件名里的时间）差几分钟到十几分钟
   - IGPSPORT 列表的 `startTime` 只有日期（`2026.10.25`），精确时间要查详情
3. 都没有 → 上传

**验证标准**：清空 state 后重跑必须 0 上传。

## 坐标系判断（重要陷阱）

迈金 FIT **已经是 GCJ-02**。不要因为「国内平台用 GCJ-02」就盲目加 `gcj2wgs` 转换。

判据：**IGPSPORT 存的 FIT 与源 FIT 坐标差 0.00 m** → 说明 IGPSPORT 原样存储，不转换。

对比：迈金 → 行者 / Strava 需要 gcj2wgs（因为那两边按 WGS-84 存）。**每个目标平台的期望坐标系不同，必须逐一实测确认。**

## fitdecode 0.11 API 坑

```python
import fitdecode
with fitdecode.FitReader(path) as fr:
    for fr_ in fr:                       # 迭代器直接吐 frame
        if fr_.frame_type != fitdecode.FIT_FRAME_DATA: continue   # 必须过滤 header
        d = {f.name: f.value for f in fr_.fields}
        lat = d['position_lat'] * 180 / 2**31   # 半圆 → 度（注意方向）
```

- `get_messages()` / `get_message()` **不存在**（那是旧版）
- 遍历时必须先按 `frame_type` 过滤，否则 `fr_.name` 报 AttributeError
- `position_lat` 是半圆整数：`* 180 / 2**31`（写反会得到 1e16 的荒谬坐标）
- `fitfile` 库（`from fitfile import File`）也可用，但 `File` 不支持 `with`，属性是 `.record` 不是 `.records`

## 凭据自动续期

两个 token 都会过期，脚本在每次运行前自动续期：

| 平台 | 过期 | 自动续期方式 |
|---|---|---|
| 顽鹿 | JWT，看 `exp` | `onelap_client.relogin()` —— 密码登录。密码来源优先级：参数 > `ONELAP_PASSWORD` 环境变量 > `.hermes/igp_sync/onelap_password` 文件（0600） |
| IGPSPORT | 7 天 | `igp_client.refresh_access_token()` —— 用 `refreshToken` 参数换新 token，**必须保存轮换后的新 refresh_token** |

**提前刷新 margin = 6 小时**（IGPSPORT 刷新端点要求 Bearer 仍有效，不能等彻底过期）。

刷新失败时脚本 `return 1` 并打印重新登录命令 —— cron 入口会把 `⚠️` 行输出到 QQ，不会静默失败几天。

## 踩过的坑

1. **FIT 下载用 POST → 405**。必须是 GET，且 fit_key 要 base64。
2. **IGPSPORT 上传不是单次 POST**。必须走三步 OSS：getSignedUrl → PUT OSS → uploadByOss。
3. **IGPSPORT 上传是异步的**。返回 `code:0` 后活动要等十几秒才出现在列表里（`dataStatus=1` 表示解析成功）。验证要等。
4. **IGPSPORT 活动列表只有日期没有时间**。`startTime` = `"2026.10.25"`。精确时间在 `queryActivityDetail`。
5. **迈金云端 FIT 文件名两种格式**，去重不能只按标题匹配。
6. **Overpass / OSM 沙盒连不上**（Network unreachable）。坐标系验证改用「IGPSPORT 存的 vs 源 FIT」直接比对。
7. **刷新 refresh_token 会轮换**。手工测试刷新后必须把新值写回文件，否则下一次刷新 403。
8. **不能伪造 JWT exp 来测过期逻辑** —— 改了 payload 签名就失效，服务端返回 401/HTML，看起来像"刷新坏了"。真实验证就调 `refresh_access_token()` 本身。
9. **IGPSPORT 索引要拉全量**（`begin_time=None` → 516 条，最早 2023-06）。用 `begin_time` 会漏更早的记录 → 历史补传时产生重复。

## 验证方法

```bash
# 幂等：连跑两次，第二次必须 0 上传
python3 ~/magene-to-igp.py --since 2026-09-25 --until 2026-10-25 --limit 50

# 清空 state 后重跑，仍必须 0 上传
rm -f ~/.hermes/igp_sync/state.json
python3 ~/magene-to-igp.py --since 2026-09-25 --until 2026-10-25 --limit 50
```

上传后**回读校验**：`queryActivityDetail/{rideId}` 检查 `rideDistance` 与源一致、`dataStatus=1`。
