#!/bin/bash
# 推送前检查：已追踪／待提交的文件里不得出现私有路径或真实标识。
# 用法：bash scripts/check_private.sh        （检查暂存区与已追踪文件）
set -uo pipefail
cd "$(dirname "$0")/.."

FILES="$( (git ls-files; git diff --cached --name-only) | sort -u)"
BAD=0

echo "== 1. 禁止入库的路径 =="
echo "$FILES" | grep -E '^(worklog/[^_]|\.supervisor/|config/lark\.json$|tasks/T[0-9]|plans/[0-9]|PROJECT\.md$|notes/|requirements/[^_]|docs/pool\.html$)' && BAD=1 || echo "OK"

echo "== 2. 真实标识与疑似密钥 =="
# oc_/ou_/cli_ 后接 16 位以上英数＝真实 Lark 标识；示例文件里用 x 占位不会命中
PAT='\b(oc|ou)_[0-9a-f]{20,}\b|\bcli_[0-9a-z]{12,}\b|app_secret"?\s*[:=]\s*"[0-9A-Za-z]{20,}'
if [ -n "$FILES" ]; then
  echo "$FILES" | while read -r f; do [ -f "$f" ] && grep -nHE "$PAT" "$f"; done | tee /tmp/work-boss-private-hits.txt
  [ -s /tmp/work-boss-private-hits.txt ] && BAD=1 || echo "OK"
fi

if [ "$BAD" -ne 0 ]; then
  echo "检查失败：上面列出的内容不得入库" >&2
  exit 1
fi
echo "检查通过"
