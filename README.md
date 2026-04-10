# LLM-Based Crypto Trading Agent

Gate.io 선물 자동매매 에이전트. ICT 차트 기반 기술적 분석 + Claude Code(Opus 4.6)의 거시적/정량적 분석으로 6시간마다 롱/숏/스킵을 결정합니다.

---

## 파이프라인 전체 흐름

6시간마다 (04, 10, 16, 22시 KST) 아래 6단계가 **순차 실행**됩니다.

```
[step1] Gate.io API → 시총 기준 상위 20종목 선정 + 5년치 일봉 다운로드
   ↓
[step2] 각 종목 ICT 분석 (BOS/CHoCH, OB, FVG, Liquidity, Premium/Discount, OTE)
        → 차트 PNG 저장 + ICT 요약 JSON 저장
   ↓
[step3] 이전 세션의 분석 JSON 정리 → 20종목 전부 Claude 프롬프트 생성
   ↓
[step4] Claude Code CLI (Opus 4.6) 호출
        → 각 종목에 대해 기술적/거시적 점수 + 롱/숏/스킵 결정
   ↓
[step5] Claude 응답 파싱 → 종목별 분석 JSON 저장
        → Kelly Criterion으로 각 종목 포지션 비율(%) 계산
   ↓
[step6] Gate.io 선물 주문 실행 (진입/유지/청산/반전)
        → 진입 시 SL/TP 트리거 주문 동시 설정
```

각 단계는 **독립 실행 가능 + 멱등**입니다. 중간에 실패해도 재실행하면 이어서 진행됩니다.

---

## 종목 선정 (step1)

- Gate.io USDT 선물 전체 종목에서 **시가총액 proxy** 기준 상위 20개 선정
- 시총 proxy = `mark_price × volume_24h_base` (Gate.io가 시총을 직접 안 줘서 대용)
- BTC, ETH 같은 대형 코인 + XAU(금), XAG(은) 같은 상품 선물도 포함
- 스테이블코인 (USDC, DAI 등), 래핑 토큰 (WBTC, WETH 등)은 제외
- 매 세션마다 **실시간으로 새로 선정** (고정 아님)

---

## 점수 체계 (step4 — Claude 분석)

**모든 20종목을 매 세션마다 풀 분석합니다** (보유 중인 종목 포함).

Claude에게 각 종목마다 2가지 점수를 요청:

| 점수 | 범위 | 기준 |
|---|---|---|
| **기술적 분석** | -25 ~ +25 | ICT 차트: BOS/CHoCH, Order Block, FVG, Liquidity, Premium/Discount, OTE |
| **거시적/정량적** | -25 ~ +25 | 뉴스 센티먼트, SNS, 토크노믹스, 온체인 데이터, 시장 전반 |

### 결정 로직

```
total_score = 기술적 + 거시적

if total_score >= +10:
    decision = "long"          # 롱 진입
elif total_score <= -10:
    decision = "short"         # 숏 진입
else:
    decision = "skip"          # 애매하면 패스
```

---

## 포지션 사이징 (step5)

**Kelly Criterion (보수적 fractional Kelly)** 기반:

```
Kelly 공식: f* = (bp - q) / b
  b = 수익/손실 비율 (1.0 ~ 2.5, total_score에서 산출)
  p = 승률 (0.35 ~ 0.65, confidence에서 산출)
  q = 1 - p

적용 비율 = f* × kelly_fraction (기본 0.25 = 풀 켈리의 25%)
```

### 제약 조건

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `kelly_fraction` | 0.25 | 보수적 Kelly 비율 |
| `max_position_pct` | 5.0% | 단일 종목 최대 잔고 비율 |
| `min_position_pct` | 0.5% | 단일 종목 최소 잔고 비율 |
| `min_cash_reserve_pct` | 30.0% | 최소 현금 보유 |
| `max_total_exposure_pct` | 60.0% | 전체 노출 한도 |

