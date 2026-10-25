# 行者 (imxingzhe.com) API 调研记录

**适用**：要把骑行记录从 Strava / OnelapFit / 顽鹿 等推到行者账号，或读取行者运动记录。

**验证日期**：2026-10-03
**验证状态**：登录 + 列表接口已验证端到端；上传接口路径找到但**未验证入库**（见末尾"上传未闭环警告"）

## 登录（已验证）

行者登录用 **RSA 加密密码** + 自家 session cookie。

```python
import requests, re, base64
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

s = requests.Session()
# 1. GET 登录页拿公钥
r = s.get('https://www.imxingzhe.com/user/login', timeout=15)
pubkey = re.search(r'id="pubkey"[^>]*>([^<]+)<', r.text).group(1).strip()
rd = s.cookies.get('rd')  # 反爬 cookie

# 2. 用公钥加密密码（密码格式：<明文>;<rd_cookie>）
key = RSA.import_key(pubkey)
cipher = PKCS1_v1_5.new(key)
encrypted = base64.b64encode(
    cipher.encrypt(f'{plain_password};{rd}'.encode())
).decode()

# 3. POST 登录
r2 = s.post('https://www.imxingzhe.com/api/v4/account/login',
    json={'account': username, 'password': encrypted, 'source': 'web'},
    timeout=15)
assert r2.json()['res'] == 1, r2.json()
# 返回里 data.userid 是用户数字 ID（不要 enuid，那个是昵称 hash）
```

**关键陷阱（踩过）：**
- 公钥**不**在登录页 `<font face="Courier...">` 老式标签里——藏在 `<textarea id="pubkey" hidden>` 里。simple5dl/ride-sync 等老代码的正则 `face="Courier New..."` 已失效
- 行者登录后 cookie 是小写 `sessionid`（不是 `SessionId`），别走大写路径
- 必须同时拿到 `csrftoken` cookie 才能调非 GET 接口——`csrftoken` 是 Django 默认的，写入 request `X-CSRFToken` header
- 密码 RSA 加密时**`rd` cookie 要拼到明文密码后面**（用 `;` 分隔），不是单独的 salt 字段——单纯加密密码服务端不认

## 列活动（已验证）

```python
r3 = s.get('https://www.imxingzhe.com/api/v4/get_user_workouts?user_id=1257924',
           timeout=15)
data = r3.json()  # 直接返回 list[...]，无 wrapper
# 返回字段：workout_id / start_time / title / total_distance / total_elapsed_time / sport / upload_time
```

**关键陷阱（踩过）：**
- 参数是 `user_id=` 不是 `userid=` 或 `uid=`——`userid=1257924` 返回空 `[]`
- 没有 `userid` 或 `uid` query 参数时也返回 `[]`——列表必须带 user_id
- start_time / upload_time 是**毫秒时间戳**（不是秒），除以 1000 才是 Unix epoch
- 默认返回 10 条最新；分页参数 `page=` 不工作（实测多次返回 10 条）
- `get_activities` 这个接口返回的是**组队骑行活动**（骑行俱乐部/约骑），不是个人运动记录——别用错

## 上传（路径找到，但未验证入库）

```python
csrf = s.cookies.get('csrftoken')
files = {'upload_file_name': ('test.fit', open('/path/to/file.fit', 'rb'),
                              'application/octet-stream')}
data = {'title': '标题', 'device': 1, 'sport': 1}
headers = {'X-CSRFToken': csrf, 'Referer': 'https://www.imxingzhe.com/upload'}
r = s.post('https://www.imxingzhe.com/upload/fit', files=files, data=data,
           headers=headers, timeout=60)
```

**关键陷阱（踩过）**：
- `simple5dl/ride-sync` 等开源项目用 `/api/v4/upload_fits` 是错的——**现在路径是 `/upload/fit` 和 `/upload/gpx`**（前端 React Router 路径），没有 `/api/v4` 前缀
- 必须带 `X-CSRFToken` header——漏了返 403
- 不带 `Referer` 也可能 403（行者是 SPA，所有写操作都要 referer 校验）

### 上传未闭环警告（重要！）

**实测结果**（2026-10-03）：8 次不同 `device`（1-8）+ `sport`（1/2/3/9）组合 + GPX/FIT 上传，全部返回 **HTTP 200 + SPA index.html**——行者服务端**全部走的是 SPA fallback**，没有真正处理上传。`get_user_workouts` 列表里也**没出现新上传的活动**。

可能原因（**未独立验证**，别当结论用）：
1. `device` / `sport` 编码不在行者白名单里——服务端默默丢弃
2. 行者服务端有防刷限制，连续短时间大量 POST 触发风控
3. 上传走异步队列，UI 显示需要刷新到另一个入口（不是 `/users/{id}/workouts`）
4. SPA fetch 路径跟表单 POST 路径不同（前端用 XHR + FormData，可能带额外字段）

**正确判断"上传成功"的唯一手段**（不是看 HTTP 状态）：
```python
# 上传后等 5-10 秒，再查列表看是否多了那条
import time; time.sleep(8)
r = s.get(f'https://www.imxingzhe.com/api/v4/get_user_workouts?user_id={uid}')
newest = sorted(r.json(), key=lambda x: x['upload_time'], reverse=True)[0]
print(f'最新一条: {newest["title"]} upload_time={newest["upload_time"]}')
# 如果最新一条的 upload_time 是当前时间附近 → 上传成功
# 如果仍然是上次记录 → 失败，重新检查参数
```

## 同步到行者的推荐路径（避开上传未验证风险）

