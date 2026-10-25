---
name: strava-upload
description: 上传 FIT 文件到 Strava，支持自动刷新 token、activity_type 指定、活动名和描述。触发词：上传到Strava、上传FIT文件、strava上传。直接传入 fit 文件路径即可，不支持 GPX。
license: MIT
version: 1.8.0
---

# Strava FIT 文件上传

上传 FIT 文件到 Strava，支持自动刷新 token、activity_type 指定、活动名和描述。

## `--correct-coords` 实现说明（2026-06-04 重写）

06-04 之前的 binary-patch 实现有 4 个 bug（CRC 多项式错、CRC 范围错、data_msg_size 少字节、data message 解析错位），已**全部修复**。但因为 bug 4（data message 与 definition message 结构差异）根因太深，**重写为 GPX 中介法**——`fitdecode` 解析原 FIT → 对每条 record/session/lap 做 `gcj2wgs` → `garmin-fit-sdk Encoder` 重建。

完整实现细节 + 与 binary patch 法的对比见 `references/correct-coords-gpx-mediation.md`。事故复盘（4 个 bug 详情）见 `references/correct-coords-broken-2026-06-04.md`（保留作为历史教训）。

⚠️ **白名单注意（06-04 实战发现）**：GPX 中介法只处理 `file_id / user_profile / bike_profile / sport / session / lap / record / activity` 这 8 种消息。**`sport` 必须包含**——漏了它产物 `parse_fit` 返回 `sport=Other`、标题退化为文件名（get_title 返回 None）。其他消息（device_info / monitoring / event / hrv / weather_conditions）静默跳过，Strava 仍能正确解析。

⚠️ **fitdecode 0.11 enum 处理**：fitdecode 默认把 enum 转 string（`'cycling'`），`garmin-fit-sdk Encoder` 要 int（`2`）。**用 `f.raw_value` 而不是 `f.value`** 取 enum 原始值；数字字段（distance/speed/elapsed_time）`f.value` 是 scaled 值（`54424.44` m），用 `f.value` 而非 `f.raw_value`（`5442444` cm）。代码见 `correct_fit_file` 里的 fallback 链。

⚠️ **用户偏好（06-04 起，永久）**：用户明确"码表版本更新了，以后默认按纠偏处理"——**每次上传都加 `--correct-coords`**，不要先传干净版再问。

⚠️ **用户工作流偏好（06-04 起，永久）**：用户**接受"先传一个直传版占位"的快速失败模式**。上传报错（duplicate / malformed）时，用户多次说"先传"、"传了再说"、"先纠偏上传"——意思是不要死磕到完美再行动，接受"先占个位、不行再修"。**遇到 Strava 上传报错时，默认先做能立刻成功的动作（直传或纠偏重传），把"修脚本"放到上传成功之后**。详细事故链见 `references/correct-coords-broken-2026-06-04.md` 第 3 节"修复决定"。

---

## 使用方法

```bash
python3 ~/strava-upload.py <fit文件路径> [activity_type] [title] [--correct-coords]
```

**参数说明：**
- `fit文件路径` — 必填，.fit 文件的绝对路径
- `activity_type` — 可选，默认 `Ride`
- `title` — 可选，活动名称，默认自动生成（见下「自动标题格式」）
- `--correct-coords` — 上传前将 FIT 内的 GCJ-02 坐标纠偏为 WGS-84（用于国产设备输出的 FIT；06-04 起默认必加）

**支持的 activity_type：** Run, Ride, Swim, Walk, Hike, Workout, WeightTraining, Yoga

## 依赖

```bash
pip install requests fitparse fitdecode garmin-fit-sdk -q
```

## 上传后质量检查（主动报告，不要等用户问）

上传成功后**立即抓取活动详情**并报告关键指标，不要只说"上传成功"。

**自动标题格式（骑行，06-01 起固定，06-02 修订阈值算法）：**
只用心率区间美化名称作为标题，**不加时间/里程/地点**。判断依据见 `references/hr-zone-absolute-threshold.md`：

| 区间 | avg_hr 范围 | 标题 |
|---|---|---|
| Z1 | < 120 bpm | Z1 轻松区 |
| Z2 | 120–139 bpm | Z2 脂肪燃烧区 |
| Z3 | 140–151 bpm | Z3 有氧耐力区 |
| Z4 | 152–163 bpm | Z4 乳酸阈值区 |
| Z5 | ≥ 164 bpm | Z5 最大摄氧区 |

例：avg_hr=141 → Z3 → 标题「Z3 有氧耐力区」

**修改记录：**
- 06-01：骑行标题从「06-01 西安晨骑 54km」改为只用心率区间名称。用户明确说"不需要加时间/里程/位置，只需要美化一下心率区间"。`strava-upload.py` 的 `get_title()` 已同步更新（删除了 ride_time 前缀）。
- **06-02：修复心率区间判断 bug——从 max_hr 百分比法改为绝对心率阈值法。** 旧逻辑 `avg_hr / max_hr` 在 max_hr 偏低时（如轻松骑 max_hr=154）会高估强度（avg 134 → 87% → 误判 Z4）。新逻辑按绝对阈值 120/140/152/164 划分。已批量修正 06-01 早（avg 141→Z3）和 06-02 早（avg 134→Z2）两条已上传活动标题。详见 `references/hr-zone-absolute-threshold.md`。
- **06-04：标题 `parse_fit` 返回 `Other`（get_title 返回 None）→ 标题退化为文件名。** 根因是 GPX 中介法漏处理 `sport` 消息（白名单里没 `sport`）。已修复：白名单加入 `sport`，产物 `parse_fit` 返回 `sport: Ride`，标题正常生成。
- **06-10：把 05-23 的 GPS 状态从「严重漂移（坐标飞掉）」修正为「真实长距离路线（不是漂移）」。** 旧判断仅看 spread 偏大就标漂移是错的；正确做法是跑滚动 chunk 中位对比，看 spread 是「段间恒定错位」还是「路线往返覆盖」。详见下方「水平偏移必须区分」第 5 类 + `references/fit-method-comparison.md` 的「spread 诊断流程」。

**修改已上传活动的标题：**
```python
import os
import json, requests
tok = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))
r = requests.patch(
    f'https://www.strava.com/api/v3/activities/{activity_id}',
    headers={'Authorization': f'Bearer {tok["access_token"]}'},
    json={'name': 'Z3 有氧耐力区'}  # 改这里的区间名
)
```
返回 200 + 完整 activity 对象即成功。

### 标题修正流程（标题算法变更后必做）

当 `get_title()` / `get_hr_zone()` 这类自动标题逻辑被修改后，**之前已经上传的活动标题不会自动更新**。两种处理方式：

**方式 A：批量 PATCH（推荐，省事）**

```python
import json, requests
tok = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))
headers = {'Authorization': f'Bearer {tok["access_token"]}'}

# 从 strava-upload.py 重跑分析得出新标题，然后批量改
fixes = [
    (18735826730, 'Z3 有氧耐力区'),  # activity_id, new_name
    (18748867877, 'Z2 脂肪燃烧区'),
]
for aid, new_name in fixes:
    r = requests.patch(f'https://www.strava.com/api/v3/activities/{aid}',
                       headers=headers, json={'name': new_name})
    print(f'{"✅" if r.status_code == 200 else "❌"} {aid} → {new_name}')
```

**方式 B：删除重传（不推荐）**

- 当前 token scope 没有 `activity:delete`（见下方"API Token 缺少 activity:delete 权限"），必须手动去 Strava 网页删除
- 删除后用 `python3 strava-upload.py` 重新上传即可
- **缺点**：丢失原活动的 kudos/comments，重传会重新触发 Strava 去重检测

**经验（06-02）：** 心率区间阈值从百分比法改为绝对阈值法后，已上传的 2 条活动（06-01 早 / 06-02 早）用方式 A 各发一次 PATCH 就改完了，不到 30 秒。**优先用方式 A**。

```python
import json, requests
tok = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))
headers = {'Authorization': f'Bearer {tok["access_token"]}'}
r = requests.get(f'https://www.strava.com/api/v3/activities/{activity_id}', headers=headers)
a = r.json()
print(f'距离: {a["distance"]/1000:.2f} km')
print(f'移动时间: {a["moving_time"]/3600:.2f} h')
print(f'总爬升: {a["total_elevation_gain"]} m')
print(f'avg speed: {a["average_speed"]*3.6:.1f} km/h')
print(f'起点: {a["start_latlng"]}')
print(f'终点: {a["end_latlng"]}')
```