모든 종목의 포지션 합이 `max_total_exposure_pct`를 넘으면 **비례 축소**. 최소 `min_cash_reserve_pct` 이상 현금 항상 보유.

---

## 한 종목의 전체 처리 흐름

### Phase 1: 분석 (step3 + step4)

**모든 종목을 매번 풀 분석합니다.** 보유 중인 종목도 분석합니다.
→ Claude가 "이제 나가야 해 (skip)" 또는 "방향 전환해야 해 (반전)" 판단 가능.

```
step3: 20종목 전부 프롬프트 생성 (예외 없음)
step4: Claude가 각 종목 분석 → decision = long / short / skip
```

### Phase 2: 주문 실행 (step6)

step6는 **분석된 종목 + 기존 포지션 보유 종목** 모두를 순회합니다.

```
┌─────────────────────────────────────────────────────────┐
│ 입력: analysis (Claude 분석 결과 또는 None)                 │
│       existing (Gate.io 현재 포지션 또는 None)              │
└─────────────────────────────────────────────────────────┘
                        │
                        ▼
               분석 결과 있는가?
              ┌────┴────┐
             NO         YES
              │          │
              ▼          ▼
        포지션 있는가?   decision 은?
        ┌──┴──┐       ┌────┬────────┐
       NO    YES    skip  같은방향   반대방향/신규
        │     │      │      │          │
        ▼     ▼      ▼      ▼          ▼
     [정리]  [유지] 포지션?  [유지]    아래 진입
     미체결   HOLD  ┌┴┐    HOLD     프로세스로
     주문만        NO YES
                    │  │
                 [정리] [청산]
                 미체결  Claude가
                 주문만  skip 판단
```

### 경우의 수 상세 (7가지)

| # | 분석 | 기존 포지션 | decision | 동작 | 설명 |
|---|---|---|---|---|---|
| 1 | 없음 | 없음 | - | **미체결 주문 정리** | 완전 무관한 종목 |
| 2 | 없음 | LONG 보유 | - | **HOLD** | top20 밖이지만 포지션 있음 → 유지 |
| 3 | 있음 | 없음 | skip | **미체결 주문 정리** | Claude가 skip → 아무것도 안 함 |
| 4 | 있음 | LONG 보유 | skip | **청산** | Claude가 더 이상 추천 안 함 → 포지션 종료 |
| 5 | 있음 | LONG 보유 | long | **HOLD** | 같은 방향 → 유지 (불필요한 거래 방지) |
| 6 | 있음 | LONG 보유 | short | **청산 → 숏 진입** | 반대 방향 → 기존 닫고 새로 잡기 |
| 7 | 있음 | 없음 | long/short | **신규 진입** | 새 포지션 열기 |

> 위 표에서 SHORT 보유 케이스도 동일하게 대칭 적용됩니다.

### 핵심 원칙

1. **모든 종목 매번 분석** — 보유 중이어도 분석해서 청산/반전 판단
2. **`decision="skip"` (Claude 판단)일 때 기존 포지션 청산** — 의도적 퇴장
3. **같은 방향 포지션은 HOLD** — 매 세션마다 포지션 열고 닫는 비용 방지
4. **top20 밖이지만 포지션 보유 중 (분석 없음)** → 유지 (SL/TP로 관리)

---

## 신규 진입 프로세스 (위 표의 #6, #7)

신규 포지션을 열 때 아래 단계를 순서대로 거칩니다. **어느 하나라도 실패하면 주문 안 나감**.

