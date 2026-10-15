#!/bin/bash
# 推送前檢查：已追蹤／待提交的檔案裡不得出現私有路徑或真實標識。
# 用法：bash scripts/check_private.sh        （檢查暫存區與已追蹤檔案）
set -uo pipefail
cd "$(dirname "$0")/.."

FILES="$( (git ls-files; git diff --cached --name-only) | sort -u)"
BAD=0

echo "== 1. 禁止入庫的路徑 =="
echo "$FILES" | grep -E '^(worklog/[^_]|\.supervisor/|config/lark\.json$|tasks/T[0-9]|plans/[0-9]|PROJECT\.md$|notes/)' && BAD=1 || echo "OK"

echo "== 2. 真實標識與疑似密鑰 =="
# oc_/ou_/cli_ 後接 16 位以上英數＝真實 Lark 標識；示例檔裡用 x 佔位不會命中
PAT='\b(oc|ou)_[0-9a-f]{20,}\b|\bcli_[0-9a-z]{12,}\b|app_secret"?\s*[:=]\s*"[0-9A-Za-z]{20,}'
if [ -n "$FILES" ]; then
  echo "$FILES" | while read -r f; do [ -f "$f" ] && grep -nHE "$PAT" "$f"; done | tee /tmp/work-boss-private-hits.txt
  [ -s /tmp/work-boss-private-hits.txt ] && BAD=1 || echo "OK"
fi

if [ "$BAD" -ne 0 ]; then
  echo "檢查失敗：上面列出的內容不得入庫" >&2
  exit 1
fi
echo "檢查通過"
