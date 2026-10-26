---
name: cycling-data-upload
description: Use when uploading cycling FIT data to Strava, 行者/XOSS (imxingzhe.com), or IGPSPORT; when syncing 迈金/Magene, 顽鹿/OneLap, or Strava activities to a Chinese platform; when debugging duplicate activities, coordinate offsets (GCJ-02/WGS-84), FIT token refresh, or the daily 11:00-11:20 sync jobs. Covers Strava→行者, 迈金→IGPSPORT, and manual FIT→Strava upload.
license: MIT
version: 1.0.0
---

# 骑行数据上传（Strava / 行者 / IGPSPORT）

三条上传链路的统一 skill。

| 链路 | 方向 | 源 → 目标 | 坐标系 | 定时 job |
|---|---|---|---|---|
| **A** | Strava → 行者 | Strava streams → 重建 FIT → 行者 | **需转** GCJ-02（投票判定） | `3670c2356238` 每天 11:10 |
| **B** | 迈金 → IGPSPORT | 迈金云端 FIT → 原样上传 | **不转**（两边都 GCJ-02） | `3e3e0194d61b` 每天 11:05 |
| **C** | 本地 FIT → Strava | 用户发的 FIT → Strava API | **需转** GCJ-02→WGS-84 | 手动 |

**坐标系是三条链路最大的差异 —— 详见下方「坐标系矩阵」，搞错就是 400-500m 偏移。**

## 脚本（都在 `scripts/`）

| 脚本 | 用途 |
|---|---|
| `strava-to-xingzhe-sync.py` | 链路 A 主脚本 |
| `run-xingzhe-sync.py` | 链路 A cron 入口（day-2~day-1 窗口，无新活动零输出） |
| `onelap_client.py` | 迈金/顽鹿 API 客户端（登录/列表/详情/FIT 下载/签名） |
| `igp_client.py` | IGPSPORT API 客户端（短信登录/刷新/三步 OSS 上传/签名） |
| `magene-to-igp.py` | 链路 B 主脚本 |
| `run-magene-igp-sync.py` | 链路 B cron 入口 |
| `_serial_lock.py` | 全局串行锁（所有 cron 入口共用，见下方「全局串行锁」） |
| `export-skill.sh` | 安全导出到 GitHub（打包前扫真值 + 校验副本一致） |
| `strava-upload.py` | 链路 C 主脚本（手动上传单个 FIT） |

## 凭据（**不含任何密钥**）

本 skill **不携带任何密钥**。两个平台签名密钥改为**运行时从环境变量 / 本地文件读取**，缺失时脚本**报错退出**（绝不用默认值兜底）：

| 变量 / 文件 | 用途 |
|---|---|
| `ONELAP_SIGN_KEY` → `~/.hermes/igp_sync/onelap_sign_key` | 顽鹿/迈金 API 签名 |
| `IGP_HMAC_KEY` → `~/.hermes/igp_sync/hmac_key` | IGPSPORT 请求签名 |
| `ONELAP_PASSWORD` → `~/.hermes/igp_sync/onelap_password` | 顽鹿密码（自动重登） |
| `XINGZHE_CLIENT_ID` / `XINGZHE_CLIENT_SECRET` | 行者 OpenAPI（在 `~/.hermes/.env`） |
| `STRAVA_CLIENT_ID` | Strava Application（建议注册自己的） |
| `~/.hermes/strava_token.json` | Strava OAuth token（含 client_secret，动态读） |

**完整准备步骤见 `CREDENTIALS.md`** —— 每个使用者照着做即可，无需读代码。

**脚本位置规则（重要）**：

| 文件 | 位置 | 原因 |
|---|---|---|
| 主脚本（`strava-to-xingzhe-sync.py` / `magene-to-igp.py` / `*_client.py` / `strava-upload.py`） | **本 skill 的 `scripts/`**（单一真相） | 自由，可任意引用 |
| cron 入口（`run-xingzhe-sync.py` / `run-magene-igp-sync.py`） | **必须是 `~/.hermes/scripts/` 下的真文件** | cron 会 `resolve()` 软链接并要求路径落在 scripts 目录内 —— 软链出去会被守卫拦下，报 `Blocked: script path resolves outside the scripts directory` |

`~/.hermes/scripts/` 下的两个入口是**独立副本**（不是软链接），内容里的 `SCRIPT` / `SKILL_SCRIPTS` 常量直接指向 skill 目录。改入口逻辑时**两处都要改**（skill 目录的归档副本 + `~/.hermes/scripts/` 的运行副本）。

