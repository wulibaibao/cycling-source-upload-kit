# 凭据配置说明

本 skill **不包含任何密钥**。所有凭据从环境变量或本地文件读取。

## 一览

| 变量 / 文件 | 用途 | 必需 | 来源 |
|---|---|---|---|
| `ONELAP_SIGN_KEY` 或 `~/.hermes/igp_sync/onelap_sign_key` | 顽鹿/迈金 API 签名密钥 | 链路 B 必需 | 顽鹿 App 逆向获取 |
| `IGP_HMAC_KEY` 或 `~/.hermes/igp_sync/hmac_key` | IGPSPORT 请求签名密钥 | 链路 B 必需 | IGPSPORT Web 端逆向获取 |
| `ONELAP_PASSWORD` 或 `~/.hermes/igp_sync/onelap_password` | 顽鹿账号密码 | 自动重登需要 | 你的账号 |
| `STRAVA_CLIENT_ID` | Strava Application client_id | 链路 A/C 建议改 | https://www.strava.com/settings/api |
| `XINGZHE_CLIENT_ID` / `XINGZHE_CLIENT_SECRET` | 行者 OpenAPI 凭据 | 链路 A 必需 | 行者开放平台 |
| `~/.hermes/strava_token.json` | Strava OAuth token（含 client_secret） | 链路 A/C 必需 | OAuth 流程获取 |
| `~/.hermes/xingzhe_sync/token.json` | 行者 OAuth token | 链路 A 必需 | OAuth 流程获取 |
| `~/.hermes/igp_sync/token.json` | IGPSPORT access/refresh token | 链路 B 必需 | 短信登录获取 |
| `~/.hermes/igp_sync/onelap_token.json` | 顽鹿 JWT | 链路 B 必需 | 密码登录获取 |

**优先级：环境变量 > 本地文件。** 两者都没有时脚本**报错退出**，不会用默认值兜底。

## 准备

```bash
# 1. 建目录
mkdir -p ~/.hermes/igp_sync && chmod 700 ~/.hermes/igp_sync

# 2. 签名密钥（内容为密钥本身，无引号无换行）
echo -n '<你的签名密钥>' > ~/.hermes/igp_sync/onelap_sign_key
echo -n '<你的 HMAC 密钥>' > ~/.hermes/igp_sync/hmac_key
chmod 600 ~/.hermes/igp_sync/onelap_sign_key ~/.hermes/igp_sync/hmac_key

# 3. 顽鹿账号密码（用于 token 过期自动重登）
echo -n '<你的顽鹿密码>' > ~/.hermes/igp_sync/onelap_password
chmod 600 ~/.hermes/igp_sync/onelap_password

# 4. 行者 OpenAPI 凭据 → 写进 ~/.hermes/.env
#    XINGZHE_CLIENT_ID=...
#    XINGZHE_CLIENT_SECRET=...

# 5. Strava Application → 注册自己的，client_id 写进 ~/.hermes/.env
#    STRAVA_CLIENT_ID=...
```

## 环境变量方式（适合 systemd / Docker）

```bash
export ONELAP_SIGN_KEY='...'
export IGP_HMAC_KEY='...'
```

写进 `~/.hermes/.env` 也可以 —— 脚本会读 `.env`（如果项目已有 dotenv 加载逻辑）。

## 安全注意

- **绝不要**把 `onelap_sign_key` / `hmac_key` / `onelap_password` / `*.json` token 文件提交到 git
- 密钥文件权限必须 `600`，目录 `700`
- 这些密钥是**平台侧凭据**，泄露后他人可操作你的账号 —— 视为密码对待
- 怀疑泄露时：立刻改密码 / 重新登录，旧 token 全部作废

## 自检

```bash
# 检查密钥能否被正确读取（不打印明文）
python3 -c "
import sys; sys.path.insert(0,'~/.hermes/skills/cycling-data-upload/scripts')
import onelap_client as ol, igp_client as ig
print('onelap key len:', len(ol.sign_key()))
print('igp hmac len:', len(ig.hmac_key()))
"
```

## 导出到 GitHub（公开仓库）

```bash
~/.hermes/skills/cycling-data-upload/scripts/export-skill.sh
```

脚本会：复制到暂存区 → 排除 `__pycache__`/凭据/state → **扫描真值泄露**（真值从本机配置动态读取，命中即中止）→ 检查个人路径 → 打包 tar.gz。

加 `--push <repo-url>` 可直接推 GitHub（推送后自动把 token 从 remote URL 里脱敏）。

**发布前务必确认**：本 skill 已不含任何密钥、任何个人路径、任何项目名 —— 扫描会替你把关。
