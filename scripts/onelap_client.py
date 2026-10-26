#!/usr/bin/env python3
"""迈金/顽鹿 (OneLap 中国版) API 客户端。

迈金中国版 App = 顽鹿运动，数据云端 = u.onelap.cn。

接口契约来自 moruoxian/SyncOnelapToXoss（逆向后整理）：
  - 登录:  POST https://www.onelap.cn/api/login  {account, password: md5hex(pw)}
  - 列表:  POST https://www.onelap.cn/api/otm/ride_record/list  {page, limit} + 签名头
  - 详情:  GET  https://www.onelap.cn/api/otm/ride_record/analysis/{record_id}
  - FIT:   GET  https://www.onelap.cn/api/otm/ride_record/analysis/fit_content/{base64(fit_key)}

签名: md5("&".join(f"{k}={v}" for k in sorted(params+nonce+timestamp)) + "&key=SIGN_KEY")
"""
import hashlib
import json
import os
import random
import string
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

WEB = "https://www.onelap.cn"
# 2026-10-26 修正：数据 API 的 host 从 u.onelap.cn 改成 www.onelap.cn。
#
# 为什么要改：两个 host 返回**不同的数据集**。
#   u.onelap.cn  /api/otm/ride_record/list  → total=450
#   www.onelap.cn /api/otm/ride_record/list  → total=452
# 差的那 2 条是「自由骑行」类活动（type=48 自由骑行 / type=15 快速燃脂），
# 也就是**最普通的那种日常骑行** —— 反而是用户最在意的类型。
# 2026-10-25 20:54 的「自由骑行」17.85km 就是这样漏掉的。
#
# 排查方法（下次再遇到「App 里有但云端拉不到」）：把 host 换着试，比对 total。
#   for h in ["https://www.onelap.cn","https://u.onelap.cn","https://otm.onelap.cn"]:
#       POST {h}/api/otm/ride_record/list {page:1,limit:5} → 看 pagination.total
# 三个 host 都有效（不是 404），但数据集不同 —— 这是最容易漏掉的坑。
APP = "https://www.onelap.cn"
LOGIN_API = f"{WEB}/api/login"
LIST_API = f"{APP}/api/otm/ride_record/list"
DETAIL_API = f"{APP}/api/otm/ride_record/analysis/{{record_id}}"
DOWNLOAD_API = f"{APP}/api/otm/ride_record/analysis/fit_content/{{fit_key}}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")

# ---- 配置（不硬编码任何密钥） ----------------------------------------
# 顽鹿签名密钥属于平台侧凭据，**不要提交到版本库**。
# 优先级：环境变量 ONELAP_SIGN_KEY → ~/.hermes/igp_sync/onelap_sign_key 文件。
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
IGP_DIR = HERMES_HOME / "igp_sync"
TOKEN_FILE = IGP_DIR / "onelap_token.json"
SIGN_KEY_FILE = IGP_DIR / "onelap_sign_key"


def _load_sign_key() -> str:
    """读取顽鹿签名密钥。缺失时抛错 —— 绝不用默认值兜底。"""
    key = os.environ.get("ONELAP_SIGN_KEY", "").strip()
    if key:
        return key
    if SIGN_KEY_FILE.exists():
        key = SIGN_KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    raise RuntimeError(
        "缺少顽鹿签名密钥。设置环境变量 ONELAP_SIGN_KEY，"
        f"或写入 {SIGN_KEY_FILE}（内容为密钥本身，权限 600）。"
    )


def sign_key() -> str:
    """惰性取签名密钥（每次调用读一次，方便测试时切换/注入）。"""
    return _load_sign_key()


def rand_nonce(length=16):
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def _flatten(params):
    out = {}
    for k, v in (params or {}).items():
        if isinstance(v, (list, dict)):
            out[k] = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        elif v is None or v == "":
            out[k] = None
        else:
            out[k] = v
    return out


def sign_headers(params=None):
    nonce = rand_nonce(16)
    timestamp = str(int(time.time()))
    allp = {**_flatten(params), "nonce": nonce, "timestamp": timestamp}
    parts = [f"{k}={allp[k]}" for k in sorted(allp) if allp[k] is not None]
    sign = hashlib.md5(("&".join(parts) + f"&key={sign_key()}").encode()).hexdigest()
    return {"nonce": nonce, "timestamp": timestamp, "sign": sign}


def _base_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Origin": APP,
        "Referer": f"{APP}/recordPage",
    })
    return s


def save_auth(token, cookies=None, uid=None, account=None):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(
        {"token": token, "cookies": cookies or {}, "uid": uid,
         "account": account, "saved_at": int(time.time())},
        ensure_ascii=False, indent=2))
    TOKEN_FILE.chmod(0o600)


def token_exp(token):
    """JWT 过期时间（epoch 秒），非 JWT 返回 None"""
    if not token or token.count(".") != 2:
        return None
    try:
        import base64
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("exp")
    except Exception:
        return None


def token_expired(token, margin=3600):
    """token 是否已过期或即将过期"""
    exp = token_exp(token)
    if exp is None:
        return None      # 未知
    return time.time() + margin > exp


def relogin(account=None, password=None):
    """用账号密码重新登录。返回 (ok, message)

    密码来源优先级：参数 > 环境变量 ONELAP_PASSWORD > 凭据文件
    """
    account = account or os.environ.get("ONELAP_ACCOUNT") or (load_auth() or {}).get("account")
    password = password or os.environ.get("ONELAP_PASSWORD")
    if not password:
        pf = TOKEN_FILE.parent / "onelap_password"
        if pf.exists():
            password = pf.read_text().strip()
    if not account or not password:
        return False, "缺少账号或密码（ONELAP_ACCOUNT / ONELAP_PASSWORD）"
    ok, tok, ck, uid, msg = login(account, password)
    if ok:
        return True, f"重新登录成功 uid={uid}"
    return False, f"重新登录失败: {msg}"