`~/` 下的 `strava-to-xingzhe-sync.py` 等是软链接指向 skill 目录 —— 手动命令行用这些路径没问题（cron 守卫只管 cron 入口）。

### 🔁 历史缺口补传 → `references/four-platform-reconciliation.md`

补历史缺口是一次性操作（不每天跑）。脚本自带 D2–D0 窗口，历史要单独传。
**只补近 30 天的真实缺口**，2024–2025 的老活动本来是逐年手工同步的，批量灌进去反而污染数据。


## ⚠️ IGPSPORT 有 74 条历史重复记录 → `references/igpsport-duplicate-records.md`

服务端删不掉（无删除 API），推测 App 端按 `dataStatus` 过滤只显示正常条目。
**不影响同步**（去重只比对正常条目）。详情见 reference。


## ⚠️ 盘点时的两个自伤坑（2026-10-26 踩过）

### 1. 行者分页参数用错会造出「几百条缺口」假象

用 `page`/`page_size` 调私有接口 `/api/v1/pgworkout/` 只会返回 **20 条**（还是最新的），
看起来像「行者缺 300 条」。正确参数是 **`offset`/`limit`/`sport=3`**（见 `strava-to-xingzhe-sync.py`
的 `fetch_xz_activities()`）。真实全量是 294 条。

**教训**：盘点出现「大批量缺口」时，先怀疑自己的分页/时间窗参数，再怀疑平台。

### 2. 用文件内容 sha1 比对 GitHub 会全部误判为「已更新」

GitHub Contents API 的 `sha` 是 **blob sha** = `sha1("blob <字节数>\0" + 文件内容)`，
不是纯文件内容的 sha1。直接比会得到「47 个文件全变了」。

**正确写法**：
```python
hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
```

更新已存在文件时还必须带上远端当前 `sha`，否则报 `"sha" wasn't supplied` (422)。

## GitHub 仓库

`wulibaibao/cycling-source-upload-kit`（公开，MIT）。发布流程见 `references/skill-export-recipe.md`。
注意：仓库根目录是 **skill 内容本身**（`SKILL.md`/`scripts/`/`references/`），没有 `cycling-data-upload/` 前缀层。

## 📐 四平台一致性架构（2026-10-26 新增）

现在有 **4 条同步链路**，共同把四个平台（迈金/OneLap、IGPSPORT、Strava、行者）对齐：

```
                    ┌──────────────┐
   迈金云端 ────────▶│  迈金        │  www.onelap.cn  (数据源 + 写入目标)
   (OneLap)         │  (OneLap)    │  u.onelap.cn 是旧副本，total 少 2 条
                    └──────┬───────┘
                           │ ① 迈金 → IGPSPORT
                           ▼
                    ┌──────────────┐
   Strava ─────────▶│  IGPSPORT    │  服务端**有**去重
                    └──────┬───────┘
                           │
   ② Strava → 迈金 ────────┤
   ③ Strava → 行者 ────────┤
                           ▼
                    ┌──────────────┐
                    │  行者        │  服务端有去重（私有接口可删）
                    └──────────────┘
```

**脚本清单**：

| 脚本 | 方向 | 服务端去重 | 备注 |
|---|---|---|---|
| `magene-to-igp.py` | 迈金 → IGPSPORT | ✅ IGPS 有 | 原始链路 |
| `strava-to-onelap.py` | Strava → 迈金 | ❌ **无** | 客户端闸门是硬要求 |
| `strava-to-xingzhe-sync.py` | Strava → 行者 | ✅ 行者有 | |

> **为什么没有「Strava → IGPSPORT」？**
> 实测 IGPSPORT 的 `uploadByOss` 接口虽然返回 `code:0 success`，
> 但 FIT 长时间不会进 `queryMyActivity` 列表（等了 2 分钟仍 516 条不变）。
> 推测是 IGPSPORT 侧的分析队列只放行**自己 App 产生**的 FIT。
> 所以 IGPSPORT 只做**单向接收**（迈金 → IGPSPORT），不做反向补齐。
> （2026-10-26 实测：Strava → IGPSPORT 传 2 条，服务端一条没入库。）

