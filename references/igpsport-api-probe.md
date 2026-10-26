# IGPSPORT 接口探测方法论（2026-10-26 实测）

排查「IGPSPORT 有没有活动删除 API」时总结出来的方法。**下次要探新端点，直接照这个流程走，别再盲猜路径。**

## 1. 先建立可靠的「存在 / 不存在」判据

这是最关键的一步。IGPSPORT 的网关对不存在的路径返回**统一错误**，盲猜时看不出真假。

用**已知存在的端点**做基准：

```python
import requests, json
B = "https://prod.zh.igpsport.com/service/web-gateway/web-analyze/activity"
body = json.dumps({"rideId": 58712510, "id": 58712510})
u = f"{B}/mergeActivities"

requests.post(u, headers=signed_headers("POST", u, token=tok, body=body), data=body)
# → 403 {"code":1002,"message":"请选择活动"}   ← 端点存在
```

| 响应 | 含义 |
|---|---|
| `code:0` / `code:1002` 等**业务码** | ✅ 端点存在，请求被业务层接收了 |
| `code:1003 服务器异常`（HTTP 500） | ❌ 路径不存在（网关兜底异常） |
| HTTP 404 `{"error":"Not Found"}` | ❌ 前缀就没路由（如 `mobile/api/Activity/xxx`） |
| HTTP 429 | ⚠️ 触发限流，**不能当命中**，要退避重测 |

> ⚠️ `OPTIONS` 返回的 `Allow` 头**不可信**。它对 `queryMyActivity` 也返回
> `Allow=GET,HEAD,DELETE,OPTIONS`，但实际 `DELETE` 一律 `code:1003` ——
> 那是 Spring 的通用 CORS 响应，不是真删除端点。**不要被 `Allow:DELETE` 误导。**

## 2. 端点路径从哪来（不要猜）

三个来源，按可靠性排序：

### 2.1 Web App 的 JS bundle（最可靠）

```python
import requests, re, concurrent.futures
UA = {"User-Agent": "Mozilla/5.0 ... Chrome/120"}
html = requests.get("https://app.zh.igpsport.com/", headers=UA, timeout=30).text
chunks = re.findall(r'src="([^"]+\.js)"', html)
# 用浏览器打开页面能拿到全部懒加载 chunk（比正则全）
```

把 chunk 全部下下来，提取路径：

