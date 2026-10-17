#!/bin/bash
# 安裝 work-boss 的 5 個 launchd 定時任務（用戶級，不需要 sudo）。
# 用法：scripts/install_launchd.sh            安裝（已存在則覆蓋並重載）
#       scripts/install_launchd.sh --uninstall 卸載
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-/usr/bin/python3}"
LOG="$ROOT/.supervisor/logs"
AGENTS="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"
JOBS="morning evening chase close sync"

if [ "${1:-}" = "--uninstall" ]; then
  for job in $JOBS; do
    launchctl bootout "gui/$UID_N/com.workboss.$job" 2>/dev/null || true
    rm -f "$AGENTS/com.workboss.$job.plist"
    echo "已卸載 $job"
  done
  exit 0
fi

[ -f "$ROOT/config/lark.json" ] || { echo "缺少 config/lark.json（見 config/lark.example.json）" >&2; exit 1; }
[ -x "$PY" ] || { echo "找不到 Python：$PY（可用 PYTHON=/path/to/python3 覆蓋）" >&2; exit 1; }

mkdir -p "$LOG" "$AGENTS"

# 安裝前自測：launchd 背景環境能否讀取本倉庫。外接卷（/Volumes/...）常被 macOS 隱私保護擋住，
# 此時任務會在凌晨悄悄失敗，所以在這裡就攔下來。
T="$(mktemp -d)"
launchctl remove com.workboss.selftest 2>/dev/null || true
launchctl submit -l com.workboss.selftest -o "$T/o" -e "$T/e" -- "$PY" -X utf8 -I "$ROOT/scripts/supervisor.py" status
for _ in 1 2 3 4 5 6; do sleep 1; if [ -s "$T/o" ] || [ -s "$T/e" ]; then break; fi; done
launchctl remove com.workboss.selftest 2>/dev/null || true
if grep -q "Operation not permitted" "$T/e" 2>/dev/null || [ ! -s "$T/o" ]; then
  echo "自測失敗：launchd 背景環境讀不到 $ROOT" >&2
  cat "$T/e" 2>/dev/null >&2 || true
  echo "解決辦法二選一（見 docs/setup-macos.md）：" >&2
  echo "  A. 把倉庫放到家目錄（例如 ~/Code/work-boss）再安裝；" >&2
  echo "  B. 系統設定 → 隱私與安全性 → 完全磁碟存取權限，加入 $PY（真實路徑：$(readlink -f "$PY" 2>/dev/null || echo "$PY")）後重試。" >&2
  exit 2
fi
echo "自測通過：launchd 可讀取倉庫"

for job in $JOBS; do
  src="$ROOT/launchd/com.workboss.$job.plist.template"
  dst="$AGENTS/com.workboss.$job.plist"
  sed -e "s|__ROOT__|$ROOT|g" -e "s|__PYTHON__|$PY|g" -e "s|__LOG__|$LOG|g" "$src" > "$dst"
  plutil -lint "$dst" >/dev/null
  launchctl bootout "gui/$UID_N/com.workboss.$job" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_N" "$dst"
  echo "已安裝 $job → $dst"
done
echo "完成。查看：launchctl list | grep workboss；日誌：$LOG"