**cron 入口**（`~/.hermes/scripts/` 下必须是真文件）：
| 时间 | 任务 | job_id |
|---|---|---|
| 11:00 | GitHub Trending | `e62707669d50` |
| 11:05 | 迈金 → IGPSPORT | `3e3e0194d61b` |
| 11:10 | Strava → 行者 | `3670c2356238` |
| 11:15 | **Strava → 迈金** | `49f30ba58248` |
| 11:20 | AI 文章抓取 | `701069159aac` |

### ⚠ 迈金上传接口要点（实测 2026-10-26）

```python
POST https://www.onelap.cn/api/otm/ride_record/upload/fit
multipart/form-data，文件字段名固定为 **jilu0**
headers: Authorization: <OneLap JWT>, Origin: https://www.onelap.cn, Referer: .../analysis
返回: {"code":200,"data":{"success_count":1,"failed_count":0,...}}
```
- **没有服务端去重** —— 重复上传会真的多出一条记录（实测把 10-25 的活动传了 2 次 → 2 条）。
  客户端闸门是**硬要求**，不是优化项。
- 删除误传：`DELETE https://www.onelap.cn/api/otm/ride_record/delete/{record_id}`
- 迈金上传是**异步入库**：上传后可能几十秒才出现在列表里，回读校验要轮询。

### ⚠ 迈金 FIT 里的 name 是空的
从 Strava 传过去的 FIT 在迈金侧 `name=None`、`type=24`。
迈金原生记录有名字（`type=13` 竞技 / `48` 自由骑行 / `27` 普通 / `49` 路线）。
标题在迈金 App 里可能显示为空 —— **这是迈金侧对外部导入 FIT 的处理，不是数据丢了**。

## ⚠⚠⚠ 同步窗口必须是 day-2 ~ day-0（含今天），且要留数据到达缓冲（2026-10-26）

**症状**：App 里骑了 2 次，行者只有 1 次。cron 报告「Strava 候选 2 条」，
但 Strava API 实际有 4 条 —— **少的 2 条在 cron 跑的那一刻还没到 Strava**。

**根因是两个独立问题叠加**：

### 问题 A：窗口只到 day-1 → 漏掉「今天早上」骑的

`--until day-1` 的窗口不含今天。本地日期是今天的活动（早上骑的）
落在窗口外 —— **一天两练（早+晚）必然漏一条**。

实测（2026-10-26）：
```
--since 10-24 --until 10-25 (day-2~day-1) → 覆盖 3 条  ❌ 漏 10-26 07:03
--since 10-24 --until 10-26 (day-2~day-0) → 覆盖 4 条  ✅
```
**正确窗口：day-2 ~ day-0。**（day-2 起步是为了容忍延迟入库 + 保证不漏昨天）

### 问题 B：源平台数据到达有延迟，cron 跑得太早扫不到

迈金 → 迈金 App → Strava（Garmin Connect 转发）这条链路有延迟。
实测 `upload_id` 是**连号**的：

```
upload_id=21883514077  #20720920299  10-25 07:11  ← 11:38 时已在 Strava
upload_id=21895985056  #20733099852  10-25 20:54  ← 11:38 时还没到
upload_id=21895985042  #20733099855  10-26 07:03  ← 11:38 时还没到
```
两条后到的 **upload_id 连号**（...85042 / ...85056），说明是**同一次批量同步**
才传到 Strava 的 —— 也就是说 10-25 傍晚骑的和 10-26 早上骑的，
**一起在今天中午才同步上去**。

**结论**：cron 在 11:10 跑，当天早上骑的活动**经常还没到 Strava**。
窗口放宽到 day-0 之后，**当天早上的活动仍然可能扫不到**，要等次日窗口。

**这是「连续两天补齐」的正常节奏，不是一次性 bug**：
- 今天 11:10 那轮 → 扫到 10-24/10-25（当时在 Strava 上的）
- 明天 11:10 那轮 → 扫到 10-25/10-26（中午才上传的会补上）

**排查时先确认源平台有没有这条**：
```python
# 拿不到就问：Strava/迈金 云端此刻到底有没有这条活动？
#   有 → 查窗口 + types 过滤
#   没有 → 是源平台数据还没到，等下一轮，不是同步脚本的锅
```

## ⚠⚠ 迈金云端 host 用错 → 日常骑行全部漏掉（2026-10-26，最隐蔽的一个）

**症状**：用户在迈金 App 里能看到骑行，IGPSPORT/行者里没有。日志显示
`顽鹿活动累计 40 条` / `窗口内候选 0 条` —— 云端"确实"没有这条记录。

**根因**：`onelap_client.APP` 原来指向 `https://u.onelap.cn`。
但 **两个 host 返回不同的数据集**：