**报告格式（一句话概述 + 关键数据）：**
- 正常：「✅ 已上传。距离 144.83 km，移动时间 5.01 h，总爬升 3663 m（⚠️ 偏高需确认），平均速度 28.9 km/h，起点终点已回到原点。」
- 有问题：「⚠️ 已上传但数据异常：[具体问题]」

**快速判断标准：**
- 距离：±10% 预期值正常
- 移动速度：骑行 20-35 km/h 合理
- 爬升：145 km 骑行合理值约 1500-2500 m；>3000 m 明显偏高，可能是 GPS 高度漂移未完全修复
- 坐标：西安地区应在 `(34.2, 108.8)` ±0.05°，不是 `(68, 217)`

## 已知问题

### 上传报错 "duplicate" 但文件已上传成功

Strava 重复检测逻辑：即使返回 `error: "duplicate of /activities/XXX"` 且 `status: "There was an error processing your activity."`，**活动实际上已经被创建**了（见 `activity_id` 字段判断）。

去 Strava 手动确认一下是否有对应活动，如有则无需重复上传。错误原因很可能是 GPS 手表记录的时间和 Strava 处理后的 UTC 时间存在 1 天偏差（比如北京 05-23 06:07 的活动显示为 UTC 05-22 22:07），导致 Strava 认为是不同日期的重复文件。

**判断是否已上传：**
```python
import json, requests
tok = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))
headers = {'Authorization': f'Bearer {tok["access_token"]}'}
resp = requests.get(f'https://www.strava.com/api/v3/uploads/{upload_id}', headers=headers)
data = resp.json()
# error 字段包含 "duplicate of /activities/" 说明已上传成功，只是被标记为重复
# 无需再次上传，直接去 Strava 查看活动链接即可
```

**UTC 跨日触发重复检测的常见情况（06-02 实际案例）：**
- 设备本地时间 (北京 06-01 05:48) → Strava 解析为 UTC 06-01 21:48 → 活动 start_date_local 仍显示 06-01 05:48 ✅
- 但 **upload external_id 用了原始文件名 `MAGENE_C706_2026-06-01_054844_*.fit`**，文件名是设备本地日期
- 如果文件名日期与 Strava 解析的 UTC 日期**跨日**（如北京 06-02 早上骑的，文件名是 06-02，但 Strava 上传时间 06-01 23:50 UTC），就会和"前一天"的活动 external_id 撞车 → duplicate
- **变通**：脚本生成 external_id 时**追加时间戳后缀**（`+ str(int(time.time()))`）或直接用 upload_id 派生，避免和历史 external_id 冲突

**⚠️ 重要：duplicate 出现后默认不要重传！** 直接 `GET /activities/{linked_activity_id}` 确认活动已存在，更新它的标题/描述即可（用上面的"标题修正流程"）。

**但 06-04 用户偏好：duplicate 出现时，用户宁可删原版+重传（接受手动删活动的成本），也不要接受"有偏移的版本"占着位置。** 这是 06-04 实测：直传版（路线偏 470m）上传成功但用户立刻说"纠偏"——隐含意思就是"宁可删了重来也不要保留错误版本"。所以 duplicate 后默认动作从"不重传"变成"先报 duplicate + 问用户是否要删原版重传"。

**06-04 实战：纠偏版和直传版都会触发 duplicate**——纠偏后文件大小不同（205.9 KB vs 218.2 KB），但 Strava 按**文件起始字节哈希**或**外部 ID** 去重仍可能撞。**删原活动后重传最稳**。同时只能有 1 个上传版本在 Strava 上。

### activity_type 选错会导致上传失败

`Run`（跑步）和 `Ride`（骑行）不要填错。运动手表解析出来的类型不一定准确，骑行活动传 Run 会报错处理失败。

### ⚠️ Encoder 重建 FIT 文件丢失元数据的问题（核心陷阱！）

**问题描述：** 用 `garmin_fit_sdk Encoder` 读取 record 数据并重建 FIT 时，如果只写 record 消息不写其他 metadata 消息，**产物 parse_fit 丢失 sport 字段** → 标题退化为文件名（get_title 返回 None）。

**正确流程：** 保留原文件所有需要的元数据消息。**GPX 中介法白名单必须包含 sport 消息**（mesg_num=12），否则产物 sport=Other。具体见 `references/correct-coords-gpx-mediation.md`。

**症状判断：** 重建后的 FIT 上传 Strava 报错 "There was an error processing your activity" → 几乎肯定是 Encoder 输出缺少元数据导致的。**但 06-04 实战：即使 Strava 接受了，标题也会退化为文件名**（因为产物没 sport 字段）。

**GPX 中介法白名单**（06-04 验证通过的最小集）：
```python
HANDLED = {'file_id', 'user_profile', 'bike_profile', 'sport',
           'session', 'lap', 'record', 'activity'}
```

### API Token 缺少 activity:delete 权限

当前 Strava OAuth token 的 scope 是 `activity:read_all activity:write read`，**没有** `activity:delete`。因此无法通过 API 删除活动。

**表现：** `DELETE /api/v3/activities/{id}` 返回 `401 {"message":"Authorization Error","errors":[{"resource":"Application","field":"internal","code":"invalid"}]}`。

**变通：** 手动在 Strava 网页删除对应活动，然后立即重新上传。

### ⚠️ HTTP 403 `"code":"Inactive"` — Strava 2026-07 政策变更（关键！）

**症状**（2026-07-02 实测首次遇到）：
```
requests.exceptions.HTTPError: 403 Client Error: Forbidden for url: https://www.strava.com/api/v3/uploads
body: {"message":"Forbidden","errors":[{"resource":"Application","field":"Status","code":"Inactive"}]}
```

**关键陷阱**：OAuth 流程**本身能成功**（`POST /oauth/token` 返回 200 + 完整 token + athlete 对象）—— 但**任何 API 调用**（`/athlete`、`/uploads`、`/activities`）全部 403 Inactive。**token 有效期 + scope 都没问题，但 Strava 后台整个 Application 标记 Inactive**。

**根因（用户截图 2026-07 确认）**：Strava 在 2026-07 调整了 API 访问政策——**今后仅向订阅（Strava Premium）用户开放**。即使用户的 Application 设置页显示"应用已升级"（绿色 banner），但 API 仍然因账号非 Premium 而 Inactive。

**怎么判断是这个问题**：
1. 跑 OAuth 拿 token → ✅ HTTP 200
2. `GET /athlete` → ❌ 403 `"code":"Inactive"`
3. `POST /uploads` → ❌ 403 `"code":"Inactive"`
4. **不是 token 过期**（`expires_at` 还很远）、**不是 scope 问题**（scope 含 `activity:write`）—— 是账号级 / 应用级封禁

**诊断命令**（30 秒确认）：
```bash
ACCESS=$(python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))['access_token'])")
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer *** https://www.strava.com/api/v3/athlete
# 输出 403 + body 含 "Inactive" → 确认是政策封禁
```

**修复路径**（用户做，agent 做不了）：
1. 用户升级 Strava Premium 订阅（按月付费）→ API 自动恢复
2. 或者跳过 Strava API，改用 Garmin Connect / 第三方平台同步（MAGENE C706 通过 OnelapFit 桥接）
3. 或者不上传，活动记录本地保留

**agent 在此场景下的正确行为**：
- ❌ 不要反复重试（不会自愈，纯浪费时间）
- ❌ 不要去 refresh token（refresh 也 403 Inactive）
- ❌ 不要去改 strava-upload.py 脚本（脚本没问题）
- ✅ 一次失败 → 立刻报"Application Inactive，403" + 报告本地已解析的活动数据（距离/HR/区间）→ 让用户决定订阅 / 换平台 / 跳过
- ✅ FIT 文件 + 纠偏结果**保留在本地**，用户决定后直接重传即可

**临时上传的变通思路**（06-30 实测 06-27 那条仍然能用——说明老 OAuth token 在 token 自身未过期前**可能**仍可用）：
- token 缓存于 `~/.hermes/strava_token.json`，`expires_at` 内仍可用
- 但一旦过期 refresh → 拿新 token → 仍 403 Inactive
- **结论**：在 2026-07 政策下，**所有未来上传都会卡在 403 Inactive**，除非用户订阅 Premium

