from __future__ import annotations
"""
Claude Code 분석 러너 - Claude API를 호출하여 각 종목을 분석하고 JSON을 생성합니다.

모든 종목을 매 세션마다 풀 분석합니다 (보유 종목 포함).
"""
import json
import re
import os
from typing import Optional

from src.config_loader import get_config
from src.prompts import SYSTEM_PROMPT, build_full_analysis_prompt
from src.file_manager import save_analysis_json, get_session_id
from src.logger import setup_logger

logger = setup_logger("claude_analyzer")


def generate_prompt_for_claude_code(
    symbol: str,
    ict_summary: dict,
    coin_info: dict,
    current_price: float,
    rank: int,
) -> str:
    """Claude Code용 프롬프트를 생성합니다."""
    return build_full_analysis_prompt(symbol, ict_summary, coin_info, current_price)


def parse_and_save_response(symbol: str, raw_response: str) -> Optional[dict]:
    """Claude Code의 응답 텍스트를 파싱하여 JSON으로 저장합니다."""
    result = _parse_analysis_response(raw_response, symbol)
    if result:
        save_analysis_json(symbol, result)
        logger.info(f"[{symbol}] Saved: score={result['total_score']}, decision={result['decision']}")
    return result


def build_batch_prompt(coins_data: list[dict]) -> str:
    """여러 종목을 한 번에 분석하는 배치 프롬프트를 생성합니다."""
    coin_sections = []
    for cd in coins_data:
        summary = cd["ict_summary"].get("summary", {})
        coin_sections.append(
            f"--- {cd['symbol']} (Rank #{cd['rank']}) ---\n"
            f"Price: ${cd['current_price']:,.6f}, "
            f"Vol24h: ${cd['coin_info'].get('volume_24h_usdt', 0):,.0f}, "
            f"Change24h: {cd['coin_info'].get('price_change_24h_pct', 0):.2f}%\n"
            f"Trend: {summary.get('current_trend','?')}, "
            f"Zone: {summary.get('premium_discount_zone','?')}, "
            f"Fib: {summary.get('fib_level',0):.3f}, "
            f"Near OTE: {summary.get('near_ote', False)}\n"
            f"OBs: {summary.get('active_bullish_obs',0)}B/{summary.get('active_bearish_obs',0)}S, "
            f"FVGs: {summary.get('active_bullish_fvgs',0)}B/{summary.get('active_bearish_fvgs',0)}S\n"
            f"BOS(30d): {summary.get('recent_bos_bullish',0)}B/{summary.get('recent_bos_bearish',0)}S, "
            f"CHoCH: {summary.get('recent_choch_count',0)}"
        )

    return f"""Analyze the following {len(coins_data)} coins. For EACH coin, output a JSON object.
Output a JSON array of objects.

{chr(10).join(coin_sections)}

For each coin: technical_score(-25~25), macro_quant_score(-25~25).
total_score = technical + macro_quant. long if >=10, short if <=-10, else skip.
Output JSON array only, no explanation. Include stop_loss_pct and take_profit_pct.
Each object must have a "symbol" field."""


def parse_batch_response(raw_response: str, symbols: list[str]) -> list[dict]:
    """배치 응답을 파싱하여 각 종목별 분석 결과를 저장합니다."""
    results = []
    try:
        json_match = re.search(r'\[[\s\S]*\]', raw_response)
        if json_match:
            parsed = json.loads(json_match.group())
            for item in parsed:
                symbol = item.get("symbol", "")
                if symbol:
                    result = _normalize_result(item, symbol)
                    save_analysis_json(symbol, result)
                    results.append(result)
                    logger.info(f"[{symbol}] Batch result: score={result['total_score']}, decision={result['decision']}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse batch response: {e}")
    return results


# ─── 내부 헬퍼 ─────────────────────────────────────────────

def _parse_analysis_response(raw_text: str, symbol: str) -> Optional[dict]:
    """Claude 응답에서 JSON을 추출하고 정규화합니다."""
    json_match = re.search(r'```json\s*([\s\S]*?)\s*```', raw_text)
    if json_match:
        json_str = json_match.group(1)
    else:
        json_match = re.search(r'\{[\s\S]*\}', raw_text)
        if json_match:
            json_str = json_match.group()
        else:
            logger.error(f"[{symbol}] No JSON found in response")
            return None
    try:
        data = json.loads(json_str)
        return _normalize_result(data, symbol)
    except json.JSONDecodeError as e:
        logger.error(f"[{symbol}] JSON parse error: {e}")
        return None


def _normalize_result(data: dict, symbol: str) -> dict:
    """분석 결과를 스키마에 맞게 정규화합니다."""
    tech = int(data.get("technical_score", 0))
    macro = int(data.get("macro_quant_score", 0))
    total = tech + macro

    tech = max(-25, min(25, tech))
    macro = max(-25, min(25, macro))
    total = max(-50, min(50, total))

    cfg = get_config()
    long_thresh = cfg["trading"]["long_threshold"]
    short_thresh = cfg["trading"]["short_threshold"]

    if total >= long_thresh:
        decision = "long"
    elif total <= short_thresh:
        decision = "short"
    else:
        decision = "skip"

    confidence = float(data.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    return {
        "symbol": symbol,
        "technical_score": tech,
        "technical_reasoning": str(data.get("technical_reasoning", "")),
        "macro_quant_score": macro,
        "macro_quant_reasoning": str(data.get("macro_quant_reasoning", "")),
        "total_score": total,
        "decision": decision,
        "confidence": confidence,
        "suggested_position_pct": 0.0,
        "stop_loss_pct": float(data.get("stop_loss_pct", 3.0)),
        "take_profit_pct": float(data.get("take_profit_pct", 6.0)),
        "analysis_skipped": False,
        "skip_reason": "",
    }
