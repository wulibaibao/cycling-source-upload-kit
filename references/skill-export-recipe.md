---
name: skill-export-recipe
description: 把 Hermes skill 打包成 tar.gz + 多种投递通道的全流程 recipe（2026-06-01 验证）
---

# Skill 打包导出 Recipe

## 核心步骤

1. **复制到临时目录**（避免污染原 skill 目录）
   ```bash
   rm -rf /tmp/skill-export/<name>
   cp -r ~/.hermes/skills/<category>/<name> /tmp/skill-export/
   ```
2. **`chmod -R a+rX`**（原文件可能是 600 owner-only，解压后别人/自己换个用户读不了）
3. **加 README.md**：5 分钟入门 + 目录树 + 阅读顺序 + License
4. **tar -czf 打包**（**不是 zip**——Linux server 默认没装 zip）
   ```bash
   cd /tmp/skill-export && tar -czf <name>-skill-<date>.tar.gz <name>/
   ```
5. **验证**：`tar -tzf <archive>.tar.gz` 列文件清单
6. **投递**（按优先级回退链）

## 投递回退链

### 1️⃣ GitHub 公开/私有 repo（**最推荐**）
- 用户给 fine-grained PAT（repo scope）
- `curl POST /user/repos` 建仓库
- `git init` → commit → `git push`（token inline 在 remote URL）
- **推送完立刻 `git remote set-url origin https://github.com/...` 脱敏**
- 永久可下载 + 可版本管理

### 2️⃣ 邮件附件（**`send_message` 不可用**！用 `python3 smtplib`）
**Hermes 邮件双通道陷阱**（常踩）：
- ❌ `send_message(target="email", ...)` → 走 `os.getenv("EMAIL_*")` → **hermes gateway 进程没这些 env var** → 失败
- ❌ `send_message(target="home")` → "Unknown platform: home"（home 不是平台名）
- ❌ `send_message(target="qqbot")` 无 `QQBOT_HOME_CHANNEL` → "No home channel set"
- ❌ `send_message(target="weixin")` → iLink `rate limited: ret=-2`（5-15 分钟恢复）
- ✅ **`python3 smtplib` 直连**（读 `config.yaml` 顶层 `EMAIL_*`）：用 STARTTLS + login + send_message
- ✅ `cronjob.create(deliver="email", ...)` → 走 `EmailAdapter` 用 config.yaml → 可用

**smtplib 模板**（163 邮箱，25 端口）：
```python
import os
import smtplib, yaml
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

cfg = yaml.safe_load(open(os.path.expanduser('~/config.yaml')))
addr = cfg['EMAIL_ADDRESS']
pwd = cfg['EMAIL_PASSWORD']
msg = EmailMessage()
msg['From'] = addr
msg['To'] = addr  # 自发自收（注意 163 可能误判为垃圾）
msg['Subject'] = '[Hermes] ...'
msg['Date'] = formatdate(localtime=True)
msg.set_content('...')
with open(attach_path, 'rb') as f:
    msg.add_attachment(f.read(), maintype='application', subtype='gzip', filename='...')
with smtplib.SMTP(cfg['EMAIL_SMTP_HOST'], int(cfg['EMAIL_SMTP_PORT']), timeout=30) as s:
    s.ehlo()
    if s.has_extn('STARTTLS'):
        s.starttls()
        s.ehlo()
    s.login(addr, pwd)
    s.send_message(msg)
```

**注意**：自发自收可能被 163 反垃圾拦截，建议**收到后查"广告/垃圾邮件"目录**。

### 3️⃣ IM 文件传输（限流重试）
- `send_message(target="weixin")` 限流时等 5-15 分钟重试
- `send_message(target="qqbot")` 需先 `hermes config set QQBOT_HOME_CHANNEL <id>`

### 4️⃣ 本地路径 + 用户自取
- 保存到 `~/strava-uploads/` 之类固定位置
- 文字告诉用户路径 + scp 命令提示

## 验证命令

```bash
# 列出 tar 内容
tar -tzf <archive>.tar.gz

# 对比原目录树（**用 ls 不用 find**——find 会被隐藏文件误报）
ls /tmp/skill-export/<name>/references/ | sort
ls ~/skills/<category>/<name>/references/ | sort
```

## 已踩的坑

- ❌ `zip -r` → `zip: command not found`（用 tar.gz 替代）
- ❌ base64 内嵌大文件到消息 → QQ/微信会被截断（>20KB 不可行）
- ❌ `git init -b main` 在某些 git 版本静默失败（先 `git init` + `git symbolic-ref HEAD refs/heads/main`）
- ❌ `find ... -type f` 在 `cp -r` 残留时多报文件（用 `ls | sort` 对比最准）
- ❌ `chmod` 没改 → tar 解压后非 owner 用户读不了（`permission denied`）
- ❌ token 留在 `git remote -v` → 任何人 `cat .git/config` 都能偷走（**用完必 `set-url` 脱敏**）