- `references/strava-api-paywall-2026-07.md` — 2026-07 Strava 政策变更（仅 Premium 开放 API）
- `references/upload-flow-2026-07.md` — **🆕 07-04 到 07-06 一条龙工作流 + 双纠偏 bug 修复 + OnelapFit vs Strava 渲染差异**。用户多次反馈"墨迹啥"/"你能干点啥"/"天天弄得事都能搞错"的核心改进。一口气走完 refresh → 查重 → cp → 上传 5 步，不要打断。
- `references/oauth-application-swap-2026-07-02.md` — **🆕 2026-07-02 实测**：从旧 Application 切到新 Application 恢复 API 的完整流程、token 截断写文件连环坑、OAuth code 一次性失效、route 一致性对比方法

**🆕 2026-07-02 实测：Application 切换恢复 API 的完整流程**见 `references/oauth-application-swap-2026-07-02.md`——用户从旧 Application (client_id=217315) 切到新 Application (client_id=178483) 后 API 恢复，不需订阅 Premium。包含 OAuth code 一次性失效、token 截断写文件的连环坑、route 一致性对比方法。

### OAuth code 直接换 token（无需 web callback）

用户从浏览器手动复制 `?code=...` URL 时，常态是：在 Strava 授权页点同意 → 跳到 `localhost:3000/api/strava/callback?code=xxx&scope=...` → 复制整条 URL 发给 agent。这时**不需要走 `<你的项目名>` 的 web callback**（依赖数据库 StravaAccount + session），**直接用 Strava OAuth 端点 + 现有 client_secret 换 token**：

```python
import json, requests
tok_old = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))
r = requests.post('https://www.strava.com/oauth/token', data={
    'client_id': '217315',                              # 来自 strava-upload.py 第 19 行硬编码
    'client_secret': tok_old['client_secret'],           # 来自上一次成功的 token.json
    'code': 'USER_PROVIDED_CODE',                        # 用户 URL 里 ?code= 的值
    'grant_type': 'authorization_code',
})
if r.status_code == 200:
    data = r.json()
    data['client_secret'] = tok_old['client_secret']     # 保留给后续 refresh
    json.dump(data, open(os.path.expanduser('~/.hermes/strava_token.json'), 'w'), indent=2)
```

**注意事项**：
- Strava OAuth code 用一次就失效——用户发的 URL 如果之前没人用过就还能用
- 如果 `client_secret` 字段在老 token 文件里缺失 → 先去 <你的项目名> web 流程走一次建立
- 写入后**立刻跑 `GET /athlete` 验证**，区分"token 拿到了能用" vs "OAuth 成功但 API 仍 Inactive"（后者是 2026-07 政策问题，见上一节）

### ⚠️ 2026-07-02 实测：OAuth code 一次性失效 + token 截断连环坑

**场景：** 用户连续发同一个 callback URL 两次（code 用过了还以为是新 code）。

| 步骤 | 结果 | 错误 |
|---|---|---|
| 第一次 curl POST `/oauth/token` 用 code `c95ee6...` | ✅ HTTP 200 + athlete | —— |
| 第二次再跑同样的 code | ❌ HTTP 400 `errors[0].code = "invalid"` | OAuth code 用一次就废 |
| 写入 token.json 时**用工具自动截断的展示值** `0cf037...930a`（不是完整 40 字符） | ❌ 后续 `/athlete` 401 | **截断的 token 是无效的** |

**核心陷阱（这是 07-02 踩的最大坑）：**
1. `requests.post(...).json()` 返回的 `access_token` 是**完整 40 字符 hex 串**
2. 但工具/Hermes 把这个值展示在回复里时**会截断成 `abc...xyz` 形式**（防 token 泄漏）
3. 如果在 Python 里又跑 `requests.post(...).json()`，**自己 new 一次 curl** 拿 raw response，不要相信展示里截断的字符串
4. **正确做法（验证长度再写文件）：**
   ```python
   import requests
   r = requests.post('https://www.strava.com/oauth/token', data={
       'client_id': '<新 ID>', 'client_secret': '<新 secret>',
       'code': '<新 code>', 'grant_type': 'authorization_code',
   })
   print('HTTP', r.status_code, 'token len:', len(r.json().get('access_token','')))
   # 必须看到 40 才算成功。0 或 <40 = 错误
   ```

**判断是否需要重新授权：**
- `HTTP 400` + body `"code":"invalid"` → OAuth code 已用过，**必须重新走浏览器授权拿新 code**
- `HTTP 401` + 刚写的 token 长度 40 → token 完整但 client_id/secret 错（应用换了）
- `HTTP 403` + body `"code":"Inactive"` → 应用被 Strava 标记 Inactive（2026-07 政策见上一节）

**典型修复流程（07-02 实测）：**
1. 用户发新 callback URL（code `85b4a97...`）
2. 新 curl 拿完整 token（access_token 40 字符）→ ✅ HTTP 200
3. 写入 `~/.hermes/strava_token.json`
4. `GET /athlete` 验证 → ✅ HTTP 200
5. 立刻 `POST /uploads` 上传 FIT → ✅ 成功

**用户场景对应：**
- "咋 token 不对 / 401 / 重新授权" → 检查 token 长度 + code 是否用过 + Application 是否 Inactive
- 用户**切换 Application**（client_id 变了）→ 之前所有 code 全部作废，**必须重新走授权**
- 用户**给新 client_secret**（旧 secret 已废）→ 用新 secret 重跑 OAuth 用过的 code → 也会 400（secret 改后之前 code 跟旧 client 不匹配）

**记号：**
- OAuth 流程**一次性**——code、client_secret、client_id 三者绑定，**任一改了就得重新授权**
- token 文件里**只存完整值**，长度 < 40 = 一定有问题

## 水平偏移必须区分（五类，06-10 修订）

| 类型 | 表现 | 原因 | 是否需修复 |
|------|------|------|----------|
| **GPS设备漂移（全失锁）** | 坐标飞到 `(68°N, 217°E)` | 星历过期，GPS完全失锁 | ✅ 是（Encoder 重建） |
| **两段式漂移** | 前段正常，后段突然整体偏移 0.1-0.5°（数十km），无过渡 | GPS信号短暂中断后重锁，锁定在错误位置 | ✅ 是（Encoder 修复偏移段） |
| **GCJ-02 偏移** ⚠️ | 路线**全程恒定方向、恒定距离**偏 300-500m | 国产码表（含 **MAGENE C706**）输出 GCJ-02 而非 WGS-84 | ✅ 是（GCJ-02→WGS-84 转换后重传） |
| **Strava 地图匹配** | 路线整体偏 30-100m | Strava 自己的算法 | ❌ 否（Strava 行为） |
| **🆕 真实长距离路线（误判高发）** | spread 大（如 lon 0.4°=40 km），但**滚动 chunk 中位单向往最远再原路返回** | 真实骑行轨迹往返覆盖范围大 | ❌ 否（路线就这样） |

**⚠️ 2026-06-10 关键修订：上面第 5 类是高频误判源！** 旧判断「spread 大 = 漂移」是错的，05-23 实测：lon spread 0.43°（约 40 km）原本被标为「严重漂移」，但跑滚动 chunk 中位对比后发现路线**单向往东 38 km 再原路返回**——spread 大是真实覆盖范围，不是 GPS 错位。**判断流程：spread 偏大 → 先跑滚动 chunk 中位对比 → 看路线是「单向往返」还是「两段恒定错位」**。详见 `references/fit-method-comparison.md` 的「spread 诊断流程」。

**判断方法**：用户说"地图偏移不对"时，按以下顺序排查：
1. **方向恒定 + 距离恒定 + 用户说"固定偏移"** → **GCJ-02 偏移**（国产码表默认行为）→ 用 `--correct-coords` 转换后重传
2. **FIT 坐标 >50° / >150°** → 设备漂移（完全失锁）→ Encoder 重建
3. **前段正常 + 后段突然整体偏移 0.1-0.5°，无过渡** → 两段式漂移 → Encoder 修复偏移段
4. **孤立跳点（前后都 < 50m，中间突然 > 1km）** → GPS 跳点 → fitdecode 删除跳点
5. **路线形状正确仅偏 30-100m** → Strava 地图匹配，无需修
6. **🆕 spread 大（lon > 0.3°），但 chunk 中位单向往返** → **真实长距离路线**（不是漂移！）→ 直接上传即可

**GCJ-02 确诊方法（最可靠）**：跑 OSM 偏差分析，把 OSM 路网**同时**转成 WGS-84 和 GCJ-02 两个版本，GPS 点跟两个版本路网都比一遍：
- "GPS 当 WGS-84 vs WGS-84 路网" 中位偏差大（如 50-500m）
- "GPS 当 GCJ-02 vs GCJ-02 路网" 中位偏差骤降到 < 10m
→ **确诊 GPS 输出 GCJ-02**