```python
paths = set(re.findall(r'["\'`](/[\w\-]*(?:/[\w\-]+){1,4})["\'`]', all_js))
```

**IGPSPORT Web App 的全部 50 条路径**里，涉及活动的只有这些：

```
/web-analyze/activity/queryMyActivity
/web-analyze/activity/uploadByOss
/web-analyze/activity/queryActivityToRouteList
/web-gateway/web-analyze/activity/mergeActivities
/web-gateway/web-analyze/webActivityAnalyze/active
```

**没有删除端点。**（Web App 本身就不提供删除功能。）

### 2.2 GitHub 代码搜索

```python
# 有效 PAT 在 wulibaibao-blog/.env 的 GITHUB_TOKEN
r = requests.get("https://api.github.com/search/code",
                 headers={"Authorization": f"Bearer {PAT}"},
                 params={"q": "queryMyActivity", "per_page": 20})
```

搜到的成熟实现（都有完整签名逻辑可直接抄）：

| 仓库 | 文件 |
|---|---|
| `NenoSann/igpsport_sync` | `endpoints.go`（Go 端点常量表，最干净） |
| `Dunky-Z/FitSync` | `src/igpsport_client.py`（Python，含 OSS 上传三步） |
| `Anathleticbicyclist/sports-data-sync-multiplatform` | `app/.../IgpsportApi.kt`（Kotlin，最接近 App 端） |
| `fooooxxxx/igpsport-export-fit-files` | 单文件，最短 |

**这 4 个实现都只有查询/详情/下载/上传，没有删除。**
第三方已经把 IGPSPORT 的公开 API 面摸全了 —— 没有删除能力。

### 2.3 App 端网关

App 走 `mobile/api/*`（大小写敏感，例 `Member/UpdateMemberSetting`）和
`login.passport.igpsport.cn` 登录网关。但 `mobile/api/` 下的活动相关模块名
无法从 Web App 反推，盲扫 1190 个组合全部 404。

## 3. 结论

**IGPSPORT 对外没有暴露活动删除 API。**

App 里能删活动（用户可操作），但那条路径要么走内部服务，要么需要 App 端鉴权上下文，
抓不到。公开 API 面（Web App + 4 个第三方实现 + 1600+ 组合盲扫）全都没有删除端点。

**唯一半成品**是 `mergeActivities`（合并活动）：

```python
# 参数名是 rideIds 数组
body = json.dumps({"rideIds": [57952751, 57995497]})
# → 403 {"code":1002,"message":"合并失败，存在时间重叠"}
```

对**重复副本**（本来就是同一条）必然失败，因为时间完全重叠。
理论上可以用来「合并」不同片段，但对同内容副本无效。

## 4. 上传侧的关键机制（2026-10-26 实测，重要）

### 4.1 IGPSPORT 按内容指纹判重，不是按文件名

实测（5-30 那条，144.84km / 17990s）：

| 上传内容 | filename | 结果 |
|---|---|---|
| 迈金云端原始 FIT（md5 `4ad2748d…`） | `Magene_C706_1780093970_470592_1780119879235.fit`（**与现存条目完全同名**） | 不入库 |
| 同上 | 改名 `…_STRAVA_0530.fit` | 不入库 |
| 迈金原始 FIT | `MATCH_470592-2026-05-30-06-32-51-log.fit` | 不入库 |
| **Strava streams 重建的 FIT**（17998 点） | `MATCH_…-strava.fit` | 不入库 |
| 已入库的 8-22 FIT 原样重传 | `MATCH_470592-2026-08-22-04-22-24-log.fit` | 不入库 |

全部返回 `{"code":0,"message":"success"}` 但列表不变（回读 60s 确认）。
**改动文件名、改内容都绕不过** —— 判重看的是 FIT 内的时间+距离指纹。

### 4.2 对照组：为什么有的能入库

8-22 / 8-15 / 9-12 三条当天都成功入库了 —— 因为 IGPSPORT **当时没有对应记录**
（8-22 那条 IGPS 只有原生 Strava 同步来的 244.97km 版本，和 221.53km 内容不同）。

> **所以「重传/覆盖已有活动」在 IGPSPORT 上做不到。**
> 能传的只有「IGPSPORT 手里还没有的新活动」。
> 用户说「重新传一条」时，先查 IGPSPORT 有没有同日期同距离的记录 ——
> 有的话传了也是白传，直接告诉他这条已经在库里、数据如何。

### 4.3 FIT 的 device 身份会影响入库吗

不会。改成 `manufacturer=107(magene) product=50` 后仍然不入库（内容指纹判重在前）。
但**字段完整度**很重要 —— IGPSPORT 的分析依赖：

| 消息 | 迈金原始 FIT | streams 重建 FIT |
|---|---|---|
| `record` | ✅ 17998 条 | ✅ 17998 条 |
| `lap` / `session` / `activity` | ✅ | ❌ 缺 |
| `event` / `weather_conditions` | ✅ | ❌ 缺 |
| `software` / `capabilities` / `user_profile` / `bike_profile` / `device_info` / `developer_data_id` | ✅ | ❌ 缺 |
| `record` 级 `temperature` / `grade` / `calories` | ✅ | ❌ 缺 |
| `record` 级 `power` / `cadence` | ✅（有传感器时） | ❌ 缺 |

**重建 FIT 只有 3 种消息，原始码表 FIT 有 15 种。**
能用原始 FIT 时**永远优先用原始的**（Strava streams 重建只作为原始 FIT 拿不到时的兜底）。

### 4.4 Strava 原始文件下载：做不到

```python
GET /api/v3/activities/{id}/download   → 404 "Record Not Found"（所有活动，非 Premium 付费功能）
GET /api/v3/uploads/{upload_id}        → 200，但只有元数据：
    {"id":19846318169,"external_id":"MAGENE_C706_2026-05-30_063250_470592.fixed.fit.gz.fit",
     "status":"Your activity is ready.","activity_id":18737004380}
```

`external_id` 只是**原始文件名**，不是下载链接。
唯一自动化路径仍是 `GET /api/v3/activities/{id}/streams?keys=…&key_by_type=true` 重建。

**注意 streams 返回格式**：
- `key_by_type=true` → 返回 **dict**（`{"distance":[...], "latlng":[[lat,lng],...]}`）
- 不带 `key_by_type` → 返回 **list**（每项一个 stream）

`latlng` 是 `[lat, lng]` 顺序。

## 5. 功率/踏频为 0 的两种原因，要分清

5-30 那条 `avgPower=0`、`avgCad=None`：

```python
# 读 IGPSPORT 下载回来的原始 FIT
with fitdecode.FitReader(path) as f:
    for fr in f:
        if fr.frame_type != fitdecode.FIT_FRAME_DATA: continue
        if fr.name != "record": continue
        for fl in fr.fields:
            if fl.name == "power": ...
```

- **5-30 那天码表 FIT 里 `power`/`cadence` 就是 0 个点** → 那天没戴功率踏频传感器
- 对照 8-22 那条：`power` 32393 点、`cadence` 32393 点 → 戴了

Strava 侧同样能验证：`device_watts=False` + `segment_efforts[].average_watts=None`
+ streams 无 power 流 ⇒ **该活动在任何平台都没有逐点功率数据**。

**这种「缺功率」重传是补不上的**，别浪费轮次试上传 —— 先查 FIT 里有没有点。
