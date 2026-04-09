#!/usr/bin/env bash
# 04시, 10시, 16시, 22시에 run_pipeline.sh를 실행하는 cron 항목을 설치합니다.
#
# 사용법:
#   bash scripts/install_cron.sh         # 설치
#   bash scripts/install_cron.sh remove  # 제거

set -e
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIPELINE="$PROJECT_ROOT/scripts/run_pipeline.sh"
MARKER="# llm_based_trader pipeline"

if [ "$1" = "remove" ]; then
    crontab -l 2>/dev/null | grep -v "$MARKER" | crontab -
    echo "Cron entries removed."
    exit 0
fi

chmod +x "$PIPELINE"

# 기존 항목 제거 후 재설치
EXISTING=$(crontab -l 2>/dev/null | grep -v "$MARKER" || true)

NEW_ENTRY="0 4,10,16,22 * * * cd $PROJECT_ROOT && bash $PIPELINE >> $PROJECT_ROOT/logs/cron.log 2>&1 $MARKER"

(echo "$EXISTING"; echo "$NEW_ENTRY") | crontab -

echo "Cron installed:"
crontab -l | grep "$MARKER"
echo ""
echo "Pipeline will run at 04:00, 10:00, 16:00, 22:00 (system local time)."
echo "Logs: $PROJECT_ROOT/logs/cron.log"