**注意（06-01 用户偏好）：** 用户说"不需要再管偏移了"——**默认情况下不主动分析 GPS 漂移**，直接上传。但用户**主动反馈"路线不对"/"位置不对"/"固定偏移"**时仍需做 OSM + GCJ-02 双向验证（这是 30 秒内就能排除主要假阳性的方法）。**06-04 起修订**：用户说"码表版本更新了，以后默认按纠偏处理"——纠偏默认必加，但 OSM 诊断仍只在用户主动反馈时做。

### 纠偏方向看起来"飘那么远"是预期行为，不是 bug（07-02 实战反馈）

**用户反馈："纠偏差那么远你搞错了吧" / "西北方向飘那么多"** —— 这其实是 GCJ-02→WGS-84 转换的**正确结果**，但因为方向反直觉容易误判。

**核心事实：**
- GCJ-02（火星坐标）相对 WGS-84 在中国境内**统一往东南方向加偏移**（让国内地图对齐）
- 反向校正（GCJ→WGS）就是把这点偏移补偿回来 → 路线**往西北方向移动 400-500m**

**西安地区典型偏移量（实测）：**

| 字段 | GCJ-02（原始） | WGS-84（纠偏后） | 偏差 |
|---|---|---|---|
| 起点 lon | 108.83802 | 108.83338 | -0.00464° ≈ **410m 西** |
| 起点 lat | 34.24736 | 34.24890 | +0.00154° ≈ **180m 北** |
| 整体方向 | —— | **西北方向 ~410m** | 整体往西北漂 |

**为什么看起来"飘"：**
- 国内 app（高德/百度/腾讯）显示 GCJ-02 → 用户在国内地图上看 FIT 原始数据**感觉是对的**
- 纠偏后发到 Strava（用 WGS-84）→ Strava 上路线**比国内地图偏西北 410m**，跟用户国内印象对不上
- 用户误以为纠偏"飘"了 → 实际上**纠偏才是国际标准位置**

**验证纠偏方向对错的方法（30 秒）：**
```python
import math

def transform_lng(x, y):  # 国测局标准算法
    PI = math.pi
    ret = 300.0 + x + 2.0*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
    ret += (20.0*math.sin(6.0*x*PI) + 20.0*math.sin(2.0*x*PI)) * 2.0/3.0
    ret += (20.0*math.sin(x*PI) + 40.0*math.sin(x/3.0*PI)) * 2.0/3.0
    ret += (150.0*math.sin(x/12.0*PI) + 300.0*math.sin(x/30.0*PI)) * 2.0/3.0
    return ret
# 在西安 (lat~34.2, lon~108.8) 跑 delta() 看 dlat, dlng 方向
# 应该是 dlat>0（北）、dlng<0（西）→ 纠偏后路线西北方向移动
```

**agent 在这种反馈下的正确行为：**
- ✅ 不要去怀疑纠偏算法（方向是对的）
- ✅ 用 `.fit` (纠偏版) 起点 / `.fit.orig` (原始版) 起点对比，向用户解释"西北方向 410m 是 GCJ→WGS 标准补偿"
- ✅ 截图 Strava 上昆明池、镐京大道等地理特征**确认纠偏版路线位置对**（如果地理特征对，那纠偏是对的）
- ❌ 不要重新反方向纠偏（GCJ→WGS 永远西北补偿）
- ❌ 不要为"消除偏移感"做高斯平滑（会把真实骑行轨迹抹掉）

**用户视角对照表：**

| 坐标系 | 显示正确的地图 |
|---|---|
| GCJ-02（国产码表原始）| 高德、百度、腾讯地图 |
| WGS-84（纠偏后）| Strava、Google Maps、Garmin、国际 GPS 标准 |

### QQ 附件缓存位置（关键！）

如果 Strava 上显示的路线跟实际路线差很多，问题**不在 Strava**，而是 GPS 手表记录的原始坐标就有漂移（如 MAGENE C706 信号飘移）。重新上传同一文件会被 Strava 判定为重复文件拒绝处理。

**解决方法：用 Encoder 重建干净 FIT 文件再上传**

见 `references/fit-rebuild-encoder.md` — 用 fitdecode 读取 + garmin-fit-sdk Encoder 重建，绕过 external_id 重复检测。

1. 删除 Strava 上原有的问题活动（API 无 delete 权限，需网页操作）
2. 用 `~/convert_fit_encoder.py` 重建 FIT 文件（venv python：`~/hermes-agent/venv/bin/python`）
3. 上传到 Strava

**2026-06-01 新增更简单方案：** 用 `fit-tool` 直接 patch 原始 FIT 文件的 record 坐标（保留所有元数据），无需重建：
```bash
fit-tool update <file.fit> --lat <new_lat_semicircles> --lon <new_lon_semicircles>
```
详见 `references/fit-rebuild-encoder.md` 的 fit-tool 方案。

### 海拔膨胀问题（Strava DEM 自动校正）

**不是 bug，是 Strava 正常行为。**

Strava 会对 FIT 文件的海拔数据进行**自己的数字高程模型（DEM）校正**，不使用 FIT 原始海拔。校正后：
- FIT 原始爬升 1140 m → Strava 显示 3664 m（**3.2 倍膨胀**）
- FIT 记录海拔 362~954 m → Strava 显示 394~974 m（整体平移 + 拉伸）

**这是 Strava 的标准流程，无法通过重建 FIT 修复。** Strava 使用自己的 ASTER DEM 而不是 FIT 的 altitude 字段。

**判断标准：**
| 骑行距离 | Strava 爬升正常范围 | 偏高警告 |
|---------|-----------------|---------|
| 145 km | 1500-2500 m | >3000 m |
| 106 km | 800-1500 m | >2000 m |
| 53 km | 300-700 m | >1000 m |

**变通方法**：在 Strava 网页端手动编辑（`Edit → Adjust Elevation`），或关闭「使用 DEM 校正高度」选项。

## QQ 附件缓存位置（关键！）

QQ 发来的 FIT 文件**不**在 strava-uploads 目录，而是缓存在：
```
~/.hermes/cache/documents/doc_<uuid>_qqdownloadftnv5
```

收到 [Attachment: xxx.fit] 后从这里复制到工作目录：
```bash
cp ~/.hermes/cache/documents/doc_<uuid>_qqdownloadftnv5 ~/strava-uploads/xxx.fit
```

**注意：文件可能已存在**（用户重复发送/会话刷新），复制前用 `ls -la` 看时间戳确认。

然后用脚本上传：
```bash
python3 ~/strava-upload.py ~/strava-uploads/xxx.fit Ride --correct-coords
```

## 脚本路径

`~/strava-upload.py`

## Skill 打包导出（2026-06-01）

当用户要求"把这个 skill 打包发给我"或类似请求时，**完整 recipe + 坑 + 投递回退链**见 `references/skill-export-recipe.md`。

核心要点（一句话版）：
1. `cp -r` 到 /tmp 临时目录 + **`chmod -R a+rX`**（原文件 600 别人读不了）
2. 加 README.md（5 分钟入门 + 目录树 + 阅读顺序）
3. **`tar -czf`** 打包（不是 zip——服务器无 zip）
4. `ls <dir> | sort` 对比原目录（**用 `ls` 不用 `find`**，find 会被隐藏文件误导）
5. 投递优先级：GitHub 私有 repo > 邮件附件（用 `python3 smtplib` 不用 `send_message`）> IM 文件传输 > 本地路径

## Hermes 通用陷阱（导出/send_message 时易踩）

**Compaction 提示词不要回显给用户（2026-06-01 教训）：**
- 上下文压缩时系统会注入 `[CONTEXT COMPACTION — REFERENCE ONLY] ...` 整块
- 看到以 `[...]` tag 开头的注入文本（compaction / system note / handoff）→ **当元数据处理**，不转给用户
- 反例：用户问"怎么样了"时若 compaction 提示恰好注入，我误以为用户在发 compaction 内容、回了一句"这一串能不能不发我了"——用户根本不知道 compaction 这事，**纯属我的错**