```
1. 미체결 좀비 주문 정리 (이전 세션의 잔여 주문)
2. 반대 포지션 있으면 먼저 청산 (#6의 경우)
3. 분석 JSON 무결성 검증 (validate_analysis)
   - 점수 ↔ decision 일치하는가
   - position_pct가 0% 초과, max% 이하인가
   - SL 거리: 0.5% ~ 30%
   - TP 거리: 0.5% ~ 100%
   - R:R 비율: TP >= SL × 0.8
4. 계약 정보 조회 (Gate.io: quanto_multiplier, order_price_round, order_size_min)
5. 현재 시장가 조회 (Gate.io ticker)
6. 슬리피지 검증: |분석 시점 가격 - 현재 가격| > 3% → 거부
7. 포지션 금액 계산: balance × position_pct%
8. 계약 수량 계산: (금액 × 레버리지) / (가격 × quanto_multiplier) → 정수
   - 수량 < min_size → "잔고 부족" 스킵
9. SL/TP 가격 계산 + tick size 반올림 (Decimal 정밀도)
   - 롱 SL → ROUND_DOWN (아래로, 더 안전)
   - 롱 TP → ROUND_UP (위로, 더 보수적)
   - 숏은 반대
10. SL/TP 방향 검증 (validate_order_prices)
    - 롱: SL < 진입가 < TP 인가?
    - 숏: TP < 진입가 < SL 인가?
    - 위반 시 → 거부 (LLM 실수 차단)
11. 잔고 충분성 검증
12. Gate.io에 레버리지 설정
13. 시장가(IOC) 주문 실행
14. SL 트리거 주문 설정 (Gate.io price_orders)
15. TP 트리거 주문 설정 (Gate.io price_orders)
16. 주문 결과 JSON 저장
```

---

## SL/TP 트리거 주문

진입과 동시에 Gate.io의 **price triggered order** API로 SL/TP를 설정합니다:

```
SL 트리거:
  - 조건: 가격이 SL 가격에 도달하면
  - 실행: 시장가(IOC)로 reduce-only 주문 (포지션 전량 청산)
  - 롱일 때: 가격 <= SL (현재가가 SL 이하로 떨어지면)
  - 숏일 때: 가격 >= SL (현재가가 SL 이상으로 올라가면)

TP 트리거:
  - 동일 구조, 반대 방향
```

가격은 **tick size(`order_price_round`)의 배수**로 반올림되어 전송됩니다.
tick에 안 맞으면 Gate.io가 `AUTO_INVALID_PARAM_TRIGGER_PRICE`로 거부합니다.

---

## 세션 관리 + 중복 방지

- 세션 ID = `YYYYMMDD_HH` (가장 가까운 이전 스케줄 시각)
  - 예: 05:30 → `20260410_04`, 15:00 → `20260410_10`
  - 22:00~03:59 → 전날 `_22` 세션
- 주문 실행 후 `data/orders/processed_sessions.json`에 세션 ID 기록
- 같은 세션 ID로 재실행 시 자동 스킵 (중복 주문 방지)
- **dry-run은 마킹 안 함** (재실행 가능)
- 수동 재실행 필요하면: `rm -f data/orders/processed_sessions.json`

---

## 안전 장치 요약

| 검증 | 시점 | 차단 대상 |
|---|---|---|
| `validate_analysis` | 주문 전 | 점수↔decision 불일치, 비정상 SL/TP 거리, 불리한 R:R |
| `validate_order_prices` | 주문 전 | 롱인데 SL≥진입 / TP≤진입 (즉시 손실) |
| `validate_slippage` | 주문 전 | 분석 시점 ↔ 현재 가격 차 > 3% |
| `validate_balance` | 주문 전 | 마진 부족 |
| Tick size 반올림 | 주문 전 | Gate.io의 가격 단위 불일치 거부 |
| 세션 중복 체크 | 실행 시 | 같은 세션 이중 주문 |
| 좀비 주문 정리 | 매 종목 | 이전 세션의 잔여 미체결/트리거 주문 |

---

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

```bash
bash scripts/setup_keys.sh
```

> Gate.io API 키 만들기: Gate.io → 계정 → API 관리 → 새 키 → **Perpetual Futures 거래 권한** 필수

### 4. Claude Code CLI

**Claude Code CLI**를 로컬에서 호출합니다 (Max 요금제).
설치 후 `claude --version`으로 확인, 한 번 로그인 필요.

