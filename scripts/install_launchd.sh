#!/usr/bin/env bash
# macOS launchd로 파이프라인 스케줄을 설치/제거합니다.
# launchd는 사용자 세션에서 실행되므로 Keychain 접근이 가능합니다.
#
# 사용법:
#   bash scripts/install_launchd.sh         # 설치
#   bash scripts/install_launchd.sh remove  # 제거
#   bash scripts/install_launchd.sh status  # 상태 확인

set -e
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIPELINE="$PROJECT_ROOT/scripts/run_pipeline.sh"
LABEL="com.llm-trader.pipeline"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [ "$1" = "remove" ]; then
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    rm -f "$PLIST"
    echo "LaunchAgent removed."
    exit 0
fi

if [ "$1" = "status" ]; then
    if launchctl print "gui/$(id -u)/${LABEL}" 2>/dev/null; then
        echo ""
        echo "Status: LOADED"
    else
        echo "Status: NOT LOADED (run install_launchd.sh to install)"
    fi
    exit 0
fi

chmod +x "$PIPELINE"
mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$PROJECT_ROOT/logs"

# plist 생성 (04, 10, 16, 22시 실행)
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${PIPELINE}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PROJECT_ROOT}</string>

    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Hour</key><integer>22</integer><key>Minute</key><integer>0</integer></dict>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>${HOME}</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <key>StandardOutPath</key>
    <string>${PROJECT_ROOT}/logs/launchd_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${PROJECT_ROOT}/logs/launchd_stderr.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLISTEOF

# 기존 등록 제거 후 재등록
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "LaunchAgent installed:"
echo "  Plist: $PLIST"
echo "  Schedule: 04:00, 10:00, 16:00, 22:00"
echo "  Stdout: $PROJECT_ROOT/logs/launchd_stdout.log"
echo "  Stderr: $PROJECT_ROOT/logs/launchd_stderr.log"
echo ""
echo "To verify: bash scripts/install_launchd.sh status"
echo "To remove: bash scripts/install_launchd.sh remove"
