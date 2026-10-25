#!/usr/bin/env python3
"""iGPSPORT (中国版) API 客户端。

参考 ridefitsync 的 lib/services/igp_service.dart（逆向自 app.zh.igpsport.com）。

上传流程（三步）：
  1. GET  /service/sportg/third-party-server/oss/getSignedUrl?fileExtension=.fit
       → {signedUrl, ossId}
  2. PUT  signedUrl  → 文件直传阿里云 OSS
  3. POST /service/web-gateway/web-analyze/activity/uploadByOss
       body: {fileName, ossName}

签名（WASM 逆向）：
  sign_str = method\npath(含query)\ntimestamp\nnonce\nsha256hex(body)
  signature = base64(HMAC-SHA256(key=<IGP_HMAC_KEY>, sign_str))
"""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path

import requests

BASE = "https://prod.zh.igpsport.com/service"
LOGIN_URL = f"{BASE}/auth/account/login"
SMS_URL = f"{BASE}/auth/account/SendVerificationCode"
SMS_LOGIN_URL = f"{BASE}/auth/account/login/phone"
ACT_BASE = f"{BASE}/web-gateway/web-analyze/activity"
LIST_URL = f"{ACT_BASE}/queryMyActivity"
DOWNLOAD_URL = f"{ACT_BASE}/getDownloadUrl/"
UPLOAD_URL = f"{ACT_BASE}/uploadByOss"
SIGNED_URL = f"{BASE}/sportg/third-party-server/oss/getSignedUrl?fileExtension=.fit"
PUBLIC_KEY_URL = f"{BASE}/edge-core/api/public/key"

APP_ID = "igpsport-web"
APP_VERSION = "8.07.08"

# ---- 配置（不硬编码任何密钥） ----------------------------------------
# IGPSPORT 的 HMAC 密钥属于平台侧凭据，**不要提交到版本库**。
# 优先级：环境变量 IGP_HMAC_KEY → ~/.hermes/igp_sync/hmac_key 文件。
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
IGP_DIR = HERMES_HOME / "igp_sync"
TOKEN_FILE = IGP_DIR / "token.json"
HMAC_KEY_FILE = IGP_DIR / "hmac_key"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")


def hmac_key() -> str:
    """读取 IGPSPORT HMAC 密钥。缺失时抛错 —— 绝不用默认值兜底。"""
    key = os.environ.get("IGP_HMAC_KEY", "").strip()
    if key:
        return key
    if HMAC_KEY_FILE.exists():
        key = HMAC_KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    raise RuntimeError(
        "缺少 IGPSPORT HMAC 密钥。设置环境变量 IGP_HMAC_KEY，"
        f"或写入 {HMAC_KEY_FILE}（内容为密钥本身，权限 600）。"
    )


def _sha256hex(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def generate_signature(method, path, timestamp, nonce, body):
    sign_str = f"{method}\n{path}\n{timestamp}\n{nonce}\n{_sha256hex(body)}"
    mac = hmac.new(hmac_key().encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def signed_headers(method, url, token=None, body=""):
    ts = str(int(time.time()))
    nonce = str(uuid.uuid4())
    from urllib.parse import urlparse
    u = urlparse(url)
    path = u.path + (f"?{u.query}" if u.query else "")
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-Hans",
        "User-Agent": UA,
        "timezone": "Asia/Shanghai",
        "qiwu-app-version": APP_VERSION,
        "x-access-key": "AKIDWebClient",
        "x-nonce": nonce,
        "x-platform": "web",
        "x-signature": generate_signature(method, path, ts, nonce, body),
        "x-timestamp": ts,
        "origin": "https://app.zh.igpsport.com",
        "referer": "https://app.zh.igpsport.com/",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _plain_headers(token=None):
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": UA,
        "origin": "https://app.zh.igpsport.com",
        "referer": "https://app.zh.igpsport.com/",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def send_sms_code(phone):
    """发送短信验证码。返回 (ok, message)"""
    r = requests.post(SMS_URL, headers=_plain_headers(),
                      json={"type": 1, "username": phone}, timeout=30)
    try:
        data = r.json()
    except Exception:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    code = data.get("code")
    ok = code in (0, 200, "0", "200")
    return ok, data.get("msg") or data.get("message") or f"code={code}"


def login_by_sms(phone, sms_code):
    """短信验证码登录。返回 (ok, token, nickname, message)"""
    body = json.dumps({"appId": APP_ID, "phone": phone, "code": sms_code})
    r = requests.post(SMS_LOGIN_URL, headers=signed_headers("POST", SMS_LOGIN_URL, body=body),
                      data=body, timeout=30)
    try:
        data = r.json()
    except Exception:
        return False, None, None, f"HTTP {r.status_code}: {r.text[:200]}"
    d = data.get("data") if isinstance(data, dict) else None
    if isinstance(d, dict) and d.get("access_token"):
        token = d["access_token"]
        save_token(token, phone, d.get("nickname"),
                   d.get("refresh_token"), d.get("expires_in"))
        return True, token, d.get("nickname"), "ok"
    return False, None, None, data.get("msg") or data.get("message") or str(data)[:200]


def save_token(token, phone=None, nickname=None, refresh_token=None, expires_in=None):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"access_token": token, "phone": phone, "nickname": nickname,
               "saved_at": int(time.time())}
    if refresh_token:
        payload["refresh_token"] = refresh_token
    if expires_in:
        payload["expires_in"] = int(expires_in)
    TOKEN_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    TOKEN_FILE.chmod(0o600)


def token_exp(token):
    """JWT 过期时间（epoch 秒），非 JWT 返回 None"""
    if not token or token.count(".") != 2:
        return None
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("exp")
    except Exception:
        return None


def token_expired(token, margin=3600):
    """token 是否已过期或即将过期。None = 未知"""
    exp = token_exp(token)
    if exp is None:
        return None
    return time.time() + margin > exp


def load_token():
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text()).get("access_token")
    except Exception:
        return None


