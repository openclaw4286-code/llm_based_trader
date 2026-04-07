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

SESSION_ID=$(date +%Y%m%d_%H | awk -F_ '{
    h=$2;
    if (h<6) sh="00"; else if (h<12) sh="06"; else if (h<18) sh="12"; else sh="18";
    print $1"_"sh
}')

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
if command -v claude &> /dev/null; then
    # Claude Code CLI를 비대화식 모드로 실행
    # --print: 응답을 stdout으로 (대화창 없이)
    # --output-format text: 순수 텍스트만
    cat "$MASTER_PROMPT" | claude --print --output-format text > "$RESPONSE_FILE" 2>&1 || {
        echo "[STEP 4] Claude CLI failed" | tee -a "$PIPELINE_LOG"
        exit 1
    }
    echo "Response saved to $RESPONSE_FILE" | tee -a "$PIPELINE_LOG"
else
    echo "[ERROR] 'claude' command not found. Install Claude Code CLI." | tee -a "$PIPELINE_LOG"
    exit 1
fi

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
