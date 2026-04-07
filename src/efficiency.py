from __future__ import annotations
"""
효율성 관리자 - efficiency_level (0~10)에 따라 분석 범위를 조절합니다.

레벨별 동작:
  0: 모든 종목을 매번 풀 분석
  1-3: 변동이 적은 종목은 이전 분석 재사용
  4-6: 거래량 하위 종목 조기 스킵, 스캠 의심 종목 빠른 스킵
  7-9: 이전 세션 대비 가격 변동 < N% 이면 스킵, 상위 종목만 집중
  10: 최대 효율 - 상위 5~10개만 분석, 나머지는 이전 결과 유지
"""
from pathlib import Path
from typing import Optional

from src.config_loader import get_config
from src.file_manager import load_analysis_json, compute_file_hash


def should_analyze(
    symbol: str,
    rank: int,
    price_change_24h_pct: float,
    volume_24h_usdt: float,
    prev_session_id: Optional[str] = None,
) -> tuple[bool, str]:
    """
    효율성 파라미터에 따라 해당 종목을 분석할지 결정합니다.

    Returns:
        (should_analyze: bool, reason: str)
    """
    cfg = get_config()
    level = cfg.get("efficiency_level", 5)

    # 레벨 0: 항상 분석
    if level == 0:
        return True, "efficiency_level=0, full analysis"

    # 이전 분석 결과 확인
    prev_analysis = None
    if prev_session_id:
        prev_analysis = load_analysis_json(symbol, prev_session_id)

    # 레벨 1-3: 이전에 스캠으로 판별된 종목은 스킵
    if level >= 1 and prev_analysis:
        if prev_analysis.get("scam_score", 0) <= cfg.get("scam_detection", {}).get("scam_threshold", -15):
            return False, f"previously detected as scam (score={prev_analysis['scam_score']})"

    # 레벨 4-6: 거래량 하위 종목 스킵 기준 강화
    if level >= 4:
        skip_below_rank = 20 - (level - 4) * 2  # lv4: 20, lv5: 18, lv6: 16
        if rank > skip_below_rank:
            return False, f"rank {rank} exceeds threshold {skip_below_rank} at efficiency_level={level}"

    # 레벨 7-9: 가격 변동이 작으면 스킵
    if level >= 7:
        min_change = (level - 6) * 1.0  # lv7: 1%, lv8: 2%, lv9: 3%
        if abs(price_change_24h_pct) < min_change and prev_analysis:
            return False, f"price change {price_change_24h_pct:.1f}% < {min_change}% threshold"

    # 레벨 10: 상위 N개만 분석
    if level >= 10:
        max_coins = 5
        if rank > max_coins:
            return False, f"efficiency_level=10, only analyzing top {max_coins}"

    return True, "passed efficiency filter"


def get_analysis_depth(rank: int) -> str:
    """
    효율성 레벨과 종목 순위에 따라 분석 깊이를 반환합니다.

    Returns:
        "full" / "standard" / "quick"
    """
    cfg = get_config()
    level = cfg.get("efficiency_level", 5)

    if level <= 2:
        return "full"

    if rank <= 5:
        return "full"
    elif rank <= 10:
        if level <= 5:
            return "full"
        return "standard"
    else:
        if level <= 3:
            return "full"
        elif level <= 6:
            return "standard"
        return "quick"
