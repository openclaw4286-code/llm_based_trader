"""
포지션 사이징 - Kelly Criterion 기반으로 각 종목의 투자 비율과 현금 보유 비율을 계산합니다.

수학적 원리:
  Kelly 공식: f* = (bp - q) / b
    f*: 최적 투자 비율
    b: 순 배당률 (기대 수익 / 투자금)
    p: 승률
    q: 패률 (1 - p)

  fractional Kelly (보수적): f = kelly_fraction * f*

  현금 보유 비율:
    모든 종목의 포지션 합이 max_total_exposure_pct를 넘지 않도록 조절하며,
    최소 min_cash_reserve_pct 이상의 현금을 항상 보유합니다.
"""
from src.config_loader import get_config


def kelly_criterion(win_rate: float, win_loss_ratio: float) -> float:
    """
    Kelly Criterion으로 최적 투자 비율을 계산합니다.

    Args:
        win_rate: 승률 (0.0 ~ 1.0)
        win_loss_ratio: 평균 수익 / 평균 손실 비율

    Returns:
        최적 투자 비율 (0.0 ~ 1.0)
    """
    if win_loss_ratio <= 0 or win_rate <= 0:
        return 0.0

    b = win_loss_ratio
    p = win_rate
    q = 1.0 - p

    f_star = (b * p - q) / b

    return max(0.0, f_star)


def calculate_position_sizes(analyses: list[dict], balance_usdt: float) -> list[dict]:
    """
    분석 결과 리스트를 받아 각 종목의 포지션 크기를 계산합니다.

    Args:
        analyses: 분석 결과 딕셔너리 리스트 (schemas.ANALYSIS_SCHEMA 형태)
        balance_usdt: 현재 USDT 잔고

    Returns:
        각 종목별 포지션 크기가 추가된 리스트
    """
    cfg = get_config()
    ps_cfg = cfg["position_sizing"]

    kelly_frac = ps_cfg["kelly_fraction"]
    max_pos_pct = ps_cfg["max_position_pct"]
    min_pos_pct = ps_cfg["min_position_pct"]
    min_cash_pct = ps_cfg["min_cash_reserve_pct"]
    max_exposure_pct = ps_cfg["max_total_exposure_pct"]

    tradeable = [a for a in analyses if a.get("decision") in ("long", "short")]

    if not tradeable:
        return analyses

    # 1단계: 각 종목의 Kelly 비율 계산
    raw_positions = []
    for a in tradeable:
        confidence = a.get("confidence", 0.5)
        total_score = a.get("total_score", 0)

        # confidence를 승률로, score 절대값을 수익/손실 비율로 변환
        win_rate = 0.5 + (confidence - 0.5) * 0.3  # 0.35 ~ 0.65 범위로 보수적 조정
        win_loss_ratio = 1.0 + abs(total_score) / 50.0 * 1.5  # 1.0 ~ 2.5

        raw_kelly = kelly_criterion(win_rate, win_loss_ratio)
        adjusted_kelly = raw_kelly * kelly_frac  # fractional Kelly

        # 비율을 % 단위로 변환하고 min/max 클램핑
        position_pct = adjusted_kelly * 100.0
        position_pct = max(min_pos_pct, min(max_pos_pct, position_pct))

        raw_positions.append({
            "symbol": a["symbol"],
            "decision": a["decision"],
            "raw_kelly_pct": position_pct,
        })

    # 2단계: 전체 노출 비율이 max_exposure_pct를 넘지 않도록 비례 축소
    total_raw = sum(p["raw_kelly_pct"] for p in raw_positions)
    available_pct = min(max_exposure_pct, 100.0 - min_cash_pct)

    scale_factor = 1.0
    if total_raw > available_pct:
        scale_factor = available_pct / total_raw

    # 3단계: 최종 포지션 크기 계산
    position_map = {}
    for p in raw_positions:
        final_pct = p["raw_kelly_pct"] * scale_factor
        final_pct = max(min_pos_pct, min(max_pos_pct, final_pct))
        position_map[p["symbol"]] = final_pct

    # 4단계: 결과를 원래 분석 리스트에 반영
    total_allocated = sum(position_map.values())
    cash_reserve_pct = 100.0 - total_allocated

    for a in analyses:
        if a["symbol"] in position_map:
            a["suggested_position_pct"] = round(position_map[a["symbol"]], 2)
            a["position_usdt"] = round(balance_usdt * position_map[a["symbol"]] / 100.0, 2)
        else:
            a["suggested_position_pct"] = 0.0
            a["position_usdt"] = 0.0

    return analyses


def calculate_cash_reserve(analyses: list[dict]) -> dict:
    """
    전체 포지션 배분 후 현금 보유 비율을 계산합니다.

    Returns:
        {
            "total_exposure_pct": float,
            "cash_reserve_pct": float,
            "num_positions": int,
            "meets_minimum": bool,
        }
    """
    cfg = get_config()
    min_cash_pct = cfg["position_sizing"]["min_cash_reserve_pct"]

    total_exposure = sum(
        a.get("suggested_position_pct", 0.0)
        for a in analyses
        if a.get("decision") in ("long", "short")
    )

    cash_pct = 100.0 - total_exposure
    num_positions = sum(1 for a in analyses if a.get("decision") in ("long", "short"))

    return {
        "total_exposure_pct": round(total_exposure, 2),
        "cash_reserve_pct": round(cash_pct, 2),
        "num_positions": num_positions,
        "meets_minimum": cash_pct >= min_cash_pct,
    }
