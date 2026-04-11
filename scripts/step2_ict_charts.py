from __future__ import annotations
#!/usr/bin/env python3
"""
파이프라인 2단계: ICT 분석 & 차트 생성
  1. step1에서 저장한 인기종목 리스트를 로드
  2. 각 종목의 캔들 데이터를 로드
  3. ICT 분석 실행
  4. ICT 분석 결과가 오버레이된 차트를 PNG로 저장
  5. ICT 분석 요약을 JSON으로 저장 (Claude Code가 읽을 수 있도록)

독립 실행 가능: 여러 번 실행해도 차트와 JSON을 덮어쓰므로 안전합니다.

사용법:
  python scripts/step2_ict_charts.py
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils import get_config
from src.top_coins import load_top_coins
from src.candle_fetcher import load_candles
from src.ict_analysis import run_ict_analysis
from src.ict_chart import generate_ict_chart
from src.utils import get_session_id, setup_logger

logger = setup_logger("step2")


def serialize_ict_summary(ict_result: dict) -> dict:
    """
    ICT 분석 결과에서 Claude Code가 읽을 수 있는 요약 정보만 추출합니다.
    (전체 결과는 너무 크므로 요약만 JSON으로 저장)
    """
    summary = ict_result.get("summary", {})
    pd_info = ict_result.get("premium_discount", {})

    # 최근 활성 Order Blocks (미미티게이트, 최근 30개만)
    active_obs = [
        {
            "type": ob["type"],
            "top": ob["top"],
            "bottom": ob["bottom"],
            "index": ob["index"],
        }
        for ob in ict_result.get("order_blocks", [])
        if not ob.get("mitigated")
    ][-30:]

    # 최근 활성 FVGs (미충전, 최근 20개만)
    active_fvgs = [
        {
            "type": fvg["type"],
            "top": fvg["top"],
            "bottom": fvg["bottom"],
            "index": fvg["index"],
        }
        for fvg in ict_result.get("fvg", [])
        if not fvg.get("filled")
    ][-20:]

    # 미스윕 유동성 레벨
    unswept_liq = [
        {
            "type": liq["type"],
            "price": liq["price"],
            "touches": liq["touches"],
        }
        for liq in ict_result.get("liquidity", [])
        if not liq.get("swept")
    ]

    # 최근 구조 변화 (마지막 10개)
    recent_structures = [
        {
            "type": s["type"],
            "direction": s["direction"],
            "price": s["price"],
            "index": s["index"],
        }
        for s in ict_result.get("market_structure", [])
    ][-10:]

    return {
        "summary": summary,
        "premium_discount": pd_info,
        "active_order_blocks": active_obs,
        "active_fvgs": active_fvgs,
        "unswept_liquidity": unswept_liq,
        "recent_structures": recent_structures,
    }


def main():
    cfg = get_config()
    session_id = get_session_id()

    logger.info(f"=== Step 2: ICT Analysis & Chart Generation (session: {session_id}) ===")

    # 1. 인기종목 로드
    coins = load_top_coins(session_id)
    if not coins:
        logger.error("No top coins found. Run step1 first.")
        sys.exit(1)

    logger.info(f"Loaded {len(coins)} coins for analysis")

    # 2. 각 종목 분석 및 차트 생성
    analysis_dir = Path(cfg["paths"]["analysis"])
    analysis_dir.mkdir(parents=True, exist_ok=True)
    chart_dir = Path(cfg["paths"]["charts"])
    chart_dir.mkdir(parents=True, exist_ok=True)

    # 이전 세션 프리픽스가 붙은 옛 파일 정리 (혼란 방지)
    # 새로운 파일명은 세션 프리픽스 없는 {symbol}.png, {symbol}_ict.json
    import re as _re
    session_pattern = _re.compile(r"^\d{8}_\d{2}_.+\.(png|json)$")
    for old_file in chart_dir.glob("*.png"):
        if session_pattern.match(old_file.name):
            old_file.unlink()
            logger.debug(f"Removed stale chart: {old_file.name}")
    for old_file in analysis_dir.glob("*_ict.json"):
        if session_pattern.match(old_file.name):
            old_file.unlink()
            logger.debug(f"Removed stale ICT JSON: {old_file.name}")

    success_count = 0
    fail_count = 0

    for coin in coins:
        symbol = coin["symbol"]
        logger.info(f"[{coin['rank']}/{len(coins)}] Analyzing {symbol}...")

        # 캔들 데이터 로드
        df = load_candles(symbol)
        if df.empty:
            logger.warning(f"[{symbol}] No candle data, skipping")
            fail_count += 1
            continue

        try:
            # ICT 분석 실행
            ict_result = run_ict_analysis(df, symbol)

            # 차트 생성
            chart_path = generate_ict_chart(df, ict_result, symbol)

            # ICT 요약을 JSON으로 저장 (Claude Code용)
            # 세션 프리픽스 없이 심볼명만 사용 (매 세션마다 덮어씀)
            ict_summary = serialize_ict_summary(ict_result)
            ict_summary["symbol"] = symbol
            ict_summary["session_id"] = session_id
            ict_summary["chart_path"] = str(chart_path)
            ict_summary["candle_count"] = len(df)

            json_path = analysis_dir / f"{symbol}_ict.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(ict_summary, f, ensure_ascii=False, indent=2, default=str)

            logger.info(f"[{symbol}] Chart: {chart_path}, ICT JSON: {json_path}")
            success_count += 1

        except Exception as e:
            logger.error(f"[{symbol}] Analysis failed: {e}", exc_info=True)
            fail_count += 1

    logger.info(f"=== Step 2 Complete: {success_count} success, {fail_count} failed ===")


if __name__ == "__main__":
    main()