**send_message 投递失败链：**
- ❌ `send_message(target="email")` 在 hermes-gateway 进程内**不可用**（`os.getenv("EMAIL_*")` 取不到，gateway 启动时没注入 env var）
- ❌ `send_message(target="home")` → "Unknown platform"（home 不是平台名）
- ❌ `send_message(target="qqbot")` 无 `QQBOT_HOME_CHANNEL` 配 → "No home channel set"
- ❌ `send_message(target="weixin")` → iLink `rate limited: ret=-2`（5-15 分钟恢复）
- ✅ 邮件附件：用 `python3 smtplib` + `config.yaml` 顶层 `EMAIL_*`（不走 `os.getenv`）
- ✅ GitHub repo：用户给 PAT → `curl POST /user/repos` → `git init/push` → **`set-url` 脱敏**
- ✅ 本地路径：保存到 `~/strava-uploads/` + 文字 + scp 提示

**Memory 存 PAT 的规矩：** 只存 `ghp_XXXX...末4位` + scope 描述，**绝不存完整 token**。换 PAT 只更新末 4 位。

**Python 缓存陷阱（2026-06-04 教训）：** 改 `strava-upload.py` 后用 `python3 -c "import importlib..."` 单测时，Python 会优先用 `~/__pycache__/strava-upload.cpython-311.pyc` 缓存。**改完源码必须 `rm -rf ~/__pycache__/strava-upload*`**，否则单测跑的是旧版本。**或用 `python3 -B`** 禁缓存。

## 各次骑行 GPS 状态记录

| 日期 | 距离 | GPS 状态 | 处理方式 |
|------|------|---------|---------|
| 05-15 | 53 km | ✅ 干净（WGS-84，无漂移） | 直接上传 |
| 05-23 | 106 km | ✅ **真实长距离路线**（不是漂移！spread lat=0.089° lon=0.43°，但滚动 chunk 中位对比显示路线**单向往东 38 km 再原路返回**，spread 大是真实轨迹覆盖范围）。⚠️ SKILL 旧版误判为「严重漂移」，06-10 修正 | 直接纠偏上传（无需分段） |
| 05-26 | 53 km | 严重漂移（坐标飞掉） | 需 Encoder 重建 |
| 05-30 | 144 km | ⚠️ 两段式漂移（前79%偏东0.22°~0.48°，后段偏东0.14°）→ GPX 中介法修复 ✅ | GPX 中介 Encoder 重建，校正后 lon 108.57°~108.95° |
| 06-01 | 54 km | ✅ 干净（spread lat=0.062°, lon=0.088°，西安城区无漂移） | 直接上传 |
| 06-02 早 | 54 km | ✅ 干净（spread lat=0.062°, lon=0.088°，西安城区无漂移）。首条触发"duplicate"误报（UTC 跨日），实际是已上传 | 直接上传（先查 status → 已存在则跳过） |
| 06-02 早 #2 | 53.8 km | ✅ 干净（spread lat=0.057°, lon=0.089°） | 直接上传 |
| 06-04 早 | 54.4 km | ⚠️ GCJ-02 偏移（码表输出 GCJ-02，Strava 路线偏 300-500m） | GPX 中介法纠偏上传（`--correct-coords`）。**06-04 起 `correct_fit_file` 重写为 GPX 中介法（之前 binary patch 有 4 个 bug，全废）** |
| 06-06 | 96.1 km | ✅ 干净（GCJ-02 纠偏后 lon 108.84~109.16 路线正常往返，Z3 有氧耐力区） | 直接纠偏上传 |
| 06-08 | 54.7 km | ✅ 干净（Z2 脂肪燃烧区） | 直接纠偏上传 |
| 06-09 | 53.6 km | ✅ 干净（Z2 脂肪燃烧区） | 直接纠偏上传 |
| 06-10 | 54.5 km | ✅ 干净（Z2 脂肪燃烧区，avg 134.1 bpm 接近 Z3 边界） | 直接纠偏上传 |
| 06-27 | 133.6 km | ✅ 干净（Z3 有氧耐力区，avg 144.8 bpm） | 直接纠偏上传 |
| 06-30 | 75.6 km | ✅ 干净（Z2 脂肪燃烧区，avg 138.3 bpm） | 直接纠偏上传 |
| 07-02 | 75.6 km | ✅ 干净（GCJ-02 纠偏后 WGS-84，起点 (34.249, 108.833)，Z2 脂肪燃烧区）| --correct-coords 默认纠偏上传（**新 Application 178483 恢复**，07-02 实测 OAuth + token 全流程）|

### 07-02 实测教训：路线"应该和 X 一致"的对比方法

**场景：** 用户上传完后说"这条路线应该和 06-30 一样"——实际对比发现两条路线起点/终点差 ~870m 起点 + 平均最近距离 487m。

**核心结论：** 两条路线起点/终点差 487m 不一定是纠偏错，**多半是两条本就不重合的骑行路线**（同方向但起点/终点不同）。

**对比方法（30 秒，给出明确判断）：**

```python
import json, requests, math
tok = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))
H = {'Authorization': f'Bearer {tok["access_token"]}'}

# 拿两个活动的 streams
def get_streams(aid):
    r = requests.get(f'https://www.strava.com/api/v3/activities/{aid}/streams?keys=latlng', headers=H)
    return [p for p in r.json()[0]['data'] if p[0] is not None]

s1 = get_streams(19144951686)  # 07-02
s2 = get_streams(19117024583)  # 06-30

def dist(a, b):
    dlat = (a[0]-b[0])*111000
    dlon = (a[1]-b[1])*111000*math.cos(math.radians(a[0]))
    return math.sqrt(dlat*dlat + dlon*dlon)

# 1. 起点终点距离
print(f'起点距离: {dist(s1[0], s2[0]):.0f}m')
print(f'终点距离: {dist(s1[-1], s2[-1]):.0f}m')

# 2. 路线平均最近距离（每隔 50 点采样）
diffs = [min(dist(p1, p2) for p2 in s2) for p1 in s1[::50]]
print(f'平均最近距离: {sum(diffs)/len(diffs):.0f}m')
print(f'最小: {min(diffs):.0f}m, 最大: {max(diffs):.0f}m')
```

**判断阈值（07-02 实测参考）：**

| 平均最近距离 | 判断 | 处理 |
|---|---|---|
| < 30m | 完全重合（同一条路线）| —— |
| 30-100m | 路线基本一致，**有少量偏差**（可能是地图匹配或纠偏微差）| 不需处理 |
| 100-500m | 两条不同但相似的骑行路线 | **不要改任何东西** |
| 500-1000m | 两条完全不重合的路线 | **确认是不是同一条** |
| > 1000m | 大概率用户记错了，或者某次 GPS 完全漂移 | 拉本地 FIT 原始数据对比 |

**纠偏方向确认（07-02 用 `06-30.fit` 和 `06-30.fit.orig` 对比 Strava streams 起点确认）：**

```python
import fitdecode
def to_deg(s): return s / (2**31/180)

# 读本地纠偏版
coords = []
with fitdecode.FitReader('strava-uploads/MAGENE_C706_2026-06-30_053052_<DEVICE_UID>.fit') as fit:
    for c in fit:
        if type(c).__name__ != 'FitDataMessage' or c.mesg_type.name != 'record': continue
        for f in c.fields:
            if f.name == 'position_lat': coords.append((to_deg(f.value),))
            if f.name == 'position_long': coords[-1] = (coords[-1][0], to_deg(f.value))
        if len(coords) >= 1: break

# Strava 上的
r = requests.get('https://www.strava.com/api/v3/activities/19117024583/streams?keys=latlng', headers=H)
strava_start = r.json()[0]['data'][0]

print(f'本地纠偏版起点: {coords[0]}')  
print(f'Strava 起点:    {strava_start}')
# 两者一致 → 上传的是纠偏版，路线位置在 Strava 上是对的
# 两者差 ~410m → 上传的是原始 GCJ-02 版，纠偏漏了
```

**07-02 实战结果：**
- 06-30 本地纠偏版起点 `(34.24404, 108.84269)` = Strava 起点 → **06-30 上传时已经纠偏**
- 06-30 vs 07-02 起点最近距离 570m / 终点最近距离 802m / 平均最近距离 487m
- 判断：**两条不同的相似骑行路线**（同区域不同起点），**不是纠偏问题**

**agent 在这种反馈下的正确行为：**
- ✅ 用 Strava streams 拉两条路线 latlng，**给出起点/终点/平均距离具体数字**
- ✅ 对比本地 `.fit` (纠偏版) 跟 Strava 起点，**确认上传的是纠偏版**还是直传版
- ✅ 如果两条路线平均最近距离 < 100m 才是真的"路线不一样"，100-500m 是不同骑行
- ❌ 不要因为用户说"应该一样"就盲改纠偏方向或重传
- ❌ 不要重新反方向纠偏（GCJ→WGS 永远是西北补偿）

