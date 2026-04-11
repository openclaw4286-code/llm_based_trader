#!/usr/bin/env bash
# =============================================================
# 메인 파이프라인 - launchd가 04:30/09:30/13:30/16:30/21:30에 호출합니다.
#
# 단계:
#   1. step1_fetch_data.py     : CoinGecko 시총 상위 20종목 + 5년치 일봉 다운로드
#   2. step2_ict_charts.py     : ICT 분석 + 차트 PNG 생성 + ICT 요약 JSON 저장
#   3. step3_per_coin.py       : 종목별로 Claude 호출 → 파싱 → 포지션사이징 → 주문
#                                (뉴스 RSS + 경제 캘린더 + 차트 이미지 모두 사용)
#
# 각 단계는 독립 실행 가능하며 멱등성을 갖습니다.
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

# ─── STEP 3: Per-Coin 분석 + 즉시 주문 ────────────────────
# 각 종목에 대해: 프롬프트 생성 → Claude 호출 → 파싱 → 포지션 사이징 → 주문
# 한 종목씩 순차 처리, 분석 직후 즉시 주문 (대기 없음)
echo "[STEP 3] Per-coin analysis + order (20 coins sequentially)..." | tee -a "$PIPELINE_LOG"

# claude 명령어 경로를 환경변수로 전달
CLAUDE_PATH=$(command -v claude 2>/dev/null || true)
if [ -z "$CLAUDE_PATH" ]; then
    echo "[ERROR] 'claude' command not found in PATH=$PATH" | tee -a "$PIPELINE_LOG"
    exit 1
fi
export CLAUDE_PATH
echo "Using claude at: $CLAUDE_PATH" | tee -a "$PIPELINE_LOG"

$PYTHON scripts/step3_per_coin.py 2>&1 | tee -a "$PIPELINE_LOG" || {
    echo "[STEP 3] FAILED" | tee -a "$PIPELINE_LOG"
    exit 1
}

echo "==========================================" | tee -a "$PIPELINE_LOG"
echo "Pipeline complete: $(date)" | tee -a "$PIPELINE_LOG"
echo "==========================================" | tee -a "$PIPELINE_LOG"
