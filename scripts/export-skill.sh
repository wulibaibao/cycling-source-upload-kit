#!/usr/bin/env bash
# 导出 cycling-data-upload skill 为可公开发布的 git 仓库。
#
# 关键安全保证：
#  1. 从 **干净副本** 打包，绝不碰原 skill 目录
#  2. 导出前跑密钥扫描 —— 命中即中止
#  3. 排除 __pycache__ / 凭据文件 / state / FIT 缓存
#
# 用法：
#   ./export-skill.sh                      # 打包到 /tmp/skill-export
#   ./export-skill.sh --push <repo-url>    # 打包并推到 GitHub（需 gh 已登录）
set -euo pipefail

SKILL_NAME="cycling-data-upload"
SRC="$HOME/.hermes/skills/$SKILL_NAME"
OUT="${OUT_DIR:-/tmp/skill-export}"
STAGE="$OUT/$SKILL_NAME"

[ -d "$SRC" ] || { echo "❌ 找不到 $SRC"; exit 1; }

echo "==> 复制到暂存区"
rm -rf "$OUT"
mkdir -p "$OUT"
cp -r "$SRC" "$STAGE"

echo "==> 排除非发布内容"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '*.pyc' -delete 2>/dev/null || true
rm -f "$STAGE"/.env "$STAGE"/*.token "$STAGE"/onelap_password \
      "$STAGE"/onelap_sign_key "$STAGE"/hmac_key 2>/dev/null || true
rm -rf "$STAGE"/fit "$STAGE"/logs 2>/dev/null || true

echo "==> 密钥扫描"
# 真值清单**从本机配置动态读取**（脚本自身不含任何真值，可安全公开）。
TRUTH_FILE="$OUT/.truth"
: > "$TRUTH_FILE"
{
  # 签名密钥（从本机密钥文件读）
  for kf in "$HOME/.hermes/igp_sync/onelap_sign_key" "$HOME/.hermes/igp_sync/hmac_key"; do
    [ -f "$kf" ] && cat "$kf" && echo
  done
  # Strava token 里的长串
  [ -f "$HOME/.hermes/strava_token.json" ] && python3 - "$HOME/.hermes/strava_token.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for k in ("access_token", "refresh_token", "client_secret"):
    if d.get(k) and len(str(d[k])) >= 8:
        print(d[k])
PY
  # IGPSPORT token
  [ -f "$HOME/.hermes/igp_sync/token.json" ] && python3 - "$HOME/.hermes/igp_sync/token.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for v in d.values():
    if isinstance(v, str) and len(v) >= 16:
        print(v)
PY
  # .env 里的 secret
  [ -f "$HOME/.hermes/.env" ] && grep -E '^(XINGZHE_CLIENT_SECRET|STRAVA_CLIENT_SECRET|ONELAP_SIGN_KEY|IGP_HMAC_KEY)=' \
    "$HOME/.hermes/.env" | cut -d= -f2- | tr -d '"'"'" || true
} >> "$TRUTH_FILE" 2>/dev/null || true

LEAK=0
while read -r val; do
  [ -z "$val" ] && continue
  [ "${#val}" -lt 8 ] && continue
  hits=$(grep -rFl --exclude=".truth" -- "$val" "$STAGE" 2>/dev/null || true)
  if [ -n "$hits" ]; then
    echo "❌ 泄露：$hits"
    LEAK=1
  fi
done < "$TRUTH_FILE"
rm -f "$TRUTH_FILE"

if [ "$LEAK" -ne 0 ]; then
  echo "中止：检测到密钥泄露，先脱敏再导出。"
  exit 1
fi
echo "    ✅ 无密钥泄露"

# 个人路径 / 项目名（排除检查脚本自身 —— 它必然含模式串）
# 模式用通用形式匹配任意 /home/<user>，不写死具体用户名
if grep -rqE --exclude="export-skill.sh" "/home/[a-zA-Z0-9._-]+/" "$STAGE" 2>/dev/null; then
  echo "❌ 仍含个人路径（/home/<user>/…）"; exit 1
fi
echo "    ✅ 无个人路径"

echo "==> 打包"
cd "$OUT"
tar -czf "${SKILL_NAME}-skill-$(date +%Y%m%d).tar.gz" "$SKILL_NAME/"
tar -tzf "${SKILL_NAME}-skill-$(date +%Y%m%d).tar.gz" | sed 's/^/    /'

echo
echo "✅ 导出完成：$OUT"
echo "   tarball: $OUT/${SKILL_NAME}-skill-$(date +%Y%m%d).tar.gz"

if [ "${1:-}" = "--push" ]; then
  REPO="${2:?用法: --push <repo-url>}"
  echo "==> 推送到 $REPO"
  cd "$STAGE"
  [ -d .git ] || git init -q
  git symbolic-ref HEAD refs/heads/main 2>/dev/null || true
  git add -A
  git -c user.email=hermes@localhost -c user.name=Hermes commit -qm "Export $SKILL_NAME skill"
  git remote remove origin 2>/dev/null || true
  git remote add origin "$REPO"
  git push -u origin main
  # 立即脱敏 remote URL 里的 token
  git remote set-url origin "${REPO}"
  echo "✅ 已推送"
fi
