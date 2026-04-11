#!/usr/bin/env bash
# =============================================================
# 메인 파이프라인 - 6시간마다 cron에서 호출됩니다.
#
# 단계:
#   1. step1: Gate.io에서 인기종목 + 5년치 캔들 다운로드
#   2. step2: ICT 분석 + 차트 PNG 생성 + ICT 요약 JSON 저장
#   3. step3 --manual: Claude Code용 master_prompt.txt 생성
#   4. claude -p: Claude Code CLI 호출 → 분석 결과 텍스트
#   5. parse_claude_response.py: 응답을 JSON으로 저장 + 포지션 사이징
#   6. step4: Gate.io에 실제 주문
#
# 각 단계는 독립 실행 가능하며 멱등성을 갖습니다.
# 한 단계가 실패해도 다음 단계가 별도로 실행 가능합니다.
# =============================================================
set -e  # 에러 발생 시 즉시 중단

# cron 환경 보정: PATH, HOME 설정
# cron은 최소 PATH만 가지므로 Homebrew/npm 경로 추가
# cron은 HOME을 안 줄 수 있어서 claude가 ~/.claude/ 설정을 못 찾음
export HOME="${HOME:-/Users/jimin}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.npm-global/bin:$PATH"

# 프로젝트 루트 (이 스크립트의 부모 디렉토리)
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# .env 로드
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Python 경로 (venv가 있으면 우선 사용)
if [ -f "$PROJECT_ROOT/venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/venv/bin/python"
else
    PYTHON="python3"
fi

# 로그 디렉토리
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

SESSION_ID=$(python3 -c "
from datetime import datetime, timedelta
SCHEDULE=[4,9,13,16,21]
now=datetime.now()
h=now.hour
cand=[x for x in SCHEDULE if x<=h]
if cand:
    print(now.strftime('%Y%m%d_')+f'{max(cand):02d}')
else:
    prev=now-timedelta(days=1)
    print(prev.strftime('%Y%m%d_')+f'{SCHEDULE[-1]:02d}')
")

PIPELINE_LOG="$LOG_DIR/pipeline_${SESSION_ID}.log"
echo "==========================================" | tee -a "$PIPELINE_LOG"
echo "Pipeline run: $(date)" | tee -a "$PIPELINE_LOG"
echo "Session ID: $SESSION_ID" | tee -a "$PIPELINE_LOG"
echo "==========================================" | tee -a "$PIPELINE_LOG"

# ─── STEP 1: 데이터 수집 ─────────────────────────────────
echo "[STEP 1] Fetching market data..." | tee -a "$PIPELINE_LOG"
$PYTHON scripts/step1_fetch_data.py 2>&1 | tee -a "$PIPELINE_LOG" || {
    echo "[STEP 1] FAILED" | tee -a "$PIPELINE_LOG"
    exit 1
}

# ─── STEP 2: ICT 분석 + 차트 ─────────────────────────────
echo "[STEP 2] Running ICT analysis and generating charts..." | tee -a "$PIPELINE_LOG"
$PYTHON scripts/step2_ict_charts.py 2>&1 | tee -a "$PIPELINE_LOG" || {
    echo "[STEP 2] FAILED" | tee -a "$PIPELINE_LOG"
    exit 1
}

# ─── STEP 3: Claude Code용 프롬프트 생성 ─────────────────
echo "[STEP 3] Generating Claude prompts..." | tee -a "$PIPELINE_LOG"
$PYTHON scripts/step3_claude_analysis.py --manual 2>&1 | tee -a "$PIPELINE_LOG" || {
    echo "[STEP 3] FAILED" | tee -a "$PIPELINE_LOG"
    exit 1
}

MASTER_PROMPT="$PROJECT_ROOT/data/analysis/prompts/${SESSION_ID}_master_prompt.txt"
RESPONSE_FILE="$PROJECT_ROOT/data/analysis/prompts/${SESSION_ID}_response.txt"

if [ ! -f "$MASTER_PROMPT" ]; then
    echo "[ERROR] Master prompt not found: $MASTER_PROMPT" | tee -a "$PIPELINE_LOG"
    exit 1
fi

# ─── STEP 4: Claude Code CLI 호출 ────────────────────────
echo "[STEP 4] Calling Claude Code (this may take a few minutes)..." | tee -a "$PIPELINE_LOG"

# claude 명령어 경로 확인
CLAUDE_PATH=$(command -v claude 2>/dev/null || true)
if [ -z "$CLAUDE_PATH" ]; then
    echo "[ERROR] 'claude' command not found in PATH=$PATH" | tee -a "$PIPELINE_LOG"
    echo "[ERROR] HOME=$HOME" | tee -a "$PIPELINE_LOG"
    exit 1
fi
echo "Using claude at: $CLAUDE_PATH" | tee -a "$PIPELINE_LOG"

# Claude CLI 실행 (stderr도 별도 캡처, set +e로 감싸서 에러 시 로깅 가능)
CLAUDE_ERR="$PROJECT_ROOT/data/analysis/prompts/${SESSION_ID}_claude_error.log"
set +e
cat "$MASTER_PROMPT" | "$CLAUDE_PATH" --print --dangerously-skip-permissions --model claude-opus-4-6 --output-format text > "$RESPONSE_FILE" 2>"$CLAUDE_ERR"
CLAUDE_EXIT=$?
set -e

if [ $CLAUDE_EXIT -ne 0 ]; then
    echo "[STEP 4] Claude CLI failed (exit code $CLAUDE_EXIT)" | tee -a "$PIPELINE_LOG"
    echo "[STEP 4] Error details:" | tee -a "$PIPELINE_LOG"
    cat "$CLAUDE_ERR" | tee -a "$PIPELINE_LOG"
    # response 파일에도 에러 내용이 있을 수 있음
    if [ -s "$RESPONSE_FILE" ]; then
        echo "[STEP 4] Response file content:" | tee -a "$PIPELINE_LOG"
        head -20 "$RESPONSE_FILE" | tee -a "$PIPELINE_LOG"
    fi
    exit 1
fi

# 응답 파일이 비어있거나 너무 작으면 실패 처리
RESPONSE_SIZE=$(wc -c < "$RESPONSE_FILE" | tr -d ' ')
if [ "$RESPONSE_SIZE" -lt 100 ]; then
    echo "[STEP 4] Response too small (${RESPONSE_SIZE} bytes), likely auth error" | tee -a "$PIPELINE_LOG"
    echo "Response content:" | tee -a "$PIPELINE_LOG"
    cat "$RESPONSE_FILE" | tee -a "$PIPELINE_LOG"
    if [ -s "$CLAUDE_ERR" ]; then
        echo "Error log:" | tee -a "$PIPELINE_LOG"
        cat "$CLAUDE_ERR" | tee -a "$PIPELINE_LOG"
    fi
    exit 1
fi

echo "Response saved to $RESPONSE_FILE (${RESPONSE_SIZE} bytes)" | tee -a "$PIPELINE_LOG"

# ─── STEP 5: 응답 파싱 + 포지션 사이징 ───────────────────
echo "[STEP 5] Parsing Claude response..." | tee -a "$PIPELINE_LOG"
$PYTHON scripts/parse_claude_response.py "$RESPONSE_FILE" 2>&1 | tee -a "$PIPELINE_LOG" || {
    echo "[STEP 5] FAILED" | tee -a "$PIPELINE_LOG"
    exit 1
}

# ─── STEP 6: 주문 실행 ───────────────────────────────────
echo "[STEP 6] Executing orders..." | tee -a "$PIPELINE_LOG"
$PYTHON scripts/step4_execute_orders.py 2>&1 | tee -a "$PIPELINE_LOG" || {
    echo "[STEP 6] FAILED" | tee -a "$PIPELINE_LOG"
    exit 1
}

echo "==========================================" | tee -a "$PIPELINE_LOG"
echo "Pipeline complete: $(date)" | tee -a "$PIPELINE_LOG"
echo "==========================================" | tee -a "$PIPELINE_LOG"
