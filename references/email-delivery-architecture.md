# Hermes 邮件发送双通道（2026-06-01 调研）

## 结论

只有 **cron 通道**能可靠发送邮件。`send_message` 工具的 email 平台在 hermes gateway 进程下不可用（环境变量缺失）。

## send_message 通道（不工作）

```python
# hermes_tools/send_message.py 内部实现
os.getenv("EMAIL_ADDRESS")
os.getenv("EMAIL_PASSWORD")
os.getenv("EMAIL_SMTP_HOST")
# ...
```

hermes gateway 启动时**不读取** config.yaml 顶层 `email.*` 字段写入环境变量。  
实测 `os.getenv("EMAIL_ADDRESS")` 在 gateway 进程下返回 `None` → 报 `SMTP AUTH extension not supported` 或直接抛 `KeyError`。

## cron 通道（工作）

```python
# hermes/gateway/platforms/email.py
class EmailAdapter:
    def __init__(self, config):  # config = top-level config["email"]
        self.address = config["address"]       # EMAIL_ADDRESS
        self.password = config["password"]     # EMAIL_PASSWORD
        self.smtp_host = config["smtp_host"]   # EMAIL_SMTP_HOST
        # ...
```

cron 任务调度时，agent 会**显式注入** config.yaml 顶层的 `email.*` 到 EmailAdapter 构造函数。  
所以 `deliver: email` 的 cron job 走这条路径 → 实际可送达用户邮箱（<your-email@example.com>）。

## 实际部署

每日 06:00 博客调研任务：
- `cronjob list` → 找到 `name=daily-blog-research`
- 它的 `deliver` 字段是 `email`
- prompt 让 agent 汇总 → 直接渲染成 HTML 邮件 → 走 EmailAdapter

用户说"你不是每天能发邮件给 <your-email@example.com> 吗"指的就是这个机制。  
**不是 send_message 工具的 email target**——那个在我们的 gateway 配置下走不通。

## 调试技巧

```bash
# 检查 cron 通道邮件是否发出来
grep -i "EmailAdapter\|SMTP\|email" ~/.hermes/logs/gateway.log | tail -50

# 检查 send_message 通道（已知失败但日志能看）
grep "send_message.*email\|EMAIL_ADDRESS" ~/.hermes/logs/gateway.log | tail -20

# 验证环境变量（gateway 进程下）
ps aux | grep hermes-gateway | head -1
cat /proc/<pid>/environ | tr '\0' '\n' | grep -i email
# 大概率空——证实 os.getenv 路径不通
```

## 给用户的答复模板

> 邮件发送走的是 cron 调度通道（每日博客任务），不是 send_message 工具。  
> send_message 的 email target 在当前 gateway 进程下缺环境变量（只 config.yaml 顶层有，gateway 不注入到 env），所以单次 send_message 邮件会失败。  
> 可行方案：
> 1. 改 config.yaml `email.from` 加上你 QQ 邮箱 → 我创建一个一次性 cron job → 走 EmailAdapter 发送
> 2. 让我把文件存到 `~/strava-uploads/` + 文字路径 + scp 命令
> 3. 重启 gateway 注入环境变量（但这是治标不治本——每次重启就丢）
