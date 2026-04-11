from __future__ import annotations
"""
ICT (Inner Circle Trader) 기술적 분석 엔진

분석 항목:
  1. Market Structure: BOS (Break of Structure), CHoCH (Change of Character)
  2. Order Blocks (OB): Bullish / Bearish
  3. Fair Value Gaps (FVG): Bullish / Bearish
  4. Liquidity Levels: Buy-side / Sell-side liquidity
  5. Premium / Discount Zones (Fibonacci 기반)
  6. Optimal Trade Entry (OTE): 0.618 ~ 0.786 피보나치 리트레이스먼트

이 모듈은 나중에 사용자가 제공하는 ICT 차트 파이썬 파일로 교체 가능하도록
모듈화되어 있습니다. replace_ict_module() 호출 없이도 독립 작동합니다.
"""
import numpy as np
import pandas as pd
from typing import Optional

from src.utils import setup_logger

logger = setup_logger("ict_analysis")


# ─── 1. Market Structure (BOS / CHoCH) ───────────────────

def find_swing_points(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """
    스윙 고점(Swing High)과 스윙 저점(Swing Low)을 식별합니다.

    Args:
        df: OHLCV DataFrame
        lookback: 좌/우 비교할 캔들 수

    Returns:
        DataFrame with 'swing_high', 'swing_low' boolean columns
    """
    df = df.copy()
    df["swing_high"] = False
    df["swing_low"] = False

    highs = df["high"].values
    lows = df["low"].values

    for i in range(lookback, len(df) - lookback):
        # 스윙 고점: 양쪽 lookback 캔들보다 high가 높음
        if all(highs[i] > highs[i - j] for j in range(1, lookback + 1)) and \
           all(highs[i] > highs[i + j] for j in range(1, lookback + 1)):
            df.iloc[i, df.columns.get_loc("swing_high")] = True

        # 스윙 저점: 양쪽 lookback 캔들보다 low가 낮음
        if all(lows[i] < lows[i - j] for j in range(1, lookback + 1)) and \
           all(lows[i] < lows[i + j] for j in range(1, lookback + 1)):
            df.iloc[i, df.columns.get_loc("swing_low")] = True

    return df


def detect_market_structure(df: pd.DataFrame) -> list[dict]:
    """
    BOS (Break of Structure)와 CHoCH (Change of Character)를 탐지합니다.

    BOS: 기존 추세 방향으로 스윙 포인트를 돌파
    CHoCH: 추세 반전 - 반대 방향으로 스윙 포인트를 돌파

    Returns:
        [{"index": int, "type": "BOS"/"CHoCH", "direction": "bullish"/"bearish",
          "price": float, "timestamp": ...}, ...]
    """
    df = find_swing_points(df)
    structures = []

    # 최근 스윙 포인트 추적
    last_swing_high = None  # (index, price)
    last_swing_low = None
    trend = None  # "bullish" / "bearish"

    for i in range(len(df)):
        row = df.iloc[i]

        if row["swing_high"]:
            last_swing_high = (i, row["high"])

        if row["swing_low"]:
            last_swing_low = (i, row["low"])

        # BOS / CHoCH 판별
        if last_swing_high and row["close"] > last_swing_high[1]:
            if trend == "bullish":
                struct_type = "BOS"
            else:
                struct_type = "CHoCH"
            trend = "bullish"
            structures.append({
                "index": i,
                "type": struct_type,
                "direction": "bullish",
                "price": last_swing_high[1],
                "timestamp": df.iloc[i]["timestamp"] if "timestamp" in df.columns else i,
            })
            last_swing_high = None

        elif last_swing_low and row["close"] < last_swing_low[1]:
            if trend == "bearish":
                struct_type = "BOS"
            else:
                struct_type = "CHoCH"
            trend = "bearish"
            structures.append({
                "index": i,
                "type": struct_type,
                "direction": "bearish",
                "price": last_swing_low[1],
                "timestamp": df.iloc[i]["timestamp"] if "timestamp" in df.columns else i,
            })
            last_swing_low = None

    return structures


# ─── 2. Order Blocks (OB) ────────────────────────────────

def detect_order_blocks(df: pd.DataFrame, lookback: int = 20) -> list[dict]:
    """
    Order Block을 탐지합니다.

    Bullish OB: 큰 하락 후 강한 상승 반전이 일어난 마지막 하락 캔들의 범위
    Bearish OB: 큰 상승 후 강한 하락 반전이 일어난 마지막 상승 캔들의 범위

    Returns:
        [{"index": int, "type": "bullish"/"bearish", "top": float, "bottom": float,
          "mitigated": bool, "timestamp": ...}, ...]
    """
    order_blocks = []
    closes = df["close"].values
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values

    for i in range(2, len(df)):
        # Bullish OB: 이전 캔들이 하락 캔들이고, 현재 캔들이 강한 상승으로 이전 고점을 돌파
        if closes[i - 1] < opens[i - 1] and closes[i] > highs[i - 1]:
            body_size = abs(closes[i] - opens[i])
            prev_body = abs(closes[i - 1] - opens[i - 1])
            if body_size > prev_body * 1.5:  # 현재 캔들이 이전보다 1.5배 이상 큼
                order_blocks.append({
                    "index": i - 1,
                    "type": "bullish",
                    "top": max(opens[i - 1], closes[i - 1]),
                    "bottom": lows[i - 1],
                    "mitigated": False,
                    "timestamp": df.iloc[i - 1]["timestamp"] if "timestamp" in df.columns else i - 1,
                })

        # Bearish OB: 이전 캔들이 상승 캔들이고, 현재 캔들이 강한 하락으로 이전 저점을 돌파
        if closes[i - 1] > opens[i - 1] and closes[i] < lows[i - 1]:
            body_size = abs(closes[i] - opens[i])
            prev_body = abs(closes[i - 1] - opens[i - 1])
            if body_size > prev_body * 1.5:
                order_blocks.append({
                    "index": i - 1,
                    "type": "bearish",
                    "top": highs[i - 1],
                    "bottom": min(opens[i - 1], closes[i - 1]),
                    "mitigated": False,
                    "timestamp": df.iloc[i - 1]["timestamp"] if "timestamp" in df.columns else i - 1,
                })

    # 미티게이션 체크: OB 영역을 가격이 다시 통과했는지
    for ob in order_blocks:
        for j in range(ob["index"] + 2, len(df)):
            if ob["type"] == "bullish" and lows[j] <= ob["bottom"]:
                ob["mitigated"] = True
                break
            elif ob["type"] == "bearish" and highs[j] >= ob["top"]:
                ob["mitigated"] = True
                break

    return order_blocks


# ─── 3. Fair Value Gaps (FVG) ────────────────────────────

def detect_fvg(df: pd.DataFrame) -> list[dict]:
    """
    Fair Value Gap (FVG)을 탐지합니다.

    Bullish FVG: 캔들3의 low > 캔들1의 high (갭 상승)
    Bearish FVG: 캔들1의 low > 캔들3의 high (갭 하락)

    Returns:
        [{"index": int, "type": "bullish"/"bearish", "top": float, "bottom": float,
          "filled": bool, "timestamp": ...}, ...]
    """
    fvgs = []
    highs = df["high"].values
    lows = df["low"].values

    for i in range(2, len(df)):
        # Bullish FVG: 3번째 캔들의 low가 1번째 캔들의 high보다 높음
        if lows[i] > highs[i - 2]:
            fvgs.append({
                "index": i - 1,  # 중간 캔들 위치
                "type": "bullish",
                "top": lows[i],
                "bottom": highs[i - 2],
                "filled": False,
                "timestamp": df.iloc[i - 1]["timestamp"] if "timestamp" in df.columns else i - 1,
            })

        # Bearish FVG: 1번째 캔들의 low가 3번째 캔들의 high보다 높음
        if lows[i - 2] > highs[i]:
            fvgs.append({
                "index": i - 1,
                "type": "bearish",
                "top": lows[i - 2],
                "bottom": highs[i],
                "filled": False,
                "timestamp": df.iloc[i - 1]["timestamp"] if "timestamp" in df.columns else i - 1,
            })

    # FVG 충전 여부 체크
    for fvg in fvgs:
        for j in range(fvg["index"] + 2, len(df)):
            if fvg["type"] == "bullish" and lows[j] <= fvg["bottom"]:
                fvg["filled"] = True
                break
            elif fvg["type"] == "bearish" and highs[j] >= fvg["top"]:
                fvg["filled"] = True
                break

    return fvgs


# ─── 4. Liquidity Levels ─────────────────────────────────

def detect_liquidity_levels(df: pd.DataFrame, lookback: int = 5) -> list[dict]:
    """
    유동성 레벨(Buy-side / Sell-side Liquidity)을 탐지합니다.

    같은 가격대를 여러 번 터치한 고점/저점 = 유동성이 쌓인 곳

    Returns:
        [{"type": "buy_side"/"sell_side", "price": float, "touches": int,
          "swept": bool, "index_start": int, "index_end": int}, ...]
    """
    df_sw = find_swing_points(df, lookback)
    highs = df["high"].values
    lows = df["low"].values

    # 스윙 고점들을 클러스터링 (0.5% 이내면 같은 레벨)
    swing_highs = [(i, highs[i]) for i in range(len(df)) if df_sw.iloc[i]["swing_high"]]
    swing_lows = [(i, lows[i]) for i in range(len(df)) if df_sw.iloc[i]["swing_low"]]

    liquidity = []

    # Buy-side liquidity (같은 고점대 반복)
    for cluster in _cluster_levels(swing_highs, tolerance_pct=0.5):
        if len(cluster) >= 2:
            avg_price = np.mean([p for _, p in cluster])
            indices = [idx for idx, _ in cluster]
            # 스윕 체크: 해당 가격을 크게 돌파했는지
            swept = any(highs[j] > avg_price * 1.005 for j in range(max(indices), len(df)))
            liquidity.append({
                "type": "buy_side",
                "price": round(float(avg_price), 6),
                "touches": len(cluster),
                "swept": swept,
                "index_start": min(indices),
                "index_end": max(indices),
            })

    # Sell-side liquidity (같은 저점대 반복)
    for cluster in _cluster_levels(swing_lows, tolerance_pct=0.5):
        if len(cluster) >= 2:
            avg_price = np.mean([p for _, p in cluster])
            indices = [idx for idx, _ in cluster]
            swept = any(lows[j] < avg_price * 0.995 for j in range(max(indices), len(df)))
            liquidity.append({
                "type": "sell_side",
                "price": round(float(avg_price), 6),
                "touches": len(cluster),
                "swept": swept,
                "index_start": min(indices),
                "index_end": max(indices),
            })

    return liquidity


def _cluster_levels(points: list[tuple], tolerance_pct: float = 0.5) -> list[list[tuple]]:
    """가격 레벨이 비슷한 포인트들을 클러스터링합니다."""
    if not points:
        return []

    sorted_pts = sorted(points, key=lambda x: x[1])
    clusters = [[sorted_pts[0]]]

    for pt in sorted_pts[1:]:
        cluster_avg = np.mean([p for _, p in clusters[-1]])
        if abs(pt[1] - cluster_avg) / cluster_avg * 100 <= tolerance_pct:
            clusters[-1].append(pt)
        else:
            clusters.append([pt])

    return clusters


# ─── 5. Premium / Discount Zones ─────────────────────────

def calculate_premium_discount(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    최근 스윙 범위를 기준으로 Premium/Discount 존을 계산합니다.

    Premium Zone: 0.5 이상 (고평가 영역 - 매도 유리)
    Discount Zone: 0.5 이하 (저평가 영역 - 매수 유리)
    Equilibrium: 0.5 근처

    Returns:
        {"swing_high": float, "swing_low": float, "current_price": float,
         "fib_level": float, "zone": "premium"/"discount"/"equilibrium",
         "ote_top": float, "ote_bottom": float}
    """
    recent = df.tail(lookback)
    swing_high = float(recent["high"].max())
    swing_low = float(recent["low"].min())
    current_price = float(df.iloc[-1]["close"])

    range_size = swing_high - swing_low
    if range_size == 0:
        fib_level = 0.5
    else:
        fib_level = (current_price - swing_low) / range_size

    if fib_level > 0.55:
        zone = "premium"
    elif fib_level < 0.45:
        zone = "discount"
    else:
        zone = "equilibrium"

    # OTE (Optimal Trade Entry): 0.618 ~ 0.786 피보나치 리트레이스먼트
    ote_bottom = swing_low + range_size * 0.618
    ote_top = swing_low + range_size * 0.786

    return {
        "swing_high": round(swing_high, 6),
        "swing_low": round(swing_low, 6),
        "current_price": round(current_price, 6),
        "fib_level": round(fib_level, 4),
        "zone": zone,
        "ote_top": round(ote_top, 6),
        "ote_bottom": round(ote_bottom, 6),
    }


# ─── 종합 분석 ───────────────────────────────────────────

def run_ict_analysis(df: pd.DataFrame, symbol: str = "") -> dict:
    """
    전체 ICT 분석을 실행하고 결과를 딕셔너리로 반환합니다.

    Args:
        df: OHLCV DataFrame (columns: timestamp, open, high, low, close, volume)
        symbol: 종목명 (로깅용)

    Returns:
        {
            "symbol": str,
            "market_structure": list,
            "order_blocks": list,
            "fvg": list,
            "liquidity": list,
            "premium_discount": dict,
            "summary": dict  # 최근 N일 기준 요약
        }
    """
    logger.info(f"[{symbol}] Running ICT analysis on {len(df)} candles")

    structures = detect_market_structure(df)
    order_blocks = detect_order_blocks(df)
    fvgs = detect_fvg(df)
    liquidity = detect_liquidity_levels(df)
    pd_zones = calculate_premium_discount(df)

    # 최근 30일 기준 요약 통계
    recent_n = 30
    recent_structures = [s for s in structures if s["index"] >= len(df) - recent_n]
    recent_obs = [ob for ob in order_blocks if ob["index"] >= len(df) - recent_n]
    recent_fvgs = [f for f in fvgs if f["index"] >= len(df) - recent_n]

    # 활성 (미충전/미미티게이트) 요소만 필터링
    active_obs = [ob for ob in order_blocks if not ob["mitigated"]]
    active_fvgs = [f for f in fvgs if not f["filled"]]
    unswept_liq = [l for l in liquidity if not l["swept"]]

    # 최근 추세 판별
    recent_bullish_bos = sum(1 for s in recent_structures if s["type"] == "BOS" and s["direction"] == "bullish")
    recent_bearish_bos = sum(1 for s in recent_structures if s["type"] == "BOS" and s["direction"] == "bearish")
    recent_choch = [s for s in recent_structures if s["type"] == "CHoCH"]

    if recent_choch:
        last_choch = recent_choch[-1]
        current_trend = last_choch["direction"]
    elif recent_bullish_bos > recent_bearish_bos:
        current_trend = "bullish"
    elif recent_bearish_bos > recent_bullish_bos:
        current_trend = "bearish"
    else:
        current_trend = "neutral"

    summary = {
        "current_trend": current_trend,
        "recent_bos_bullish": recent_bullish_bos,
        "recent_bos_bearish": recent_bearish_bos,
        "recent_choch_count": len(recent_choch),
        "last_choch_direction": recent_choch[-1]["direction"] if recent_choch else None,
        "active_bullish_obs": len([ob for ob in active_obs if ob["type"] == "bullish"]),
        "active_bearish_obs": len([ob for ob in active_obs if ob["type"] == "bearish"]),
        "active_bullish_fvgs": len([f for f in active_fvgs if f["type"] == "bullish"]),
        "active_bearish_fvgs": len([f for f in active_fvgs if f["type"] == "bearish"]),
        "unswept_buy_side_liq": len([l for l in unswept_liq if l["type"] == "buy_side"]),
        "unswept_sell_side_liq": len([l for l in unswept_liq if l["type"] == "sell_side"]),
        "premium_discount_zone": pd_zones["zone"],
        "fib_level": pd_zones["fib_level"],
        "near_ote": pd_zones["ote_bottom"] <= pd_zones["current_price"] <= pd_zones["ote_top"],
    }

    logger.info(f"[{symbol}] ICT summary: trend={current_trend}, zone={pd_zones['zone']}, "
                f"active OBs={summary['active_bullish_obs']}B/{summary['active_bearish_obs']}S, "
                f"active FVGs={summary['active_bullish_fvgs']}B/{summary['active_bearish_fvgs']}S")

    return {
        "symbol": symbol,
        "market_structure": structures,
        "order_blocks": order_blocks,
        "fvg": fvgs,
        "liquidity": liquidity,
        "premium_discount": pd_zones,
        "summary": summary,
    }
