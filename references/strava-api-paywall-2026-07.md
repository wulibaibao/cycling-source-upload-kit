# Strava API 2026-07 政策变更：付费墙

## 政策摘要

Strava 在 **2026 年 7 月** 调整了开发者 API 的访问权限：**今后 API 仅向 Strava 订阅用户（Strava Premium）开放**。免费账号（含历史已授权的应用）所有 API 调用都会被后端标记为 `Inactive`。

## 截图证据（2026-07-02）

用户截图来自 https://www.strava.com/settings/api（「我的 API 应用程序」）：

**绿色 banner**：
> 应用已升级。

**蓝色 banner**：
> 我们正在调整 API 访问权限，今后仅向订阅者开放。立即订阅以确保你的访问权限不受影响。
>
> （"立即订阅" 是蓝色下划线链接）

**含义**：Strava 在 UI 上明确告知——应用虽然"已升级"，但 API 调用权限现在取决于账号的 Premium 订阅状态。

## 实际症状（2026-07-02 实测）

### 症状序列

1. **OAuth 流程本身能成功**——用户在 Strava 授权页点同意，跳转到 `localhost:3000/api/strava/callback?code=xxx&scope=read,activity:write,activity:read_all`。
2. 用 `client_id=217315` + 老 `client_secret`（从 `~/.hermes/strava_token.json` 读）调 `POST https://www.strava.com/oauth/token` → **HTTP 200**，返回完整 token + athlete 对象（firstname=路口, city=西安, **`premium: false`**）。
3. 写入 `~/.hermes/strava_token.json`。
4. **立刻验证** `GET /api/v3/athlete` → ❌ **HTTP 403**：
   ```json
   {"message":"Forbidden","errors":[{"resource":"Application","field":"Status","code":"Inactive"}]}
   ```
5. 调 `POST /api/v3/uploads`（带 gzip FIT 文件）→ ❌ **HTTP 403**，相同 body。
6. 任何其他端点（`/activities`、`/athlete/activities`）都会 403 Inactive。

### 关键陷阱：区分不同 403

| 错误模式 | 含义 | 可修复性 |
|---|---|---|
| HTTP 401 `"Authorization Error" "code":"invalid"` | token 过期 / 错误 / 撤销 | 重新 OAuth |
| HTTP 401 `"code":"missing"` | scope 不够（如缺 `activity:delete`）| 重新 OAuth 加 scope |
| HTTP 429 | 频率限制（15 分钟冷却）| 等待 + 重试 |
| **HTTP 403 `"code":"Inactive"`** | **账号非 Premium / 应用被封禁** | **用户必须订阅 Premium** |

旧版 SKILL 没区分这几种 403，未来 session 可能反复调试 token 才发现是政策问题。**看到 `"code":"Inactive"` 第一反应应是：账号订阅状态，不是 token。**

## 用户画像核对

`/oauth/token` 返回的 athlete 字段里有 `premium: false`（用户的免费账号）—— 这是关键诊断信号：

```python
r = requests.post('https://www.strava.com/oauth/token', data={...})
if r.status_code == 200:
    athlete = r.json().get('athlete', {})
    print(f"premium: {athlete.get('premium')}")  # False = 免费用户 → API Inactive
```

**`premium: false` + `code: Inactive` = 必然命中**。可以省去后续调试步骤。

## 修复路径（按推荐度）

### 1. 用户升级 Strava Premium 订阅（最直接）

- 按月付费（具体价格看 Strava 当时定价）
- 升级后 OAuth 拿新 token → API 自动恢复
- **优点**：原有 `strava-upload.py` + 全部流程无需改
- **缺点**：每月固定成本

### 2. 跳过 Strava API，用 Garmin Connect 中转

MAGENE C706 支持通过 OnelapFit（顽鹿运动）App 同步到多个平台：
- **OnelapFit → Garmin Connect**：C706 可直连 Garmin Connect，自动同步
- **Garmin Connect → Strava**：Garmin 官方支持通过 `connect.garmin.com/modern/settings/connections` 授权同步到 Strava
- **优势**：完全绕开 Strava API 限制，免费 + 自动

⚠️ 但这条路有取舍：
- Garmin Connect 的 OAuth 也是有配额限制的（2024 年起免费账号 200 次/天，足够个人用）
- Garmin → Strava 同步延迟 5-15 分钟（不是实时的）
- 没有"自定义 HR 区间标题"（被 Garmin 替换成 "Cycling" 之类）

### 3. 只在本地记录，不上传 Strava

- FIT 文件留在 `~/.hermes/cache/documents/` + `~/strava-uploads/`
- 本地解析出距离 / HR / 区间 / 海拔 → 手动记录到训练日志
- **优势**：零成本、零依赖
- **缺点**：丧失 Strava 的社交 / 训练日历 / 路线图功能

## Agent 在此场景下的标准动作

用户发来新 FIT、运行上传脚本后看到 403 Inactive，**正确动作**：

1. **不要重试**——同请求会再 403
2. **不要 refresh token**——`/oauth/token` 返回 200 但后续调用仍 Inactive（refresh 拿到的新 token 也是非 Premium 用户的，等价）
3. **不要改 `strava-upload.py`**——脚本逻辑没问题
4. **一次性报告**：
   ```
   ⚠️ Strava 上传失败：Application Inactive (HTTP 403)
   原因：账号非 Strava Premium，2026-07 政策要求订阅才能用 API
   本地解析结果：
   - 距离 X km，时长 X h
   - 均速 X km/h，avg HR X bpm
   - 心率区间：Z? xxx 区
   - 起点→终点：X, Y
   FIT 文件保留在 ~/strava-uploads/，等你决定路径后再说。
   ```
5. **给三个选项**：订阅 / 走 Garmin / 跳过，让用户选

## 时间线记录

- **2026-05 ~ 2026-06**：Strava API 完全开放，所有上传正常
- **2026-06-30**：06-30 骑行成功上传，token 刷新正常（token 用 06-27 那批老的，仍有效）
- **2026-07-01**：Strava 后台可能开始调整（用户没察觉）
- **2026-07-02**：07-02 骑行首次撞到 403 Inactive，OAuth 仍能拿到 token 但 API 全 Inactive
- **2026-07-02（用户截图）**：Strava 官方公布政策：仅订阅 Premium 可用 API

## 经验教训

1. **平台政策的 vendor lock-in 风险**：依赖单一平台（Strava）的 API 是脆弱的，**MAGENE C706 数据建议本地保留 + 多平台备份**。
2. **403 Inactive 是账号级而非应用级**：重新 OAuth 没用，必须升级账号。
3. **token 拿到 ≠ API 可用**：OAuth 200 只代表授权流程通过，不代表账号有权调用 API。
4. **诊断优先级**：token 过期 → scope → 频率限制 → **账号订阅状态**。最后一步常被忽略。

## 相关链接

- 政策原文（截图）：用户已提供 https://www.strava.com/settings/api
- Premium 订阅页：https://www.strava.com/settings/premium
- Garmin Connect 同步设置：https://connect.garmin.com/modern/settings/connections
- MAGENE C706 → OnelapFit：见 `references/magene-c706-gcj02-offset.md` 末尾的星历同步说明