# LLM-Based Crypto Trading Agent

Gate.io USDT 무기한 선물 자동매매 에이전트.

매 세션마다:
1. CoinGecko 시가총액 상위 + 상품 선물(금/은) 20개 선정
2. 각 종목을 Claude Code CLI(Opus 4.6)로 분석 — ICT 차트 이미지 + 뉴스 + 경제 캘린더 참고
3. ICT 구조 기반 SL/TP 자동 계산 (멀티 TP)
4. Kelly Criterion + 동적 레버리지로 즉시 주문

로컬 Mac에서 `launchd`가 **매일 5회** (04:30, 09:30, 13:30, 16:30, 21:30 KST, ICT Kill Zone 기반) 전체 파이프라인을 자동 실행합니다.

---

## 파이프라인 전체 흐름

```
[step1] CoinGecko 시총 상위 18개 + 금/은 2개 = 20종목 선정
        Gate.io에서 각 종목의 5년치 일봉 다운로드
           ↓
[step2] 각 종목 ICT 분석 → charts/{symbol}.png + data/analysis/{symbol}_ict.json
           ↓
[step3] 종목별 순차 처리 (20회 반복):
          ┌─ 프롬프트 생성 (ICT 요약 + 뉴스 + 경제 캘린더 + 차트 경로)
          ├─ Claude Code CLI 1회 호출
          ├─ 응답 파싱 → 종목 분석 JSON 저장
          ├─ Kelly Criterion 포지션 사이징
          ├─ 동적 레버리지 계산
          ├─ ICT 기반 SL/TP 계산 (R:R 검증)
          └─ Gate.io 주문 실행 (진입/유지/청산/반전)
```

각 단계는 **독립 실행 + 멱등**입니다. 중간에 실패해도 재실행 가능.

---

## 1. 종목 선정 — `src/top_coins.py`

1. **CoinGecko API**에서 시가총액 상위 100개 크립토 조회 (순환공급 × 가격 = 진짜 시총)
2. Gate.io USDT 선물에 존재하는 종목만 필터
3. 스테이블코인(USDC, DAI 등)과 래핑 토큰(WBTC 등) 제외
4. **상품 선물**(`XAU_USDT` 금, `XAG_USDT` 은)은 항상 포함
5. 최종: 크립토 18개 + 상품 2개 = 20개

CoinGecko API 장애 시 거래량 기준 폴백.

---

## 2. ICT 분석 + 차트 — `src/ict_analysis.py`, `src/ict_chart.py`

각 종목의 5년치 일봉으로:

- **Market Structure**: BOS (Break of Structure) / CHoCH (Change of Character)
- **Order Blocks** (OB): Bullish / Bearish, 미티게이션 여부
- **Fair Value Gaps** (FVG): 충전 여부
- **Liquidity Levels**: Buy/Sell-side, 스윕 여부
- **Premium/Discount Zone**: 최근 50일 스윙 범위 기준 피보나치
- **OTE Zone**: 0.618~0.786 리트레이스먼트

결과:
- `charts/{symbol}.png` — 다크 테마 캔들스틱 + ICT 오버레이 (최근 120일 표시)
- `data/analysis/{symbol}_ict.json` — 텍스트 요약 (OB/FVG 리스트, 현재가 위치 등)

> 파일명에 세션 ID 없음 — **매 세션마다 덮어씀**. Claude가 항상 최신 파일 하나만 읽습니다.

---

## 3. Claude 분석 — `scripts/step3_per_coin.py`

### 세션당 20회 Claude CLI 호출

종목 하나당 개별 프롬프트를 만들어 `claude --print --model claude-opus-4-6`으로 stdin 파이프 호출.

### 프롬프트에 포함되는 것 (4가지)