## fitdecode 正确用法（多次踩坑，已验证）

`fitdecode` 没有 `FitFile` 类，用 `FitReader`。fitdecode 的 FitDataMessage 对象**没有 `.get()` 方法、字段名是 `FitDataMessage` 的动态属性**，正确模式：

```python
import fitdecode

with fitdecode.FitReader('/path/to/file.fit') as fit:
    for chunk in fit:
        # 跳过 FitDefinitionMessage、FitHeader 等非数据消息
        if type(chunk).__name__ != 'FitDataMessage':
            continue
        # chunk.mesg_type.name 是消息类型字符串（'record', 'session', ...）
        if chunk.mesg_type.name != 'record':
            continue
        # ⚠️ 用 getattr 而非 dict comprehension：
        for f in chunk.fields:
            # f.name 字符串, f.value 原始值（semicircles int / datetime / float）
            v = getattr(chunk, f.name, None)  # 这样访问最稳
            # 或者：f.value 直接拿值
            print(f.name, f.value)
```

**关键陷阱（多次重试才意识到）：**
1. ❌ `chunk.get('position_lat')` → AttributeError, FitDataMessage 没有 .get()
2. ❌ `{f.name: f.value for f in chunk.fields}` → 看似对，但访问 field 自身属性会失败，必须用 `f.value` 而非 `f`
3. ❌ `chunk.timestamp` → AttributeError, 因为 chunk.fields 是 `FieldData` 对象，不是直接属性
4. ✅ 正确：`getattr(chunk, f.name, None)` 或 `f.value`（在循环里用 f.value 最简洁）

**fitdecode 0.11 enum / scaled 字段值（06-04 实战发现）：**
- `f.value`：enum 是 string（`'cycling'`），scaled 数字是米（`54424.44`），datetime 是 timezone-aware
- `f.raw_value`：enum 是 int（`2`），scaled 数字是底层 unscaled int（`5442444` cm）
- **garmin-fit-sdk Encoder 期望**：enum 用 int（`f.raw_value`），scaled 数字用 m/s 形式（`f.value`），datetime 用 FIT uint32 时间戳
- **混合策略**：先用 `_try_int(f.value)`，失败且 `f.value` 是 str 时回退 `_try_int(f.raw_value)`

**距离字段 `distance`**：单位是**米**，不是公里！`max(r['distance'])` 直接除以 `1000` 得 km。

**坐标转换（semicircles → degrees）**：
```python
def semicircles_to_deg(s):
    return s / (2**32 / 360)
# 简化：s / (2**31/180) 也行（精度够用）
```

## 收到 FIT 附件后的工作流程（06-04 修订：默认纠偏后上传）

**06-04 起新工作流（用户更新码表版本后）：** 用户明确说"码表版本更新了，以后默认按纠偏处理" → **每次上传都加 `--correct-coords` 标志**，不要先传干净版再问用户。

1. **QQ 附件找到**：`cp ~/.hermes/cache/documents/doc_<uuid>_qqdownloadftnv5 ~/strava-uploads/xxx.fit`
2. **纠偏后上传**：`python3 ~/strava-upload.py ~/strava-uploads/xxx.fit Ride --correct-coords`
3. **抓取活动详情报告**（距离/时间/爬升/均速/起点终点）

**历史规则（已作废）：** 06-01~06-02 期间用户曾说"不需要再管偏移了"采用默认直传，06-04 修订。

**仅当用户明确说"路线不对"或"地图偏移"时**，才进入诊断流程（见下「旧的 GPS 诊断工作流」+「06-10 新增：滚动 chunk 诊断」）。

### 旧的 GPS 诊断工作流（仅供用户主动要求时使用）

```python
from fitdecode.reader import FitReader

with FitReader('/path/to/file.fit') as fit:
    records = []
    for block in fit:
        if type(block).__name__ == 'FitDataMessage' and block.mesg_type.name == 'record':
            r = {f.name: f.value for f in block.fields}
            records.append(r)

lons = [r['position_long'] for r in records if 'position_long' in r]
lats = [r['position_lat'] for r in records if 'position_lat' in r]
distances = [r.get('distance', 0) for r in records]

total_dist_km = max(distances) / 1000
def semicircles_to_deg(s):
    return s / (2**32 / 360)

print(f'距离: {total_dist_km:.1f} km')
print(f'Lon: {semicircles_to_deg(min(lons)):.4f} ~ {semicircles_to_deg(max(lons)):.4f}')
print(f'Lat: {semicircles_to_deg(min(lats)):.4f} ~ {semicircles_to_deg(max(lats)):.4f}')

timestamps = [r['timestamp'] for r in records if 'timestamp' in r]
if timestamps:
    dur_h = (timestamps[-1] - timestamps[0]).total_seconds() / 3600
    print(f'时长: {dur_h:.2f} h, 均速: {total_dist_km/dur_h:.1f} km/h')
```

- 坐标正常（34°N 附近，lon ≈ 108.8°）→ 直接上传
- 坐标漂移（lat > 50° 或 lon > 150°）→ 提示用户去 OnelapFit 更新星历
- 坐标正常但 lon 有两个聚类（差值 0.1-0.5°，无过渡）→ 两段式漂移，用 `scripts/correct_gps.py` 修复后上传

### 🆕 滚动 chunk 诊断（06-10 新增，区分「真实长路线」vs「漂移」）

**问题：** 旧的"看 spread 大小"判断不够——spread 大可能是漂移，也可能是真实长距离路线。05-23 实测：lon spread 0.43° 旧版标为"严重漂移"，新版发现是真实 38 km 往返路线。

**做法：** 把 records 分成 N 个等长 chunk（推荐 500 records/chunk），看每个 chunk 的 lon 中位变化模式：

```python
def rolling_chunk_median(records, field, chunk_size=500):
    """每 chunk_size 个 record 取一次中位"""
    n = len(records)
    out = []
    for i in range(0, n, chunk_size):
        chunk = records[i:i+chunk_size]
        vals = [semicircles_to_deg(r[field]) for r in chunk if field in r]
        if vals:
            out.append((i, min(vals), max(vals), sorted(vals)[len(vals)//2]))
    return out

def diagnose_route(lons_rolling, lats_rolling):
    """判断「真实长路线」vs「漂移」vs「正常」"""
    if not lons_rolling: return 'unknown'
    lon_meds = [c[3] for c in lons_rolling]
    lon_spread = max(lon_meds) - min(lon_meds)
    
    if lon_spread < 0.1:
        return 'normal'  # 城区短路线
    if lon_spread > 0.3:
        # spread 大——看 chunk 中位是不是「先涨到最远再降回来」
        peak_idx = lon_meds.index(max(lon_meds))
        # 如果峰值在中段，前后都下降 → 真实长路线往返
        pre = lon_meds[:peak_idx]
        post = lon_meds[peak_idx+1:]
        going_up = all(pre[i] <= pre[i+1] for i in range(len(pre)-1)) if pre else True
        coming_back = all(post[i] >= post[i+1] for i in range(len(post)-1)) if post else True
        if going_up and coming_back:
            return 'real_long_route'  # 真实长距离路线
        else:
            return 'drift'  # 真漂移
    return 'normal_or_short'
```

**05-23 实测结果：**
```
[    0-  500] med 108.86   [8500- 9000] med 109.28 (peak)
[ 4500- 5000] med 109.16   [14000-14500] med 108.89
→ diagnose_route 返回 'real_long_route' ✅
```

**决策表：**

| diagnose_route 返回 | 处理方式 |
|---|---|
| `normal` | 直接上传 |
| `real_long_route` | 直接上传（路线就这样） |
| `drift` | 用 `scripts/correct_gps.py` 修复偏移段或上 GPX 中介法 |

### GPS 正常的根本原因（星历同步）

骑前在 **OnelapFit（顽鹿运动）App** 连接码表**同步了最新星历**，GPS 全程没有失锁。每次骑车前都需执行此操作（星历有效期约 2-4 小时）。

## 07-04 用户反馈：上传 FIT 必须一条龙做完（"墨迹啥"/"你能干点啥"/"天天弄得事都能搞错"）

**用户多次反馈（07-04 到 07-06 实测）：** "墨迹啥"、"你能干点啥"、"你是不是忘了纠偏了"、"天天弄得事都能搞错"——**核心痛点**：每次上传 FIT 都被打断问问题、token 过期又走一遍、duplicate 又问一次、纠偏出错又回头查。

**正确工作流（一口气做完，不要中途打断）：**

