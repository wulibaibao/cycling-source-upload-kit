# 2026-07-02 Strava OAuth + Application 切换事故链

## 完整时间线

| 时间 | 事件 |
|---|---|
| 06 月 | strava-upload.py 用旧 Application (client_id=217315)，token + 上传一直正常 |
| 07-01 早 | 用户反馈 OAuth code 收到，新 secret 给到，但 401 |
| 07-01 早 | 试 refresh token → 401（refresh 必须用原 client_id） |
| 07-02 上午 | 用旧 client_id (217315) + 旧 client_secret 跑 OAuth → ❌ HTTP 400 invalid grant |
| 07-02 上午 | 用户截图 Strava 后台：**"应用已升级" + 蓝色提示"今后仅向订阅者开放"** |
| 07-02 上午 | 试 OAuth 拿 token → ✅ HTTP 200 + athlete (但 athlete bio 仍 free tier) |
| 07-02 上午 | GET /athlete → ❌ **HTTP 403 Inactive** |
| 07-02 上午 | POST /uploads → ❌ **HTTP 403 Inactive** |
| 07-02 上午 | 写入 token 文件时**用了工具截断的展示值 `0cf037...930a`**（实际值 40 字符） |
| 07-02 上午 | 下一次 GET /athlete → ❌ **HTTP 401**（截断的 token 不完整）|
| 07-02 上午 | 用户问"不是你确定好阿 token 对不对" |
| 07-02 上午 | 用户换新 client_id + 新 secret：`client_id=178483 / client_secret=cf4c7e54...2e3` |
| 07-02 上午 | 用旧 code + 新 client → ❌ HTTP 400 invalid（code 是跟旧 client 绑定的） |
| 07-02 上午 | 用户重新走浏览器授权，发新 callback URL（code `85b4a97...ce66`）|
| 07-02 上午 | 用新 client + 新 code → ✅ HTTP 200，access_token **完整 40 字符** |
| 07-02 上午 | 写入 token.json → ✅ |
| 07-02 上午 | GET /athlete → ✅ HTTP 200，athlete 完整返回 |
| 07-02 上午 | POST /uploads → ✅ 成功，活动 ID 19144951686 |
| 07-02 上午 | 用户问"纠偏差那么远" → 解释 GCJ→WGS 西北方向补偿（410m 西 + 180m 北）|
| 07-02 上午 | 用户问"应该和 06-30 一致" → 拉 streams 对比，确认两条路线 487m 平均差（同区域不同骑行，不是纠偏问题）|

## 核心教训

### 1. OAuth code 是**一次性**的（这个之前在 SKILL 写了，没记住）

- 用户连续发同一个 callback URL 是常态（code 已用过）
- 第一次 HTTP 200 + athlete 不代表能用 API（Inactive 状态）
- 第二次同 code 必 400 invalid

### 2. **token 截断写文件是 07-02 最大坑**（之前 SKILL 没说！）

- `requests.post(...).json()` 拿到的 access_token 是完整 40 字符 hex 串
- 但工具/Hermes 展示时截断成 `abc...xyz` 防泄漏
- 如果 Python 里再次调用时**没重新 new 一次 curl** 而是照抄截断字符串，写入文件后 401
- **正确做法**：每次都 new `requests.post(...)` 拿 raw response，**打印 token 长度**（必须 40）再写文件

### 3. **Application 切换是 2026-07 解决 Inactive 的关键**

- 旧 Application (client_id=217315) 被 Strava 标记 Inactive
- 新建 Application (client_id=178483) → 新建 client_secret → 重新 OAuth → API 恢复
- 即使账号 `premium: false` 也没关系（新 app 也没要订阅）
- **判断流程**：
  1. OAuth 拿 token HTTP 200 → 跑 GET /athlete
  2. GET /athlete HTTP 403 Inactive → 整个 Application 被锁
  3. 解决路径：建新 Application（用户操作）+ 重新 OAuth

### 4. **OAuth code 跟 client_id/secret 三者绑定**

- 改 secret 但用旧 code → 400 invalid（secret 改了前一个 code 就跟旧 secret 不匹配）
- 改 client_id 但用旧 code → 400 invalid（client_id 改了前一个 code 跟旧 client 不匹配）
- **三者任一改，必须重新走浏览器授权**

## 根因诊断树（agent 跑）

```
OAuth 拿到 token 了，但 API 失败
├── 401 → token 不完整（截断写文件？长度 < 40？）
│       fix: 重新 new requests.post() 拿 raw，写文件前 print(len)
├── 403 Inactive → 整个 Application 被锁
│       fix: 让用户在 Strava 后台新建 Application，发新 client_id/secret + 重新走浏览器授权
├── 400 invalid grant → OAuth code 已用过 或 client_id/secret 错
│       fix: 重新走浏览器授权拿新 code（确认 client_id/secret 是新的）
└── 200 + athlete full → OK，上传
```

## 防错检查清单（写 token.json 前）

- [ ] `access_token` 长度 = 40
- [ ] `refresh_token` 长度 = 40
- [ ] `expires_at` 跟现在时间差 < 6 小时（access_token 6 小时有效期）
- [ ] `scope` 含 `activity:write`（上传必需）
- [ ] 跑 GET /athlete 拿到 200 + athlete.id 跟之前一致（防 client_id 错）

## 跟用户说的话术（07-02 实战）

| 用户反馈 | agent 正确回复 |
|---|---|
| "传不了吗" | "试一下，立刻把 07-02 FIT 用新 token 上传" |
| "不是你确定好阿 token 对不对" | "你说得对——我上一轮用了截断的展示值，不完整 401。重新 new curl 拿完整 40 字符" |
| "西北方向飘那么多" | "GCJ→WGS 就是西北方向补偿 410m，这是标准行为，不是 bug" |
| "轨迹应该是和 630 的轨迹一致" | "拉 Strava streams 对比两条路线，487m 平均最近距离——两条不同的相似骑行" |