1. **ICT 차트 이미지 경로** — Claude가 `Read` 도구로 직접 이미지 시각 분석
2. **ICT 요약 텍스트** — 숫자로 정확한 OB/FVG/Liquidity 좌표
3. **최근 24시간 뉴스** — `src/news_fetcher.py`가 5개 RSS 피드(CoinDesk, Cointelegraph, Decrypt, Bitcoin Magazine, CryptoNews)에서 수집, 종목 키워드 매칭
4. **경제 캘린더** — `src/economic_calendar.py`가 Forex Factory 미러(`faireconomy.media`)에서 고임팩트 이벤트(FOMC/CPI/NFP/GDP/ECB) 수집, 지난 24시간 발표 결과 + 앞으로 72시간 예정

### Claude의 출력 (-10 ~ +10)

```json
{
  "technical_score": <int -10 to 10>,
  "technical_reasoning": "...",
  "macro_quant_score": <int -10 to 10>,
  "macro_quant_reasoning": "...",
  "total_score": <int>,
  "decision": "long" | "short" | "skip",
  "confidence": <float 0.0 to 1.0>
}
```

`decision`은 참고용이고 **최종 결정은 Python이 config threshold로 판단**합니다.

---

## 4. 진입 판단 — `src/claude_analyzer.py:_normalize_result`

```python
total_score = technical_score + macro_quant_score   # 범위: -20 ~ +20

if total_score >= config.trading.long_threshold:
    decision = "long"
elif total_score <= config.trading.short_threshold:
    decision = "short"
else:
    decision = "skip"
```

기본값: `long_threshold=3`, `short_threshold=-8` (숏은 더 보수적).

---

## 5. 포지션 사이징 — `scripts/step3_per_coin.py:compute_position_pct`

**Kelly Criterion** 기반:

```
win_rate = 0.5 + (confidence - 0.5) * 0.3          # 0.35 ~ 0.65
win_loss_ratio = 1.0 + abs(total_score) / 20 * 1.5 # 1.0 ~ 2.5
f* = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio
position_pct = f* * kelly_fraction * 100           # kelly_fraction=1.0 (현재)
position_pct = clamp(position_pct, min_position_pct, max_position_pct)
```

### 전체 노출 한도

각 종목 진입마다 `running_exposure`를 누적 추적. `max_total_exposure_pct` (기본 60%) 초과 시 나머지 여유분만 배정하거나 스킵.

---

## 6. 동적 레버리지 — `src/order_executor.py:compute_dynamic_leverage`

점수 절대값에 따라 **선형 보간**으로 레버리지 자동 결정.

현재 `config.yaml` 설정 기준:

| 방향 | 점수 | 레버리지 |
|---|---|---|
| long | +3 (임계값) | 5x |
| long | +10 | 7.1x |
| long | +20 (최대) | 10x |
| short | -8 (임계값) | 1x |
| short | -14 | 3x |
| short | -20 (최대) | 5x |

**숏은 최소값이 작은 이유**: 크립토는 상승 급등 위험이 커서 보수적으로.

범위 밖 점수는 경계값으로 클램핑.

---

## 7. SL/TP 계산 — `src/risk_reward.py`

**Claude가 SL/TP를 정하지 않습니다.** 대신 ICT 실제 구조에서 계산 → 고빈도 트레이딩용 타이트 제약 적용.

### SL 결정 (`calculate_sl`)

1. **롱**: 현재가 아래에서 가장 가까운 [Bullish OB 하단 / Swing Low] 탐색
2. **숏**: 현재가 위에서 가장 가까운 [Bearish OB 상단 / Swing High] 탐색
3. **거리 필터**: 현재가 ± `max_level_distance_pct` (기본 3.5%) 이내 레벨만 후보
4. **버퍼**: 찾은 레벨에서 `sl_buffer_pct` (기본 0.1%) 여유
5. **폴백**: 후보 없으면 `sl_fallback_pct` (기본 1.5%)
6. **최대 클램핑**: SL이 `max_sl_pct` (기본 2.0%) 초과 시 강제 잘림

### TP 결정 (`calculate_tp_targets`) — 멀티 타겟

포지션을 **50% / 30% / 20%**로 분할 청산:

