from __future__ import annotations
"""
주문 검증 - LLM 실수와 비정상 상태로부터 자금을 보호합니다.

검증 항목:
  1. 분석 JSON 무결성: 점수와 decision 일치 여부
  2. SL/TP 방향성: 롱은 SL<진입가<TP, 숏은 반대
  3. SL/TP 거리: 너무 가까우면 노이즈로 즉시 청산됨
  4. 포지션 크기 한도: max_position_pct 초과 금지
  5. 슬리피지: 분석 시점 가격과 현재 가격 차이가 너무 크면 거부
  6. 잔고 충분성: 마진이 부족하면 거부
"""
from typing import Optional

from src.config_loader import get_config
from src.logger import setup_logger

logger = setup_logger("validator")

# 안전 한계
MIN_SL_DISTANCE_PCT = 0.5    # SL이 진입가에서 0.5% 미만이면 거부 (노이즈 청산)
MIN_TP_DISTANCE_PCT = 0.5    # TP도 동일
MAX_SL_DISTANCE_PCT = 30.0   # SL이 30% 넘으면 거부 (실수)
MAX_TP_DISTANCE_PCT = 100.0  # TP가 100% 넘으면 거부 (비현실적)
MAX_SLIPPAGE_PCT = 3.0       # 분석가 ↔ 현재가 차이 3% 초과면 거부


def validate_analysis(analysis: dict) -> tuple[bool, str]:
    """
    분석 JSON 자체의 일관성을 검증합니다.

    Returns:
        (valid: bool, reason: str)
    """
    symbol = analysis.get("symbol", "?")
    decision = analysis.get("decision", "skip")
    total_score = analysis.get("total_score", 0)
    tech = analysis.get("technical_score", 0)
    macro = analysis.get("macro_quant_score", 0)
    confidence = analysis.get("confidence", 0)
    position_pct = analysis.get("suggested_position_pct", 0)
    sl_pct = analysis.get("stop_loss_pct", 0)
    tp_pct = analysis.get("take_profit_pct", 0)

    cfg = get_config()
    long_thresh = cfg["trading"]["long_threshold"]
    short_thresh = cfg["trading"]["short_threshold"]
    max_pos = cfg["position_sizing"]["max_position_pct"]

    # 1. 점수 합계가 실제로 맞는지
    expected_total = tech + macro
    if abs(total_score - expected_total) > 1:
        return False, f"total_score({total_score}) != tech({tech}) + macro({macro})"

    # 2. decision과 점수 일치
    if decision == "long" and total_score < long_thresh:
        return False, f"decision=long but total_score={total_score} < {long_thresh}"
    if decision == "short" and total_score > short_thresh:
        return False, f"decision=short but total_score={total_score} > {short_thresh}"

    # 3. confidence 범위
    if not (0.0 <= confidence <= 1.0):
        return False, f"confidence out of range: {confidence}"

    # 4. position_pct 한도
    if position_pct < 0:
        return False, f"negative position_pct: {position_pct}"
    if position_pct > max_pos + 0.01:
        return False, f"position_pct {position_pct}% exceeds max {max_pos}%"

    # 5. SL/TP 거리 검증
    if decision in ("long", "short"):
        if sl_pct < MIN_SL_DISTANCE_PCT:
            return False, f"stop_loss_pct {sl_pct}% too small (min {MIN_SL_DISTANCE_PCT}%)"
        if sl_pct > MAX_SL_DISTANCE_PCT:
            return False, f"stop_loss_pct {sl_pct}% too large (max {MAX_SL_DISTANCE_PCT}%)"
        if tp_pct < MIN_TP_DISTANCE_PCT:
            return False, f"take_profit_pct {tp_pct}% too small (min {MIN_TP_DISTANCE_PCT}%)"
        if tp_pct > MAX_TP_DISTANCE_PCT:
            return False, f"take_profit_pct {tp_pct}% too large (max {MAX_TP_DISTANCE_PCT}%)"

        # 6. 리스크/리워드 비율 (TP가 SL보다 작으면 음의 기댓값)
        if tp_pct < sl_pct * 0.8:
            return False, f"unfavorable R:R - TP({tp_pct}%) < SL({sl_pct}%) * 0.8"

    return True, "valid"


def validate_order_prices(
    decision: str,
    entry_price: float,
    stop_loss_price: float,
    take_profit_price: float,
) -> tuple[bool, str]:
    """
    실제 주문 가격(SL/TP)이 진입가 대비 올바른 방향인지 검증합니다.
    LLM이 SL과 TP를 뒤바꾸는 실수를 막습니다.
    """
    if entry_price <= 0:
        return False, f"invalid entry_price: {entry_price}"

    if decision == "long":
        # 롱: SL은 진입가보다 낮아야, TP는 높아야
        if stop_loss_price >= entry_price:
            return False, f"LONG SL({stop_loss_price}) >= entry({entry_price}) - would trigger immediately"
        if take_profit_price <= entry_price:
            return False, f"LONG TP({take_profit_price}) <= entry({entry_price}) - would trigger immediately"

    elif decision == "short":
        # 숏: SL은 진입가보다 높아야, TP는 낮아야
        if stop_loss_price <= entry_price:
            return False, f"SHORT SL({stop_loss_price}) <= entry({entry_price}) - would trigger immediately"
        if take_profit_price >= entry_price:
            return False, f"SHORT TP({take_profit_price}) >= entry({entry_price}) - would trigger immediately"

    return True, "valid"


def validate_slippage(analysis_price: float, current_price: float) -> tuple[bool, str]:
    """
    분석 시점 가격과 현재 가격의 차이가 한도 내인지 검증합니다.
    너무 많이 움직였으면 분석이 무효화된 것으로 간주합니다.
    """
    if analysis_price <= 0 or current_price <= 0:
        return True, "skipped (no analysis price)"

    diff_pct = abs(current_price - analysis_price) / analysis_price * 100

    if diff_pct > MAX_SLIPPAGE_PCT:
        return False, f"slippage {diff_pct:.2f}% > {MAX_SLIPPAGE_PCT}% - market moved too much since analysis"

    return True, f"slippage {diff_pct:.2f}% OK"


def validate_balance(required_margin: float, available_balance: float) -> tuple[bool, str]:
    """필요한 마진이 잔고로 커버되는지 검증합니다."""
    if required_margin > available_balance:
        return False, f"required ${required_margin:.2f} > available ${available_balance:.2f}"
    return True, "balance sufficient"
