from __future__ import annotations
"""
JSON 스키마 정의 - 모듈 간 통신에 사용되는 데이터 구조를 정의합니다.

파이프라인 흐름:
  1. fetch_candles.py → data/candles/{symbol}.csv
  2. ict_chart.py → charts/{session}_{symbol}.png
  3. Claude Code → data/analysis/{session}_{symbol}.json  (이 스키마)
  4. execute_orders.py → data/orders/{session}_orders.json
"""

# Claude Code가 생성하는 분석 결과 JSON 스키마
ANALYSIS_SCHEMA = {
    "session_id": "20260406_00",         # 세션 ID (YYYYMMDD_HH)
    "symbol": "BTC_USDT",                # 거래쌍
    "timestamp": "2026-04-06T00:05:00",  # 분석 시각

    # 기술적 분석 (ICT 차트 기반)
    "technical_score": 0,                # -25 ~ +25
    "technical_reasoning": "",           # 판단 근거

    # 거시적/정량적 분석
    "macro_quant_score": 0,              # -25 ~ +25
    "macro_quant_reasoning": "",         # 판단 근거

    # 스캠 탐지
    "scam_score": 0,                     # -25(스캠 확실) ~ +25(안전)
    "scam_reasoning": "",

    # 최종 판단
    "total_score": 0,                    # technical + macro_quant
    "decision": "skip",                  # "long" / "short" / "skip"
    "confidence": 0.0,                   # 0.0 ~ 1.0

    # 포지션 사이징 (주문 모듈에서 참고)
    "suggested_position_pct": 0.0,       # 잔고 대비 % (Kelly 기반)
    "stop_loss_pct": 0.0,               # 손절 % (진입가 대비)
    "take_profit_pct": 0.0,             # 익절 % (진입가 대비)

    # 효율성 관련
    "analysis_skipped": False,           # 효율성 파라미터로 스킵됨
    "skip_reason": "",                   # 스킵 사유
}

# 주문 실행 결과 JSON 스키마
ORDER_SCHEMA = {
    "session_id": "20260406_00",
    "symbol": "BTC_USDT",
    "decision": "long",
    "side": "buy",                       # buy / sell
    "size": 0.0,                         # 계약 수량
    "leverage": 5,
    "entry_price": 0.0,
    "stop_loss_price": 0.0,
    "take_profit_price": 0.0,
    "order_id": "",
    "status": "pending",                 # pending / filled / failed
    "executed_at": "",
    "error": "",
}

# 인기종목 리스트 JSON 스키마
TOP_COINS_SCHEMA = {
    "session_id": "20260406_00",
    "fetched_at": "",
    "coins": [
        {
            "symbol": "BTC_USDT",
            "rank": 1,
            "volume_24h_usdt": 0.0,
            "price_change_24h_pct": 0.0,
            "last_price": 0.0,
        }
    ],
}