| 단계 | 포지션 비율 | 타겟 타입 |
|---|---|---|
| TP1 | 50% | 가장 가까운 Liquidity Pool |
| TP2 | 30% | 가장 가까운 미충전 FVG |
| TP3 | 20% | 가장 가까운 반대편 OB |

모두 `max_level_distance_pct` 이내 + `max_tp_pct` (기본 6.0%) 상한.

### 폴백 (레벨 없을 때)

```
TP1 = 현재가 ± (SL 거리 × min_rr_ratio)        # 예: 2.5x
TP2 = 현재가 ± (SL 거리 × (min_rr_ratio + 0.3))
TP3 = 현재가 ± (SL 거리 × (min_rr_ratio + 0.6))
```

→ **R:R 자동 보장**.

### R:R 검증 (`check_rr_ratio`)

`TP1 거리 / SL 거리 >= min_rr_ratio` (기본 **2.5:1**).
미달 시 **진입 거부** (`rr_rejected`).

---

## 8. 한 종목 처리 — `src/order_executor.py:_process_single_coin`

모든 경우의 수:

| 기존 포지션 | Claude 결정 | 동작 |
|---|---|---|
| 없음 | skip | **미체결 주문 정리**만 |
| 없음 | long/short | 미체결 정리 → 검증 → **신규 진입** |
| 같은 방향 | 같은 방향 | **HOLD** (불필요한 재거래 방지) |
| 반대 방향 | 반대 방향 | 미체결 정리 → 기존 **청산** → 신규 **반전 진입** |
| 있음 | skip | 미체결 정리 → **청산** |
| 있음 | 분석 실패 | **HOLD** (안전) |

### 신규 진입 프로세스 (검증 11단계)

```
1. 분석 JSON 무결성 검증 (점수↔decision 일치, position_pct 한도)
2. 계약 정보 조회 (Gate.io: quanto_multiplier, order_price_round, order_size_min)
3. 현재 시장가 조회
4. 슬리피지 검증: |분석 시점 가격 - 현재가| > 3% → 거부
5. 포지션 금액 계산: balance × position_pct%
6. 계약 수량 계산: (금액 × 레버리지) / (가격 × quanto_multiplier) → 정수
   - 수량 < min_size → 잔고 부족으로 스킵
7. ICT 기반 SL/TP 계산 (tick size 반올림, Decimal 정밀도)
8. R:R 검증 (≥ min_rr_ratio)
9. SL/TP 방향 검증 (롱: SL<진입<TP, 숏: 반대)
10. 잔고 충분성 검증
11. 좀비 미체결 주문 정리
   ↓
Gate.io 레버리지 설정 → 시장가 IOC 주문 → SL 트리거 → 멀티 TP 트리거
```

---

## 9. 실행 스케줄 — `scripts/install_launchd.sh`

macOS **launchd**로 매일 5회 자동 실행 (사용자 로그인 세션, Keychain 접근 가능):

| KST | ICT Kill Zone |
|---|---|
| 04:30 | NY Lunch End (NY 오후 세션 재개) |
| 09:30 | Asian Open |
| 13:30 | Asian Lunch End (도쿄 점심 후 재개) |
| 16:30 | London Open |
| 21:30 | NY Open + London Lunch End |

> cron도 가능(`install_cron.sh` 파일은 제거됨)하지만 macOS cron은 Keychain 접근 불가 → `claude` 인증 실패. **반드시 launchd 사용**.

---

## 10. 설정 — `config.yaml`