def load_auth():
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text())
    except Exception:
        return None


def login(account, password):
    """密码登录。返回 (ok, token, cookies, uid, message)"""
    pw_md5 = hashlib.md5(password.encode()).hexdigest()
    r = requests.post(LOGIN_API, headers={"User-Agent": UA, "Content-Type": "application/json"},
                      json={"account": account, "password": pw_md5}, timeout=30)
    try:
        data = r.json()
    except Exception:
        return False, None, None, None, f"HTTP {r.status_code}: {r.text[:200]}"
    if not isinstance(data, dict):
        return False, None, None, None, str(data)[:200]
    if data.get("code") not in (0, 200, "0", "200", None):
        return False, None, None, None, data.get("msg") or data.get("message") or str(data)[:200]
    d = data.get("data")
    if isinstance(d, list) and d:
        d = d[0]
    if not isinstance(d, dict):
        return False, None, None, None, f"无 data: {str(data)[:200]}"
    token = d.get("token")
    if not token:
        return False, None, None, None, f"无 token: {str(data)[:200]}"
    cookies = {c.name: c.value for c in r.cookies}
    uid = None
    ui = d.get("userinfo")
    if isinstance(ui, dict):
        uid = ui.get("uid")
    # OneLap 也用 cookie 形式
    cookies.setdefault("ouid", str(uid or ""))
    cookies.setdefault("XSRF-TOKEN", token)
    if d.get("refresh_token"):
        cookies.setdefault("OTOKEN", d["refresh_token"])
    save_auth(token, cookies, uid, account)
    return True, token, cookies, uid, "ok"


def build_session(auth):
    s = _base_session()
    if not auth:
        return s
    s.headers["Authorization"] = auth.get("token", "")
    s.cookies.update(auth.get("cookies") or {})
    return s


def list_rides(session, page=1, limit=20):
    """拉取骑行记录列表。返回 (items, ok, message)"""
    payload = {"page": page, "limit": limit}
    headers = sign_headers(payload)
    r = session.post(LIST_API, json=payload, headers=headers, timeout=30)
    try:
        data = r.json()
    except Exception:
        return [], False, f"HTTP {r.status_code}: {r.text[:200]}"
    if r.status_code != 200:
        return [], False, f"HTTP {r.status_code}: {str(data)[:200]}"
    pd = (data.get("data") or {}) if isinstance(data, dict) else {}
    return pd.get("list") or [], True, None


def fetch_detail(session, record_id):
    r = session.get(DETAIL_API.format(record_id=record_id), timeout=30)
    r.raise_for_status()
    return r.json()


def extract_fit_key(detail_data, record):
    """从详情/记录里提取 FIT key（优先 fitUrl/fit_url，其次 fit/fitKey/fileKey）。"""
    cands = []

    def add(v, pri):
        if v is None:
            return
        v = str(v).strip()
        if v:
            cands.append((pri, v))

    def walk(v):
        if isinstance(v, dict):
            for k, item in v.items():
                kl = str(k).lower()
                if kl in {"fiturl", "fit_url"}:
                    add(item, 0)
                elif kl in {"fit", "fitkey", "filekey", "file_key"}:
                    add(item, 1)
                elif kl in {"url", "path"}:
                    add(item, 2)
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(record)
    walk(detail_data)
    seen = set()
    for _, v in sorted(cands, key=lambda x: x[0]):
        if v not in seen:
            seen.add(v)
            return v
    return ""


def download_candidates(fit_url):
    out, seen = [], set()

    def add(v):
        if not v:
            return
        v = str(v).strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)

    add(fit_url)
    add(unquote(fit_url))
    if fit_url.startswith(("http://", "https://")):
        p = urlparse(fit_url)
        add(p.path)
        if p.path:
            add(p.path.rsplit("/", 1)[-1])
    elif "/" in fit_url:
        add(fit_url.rsplit("/", 1)[-1])
    return out


def download_fit(session, fit_url):
    """下载 FIT。

    注意两个关键点（照 SyncOnelapToXoss 的实现）：
      1. 方法是 **GET**（不是 POST）
      2. fit_key 必须 **base64 编码**后再拼进 URL
    """
    import base64
    last = None
    for cand in download_candidates(fit_url):
        key_b64 = base64.b64encode(cand.encode("utf-8")).decode("ascii")
        url = DOWNLOAD_API.format(fit_key=key_b64)
        try:
            r = session.get(url, timeout=90, stream=True)
            if r.status_code == 200:
                data = r.content
                if len(data) > 100:
                    return data, None
                last = f"HTTP 200 但内容过短 ({len(data)})"
            else:
                last = f"HTTP {r.status_code} len={len(r.content)}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    return None, last


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "login":
        ok, tok, ck, uid, msg = login(sys.argv[2], sys.argv[3])
        print(("✅" if ok else "❌"), f"uid={uid}", msg)
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        auth = load_auth()
        if not auth:
            print("未登录")
            sys.exit(1)
        s = build_session(auth)
        items, ok, msg = list_rides(s, 1, 20)
        print(f"ok={ok} count={len(items)} {msg or ''}")
        for it in items[:10]:
            print("  ", {k: it.get(k) for k in ("_id", "title", "activity_time", "distance", "fileKey") if k in it})
