#!/usr/bin/env bash
# run_all.sh — 串行跑完所有学校，日志同时写到终端和文件
# 用法（在 SuperviScore/ 或 main/ 任意位置执行均可）:
#   bash main/scripts/run_all.sh            # 跑所有未完成的学校
#   bash main/scripts/run_all.sh --list     # 仅列出状态，不执行
#   bash main/scripts/run_all.sh sjtu_cs    # 强制单跑某学校

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MAIN_DIR="$(dirname "$SCRIPT_DIR")"
REPO_DIR="$(dirname "$MAIN_DIR")"

LOG_DIR="$REPO_DIR/运行日志"
mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/run_all_$(date +'%Y%m%d_%H%M%S').log"

echo "========================================================"
echo "  SuperviScore 全校串行执行脚本"
echo "  工作目录: $MAIN_DIR"
echo "  日志文件: $LOGFILE"
echo "  开始时间: $(date +'%Y-%m-%d %H:%M:%S')"
echo "========================================================"

# 切换到 main/ 目录，保证 config.py / schools/ 的相对路径可用
cd "$MAIN_DIR"

# 用 tee 同时打印到终端和日志文件（UTF-8 友好）
python scripts/run_all.py "$@" 2>&1 | tee "$LOGFILE"

EXIT_CODE=${PIPESTATUS[0]}
echo ""
echo "========================================================"
echo "  结束时间: $(date +'%Y-%m-%d %H:%M:%S')"
echo "  日志已保存: $LOGFILE"
echo "========================================================"
exit $EXIT_CODE