```yaml
trading:
  top_n_coins: 20                  # 분석 종목 수
  long_threshold: 3                # total_score >= 3 → 롱
  short_threshold: -8              # total_score <= -8 → 숏
  leverage: 5                      # 폴백 (동적 레버리지 비활성 시)
  margin_mode: isolated
  dynamic_leverage:
    enabled: true
    long:
      min_score: 3                 # +3 → leverage 5
      max_score: 20                # +20 → leverage 10
      min_leverage: 5
      max_leverage: 10
    short:
      min_score: -8                # -8 → leverage 1
      max_score: -20               # -20 → leverage 5
      min_leverage: 1
      max_leverage: 5

position_sizing:
  kelly_fraction: 1.0              # 풀 켈리 (1.0). 보수적으로 0.25도 가능
  max_position_pct: 5.0            # 단일 종목 최대 잔고 5%
  min_position_pct: 0.5
  min_cash_reserve_pct: 30.0       # 최소 현금 30% 보유
  max_total_exposure_pct: 60.0     # 전체 노출 한도 60%

scoring:
  technical_min: -10
  technical_max: 10
  macro_quant_min: -10
  macro_quant_max: 10

risk_reward:
  min_rr_ratio: 2.5                # 최소 R:R 2.5:1
  sl_buffer_pct: 0.1               # OB/Swing에서 추가 버퍼
  max_level_distance_pct: 3.5      # ICT 레벨 거리 필터 (3.5% 이내만)
  max_sl_pct: 2.0                  # SL 최대 거리 클램핑
  max_tp_pct: 6.0                  # TP 최대 거리 클램핑
  sl_fallback_pct: 1.5             # 레벨 없을 때 SL 폴백
  tp_fallback_pct: 4.0             # 레벨 없을 때 TP 폴백
  tp_targets:
    - {pct: 50, target: liquidity}
    - {pct: 30, target: fvg}
    - {pct: 20, target: opposite_ob}

schedule:
  timezone: Asia/Seoul
  run_hours: [4, 9, 13, 16, 21]
  run_minute: 30
```

---

## 파일 구조

```
llm_based_trader/
├── config.yaml                # 모든 트레이딩 설정
├── .env                       # API 키 (gitignore)
├── requirements.txt
├── README.md
│
├── src/                       # 핵심 모듈
│   ├── utils.py               # config 로더 + 로거 + 세션/파일 관리 (통합)
│   ├── gateio_client.py       # Gate.io API v4 클라이언트
│   ├── top_coins.py           # CoinGecko 시총 + 상품 선물 선정
│   ├── candle_fetcher.py      # 5년치 일봉 다운로드
│   ├── ict_analysis.py        # ICT 분석 엔진 (교체 가능)
│   ├── ict_chart.py           # 차트 시각화 (교체 가능)
│   ├── news_fetcher.py        # RSS 뉴스 수집
│   ├── economic_calendar.py   # Forex Factory 경제 캘린더
│   ├── prompts.py             # Claude 프롬프트 템플릿
│   ├── claude_analyzer.py     # Claude 응답 파싱/정규화
│   ├── risk_reward.py         # ICT 기반 SL/TP 계산
│   ├── position_sizing.py     # Kelly Criterion
│   ├── order_validator.py     # 주문 전 검증 (슬리피지/방향/잔고)
│   └── order_executor.py      # 주문 실행 + 포지션 관리 + 동적 레버리지
│
├── scripts/                   # 진입점
│   ├── run_pipeline.sh        # launchd가 호출하는 메인 셸
│   ├── step1_fetch_data.py    # 종목 선정 + 캔들 다운로드
│   ├── step2_ict_charts.py    # ICT 분석 + 차트 생성
│   ├── step3_per_coin.py      # 종목별 Claude 분석 + 즉시 주문
│   ├── step4_execute_orders.py # 수동 주문/상태/비상청산 유틸
│   ├── install_launchd.sh     # macOS 자동화 설치
│   ├── setup_keys.sh          # API 키 대화식 입력
│   └── show_state.py          # Gate.io 상태 조회
│
├── data/
│   ├── candles/               # 5년치 일봉 CSV
│   ├── analysis/              # ICT JSON + Claude 분석 JSON
│   │   └── prompts/           # 프롬프트 + 응답 파일 (디버그용)
│   └── orders/                # 세션별 주문 기록 + processed_sessions.json
├── charts/                    # ICT 차트 PNG (심볼당 1개, 덮어씀)
└── logs/                      # 실행 로그
```

---

## 설치 및 실행

