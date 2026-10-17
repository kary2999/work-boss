#!/bin/bash
# 安装 work-boss 的 5 个 launchd 定时任务（用户级，不需要 sudo）。
# 用法：scripts/install_launchd.sh            安装（已存在则覆盖并重载）
#       scripts/install_launchd.sh --uninstall 卸载
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
    echo "已卸载 $job"
  done
  exit 0
fi

[ -f "$ROOT/config/lark.json" ] || { echo "缺少 config/lark.json（见 config/lark.example.json）" >&2; exit 1; }
[ -x "$PY" ] || { echo "找不到 Python：$PY（可用 PYTHON=/path/to/python3 覆盖）" >&2; exit 1; }

mkdir -p "$LOG" "$AGENTS"

# 安装前自测：launchd 背景环境能否读取本仓库。外接卷（/Volumes/...）常被 macOS 隐私保护挡住，
# 此时任务会在凌晨悄悄失败，所以在这里就拦下来。
T="$(mktemp -d)"
launchctl remove com.workboss.selftest 2>/dev/null || true
launchctl submit -l com.workboss.selftest -o "$T/o" -e "$T/e" -- "$PY" -X utf8 -I "$ROOT/scripts/supervisor.py" status
for _ in 1 2 3 4 5 6; do sleep 1; if [ -s "$T/o" ] || [ -s "$T/e" ]; then break; fi; done
launchctl remove com.workboss.selftest 2>/dev/null || true
if grep -q "Operation not permitted" "$T/e" 2>/dev/null || [ ! -s "$T/o" ]; then
  echo "自测失败：launchd 背景环境读不到 $ROOT" >&2
  cat "$T/e" 2>/dev/null >&2 || true
  echo "解决办法二选一（见 docs/setup-macos.md）：" >&2
  echo "  A. 把仓库放到家目录（例如 ~/Code/work-boss）再安装；" >&2
  echo "  B. 系统设置 → 隐私与安全性 → 完全磁盘访问权限，加入 $PY（真实路径：$(readlink -f "$PY" 2>/dev/null || echo "$PY")）后重试。" >&2
  exit 2
fi
echo "自测通过：launchd 可读取仓库"

for job in $JOBS; do
  src="$ROOT/launchd/com.workboss.$job.plist.template"
  dst="$AGENTS/com.workboss.$job.plist"
  sed -e "s|__ROOT__|$ROOT|g" -e "s|__PYTHON__|$PY|g" -e "s|__LOG__|$LOG|g" "$src" > "$dst"
  plutil -lint "$dst" >/dev/null
  launchctl bootout "gui/$UID_N/com.workboss.$job" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_N" "$dst"
  echo "已安装 $job → $dst"
done
echo "完成。查看：launchctl list | grep workboss；日志：$LOG"
