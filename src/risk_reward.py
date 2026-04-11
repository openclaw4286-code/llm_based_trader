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

    고빈도 모드:
      - max_level_distance_pct 이내의 레벨만 사용 (먼 레벨 무시)
      - max_sl_pct 초과 시 강제 클램핑
    """
    cfg = get_config()
    rr_cfg = cfg.get("risk_reward", {})
    buffer_pct = rr_cfg.get("sl_buffer_pct", 0.1) / 100.0
    fallback_pct = rr_cfg.get("sl_fallback_pct", 1.5) / 100.0
    max_level_dist = rr_cfg.get("max_level_distance_pct", 3.5) / 100.0
    max_sl_pct = rr_cfg.get("max_sl_pct", 2.0) / 100.0

    obs = ict_summary.get("active_order_blocks", [])
    pd_info = ict_summary.get("premium_discount", {})

    # 레벨 후보 수집 (현재가에서 max_level_dist 이내만)
    max_dist_price = current_price * max_level_dist

    if decision == "long":
        candidates = []
        for ob in obs:
            if ob["type"] == "bullish" and ob["bottom"] < current_price:
                # 너무 먼 레벨 제외
                if current_price - ob["bottom"] <= max_dist_price:
                    candidates.append(ob["bottom"])

        swing_low = pd_info.get("swing_low", 0)
        if 0 < swing_low < current_price and (current_price - swing_low) <= max_dist_price:
            candidates.append(swing_low)

        if candidates:
            nearest = max(candidates)
            sl = nearest * (1 - buffer_pct)
            logger.info(f"SL (long): nearest support={nearest:.6f}, with buffer={sl:.6f}")
        else:
            sl = current_price * (1 - fallback_pct)
            logger.info(f"SL (long): no nearby OB/Swing, fallback {fallback_pct*100}% → {sl:.6f}")

        # max_sl_pct 클램핑: SL이 너무 멀면 잘라냄
        min_sl_price = current_price * (1 - max_sl_pct)
        if sl < min_sl_price:
            logger.info(f"SL (long) clamped: {sl:.6f} → {min_sl_price:.6f} (max {max_sl_pct*100}%)")
            sl = min_sl_price
        return sl

    else:  # short
        candidates = []
        for ob in obs:
            if ob["type"] == "bearish" and ob["top"] > current_price:
                if ob["top"] - current_price <= max_dist_price:
                    candidates.append(ob["top"])

        swing_high = pd_info.get("swing_high", 0)
        if swing_high > current_price and (swing_high - current_price) <= max_dist_price:
            candidates.append(swing_high)

        if candidates:
            nearest = min(candidates)
            sl = nearest * (1 + buffer_pct)
            logger.info(f"SL (short): nearest resistance={nearest:.6f}, with buffer={sl:.6f}")
        else:
            sl = current_price * (1 + fallback_pct)
            logger.info(f"SL (short): no nearby OB/Swing, fallback {fallback_pct*100}% → {sl:.6f}")

        # max_sl_pct 클램핑
        max_sl_price = current_price * (1 + max_sl_pct)
        if sl > max_sl_price:
            logger.info(f"SL (short) clamped: {sl:.6f} → {max_sl_price:.6f} (max {max_sl_pct*100}%)")
            sl = max_sl_price
        return sl


def calculate_tp_targets(
    decision: str,
    current_price: float,
    ict_summary: dict,
    sl_price: float = 0.0,
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
    min_rr = rr_cfg.get("min_rr_ratio", 2.5)
    max_tp_pct = rr_cfg.get("max_tp_pct", 6.0) / 100.0
    max_level_dist = rr_cfg.get("max_level_distance_pct", 3.5) / 100.0

    if sl_price > 0:
        sl_distance = abs(current_price - sl_price)
    else:
        sl_distance = current_price * 0.015  # 안전 폴백

    max_dist_price = current_price * max_level_dist

    liqs = ict_summary.get("unswept_liquidity", [])
    fvgs = ict_summary.get("active_fvgs", [])
    obs = ict_summary.get("active_order_blocks", [])

    targets = []

    if decision == "long":
        # 각 타입별 레벨 후보 (현재가 위 + max_level_dist 이내만)
        liq_prices = sorted([
            l["price"] for l in liqs
            if l["type"] == "buy_side" and current_price < l["price"] <= current_price + max_dist_price
        ])
        fvg_prices = sorted([
            f["bottom"] for f in fvgs
            if f["type"] == "bearish" and current_price < f["bottom"] <= current_price + max_dist_price
        ])
        ob_prices = sorted([
            ob["bottom"] for ob in obs
            if ob["type"] == "bearish" and current_price < ob["bottom"] <= current_price + max_dist_price
        ])

        price_pools = {
            "liquidity": liq_prices,
            "fvg": fvg_prices,
            "opposite_ob": ob_prices,
        }

        fallback_multipliers = [min_rr, min_rr + 0.3, min_rr + 0.6]

        for i, tp_spec in enumerate(tp_config):
            target_type = tp_spec["target"]
            pct = tp_spec["pct"]
            pool = price_pools.get(target_type, [])

            if pool:
                price = pool.pop(0)
                targets.append({"price": price, "pct_of_position": pct, "reason": target_type})
            else:
                mult = fallback_multipliers[min(i, len(fallback_multipliers) - 1)]
                extended = current_price + (sl_distance * mult)
                targets.append({"price": extended, "pct_of_position": pct, "reason": f"{target_type}_fallback_{mult}x"})

        # max_tp_pct 클램핑 (1차 TP 기준)
        max_tp_price = current_price * (1 + max_tp_pct)
        for t in targets:
            if t["price"] > max_tp_price:
                logger.info(f"TP (long) clamped: {t['price']:.6f} → {max_tp_price:.6f} (max {max_tp_pct*100}%)")
                t["price"] = max_tp_price

    else:  # short
        liq_prices = sorted([
            l["price"] for l in liqs
            if l["type"] == "sell_side" and current_price - max_dist_price <= l["price"] < current_price
        ], reverse=True)
        fvg_prices = sorted([
            f["top"] for f in fvgs
            if f["type"] == "bullish" and current_price - max_dist_price <= f["top"] < current_price
        ], reverse=True)
        ob_prices = sorted([
            ob["top"] for ob in obs
            if ob["type"] == "bullish" and current_price - max_dist_price <= ob["top"] < current_price
        ], reverse=True)

        price_pools = {
            "liquidity": liq_prices,
            "fvg": fvg_prices,
            "opposite_ob": ob_prices,
        }

        fallback_multipliers = [min_rr, min_rr + 0.3, min_rr + 0.6]

        for i, tp_spec in enumerate(tp_config):
            target_type = tp_spec["target"]
            pct = tp_spec["pct"]
            pool = price_pools.get(target_type, [])

            if pool:
                price = pool.pop(0)
                targets.append({"price": price, "pct_of_position": pct, "reason": target_type})
            else:
                mult = fallback_multipliers[min(i, len(fallback_multipliers) - 1)]
                extended = current_price - (sl_distance * mult)
                targets.append({"price": extended, "pct_of_position": pct, "reason": f"{target_type}_fallback_{mult}x"})

        # max_tp_pct 클램핑 (숏)
        min_tp_price = current_price * (1 - max_tp_pct)
        for t in targets:
            if t["price"] < min_tp_price:
                logger.info(f"TP (short) clamped: {t['price']:.6f} → {min_tp_price:.6f} (max {max_tp_pct*100}%)")
                t["price"] = min_tp_price

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
    tp_targets = calculate_tp_targets(decision, current_price, ict_summary, sl_price=sl_price)
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