### 1. 저장소 클론 + Python 환경

```bash
cd ~
git clone <repo-url> llm_based_trader
cd llm_based_trader
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Gate.io API 키

```bash
bash scripts/setup_keys.sh
```

> Gate.io → 계정 → API 관리 → **Perpetual Futures Read + Trade 권한** 필수

### 3. Claude Code CLI

로컬에 설치 + 로그인 (`claude` 명령이 `/opt/homebrew/bin/claude`에 있어야 함). 이 프로젝트는 Claude Max 구독 기반으로 CLI subprocess를 호출합니다.

### 4. 테스트 실행 (수동)

```bash
source venv/bin/activate
bash scripts/run_pipeline.sh
```

5~15분 소요 (20종목 순차 처리). 각 종목마다 Claude 호출 → 주문.

### 5. 자동화 설치

```bash
bash scripts/install_launchd.sh
bash scripts/install_launchd.sh status  # 확인
```

이제 04:30, 09:30, 13:30, 16:30, 21:30 KST에 자동 실행됩니다.

> **Mac 슬립 방지 필수**: 시스템 설정 → 배터리 → "전원 어댑터 연결 시 자동 잠자기 방지" 체크. 또는 `caffeinate -d -i -s &`.

### 6. 자동화 제거

```bash
bash scripts/install_launchd.sh remove
```

---

## 유틸리티 명령어

```bash
# Gate.io 현재 상태 (잔고, 포지션, 미체결, SL/TP 트리거)
python scripts/show_state.py

# 현재 세션 분석/포지션 요약
python scripts/step4_execute_orders.py --status

# 주문 없이 시뮬레이션
python scripts/step4_execute_orders.py --dry-run

# 비상: 모든 포지션 청산 + 미체결/트리거 전량 취소
python scripts/step4_execute_orders.py --close-all

# 같은 세션 수동 재실행 (중복 방지 해제)
rm -f data/orders/processed_sessions.json
bash scripts/run_pipeline.sh
```

---

## 안전장치 요약

| 검증 | 위치 | 차단 대상 |
|---|---|---|
| **점수 일관성** | `order_validator.validate_analysis` | 점수 ↔ decision 불일치, position_pct 한도 |
| **SL/TP 방향** | `order_validator.validate_order_prices` | 롱 SL≥진입 / TP≤진입 같은 LLM 실수 |
| **슬리피지** | `order_validator.validate_slippage` | 분석↔실행 가격차 > 3% |
| **잔고** | `order_validator.validate_balance` | 마진 부족 |
| **R:R 최소** | `risk_reward.check_rr_ratio` | TP1/SL < min_rr_ratio (2.5:1) |
| **Tick size 정렬** | `order_executor._round_to_tick` | Gate.io `order_price_round` 배수로 Decimal 반올림 |
| **수량 최소** | `order_executor` | 계산된 수량 < `order_size_min` 이면 스킵 |
| **세션 중복** | `utils.get_processed_sessions` | 같은 세션 이중 주문 방지 |
| **좀비 주문 정리** | `order_executor._cleanup_pending_orders` | 이전 세션의 미체결/트리거 자동 취소 |
| **노출 한도 추적** | `step3_per_coin` running_exposure | `max_total_exposure_pct` 초과 방지 |

---

## ICT 차트 모듈 교체

나중에 직접 만든 ICT 엔진/차트로 교체 시 `src/ict_analysis.py`와 `src/ict_chart.py`만 바꾸면 됩니다. 인터페이스 유지:

```python
# ict_analysis.py
def run_ict_analysis(df: pd.DataFrame, symbol: str) -> dict:
    """OHLCV DataFrame → 분석 결과 딕셔너리"""

# ict_chart.py
def generate_ict_chart(df, ict_result, symbol, output_path=None) -> Path:
    """차트 PNG 저장, Path 반환"""
```

---

## 면책

학습/연구 목적입니다. 실거래 손실은 사용자 책임. 반드시 소액으로 충분히 테스트 후 사용하세요.