### 5. 설정 조정

`config.yaml`:

```yaml
trading:
  top_n_coins: 20            # 시총 상위 종목 수
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
bash scripts/run_pipeline.sh       # 전체 파이프라인 1회
```

같은 세션 재실행 시:

```bash
rm -f data/orders/processed_sessions.json
bash scripts/run_pipeline.sh
```

### 자동 실행 (macOS launchd — 권장)

```bash
bash scripts/install_launchd.sh
```

launchd는 macOS 기본 스케줄러로, cron과 달리 **Keychain 접근 가능** (Claude CLI 인증 작동).
04시, 10시, 16시, 22시에 자동 실행됩니다.

```bash
bash scripts/install_launchd.sh status  # 상태 확인
bash scripts/install_launchd.sh remove  # 제거
tail -f logs/launchd_stdout.log         # 로그
```

## 유틸리티

```bash
python scripts/show_state.py                    # Gate.io 잔고/포지션/트리거 조회
python scripts/test_orders.py                   # 시뮬레이션 (주문 안 나감)
python scripts/step4_execute_orders.py --status  # 세션/분석 요약
python scripts/step4_execute_orders.py --dry-run # dry-run
```

## 비상 종료

```bash
python scripts/step4_execute_orders.py --close-all
```

## 디렉토리 구조

```
llm_based_trader/
├── config.yaml             # 트레이딩 설정
├── .env                    # API 키 (gitignore)
├── src/
│   ├── gateio_client.py    # Gate.io API v4 클라이언트
│   ├── ict_analysis.py     # ICT 분석 엔진
│   ├── ict_chart.py        # 차트 시각화 (교체 가능)
│   ├── claude_analyzer.py  # Claude 응답 파싱 + 정규화
│   ├── order_executor.py   # 주문 실행 + 포지션 관리
│   ├── order_validator.py  # 주문 전 검증 (SL/TP/슬리피지/잔고)
│   ├── position_sizing.py  # Kelly Criterion 포지션 사이징
│   ├── prompts.py          # Claude 프롬프트 템플릿
│   ├── file_manager.py     # 세션 ID, JSON 읽기/쓰기, 중복 방지
│   ├── config_loader.py    # config.yaml + .env 로더
│   ├── top_coins.py        # 시총 기준 상위 N종목 선별
│   ├── candle_fetcher.py   # 5년치 일봉 다운로드
│   └── logger.py           # 콘솔 + 파일 로깅
├── scripts/
│   ├── run_pipeline.sh     # 전체 파이프라인 자동화
│   ├── step1_fetch_data.py
│   ├── step2_ict_charts.py
│   ├── step3_claude_analysis.py
│   ├── step4_execute_orders.py
│   ├── parse_claude_response.py
│   ├── show_state.py       # Gate.io 상태 조회
│   ├── test_orders.py      # 시뮬레이션
│   ├── install_launchd.sh  # macOS 자동화 (권장)
│   ├── install_cron.sh     # cron 자동화 (레거시)
│   ├── setup_keys.sh       # API 키 대화식 입력
│   └── debug_gateio.py     # API 연결 디버그
├── data/
│   ├── candles/            # 5년치 일봉 CSV
│   ├── analysis/           # 세션별 ICT/분석 JSON
│   │   └── prompts/        # Claude 프롬프트/응답
│   └── orders/             # 주문 기록 + processed_sessions.json
├── charts/                 # ICT 차트 PNG
└── logs/                   # 실행 로그
```

## ICT 차트 모듈 교체

`src/ict_chart.py`만 바꾸면 됩니다. 인터페이스:

```python
def generate_ict_chart(df, ict_result, symbol, output_path=None) -> Path:
    ...
```

## 면책

이 프로젝트는 학습/연구 목적입니다. 실제 자금 사용 시 발생하는 손실은 사용자 책임입니다. 반드시 작은 금액으로 충분히 테스트한 후 사용하세요.
