# LLM-Based Crypto Trading Agent

Gate.io 선물 자동매매 에이전트. ICT 차트 기반 기술적 분석 + Claude Code의 거시적/정량적 분석으로 6시간마다 롱/숏/스킵을 결정합니다.

## 시스템 개요

```
6시간마다 (0, 6, 12, 18시):
  step1   Gate.io 인기 20종목 + 5년치 일봉 다운로드
  step2   ICT 분석 + 차트 PNG 저장
  step3   Claude Code용 프롬프트 생성
  Claude  로컬 Claude Code CLI 호출 → 종목별 점수/판단
  parse   응답 → JSON 저장 + Kelly Criterion 포지션 사이징
  step4   Gate.io 선물 주문 실행
```

각 단계는 **독립 실행 가능 + 멱등**입니다. 중간에 실패해도 재실행하면 이어서 진행됩니다.

## 점수 체계

- **기술적 분석** (-25 ~ +25): ICT 차트 기반 (BOS/CHoCH, OB, FVG, Liquidity, Premium/Discount, OTE)
- **거시적/정량적** (-25 ~ +25): 뉴스, SNS, 화이트페이퍼, 토크노믹스, 스캠 탐지
- **합산 점수**:
  - `≥ +10` → **롱**
  - `≤ -10` → **숏**
  - 그 외 → **스킵**
- **스캠 강제 스킵**: `scam_score ≤ -15` 이면 점수와 무관하게 스킵

## 안전 장치

- LLM 실수 차단: SL/TP가 진입가 반대편에 있으면 거부
- 슬리피지 차단: 분석↔실행 가격 차 > 3% 거부
- 중복 주문 방지: 세션별 처리 기록
- 좀비 주문 정리: 매 세션마다 미체결/SL/TP 자동 취소

## 효율성 파라미터 (`config.yaml`의 `efficiency_level`)

| 값 | 동작 |
|---|---|
| 0 | 모든 종목 풀 분석 (최대 비용) |
| 1-3 | 이전 스캠 탐지 종목 자동 스킵 |
| 4-6 | 거래량 하위 종목 점진 스킵, 분석 깊이 standard로 |
| 7-9 | 가격 변동 < N% 종목 스킵, quick 분석 |
| 10 | 상위 5종목만 풀 분석 (최대 효율) |

## 설치

### 1. 프로젝트 다운로드

```bash
git clone <repo-url> ~/llm_based_trader
cd ~/llm_based_trader
```

### 2. Python 가상환경 + 의존성

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. API 키 설정

`.env.example`을 `.env`로 복사하고 키를 입력:

```bash
cp .env.example .env
nano .env
```

```
GATEIO_API_KEY=your_gateio_api_key
GATEIO_API_SECRET=your_gateio_api_secret
```

> Gate.io API 키 만들기: Gate.io → 계정 → API 관리 → 새 키 → **선물 거래 권한** 필수, IP 화이트리스트 권장

### 4. Claude Code CLI 설치

이 프로젝트는 **Claude Code CLI**를 로컬에서 호출합니다 (Max 요금제로 비용 효율적).

설치 후 `claude --version` 으로 확인. 한 번 로그인하면 cron에서도 동작합니다.

### 5. 설정 조정

`config.yaml`에서 트레이딩 파라미터 조정:

```yaml
efficiency_level: 5          # 0(풀분석) ~ 10(최대효율)
trading:
  top_n_coins: 20            # 분석 종목 수
  long_threshold: 10         # 롱 진입 임계 점수
  short_threshold: -10
  leverage: 5
position_sizing:
  kelly_fraction: 0.25       # 보수적 Kelly (0.25배)
  max_position_pct: 5.0      # 단일 종목 최대 5%
  min_cash_reserve_pct: 30.0 # 최소 현금 30% 보유
  max_total_exposure_pct: 60.0
```

## 실행

### 수동 실행 (테스트)

```bash
# 전체 파이프라인 1회 실행
bash scripts/run_pipeline.sh

# 또는 단계별로
python scripts/step1_fetch_data.py        # 데이터 수집
python scripts/step2_ict_charts.py        # ICT 분석 + 차트
python scripts/step3_claude_analysis.py --manual  # 프롬프트 생성

# Claude Code CLI 수동 호출 (또는 셸 스크립트가 자동으로)
cat data/analysis/prompts/<session>_master_prompt.txt | claude --print > response.txt
python scripts/parse_claude_response.py response.txt

# 주문 실행 (실제)
python scripts/step4_execute_orders.py

# 시뮬레이션만
python scripts/step4_execute_orders.py --dry-run

# 상태 조회
python scripts/step4_execute_orders.py --status
```

### 자동 실행 (cron 설치)

```bash
bash scripts/install_cron.sh
```

설치되면 0시, 6시, 12시, 18시(시스템 로컬 시간)에 자동 실행됩니다.

```bash
# 제거
bash scripts/install_cron.sh remove

# 로그 확인
tail -f logs/cron.log
tail -f logs/pipeline_*.log
```

## 비상 종료

```bash
# 모든 포지션 즉시 청산 + 미체결 주문 취소
python scripts/step4_execute_orders.py --close-all
```

## 디렉토리 구조

```
llm_based_trader/
├── config.yaml             # 모든 트레이딩 설정
├── .env                    # API 키 (gitignore)
├── src/                    # 핵심 모듈
│   ├── gateio_client.py    # Gate.io API
│   ├── ict_analysis.py     # ICT 분석 엔진
│   ├── ict_chart.py        # 차트 시각화 (교체 가능)
│   ├── claude_analyzer.py  # LLM 호출 + 응답 파싱
│   ├── order_executor.py   # 주문 실행
│   ├── order_validator.py  # LLM 실수 방어
│   ├── position_sizing.py  # Kelly Criterion
│   └── efficiency.py       # 효율성 파라미터 적용
├── scripts/                # 파이프라인 진입점
│   ├── step1_fetch_data.py
│   ├── step2_ict_charts.py
│   ├── step3_claude_analysis.py
│   ├── step4_execute_orders.py
│   ├── parse_claude_response.py
│   ├── run_pipeline.sh     # 전체 자동화
│   └── install_cron.sh
├── data/
│   ├── candles/            # 5년치 일봉 CSV
│   ├── analysis/           # 세션별 ICT/분석 JSON
│   │   └── prompts/        # Claude 프롬프트/응답
│   └── orders/             # 주문 기록 + processed_sessions.json
├── charts/                 # ICT 차트 PNG
└── logs/                   # 실행 로그
```

## ICT 차트 모듈 교체

나중에 직접 작성한 ICT 차트 스크립트로 교체하려면 `src/ict_chart.py`만 바꾸면 됩니다. 인터페이스:

```python
def generate_ict_chart(df, ict_result, symbol, output_path=None) -> Path:
    ...
```

## 면책

이 프로젝트는 학습/연구 목적입니다. 실제 자금 사용 시 발생하는 손실은 사용자 책임입니다. 반드시 작은 금액으로 충분히 테스트한 후 사용하세요.