def refresh_access_token():
    """用 refresh_token 换新的 access_token。

    端点: GET /service/auth/token/refresh?refreshToken=***
    参数名是驼峰 `refreshToken`（用 `refresh_token` 会报 Parameter is required）
    **必须带 Authorization: Bearer <access_token>** —— 无 Bearer 时服务端返回 HTML 错误页（非 JSON）。
    ⇒ 刷新必须在 access_token 仍有效时进行，不能等它彻底过期。
    refresh_token 每次刷新后会被轮换，必须保存新值，否则下次刷新 403。
    """
    if not TOKEN_FILE.exists():
        return False, "无 token 文件"
    try:
        data = json.loads(TOKEN_FILE.read_text())
    except Exception:
        return False, "token 文件损坏"
    rt = data.get("refresh_token")
    if not rt:
        return False, "无 refresh_token，需重新登录"
    old = data.get("access_token") or ""
    r = requests.get(f"{BASE}/auth/token/refresh",
                     headers={**_plain_headers(old)},
                     params={"refreshToken": rt}, timeout=30)
    try:
        j = r.json()
    except Exception:
        return False, f"HTTP {r.status_code}: 响应非 JSON（access_token 可能已彻底过期，需重新登录）"
    if r.status_code != 200 or j.get("code") != 0:
        return False, f"刷新失败 HTTP {r.status_code}: {j.get('message') or j}"
    nd = j.get("data") or {}
    if not nd.get("access_token"):
        return False, f"刷新响应无 access_token: {j}"
    data["access_token"] = nd["access_token"]
    data["saved_at"] = int(time.time())
    if nd.get("refresh_token"):
        data["refresh_token"] = nd["refresh_token"]
    TOKEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    TOKEN_FILE.chmod(0o600)
    return True, "刷新成功"


def ensure_token():
    """确保有可用 token：过期则自动刷新。返回 (ok, message)"""
    tok = load_token()
    if not tok:
        return False, "无 token，需重新登录"
    if token_expired(tok, margin=3600):
        ok, msg = refresh_access_token()
        if not ok:
            return False, f"token 过期且刷新失败（{msg}），需重新登录"
        return True, "token 已自动刷新"
    return True, "token 有效"


def upload_fit(fit_bytes, file_name, token=None):
    """上传 FIT 到 IGPSPORT（三步 OSS 流程）。返回 (ok, message)"""
    token = token or load_token()
    if not token:
        return False, "未登录（无 token）"

    # Step 1: 取 OSS 预签名 URL
    r = requests.get(SIGNED_URL, headers=signed_headers("GET", SIGNED_URL, token=token),
                     timeout=30)
    try:
        sd = r.json()
    except Exception:
        return False, f"getSignedUrl HTTP {r.status_code}: {r.text[:200]}"
    if sd.get("code") != 0:
        return False, f"getSignedUrl 失败: {sd.get('message') or sd}"
    d = sd.get("data") or {}
    oss_url = d.get("signedUrl") or d.get("url")
    oss_name = d.get("ossId") or d.get("key") or d.get("ossName")
    if not oss_url:
        return False, f"getSignedUrl 缺 OSS URL: {sd}"
    if not oss_name:
        oss_name = uuid.uuid4().hex + ".fit"

    # Step 2: PUT 到 OSS
    pr = requests.put(oss_url, data=fit_bytes, timeout=180)
    if pr.status_code != 200:
        return False, f"OSS 上传失败 HTTP {pr.status_code}: {pr.text[:200]}"

    # Step 3: 通知 iGPSPORT 注册
    body = json.dumps({"fileName": file_name, "ossName": oss_name})
    rr = requests.post(UPLOAD_URL,
                        headers=signed_headers("POST", UPLOAD_URL, token=token, body=body),
                        data=body, timeout=60)
    try:
        rd = rr.json()
    except Exception:
        return False, f"uploadByOss HTTP {rr.status_code}: {rr.text[:200]}"
    if rr.status_code == 200 and rd.get("code") == 0:
        return True, f"上传成功 ossName={oss_name} data={rd.get('data')}"
    return False, f"uploadByOss 失败: {rd.get('message') or rd}"


def list_activities(token=None, begin_time=None, page_size=20):
    """拉取 iGPSPORT 活动列表。begin_time: 'YYYY-MM-DD'"""
    token = token or load_token()
    if not token:
        return []
    out = []
    page = 1
    while True:
        params = {"pageNo": page, "pageSize": page_size, "sort": 1, "reqType": 0}
        if begin_time:
            params["beginTime"] = begin_time
        r = requests.get(LIST_URL, headers=_plain_headers(token), params=params, timeout=45)
        try:
            data = r.json()
        except Exception:
            break
        raw = (data.get("data") or {}) if isinstance(data, dict) else {}
        rows = raw.get("rows") or []
        out.extend(rows)
        if not rows or raw.get("totalPage", 0) <= page:
            break
        page += 1
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "sms":
        ok, msg = send_sms_code(sys.argv[2])
        print(("✅" if ok else "❌"), msg)
    elif len(sys.argv) > 2 and sys.argv[1] == "login":
        ok, token, nick, msg = login_by_sms(sys.argv[2], sys.argv[3])
        print(("✅" if ok else "❌"), nick or "", msg)