**优先级 0（2026-10-03 新增）**：用**官方 OpenAPI**——`POST /openapi/v1/uploads/` + Bearer token + multipart + JSON payload。OAuth2 走 `https://www.imxingzhe.com/oauth2/v2/authorize` + `POST /oauth2/v2/access_token/`。详见下一节「官方 OpenAPI」。

**优先级 1**：打开行者 App（手机）→ "运动" → "导入设备数据" → 直接选本地 FIT 文件——**行者 App 内部有验证过的上传通道**。

**优先级 2**：用浏览器打开 `https://www.imxingzhe.com/upload/fit` → DevTools 拦截真实 fetch/XHR 请求 → 看前端代码**真正发了什么**（包括隐藏字段、Header、Content-Type）。把前端实际请求复制到 Python 脚本里。

**优先级 3**：基于本 reference 的 `/upload/fit` POST 路径自己改参数（`device` 试 0-20 范围、文件加 `.fit.gz` 后缀、添加 `compression` 字段等）——本 reference **没有给出成功的字段组合**，需自行实验。

**不推荐**：在没验证上传闭环前，把脚本跑成 cron 自动同步——可能反复静默失败浪费 Strava API 配额和行者账号风控额度。

## 官方 OpenAPI（2026-10-03 发现，验证待跑通）

**端点**：`https://developer.imxingzhe.com/docs/openapi/uploads`

**两条独立接口**（不要混）：

1. **POST `/openapi/v1/uploads/`** —— 上传运动数据
2. **GET `/openapi/v1/uploads/`** —— 列出已上传历史（offset + limit）

### 上传 Payload 字段（官方文档明确）

| 字段 | 类型 | 含义 |
|---|---|---|
| `name` | string | 标题，≤32 字符 |
| `detail` | string | 详情，≤1500 字符 |
| `file_type` | string | `fit`（已支持）|
| `fit_filename` | string | 文件名（后缀要跟 file_type 一致）|
| `md5` | string | 文件 md5（32 位小写），服务端校验 |

### 成功响应（HTTP 201）

```json
{
  "code": 0, "msg": "上传成功",
  "data": {
    "new": true,
    "upload_id": 1,
    "workout_id": 9,
    "credits": 100.0,
    "uuid": "123a1ba3-70d9-4867-b680-326e93f67a8e"
  }
}
```

### OAuth2 流程（与登录态无关，纯 client credentials + authorization code）

**应用注册**：登录 `https://www.imxingzhe.com/home/#/settings/api` → 创建应用 → 拿到 `client_id` + `client_secret`。**回调域名**必须先在应用页配置（如 `https://yourdomain.com/api/callback`），OAuth 才会跳回。

**Step 1：浏览器跳授权 URL**
```
https://www.imxingzhe.com/oauth2/v2/authorize
  ?client_id=<client_id>
  &response_type=code
  &scope=read         # read=只读；write=允许上传
  &state=<random>     # 防 CSRF，单次使用
  &redirect_uri=<callback_url>
```
用户在行者授权页点「同意授权」→ 跳回 `<callback_url>?code=xxx&state=yyy`。

**Step 2：换 access_token**
```python
import requests
r = requests.post('https://www.imxingzhe.com/oauth2/v2/access_token/',
    data={
        'client_id': '<client_id>',
        'client_secret': '<client_secret>',
        'code': '<code>',
        'grant_type': 'authorization_code',
    }, timeout=15)
# multipart/form-data（不是 JSON！）—— 官方文档要求
token = r.json()  # access_token + refresh_token + expires_at + scope
```

**Step 3：上传 FIT**
```python
import hashlib
fit_bytes = open('/path/to/file.fit', 'rb').read()
md5 = hashlib.md5(fit_bytes).hexdigest()
r = requests.post(
    'https://www.imxingzhe.com/openapi/v1/uploads/',
    data={
        'name': '骑行标题',
        'detail': '',
        'file_type': 'fit',
        'fit_filename': 'file.fit',
        'md5': md5,
    },
    files={'file': ('file.fit', fit_bytes, 'application/octet-stream')},
    headers={'Authorization': f'Bearer {token["access_token"]}'},
    timeout=60,
)
# 期望 HTTP 201 + JSON {"code":0, "msg":"上传成功", ...}
```

**Step 4：refresh_token 续期**
```python
r = requests.post('https://www.imxingzhe.com/oauth2/v2/access_token/',
    data={
        'client_id': '<client_id>',
        'client_secret': '<client_secret>',
        'grant_type': 'refresh_token',
        'refresh_token': token['refresh_token'],
    }, timeout=15)
```

### 速率限制（官方文档）

- 每 15 分钟 3000 次
- 每天 30000 次

（远高于个人使用需求——一天多次骑行都不会撞限制）

### 与「登录后 POST /upload/fit」路径对比

| 维度 | `/upload/fit`（登录态） | `/openapi/v1/uploads/`（OAuth）|
|---|---|---|
| 鉴权 | sessionid cookie + CSRF | Bearer token |
| 响应 | HTTP 200 + SPA HTML（silent fallback）| HTTP 201 + JSON |
| 数据是否真入库 | ❌ 实测未入库 | 文档承诺入库（待实测验证）|
| 应用要求 | 登录账号即可 | 需在 `/home/#/settings/api` 创建应用 + 配回调域名 |
| 速率限制 | 未知（防刷策略）| 3000/15min, 30000/day |
| 适用场景 | 行者 App 网页手工操作 | 自动化同步脚本 |

**重要：10-03 实测 `/upload/fit` 静默失败 ≠ `/openapi/v1/uploads/` 也失败**——两条路服务端实现可能完全独立。**OAuth + OpenAPI 是有官方文档背书的路径**，理论上更靠谱。**但 OAuth 流程必须用户在浏览器点「同意授权」一次**，脚本无法绕过。