| host | `/api/otm/ride_record/list` 的 `pagination.total` |
|---|---|
| `https://u.onelap.cn` | **450** |
| `https://www.onelap.cn` | **452** ← 正确的 |
| `https://otm.onelap.cn` | 450 |

差的 2 条恰恰是 **`type=48` 自由骑行 / `type=15` 快速燃脂** —— 也就是
**最普通的日常骑行**，恰恰是用户最在意的类型。
（`type=13` 竞技/比赛类、`type=49` 路线类反而都在 u.onelap.cn 里。）

**实测漏掉的**：
```
2026-10-25 20:54:09  自由骑行   17.85km  0:42:40  375m   ← 漏
2026-10-26 06:55:55  快速燃脂4  17.57km  0:45:34  186m   ← 漏
```
修复后两条都补传成功（rideId 58709824 / 58709823）。

**排查铁律：App 里有、云端拉不到 → 先换 host 试，别急着下结论说"没上传"。**
```python
for h in ["https://www.onelap.cn","https://u.onelap.cn","https://otm.onelap.cn"]:
    payload={"page":1,"limit":5}
    r=sess.post(h+"/api/otm/ride_record/list", json=payload, headers=ol.sign_headers(payload), timeout=20)
    print(h, r.json()["data"]["pagination"]["total"])   # 比对 total
```
三个 host 都返回 200（不是 404），**只有 total 不同** —— 这是最容易漏掉的坑，
因为「请求成功」给人的印象是「数据没问题」。

**连带修正**：FIT 下载的 `fit_key` 必须 **base64 编码**后再拼 URL
（`base64.b64encode(key.encode()).decode()`），不编码会返回
`HTTP 500 {"error":"文件处理失败"}` —— 同样是「请求成功但拿不到数据」的假象。

## ⚠ 「同步没生效」排查（2026-10-26 用户反馈后新增）

**症状**：cron 显示 `last_status=ok`，但行者/IGPSPORT 里没有新活动。

**`ok` 只代表脚本退出码 0** —— 骑行同步没数据时本来就静默返回 0。
所以「ok」和「没生效」并不矛盾，不要只看 cron 状态就下结论。

**排查顺序**（照着查，不要跳步）：

1. **看日志**（`~/.hermes/xingzhe_sync/logs/sync-YYYYMMDD.log`）里这行：
   `Strava 候选 N 条` —— N=0 说明根本没抓到活动；N>0 但「跳过」说明被去重挡了。