```bash
# 1. refresh token（30 秒，失败才走 OAuth）
python3 -c "
import json, requests
d = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))
r = requests.post('https://www.strava.com/oauth/token', data={
    'client_id': d['client_id'], 'client_secret': d['client_secret'],
    'grant_type': 'refresh_token', 'refresh_token': d['refresh_token']
})
if r.status_code == 200:
    new = r.json()
    new['client_id'] = d['client_id']
    new['client_secret'] = d['client_secret']
    json.dump(new, open(os.path.expanduser('~/.hermes/strava_token.json'), 'w'), indent=2)
    tok = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))
    assert len(tok['access_token']) == 40, f'❌ token 长度不对: {len(tok[\"access_token\"])}'
"

# 2. 检查 Strava 上是否已有该日期活动（避免 duplicate）
python3 -c "
import json, requests
tok = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))
H = {'Authorization': f'Bearer {tok[\"access_token\"]}'}
r = requests.get('https://www.strava.com/api/v3/athlete/activities?per_page=15', headers=H)
date = 'YYYY-MM-DD'
for a in r.json():
    if date in a.get('start_date_local',''):
        print(f'已存在: {a[\"id\"]} {a[\"distance\"]/1000:.1f}km ext_id={a.get(\"external_id\")!r}')
"

# 3. cp QQ 附件（去掉 doc_<uuid>_ 前缀，保留 MAGENE_C706_YYYY-MM-DD_*.fit）
QQ=~/cache/documents/doc_<uuid>_MAGENE_C706_YYYY-MM-DD_*.fit
DEST=~/strava-uploads/MAGENE_C706_YYYY-MM-DD_HHMMSS_<DEVICE_UID>.fit
cp $QQ $DEST
rm -rf __pycache__/strava-upload*  # 避免 Python 缓存陷阱

# 4. 上传（带纠偏）
python3 -B ~/strava-upload.py $DEST Ride --correct-coords

# 5. 抓活动详情 + 报告起点距离
python3 -c "
import json, requests
tok = json.load(open(os.path.expanduser('~/.hermes/strava_token.json')))
H = {'Authorization': f'Bearer {tok[\"access_token\"]}'}
r = requests.get(f'https://www.strava.com/api/v3/activities/<aid>', headers=H)
a = r.json()
print(f'✅ {a.get(\"name\")} | {a[\"distance\"]/1000:.1f}km | {a[\"moving_time\"]/3600:.2f}h')
print(f'   起点 {a[\"start_latlng\"]}  终点 {a[\"end_latlng\"]}')
print(f'   https://www.strava.com/activities/{a[\"id\"]}')
"
```

**agent 在每次收到 FIT 时的正确行为（07-04 修订）：**

- ✅ **一口气走完** refresh → 查重 → 复制 → 上传 → 报告 五个步骤，**不要中途打断用户**
- ✅ 如果步骤 2 查到 duplicate（已上传），**直接报"已存在 activity id"**，不要让用户决定
- ✅ token 长度必须 40 字符，**写入后立刻验证**（`assert len == 40`）
- ✅ QQ 附件 cp 时**去掉 `doc_<uuid>_` 前缀**保留 `MAGENE_C706_*.fit` 命名（脚本不识别前缀会 FileNotFoundError）
- ❌ 不要"先 refresh token → 失败 → 问你是否走 OAuth → 等你回话"这种分段
- ❌ 不要"先上传 → duplicate → 问你删不删原版"（07-04 用户反馈："墨迹啥"）
- ❌ 不要"上传成功就完事"（07-04 用户反馈："你是不是忘了纠偏"——必须报告纠偏动作 + 起点距离对比）

---

## ⚠️ 07-06 双纠偏 bug（重大！必须修脚本）

**症状：** 上传 FIT 后 Strava 路线偏西 ~825m（而不是预期的 ~410m 单次纠偏）。

**根因：** `strava-upload.py` 第 763-775 行的纠偏逻辑**不检查文件是否已经被纠偏过**。当 `fit_path` 已经是纠偏版（WGS-84）但脚本还以为是原始 GCJ-02，调用 `correct_fit_file()` 又做一次 GCJ→WGS 反算 → 偏移量翻倍叠加。

**触发流程：**
1. 第一次上传 `MAGENE_C706_2026-07-06_*.fit`：原始 GCJ-02 → 纠偏成 WGS-84（-410m 西）→ 备份为 `.fit.orig`
2. 第二次上传（用户重新发 QQ 同样的 FIT）→ `cp` 覆盖了 `fit_path` 但 `.fit.orig` 还在
3. 脚本检查 `.fit.orig` 存在 → **仍然调用纠偏** → 把 WGS-84 当 GCJ-02 反算 → 再 -410m 西
4. 最终路线比真实 WGS-84 位置**偏西 825m**

**验证方法（30 秒）：**

```python
# 起点对比：QQ原始 GCJ-02 vs 纠偏后 vs Strava 显示
import fitdecode, json, requests
def to_deg(s): return s / (2**31/180)
def read_start(p):
    with fitdecode.FitReader(p) as fit:
        for c in fit:
            if type(c).__name__ != 'FitDataMessage': continue
            if c.mesg_type.name != 'record': continue
            la = lo = None
            for f in c.fields:
                if f.name == 'position_lat': la = to_deg(f.value)
                if f.name == 'position_long': lo = to_deg(f.value)
            if la and lo: return (la, lo)

qq = read_start('~/cache/documents/doc_*.fit')
fix = read_start('~/strava-uploads/MAGENE_C706_*.fit')

# 单次纠偏应该是 lon 偏移 ~-0.0046° (410m 西)
# 双纠偏会是 lon 偏移 ~-0.0093° (825m 西)
diff_lon = fix[1] - qq[1]
if abs(diff_lon) > 0.008:  # > 0.008° = 一定是双纠偏
    print(f'❌ 双纠偏! diff_lon = {diff_lon:.5f}°')
else:
    print(f'✅ 单次纠偏. diff_lon = {diff_lon:.5f}°')
```

**修复方案（已应用到 `strava-upload.py` 第 763-783 行）：**

```python
# 坐标纠偏（如需要）
fit_path = args.fit_file
if args.correct_coords:
    # 07-06 修复：如果当前 fit_path 已经有 .orig 备份（即文件已经是纠偏版），跳过纠偏
    orig_marker = fit_path + '.orig'
    already_corrected = os.path.exists(orig_marker)
    if already_corrected:
        print(f"\n[*] 检测到 .orig 备份（{orig_marker}），跳过纠偏（已是 WGS-84）")
    else:
        print(f"\n[*] 正在进行 GCJ-02 → WGS-84 坐标纠偏...")
        # ... 原纠偏逻辑
```

**更彻底的方案（推荐，未来实施）：** 不用 `.orig` 文件存在与否判断，改用**坐标特征判断**——如果西安地区坐标 `lon < 108.84` 且 `lat < 34.24`，说明已经是 WGS-84（西安真实经度 108.84-109.0），直接跳过纠偏。

**事故复盘（07-06 实测）：**

| 步 | 起点 lon | 偏差（vs 原始 GCJ-02）|
|---|---|---|
| QQ 原始 GCJ-02 | 108.8473 | —— |
| 第一次纠偏后 | 108.8427 | -0.0046° = **410m 西** ✅ |
| **第二次"纠偏"后**（bug）| **108.8381** | **-0.0092° = 825m 西** ❌ |
| 真实 WGS-84 | ~108.8427 | -0.0046° |
| Strava 显示 | 108.8381 | 偏西 825m |

**用户的反馈链条（07-04 到 07-06）：**

1. "你是不是忘了纠偏？" → 我先分辩"没忘"→ 才去查 → 才验证 → 才承认双纠偏
2. "**先验证再分辩**"——这是 07-02 教训的重复，**再次踩坑**

**agent 在这种反馈下的正确行为：**

- ✅ 听到用户质疑时，**先跑命令验证**，再分辩或认错
- ✅ 不要默认自己是正确的
- ✅ 双纠偏这种 bug 应该在**纠偏完成后自动对比 `.orig` 起点 + `.fit` 起点 + Strava 起点距离**，单次 vs 双重一目了然
- ❌ 不要急着分辩"我做了"，结果被数据打脸

---

## OnelapFit vs Strava 路线位置差异（07-02 用户发截图）

**用户发截图发现：** OnelapFit 上看纠偏后的 07-02 路线**跟 06-30 路线不对齐**（差 ~487m 平均距离），质疑纠偏"飘那么多"。

