from __future__ import annotations
"""
ICT 구조 기반 Risk & Reward 계산기

SL/TP를 Claude의 임의 퍼센트가 아닌 ICT 기술적 레벨에서 계산합니다:
  - SL: 가장 가까운 OB 하단/상단 또는 Swing Low/High
  - TP: Liquidity Pool, 미충전 FVG, 반대편 OB (멀티 타겟)
  - R:R 최소 3:1 미달 시 진입 거부
"""
from src.config_loader import get_config
from src.logger import setup_logger

logger = setup_logger("risk_reward")


def calculate_sl(
    decision: str,
    current_price: float,
    ict_summary: dict,
) -> float:
    """
    ICT 구조 기반으로 SL 가격을 결정합니다.

    롱: 현재가 아래 가장 가까운 Bullish OB 하단 또는 Swing Low
    숏: 현재가 위 가장 가까운 Bearish OB 상단 또는 Swing High
    """
    cfg = get_config()
    rr_cfg = cfg.get("risk_reward", {})
    buffer_pct = rr_cfg.get("sl_buffer_pct", 0.2) / 100.0
    fallback_pct = rr_cfg.get("sl_fallback_pct", 3.0) / 100.0

    obs = ict_summary.get("active_order_blocks", [])
    pd_info = ict_summary.get("premium_discount", {})

    if decision == "long":
        # 현재가 아래의 Bullish OB 하단 찾기
        candidates = []
        for ob in obs:
            if ob["type"] == "bullish" and ob["bottom"] < current_price:
                candidates.append(ob["bottom"])

        # Swing Low도 후보에 추가
        swing_low = pd_info.get("swing_low", 0)
        if 0 < swing_low < current_price:
            candidates.append(swing_low)

        if candidates:
            # 현재가에 가장 가까운 (가장 높은) 지지 레벨
            nearest = max(candidates)
            sl = nearest * (1 - buffer_pct)
            logger.info(f"SL (long): nearest support={nearest:.6f}, with buffer={sl:.6f}")
            return sl

        # 폴백: 현재가의 fallback_pct%
        sl = current_price * (1 - fallback_pct)
        logger.info(f"SL (long): no OB/Swing found, fallback {fallback_pct*100}% → {sl:.6f}")
        return sl

    else:  # short
        # 현재가 위의 Bearish OB 상단 찾기
        candidates = []
        for ob in obs:
            if ob["type"] == "bearish" and ob["top"] > current_price:
                candidates.append(ob["top"])

        # Swing High도 후보
        swing_high = pd_info.get("swing_high", 0)
        if swing_high > current_price:
            candidates.append(swing_high)

        if candidates:
            nearest = min(candidates)
            sl = nearest * (1 + buffer_pct)
            logger.info(f"SL (short): nearest resistance={nearest:.6f}, with buffer={sl:.6f}")
            return sl

        sl = current_price * (1 + fallback_pct)
        logger.info(f"SL (short): no OB/Swing found, fallback {fallback_pct*100}% → {sl:.6f}")
        return sl