2. **N=0 → 查两处**：
   - **窗口**：cron 11:05 跑，day-2~day-1 覆盖前天+昨天。如果窗口写死成单日 day-2，昨天骑的永远扫不到。
   - **`--types` 白名单**：默认 `Ride,VirtualRide`。**VirtualRide 是虚拟/室内骑行，
     曾经被漏掉**（原默认只有 `Ride`，`SPORT_MAP` 里虽有 `VirtualRide→3` 映射但白名单把它挡在门外）→
     用户 2026-10-25 的 20.0km VirtualRide (#20720920299) 一直没同步到行者。
     **排查时先确认活动的 `type` 字段**（Strava API: `type` / `sport_type`）。
3. **N>0 但全跳过** → 看「跳过」原因：行者已有相同活动（workout_id 命中）= 正常，幂等生效。

**实测数据（2026-10-26）**：
```
Strava #20720920299  type=VirtualRide  20.0km  3322 GPS点 + HR + Cadence
  → 原被 --types=Ride 静默过滤，cron 一个字都没说
  → 修复后：上传成功 workout=225878773，point_counts=3322，is_valid=1
             avg_hr=114 max_hr=148 avg_cad=61 avg_power=132 NP=159 FTP=260
```

**注意**：虚拟骑行上传后 `avg_speed` / `max_speed` / `calories` / `cities` 都是 0 或空 ——
没有真实 GPS 速度，属正常，**不是上传失败**。判断成功看 `point_counts` + `is_valid=1` + `distance`。

**入口脚本新增自检**：静默前会区分「0 候选」和「候选全被跳过」，后者会打印说明而非完全静默。

## 全局串行锁（2026-10-25）

两个同步 cron 与博客的三个任务（Trending 刷新 11:00、AI 文章抓取 11:20）**共用同一把全局锁**：

```
~/.hermes/scripts/_serial_lock.py  →  ~/.hermes/run/serial.lock
```

- `acquire(task, wait_s=N)` 用 `fcntl.flock(fd, LOCK_EX | LOCK_NB)`
- 锁只加在 **cron 入口层**（`~/.hermes/scripts/run-*-sync.py`）；脚本本体不含锁，避免手动调用时误伤
- 诊断当前持锁者：`cat ~/.hermes/run/serial.lock`

### 等待窗口（2026-10-26 修正）

**原逻辑**：抢不到锁 → 立即放弃本轮，返回 0，**静默无输出**。
**问题**：cron 间隔 5 分钟，但 AI 抓取要跑 3-9 分钟 → 后面几条**根本没执行**，
用户看到的是「没动静」，和「跑了但没新数据」完全一样，无法区分。
2026-10-26 手动跑时实测：迈金那轮被 Strava 占锁挡掉，**连日志都没写**。

**现逻辑**：
```
acquire(task, wait_s=N)   # 抢不到 → 每 2 秒重试，最多等 N 秒
  拿到 → 正常执行
  等满 → 明确报告「本轮未执行：全局锁被占用（持锁: xxx）」，不再静默
```

各任务等待窗口：

| 任务 | 时间 | wait_s | 理由 |
|---|---|---|---|
| Trending 刷新 | 11:00 | 120 | 约 10 秒，最短，最容易抢到 |
| 迈金→IGPSPORT | 11:05 | 180 | — |
| Strava→行者 | 11:10 | 180 | — |
| AI 文章抓取 | 11:20 | 240 | 本身要 3-9 分钟，最长，最容易被挡 |

**注意**：等待是**有限**的（不是无限排队）。前一个任务结束后，等待者会立刻接上跑完；
真的拿不到（比如 AI 抓取跑了 9 分钟）→ 明确报告，下一个 cron 窗口自动重试。

### 三副本一致性（防漂移）

同一份代码存在三处拷贝：
1. `~/.hermes/scripts/*.py` ← **生产**（cron 实际执行，非 git 仓库）
2. `~/.hermes/skills/cycling-data-upload/scripts/*.py` ← skill 副本（导出源）
3. GitHub 仓库 ← 公开副本

改了一处忘同步 = 漂移，公开仓库里的示例代码与实际跑的不符，会误导使用者。

```bash
python3 ~/.hermes/scripts/verify-no-drift.py          # 检查
python3 ~/.hermes/scripts/verify-no-drift.py --fix    # 自动修复（以生产版为准）
```

`export-skill.sh` 在打包前会自动跑这个校验，**有漂移即中止导出**。

辅助脚本（诊断/修复）：`correct_fit_gpx.py`（GPX 中介纠偏）、`correct_gps.py`（两段式漂移）、`rolling_chunk_diagnose.py`（区分真实长路线 vs 漂移）、`detect_gcj02_offset.py`（OSM 双向 GCJ-02 诊断）。

**改完脚本后注意**：Python 会缓存 `__pycache__`，改 `strava-upload.py` 要 `rm -rf ~/__pycache__/strava-upload*` 或用 `python3 -B`。

⚠️ 因为脚本本体在 skill 目录里，`import igp_client` 产生的 `__pycache__` 会**落在 `scripts/__pycache__/`**（Python 按源文件位置缓存）。这是正常的，删掉即可；不要把这个目录当垃圾文件误删源码。

## 坐标系矩阵（最容易踩的坑）

| 源 | 目标平台存什么 | 需要转换？ |
|---|---|---|
| 迈金/顽鹿 FIT | IGPSPORT 存 **GCJ-02**（实测差 0.00 m） | ❌ **不转** |
| 迈金/顽鹿 FIT | 行者 / Strava 存 **WGS-84** | ✅ gcj2wgs |
| Strava（多数活动） | 行者存 **GCJ-02** | ✅ wgs2gcj（投票判定） |
| Strava（MAGENE app 上传的） | 已经是 GCJ-02 | ✅ 同上，投票会自动判出来 |
| 本地迈金 FIT | Strava 存 **WGS-84** | ✅ gcj2wgs（`--correct-coords`） |

**判据（最可靠）**：下载目标平台已存的同一条活动 FIT，与源 FIT 比坐标。
- 差 **0.00 m** → 原样存储，不转
- 差 **~450 m**（中国境内典型 GCJ 偏移） → 要转

**不要因为「国内平台都用 GCJ-02」就一刀切。IGPSPORT 是个例外。**

### 投票法（借鉴 ridefitsync，方向修正过）

ridefitsync 原版判定方向**写反了**（实测 10/10 全票判错）。正确：

```
margin = (off_gcj - off_wgs) / max(...)
margin > 0 ⇒ WGS-84
```

单点即可判对；混入境外点/重复停站点均不影响。境外坐标两种转换都是恒等，**必须提前短路**否则除零。

**Strava 的坐标系不固定**（取决于用户用什么 App 上传），所以平台表里 `SOURCE_COORD_SYSTEM["strava"] = None`，必须靠投票。

## 各平台接口事实 → `references/platform-api-facts.md`

接口地址、签名方式、字段含义、去重键、错误码全在这份文档里。改接口前先看它。


## 去重设计（三层闸门）

**服务端索引是真相，本地 state 只是缓存。**

### 链路 A（Strava → 行者）
1. 服务端活动索引命中 → 跳过。键 = `(start_time ± 15s, distance ± max(30m, 0.5%))`
   用 `server_has_healthy_activity()`（查健康），**不是** `server_has_activity()`
2. 服务端 uploads 的 MD5 命中 → 跳过
3. 都没有 → 才上传

三态语义（防"超时后重传导致重复"）：`uploaded` 跳过 / `unknown`（超时）→ **下一轮先查服务端索引**，查到就补记，查不到才重传，**绝不盲传** / `failed` → 重试。

### 链路 B（迈金 → IGPSPORT）
1. 本地 state（`.hermes/igp_sync/state.json`，key=`{record_id}:{fitUrl}`）命中 → 跳过
2. IGPSPORT 活动列表按 `日期 + 距离容差` 命中 → 跳过。容差 `max(50m, 距离×0.5%)`
   - 用**日期**不用精确时间 —— OneLap 的 `start_riding_time`（FIT 开始）和 IGPSPORT 记录时间差几分钟到十几分钟
   - IGPSPORT 索引要拉**全量**（`begin_time=None` → 516 条，最早 2023-06），用 begin_time 会漏更早记录 → 补传时产生重复
3. 都没有 → 上传

**验证标准（必做）**：清空 state 后重跑必须 0 上传。

## 健康检查（防坏记录永久挡住）

`xz_record_healthy(access, workout_id, expect)`：拉 `POST /openapi/v1/workout/{id}/stream/` 取首点。
- 有 `expect`（Strava 原始首点）→ 偏差 > 2 km 判坏（最准，能抓经纬度互换）
- 无 `expect` → 启发式：`lat == lon` 或 `90<|lat|<140 且 20<|lon|<50` 判坏

**判坏后自动删除（私有接口）再重传**，日志打 `🧹 已删除损坏记录 workout <id>`。不要依赖用户手动删。

## 凭据自动续期

| 平台 | 过期 | 自动续期 |
|---|---|---|
| 顽鹿 | JWT，看 `exp` | `onelap_client.relogin()` —— 密码登录。密码来源：参数 > `ONELAP_PASSWORD` 环境变量 > `.hermes/igp_sync/onelap_password` 文件（0600） |
| IGPSPORT | 7 天 | `igp_client.refresh_access_token()` —— `refreshToken` 参数，**必须保存轮换后的新 refresh_token** |
| Strava | 6 小时 | `POST /oauth/token` `grant_type=refresh_token`，**写入后立刻验证长度 == 40** |

**提前刷新 margin = 6 小时**（IGPSPORT 刷新端点要求 Bearer 仍有效，不能等彻底过期）。

刷新失败 → 脚本 `return 1` + 打印重新登录命令 → cron 入口输出 `⚠️` 到 QQ，**不会静默失败几天**。

凭据文件：
- `~/.hermes/igp_sync/token.json`（IGPSPORT，0600）
- `~/.hermes/igp_sync/onelap_token.json`（顽鹿，0600）
- `~/.hermes/igp_sync/onelap_password`（顽鹿密码，0600）
- `~/.hermes/strava_token.json`（Strava）
- `~/.hermes/xingzhe_sync/token.json`（行者）

## 定时任务

| job_id | 名称 | 时间 | 脚本 |
|---|---|---|---|
| `3670c2356238` | Strava→行者 每日同步 | 每天 **11:10** | `run-xingzhe-sync.py` |
| `3e3e0194d61b` | 迈金→IGPSPORT 每日同步 | 每天 **11:05** | `run-magene-igp-sync.py` |

都 `no_agent=true`（脚本模式）。

**窗口 = day-2 ~ day-1（前天 + 昨天）00:00~23:59**，不要改成"最近 N 天"（会拉大量历史，撞库闸门成本高）。
- 2026-10-26 修正：原为 **day-2 单日**。cron 在 11:05 跑，只扫前天 → 昨天骑的活动当天扫不到，
  要等后天 11:05 才同步（用户反馈「同步没生效」）。改成 day-2~day-1 后，昨天骑的最迟次日 11:10 入库。
- 服务端去重闸门（行者 workout_id / IGPSPORT md5）保证 day-2 已同步的不会重复上传。

输出规则：有新同步/失败 → 报告给 QQ；无新活动 → 零字节输出（静默）。

## 链路 C：手动上传 FIT 到 Strava

```bash
python3 ~/strava-upload.py <fit文件路径> [activity_type] [title] [--correct-coords]
```

- `activity_type`：Run / Ride / Swim / Walk / Hike / Workout / WeightTraining / Yoga（默认 Ride）
- `--correct-coords`：上传前 GCJ-02 → WGS-84。**迈金 FIT 必加**
- QQ 附件缓存在 `~/.hermes/cache/documents/doc_<uuid>_qqdownloadftnv5`，**要改名去掉 `doc_<uuid>_` 前缀**（脚本要 `MAGENE_C706_*.fit` 命名）

**上传后必须抓活动详情报告**（距离/时间/爬升/均速/起点终点），不要只说"上传成功"。

**标题**：只用心率区间名（Z1 轻松区 / Z2 脂肪燃烧区 / Z3 有氧耐力区 / Z4 乳酸阈值区 / Z5 最大摄氧区），**不加时间/里程/地点**。阈值 120/140/152/164 bpm（绝对值，不是 max_hr 百分比）。

**双纠偏 bug（07-06）**：纠偏逻辑若不检查文件是否已纠偏过，重复上传会叠加成 825m 偏移。脚本用 `.orig` 备份存在性判断；**更彻底是按坐标特征判断**（已 WGS-84 就跳过）。改纠偏逻辑后**必须验证起点 lon 偏移是 0.0046°（单次）不是 0.0093°（双重）**。

## fitdecode 0.11 API 坑

```python
import fitdecode
with fitdecode.FitReader(path) as fr:
    for fr_ in fr:                       # 迭代器直接吐 frame
        if fr_.frame_type != fitdecode.FIT_FRAME_DATA: continue   # 必须过滤 header
        d = {f.name: f.value for f in fr_.fields}
        lat = d['position_lat'] * 180 / 2**31   # 半圆 → 度
```

- `get_messages()` / `get_message()` **不存在**
- 遍历时必须先按 `frame_type` 过滤，否则 `fr_.name` 报 AttributeError
- `position_lat` 是半圆整数：`* 180 / 2**31`（写反 → 1e16 的荒谬坐标）
- enum：`f.value` 是 string（`'cycling'`），`f.raw_value` 是 int（`2`）—— Encoder 要 int
- scaled 数字：`f.value` 是米（`54424.44`），`f.raw_value` 是 cm（`5442444`）
- **Encoder 重建必须保留 `sport` 消息**（mesg_num=12），漏了 → `parse_fit` 返回 `sport=Other`、标题退化成文件名
- `fitfile` 库（`from fitfile import File`）也可用，但 `File` 不支持 `with`，属性是 `.record` 不是 `.records`

## 踩过的坑

1. **经纬度写反**：`lat, lon = lat2, lon2` 顺序不能反 —— 写反轨迹塌成直线，lat==lon
2. **Strava `start_date_local` 带 Z 但内容是本地时间**：`.rstrip("Z")` 再 parse。直接 `fromisoformat(x).astimezone()` 多偏 8 小时 → 去重全部失配 → 全部重复上传
3. **GCJ 公式分母漏 `a` 因子** → 算出 29047° 经度
4. **行者 FIT `sub_sport` 用 1(treadmill)** → 被判成跑步
5. **迈金 FIT 下载用 POST → 405**：必须 GET + base64
6. **IGPSPORT 上传不是单次 POST**：必须三步 OSS
7. **IGPSPORT 上传是异步的**：`code:0` 后要等十几秒才进列表（`dataStatus=1` = 解析成功）
8. **IGPSPORT 列表只有日期没有时间**
9. **迈金 fitUrl 两种格式**，去重不能只按标题匹配
10. **Overpass / OSM 沙盒连不上**（Network unreachable）→ 坐标系验证改用「目标平台存的 vs 源 FIT」直接比对
11. **刷新 refresh_token 会轮换** → 手工测试后必须写回新值
12. **不能伪造 JWT exp 测过期逻辑** → 改 payload 签名就失效，服务端返回 401/HTML，看着像"刷新坏了"
13. **Python 缓存陷阱**：改完 `strava-upload.py` 要 `rm -rf ~/__pycache__/strava-upload*`，或用 `python3 -B`
14. **Strava duplicate 报错其实已上传成功** → 查 `activity_id` 字段确认，别重传
15. **Strava 海拔膨胀是 DEM 校正**（不是 bug）：145km 骑行 FIT 1140m → Strava 显示 3664m，无法通过重建修复
16. **cron 脚本不能是软链接**：`cron/scheduler_script.py::_resolve_script_path` 会 `path.resolve()` 后要求 `relative_to(HERMES_HOME/scripts)` —— 软链到 skill 目录会被拦：`Blocked: script path resolves outside the scripts directory`。cron 入口必须是 `~/.hermes/scripts/` 下的**真文件**，内容指向 skill 目录里的主脚本。
17. **密钥不能硬编码进 skill**（公开仓库泄露风险）：平台签名密钥（顽鹿 `SIGN_KEY`、IGPSPORT `HMAC_KEY`）必须**运行时从环境变量 / 600 权限文件读**，缺失时抛错退出 —— **绝不用默认值兜底**（默认值的语义就是"密钥在代码里"）。个人路径同理，改用 `HERMES_HOME` 环境变量或 `Path.home()` 推导。Strava `client_secret` 一直是从 token 文件动态读的，这个模式要照抄。
18. **导出脚本自身会污染扫描结果**：如果扫描脚本里写死了真值清单，它会检出自己 → 永远自我报警。正确做法是**从本机配置动态读取真值**，并用 `--exclude` 排除扫描器自身。

## 验证方法

```bash
# 链路 A 幂等
python3 ~/strava-to-xingzhe-sync.py --since 2026-10-01 --limit 30

# 链路 A 标准回归（清空 state + FIT 缓存后必须 0 新增）
rm -f ~/.hermes/xingzhe_sync/fit/*.fit
python3 -c "import json,os; p=os.path.expanduser('~/.hermes/xingzhe_sync/strava_state.json'); s=json.load(open(p)); s['synced']={}; json.dump(s,open(p,'w'),indent=2)"
python3 ~/strava-to-xingzhe-sync.py --since 2026-10-01 --until 2026-10-20 --limit 30

# 链路 B 幂等 + 清空 state
python3 ~/magene-to-igp.py --since 2026-09-25 --until 2026-10-25 --limit 50
rm -f ~/.hermes/igp_sync/state.json
python3 ~/magene-to-igp.py --since 2026-09-25 --until 2026-10-25 --limit 50
```

**上传后回读校验**：
- 链路 A：`GET /openapi/v1/activities/{workout_id}/` 检查 `sport=3`、距离误差 < 2%
- 链路 B：`queryActivityDetail/{rideId}` 检查 `rideDistance` 与源一致、`dataStatus=1`
- 链路 C：`GET /api/v3/activities/{id}` 检查距离/爬升/起点终点

## 详细文档

- `references/strava-xingzhe-sync-detail.md` — 链路 A 完整细节（投票法实现、9006 真相、健康检查、坏记录自动清理）
- `references/magene-igp-sync-detail.md` — 链路 B 完整细节（OneLap 签名、三步 OSS、token 续期）
- `references/strava-upload-detail.md` — 链路 C 完整细节（Strava API 政策、OAuth 流程、标题算法、水平偏移五分类、诊断脚本）
- `references/strava-streams-to-fit.md` — Strava Streams → FIT 重建（跨平台同步到国产平台的核心）
- `references/magene-c706-gcj02-offset.md` — GCJ-02 系统偏移识别（OSM 双向验证）
- `references/fit-method-comparison.md` — 三种 FIT 修复方法决策树
- `references/fitdecode-encoder-pitfalls.md` — fitdecode + Encoder 三个实操坑
- `references/hr-zone-absolute-threshold.md` — 心率区间绝对阈值法
- `references/oauth-application-swap-2026-07-02.md` — Strava Application 切换恢复 API 完整流程
- `references/strava-api-paywall-2026-07.md` — 2026-07 Strava 政策变更
- 其余 reference 见目录