**实际原因：** **不是双纠偏 bug，是坐标系渲染差异**：
- OnelapFit 用 **GCJ-02（火星坐标）** 渲染
- Strava 用 **WGS-84** 渲染
- 纠偏后的 WGS-84 FIT 在 OnelapFit 上被**当 GCJ-02 解释** → 渲染时往东南偏 410m
- 结果：OnelapFit 上看纠偏后 FIT = **比真实位置往东南飘 410m**
- 而 06-30 用户是直接在 OnelapFit 看（GCJ-02）→ 位置对国内地图对齐
- 两条路线"差 487m"是因为**坐标系不一致 + 起点本来就不一样**（不是同一条骑行）

**判断方法：** 拿 OnelapFit 上的截图 vs Strava 上的截图，**截图坐标说明写的"雁塔区/昆明池/斗门街道"**——如果两个截图都标这些地方 = 位置对得上（路线本就相似，不完全重合）。

**agent 在这种反馈下的正确行为：**

- ✅ 解释"GCJ-02 vs WGS-84 渲染差异"，让用户理解为什么看着位置不一样
- ✅ 用 Strava streams 拉两条路线 latlng，**给出平均最近距离**（487m = 是不同骑行不是 bug）
- ✅ 不要急着"再纠偏一次"或"反方向纠偏"
- ❌ 不要重新反方向纠偏（GCJ→WGS 永远是西北补偿，反方向就回到原始位置 = Strava 上反而飘）

---

## 03-31 历史数据先查 Strava 再上传（07-04 实测）

**07-04 用户发 03-31 FIT**（最早的骑行），脚本上传后报 duplicate of `17925960501`（已经在 Strava 上的"Morning Ride" 109.1km）。用户说"331 是刚才发错的，传今天就行了"——意思是 **03-31 用户不是想传，是 QQ 选错文件**。

**正确流程（07-04 修订）：** 收到 FIT 后**立刻查 Strava 是否有该日期活动**：

```python
# 查 Strava 该日期活动
r = requests.get(f'https://www.strava.com/api/v3/athlete/activities?per_page=50', headers=H)
for a in r.json():
    if '03-31' in a.get('start_date_local',''):
        print(f'  {a["id"]} {a["start_date_local"][:10]} {a["distance"]/1000:.1f}km {a.get("name")}')
```

如果查到已存在 → **直接报"已存在 activity id"**，不要传。

---

## QQ 附件 cp 时文件名前缀陷阱（07-04/07-06 实测）

**QQ 附件缓存命名：** `~/.hermes/cache/documents/doc_<uuid>_qqdownloadftnv5` 或 `doc_<uuid>_qqdownloadxxxxx`。

**直接 cp 不改名** → 文件名是 `doc_09082eb3f7aa_MAGENE_C706_2026-07-05_*.fit` → 脚本找不到（脚本要 `MAGENE_C706_*.fit` 命名）。

**正确做法：**

```bash
cp ~/cache/documents/doc_<uuid>_MAGENE_C706_YYYY-MM-DD_*.fit \
   ~/strava-uploads/MAGENE_C706_YYYY-MM-DD_HHMMSS_<DEVICE_UID>.fit
```

**别忘了删缓存里那个 `doc_<uuid>_` 前缀的文件**——直接 `cp` 然后手动改 dest 路径。

---

## 参考资料

- `references/strava-streams-to-fit.md` — **🆕 Strava Streams API → FIT 重建（用于跨平台同步到国产平台）**。OAuth Bearer 拿不到 Strava 网页 export 时的退路。5970 条 record 重建实测。
- `references/xingzhe-api-mechanics.md` — **🆕 行者 (imxingzhe.com) API 已验证机制**：RSA 登录 + `get_user_workouts` 列活动 + `/upload/fit` 路径与 CSRF 要求。**上传闭环未验证**——silent 200 SPA fallback 警告与规避路径。
- `references/correct-coords-gpx-mediation.md` — **🆕 `--correct-coords` GPX 中介法（2026-06-04 实现）**——为什么放弃 binary patch、白名单、fitdecode 0.11 raw_value 笔记、实测数据
- `references/correct-coords-broken-2026-06-04.md` — **🆕 旧 binary patch 4 个 bug 事故复盘**（已修复，保留作为历史教训）
- `references/check_fit_gps.md` — GPS 质量快速检查脚本（上传前必跑）
- `references/strava-upload-status-check.md` — Strava API 上传状态查询、重复检测、活动 ID 反查方法
- `references/fit-rebuild-encoder.md` — fitdecode + garmin-fit-sdk Encoder 重建干净 FIT 文件的完整方案（GPS 漂移修复核心参考）。**重点：必须用 Decoder 的 mesg_listener 把所有消息原样透传给 Encoder，只修改 record 坐标；否则 Strava 报 "There was an error processing your activity"（元数据丢失）**
- `references/gpx-intermediary-method.md` — ✅ **GPX 中介法**（5/30 验证通过，用于 GPS 两段式漂移修复）。GPX 中介法也用于纠偏（06-04），见 `correct-coords-gpx-mediation.md`
- `references/osm-snap-to-road-analysis.md` — **OSM 路网偏差分析（2026-06-01 新增）**：用 Overpass API + Shapely 给每个 GPS 点找最近路网，区分「GPS 真偏」vs「路网盲区」/「走了 OSM 没覆盖的小路」。5min 滑动窗是核心判断工具。
- `references/two-segment-gps-drift.md` — 两段式 GPS 漂移识别：spread 对比判断「两段式漂移」vs「路线本身更远」，附 `scripts/correct_gps.py` 直接可用
- ~~`references/magene-c706-gps-drift.md`~~ — **已删除（2026-06-01 归档）**：原文档基于错误的 "MAGENE C706 输出 WGS-84" 假设，整篇结论与新发现矛盾。被 `references/magene-c706-gcj02-offset.md` 完整覆盖。
- `references/magene-c706-gcj02-offset.md` — GCJ-02 系统偏移识别（**2026-06-01 推翻重写**：MAGENE C706 实际输出 GCJ-02，路线恒定方向偏 470-490m；OSM 双向验证是最可靠的诊断方法）
- `references/strava-gcj02-wgs84-correction.md` — ✅ GCJ-02→WGS-84 迭代纠偏算法（来自 [xqdoo00o/strava_auto](https://github.com/xqdoo00o/strava_auto)）。**⚠️ 该 reference 的 FIT CRC 多项式部分（0xEDB88320）写错了**——FIT 用 nibble-table（0xCC01 头），不是 0xEDB88320；算法本身正确
- `references/hr-zone-absolute-threshold.md` — **🆕 心率区间绝对阈值法（2026-06-02 修订）**：从 max_hr 百分比法改为绝对阈值（120/140/152/164）的原因、bug 案例、修正方法、版本历史。判断心率区间前先看这个
- `references/fit-method-comparison.md` — ✅ **新增（2026-06-01）**：三种 FIT 修复方法（binary patch / fit-tool update / GPX 中介法）的决策树。给出偏移类型→方法映射、各方法优缺点、决策流程。**遇到 GPS 偏移问题先看这个，再选具体 reference**
- `references/fitdecode-encoder-pitfalls.md` — ✅ **新增（2026-06-04）**：用 fitdecode+garmin-fit-sdk Encoder 重建 FIT 的三个实操坑：(1) fitdecode `f.value` 是 scaled/enum-string，`f.raw_value` 才是 int；(2) `_MESG_NUM` 白名单必须含 `sport` (mesg_num=12)，漏了 Strava 标题变文件名；(3) Encoder 写 scaled float 丢精度（avg_speed 9.088 → 9.0），解决方案：.orig 备份 + 上传后 PATCH description。**实现 GPX 中介法前必看**
- `scripts/correct_fit_gpx.py` — GPX 中介纠偏脚本（06-04 实战版，独立维护副本）。`strava-upload.py` 内的 GPX 中介法是同步版
- `scripts/detect_gcj02_offset.py` — **🆕 GCJ-02 偏移一键诊断（2026-06-01）**：OSM 路网双向投影（WGS-84 vs GCJ-02），输出中位偏差对比。5-10 分钟给出明确结论。用法：`python3 scripts/detect_gcj02_offset.py <file.fit>`
- `scripts/correct_gps.py` — 两段式漂移坐标修正
- `scripts/rolling_chunk_diagnose.py` — **🆕 滚动 chunk 中位对比（2026-06-10 新增）**：区分「真实长距离路线」vs「漂移」。用法：`python3 scripts/rolling_chunk_diagnose.py <file.fit>`。详见 SKILL.md「滚动 chunk 诊断」节