def calculate_tp_targets(
    decision: str,
    current_price: float,
    ict_summary: dict,
) -> list[dict]:
    """
    ICT 구조 기반으로 멀티 TP 타겟을 결정합니다.

    Returns:
        [{"price": float, "pct_of_position": int, "reason": str}, ...]

    롱 TP 순서: Buy-side Liquidity → 미충전 Bearish FVG → 반대편 Bearish OB
    숏 TP 순서: Sell-side Liquidity → 미충전 Bullish FVG → 반대편 Bullish OB
    """
    cfg = get_config()
    rr_cfg = cfg.get("risk_reward", {})
    tp_config = rr_cfg.get("tp_targets", [
        {"pct": 50, "target": "liquidity"},
        {"pct": 30, "target": "fvg"},
        {"pct": 20, "target": "opposite_ob"},
    ])
    fallback_pct = rr_cfg.get("tp_fallback_pct", 6.0) / 100.0

    liqs = ict_summary.get("unswept_liquidity", [])
    fvgs = ict_summary.get("active_fvgs", [])
    obs = ict_summary.get("active_order_blocks", [])

    targets = []

    if decision == "long":
        # 1차: Buy-side Liquidity (현재가 위의 가장 가까운)
        liq_prices = [l["price"] for l in liqs if l["type"] == "buy_side" and l["price"] > current_price]
        liq_prices.sort()

        # 2차: 미충전 Bearish FVG (현재가 위)
        fvg_prices = [f["bottom"] for f in fvgs if f["type"] == "bearish" and f["bottom"] > current_price]
        fvg_prices.sort()

        # 3차: Bearish OB (현재가 위)
        ob_prices = [ob["bottom"] for ob in obs if ob["type"] == "bearish" and ob["bottom"] > current_price]
        ob_prices.sort()

        price_pools = {
            "liquidity": liq_prices,
            "fvg": fvg_prices,
            "opposite_ob": ob_prices,
        }

        for tp_spec in tp_config:
            target_type = tp_spec["target"]
            pct = tp_spec["pct"]
            pool = price_pools.get(target_type, [])

            if pool:
                price = pool.pop(0)  # 가장 가까운 것 사용
                targets.append({"price": price, "pct_of_position": pct, "reason": target_type})
            else:
                # 해당 타입의 레벨이 없으면 이전 TP에서 확장
                if targets:
                    last_price = targets[-1]["price"]
                    extended = last_price * (1 + fallback_pct / 3)
                else:
                    extended = current_price * (1 + fallback_pct)
                targets.append({"price": extended, "pct_of_position": pct, "reason": f"{target_type}_fallback"})

    else:  # short
        liq_prices = [l["price"] for l in liqs if l["type"] == "sell_side" and l["price"] < current_price]
        liq_prices.sort(reverse=True)

        fvg_prices = [f["top"] for f in fvgs if f["type"] == "bullish" and f["top"] < current_price]
        fvg_prices.sort(reverse=True)

        ob_prices = [ob["top"] for ob in obs if ob["type"] == "bullish" and ob["top"] < current_price]
        ob_prices.sort(reverse=True)

        price_pools = {
            "liquidity": liq_prices,
            "fvg": fvg_prices,
            "opposite_ob": ob_prices,
        }

        for tp_spec in tp_config:
            target_type = tp_spec["target"]
            pct = tp_spec["pct"]
            pool = price_pools.get(target_type, [])

            if pool:
                price = pool.pop(0)
                targets.append({"price": price, "pct_of_position": pct, "reason": target_type})
            else:
                if targets:
                    last_price = targets[-1]["price"]
                    extended = last_price * (1 - fallback_pct / 3)
                else:
                    extended = current_price * (1 - fallback_pct)
                targets.append({"price": extended, "pct_of_position": pct, "reason": f"{target_type}_fallback"})

    for t in targets:
        logger.info(f"TP target ({decision}): ${t['price']:.6f} ({t['pct_of_position']}%, {t['reason']})")

    return targets


def check_rr_ratio(
    decision: str,
    entry_price: float,
    sl_price: float,
    tp_targets: list[dict],
) -> tuple[float, bool]:
    """
    R:R 비율을 계산하고 최소 기준을 충족하는지 확인합니다.

    1차 TP(가장 가까운)를 기준으로 R:R을 계산합니다.

    Returns:
        (rr_ratio: float, passes: bool)
    """
    cfg = get_config()
    rr_cfg = cfg.get("risk_reward", {})
    min_rr = rr_cfg.get("min_rr_ratio", 3.0)

    sl_distance = abs(entry_price - sl_price)
    if sl_distance == 0:
        return 0.0, False

    if not tp_targets:
        return 0.0, False

    # 1차 TP 기준 R:R
    tp1_price = tp_targets[0]["price"]
    tp_distance = abs(tp1_price - entry_price)
    rr = tp_distance / sl_distance

    passes = rr >= min_rr
    logger.info(f"R:R = {rr:.2f}:1 (min={min_rr}:1) → {'PASS' if passes else 'REJECT'}")

    return round(rr, 2), passes


def calculate_risk_reward(
    decision: str,
    current_price: float,
    ict_summary: dict,
) -> dict:
    """
    종합: SL, TP 타겟, R:R을 한 번에 계산합니다.

    Returns:
        {
            "sl_price": float,
            "tp_targets": [{"price", "pct_of_position", "reason"}, ...],
            "rr_ratio": float,
            "rr_passes": bool,
            "sl_pct": float,   # 진입가 대비 SL 거리 %
            "tp1_pct": float,  # 진입가 대비 1차 TP 거리 %
        }
    """
    sl_price = calculate_sl(decision, current_price, ict_summary)
    tp_targets = calculate_tp_targets(decision, current_price, ict_summary)
    rr_ratio, rr_passes = check_rr_ratio(decision, current_price, sl_price, tp_targets)

    sl_pct = abs(current_price - sl_price) / current_price * 100
    tp1_pct = abs(tp_targets[0]["price"] - current_price) / current_price * 100 if tp_targets else 0

    return {
        "sl_price": sl_price,
        "tp_targets": tp_targets,
        "rr_ratio": rr_ratio,
        "rr_passes": rr_passes,
        "sl_pct": round(sl_pct, 2),
        "tp1_pct": round(tp1_pct, 2),
    }
