from __future__ import annotations
"""
Claude Code 분석 러너 - Claude API를 호출하여 각 종목을 분석하고 JSON을 생성합니다.

이 모듈은 두 가지 모드로 동작합니다:
  1. API 모드: Anthropic API를 직접 호출 (자동화용)
  2. Manual 모드: 프롬프트만 생성하고 사람이 Claude Code에 입력 (비용 절약)

Claude Code 세션에서 실행될 때는 step3_claude_analysis.py가
프롬프트를 stdout으로 출력하고, Claude Code가 응답을 JSON으로 저장합니다.
"""
import json
import re
import os
from typing import Optional

from src.config_loader import get_config
from src.prompts import (
    SYSTEM_PROMPT,
    build_full_analysis_prompt,
    build_standard_analysis_prompt,
    build_quick_analysis_prompt,
)
from src.efficiency import get_analysis_depth
from src.file_manager import save_analysis_json, get_session_id
from src.logger import setup_logger

logger = setup_logger("claude_analyzer")


def analyze_coin_api(
    symbol: str,
    ict_summary: dict,
    coin_info: dict,
    current_price: float,
    rank: int,
) -> Optional[dict]:
    """
    Anthropic API를 통해 종목을 분석합니다.

    Returns:
        분석 결과 딕셔너리 또는 None (실패 시)
    """
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed. Run: pip install anthropic")
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set")
        return None

    # 분석 깊이에 따른 프롬프트 선택
    depth = get_analysis_depth(rank)
    prompt = _build_prompt(depth, symbol, ict_summary, coin_info, current_price)

    logger.info(f"[{symbol}] Calling Claude API (depth={depth})...")

    try:
        client = anthropic.Anthropic(api_key=api_key)

        # 토큰 절약을 위한 max_tokens 조절
        max_tokens_map = {"full": 1024, "standard": 512, "quick": 256}
        max_tokens = max_tokens_map.get(depth, 512)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = response.content[0].text.strip()
        result = _parse_analysis_response(raw_text, symbol)

        if result:
            _apply_scam_override(result)
            save_analysis_json(symbol, result)
            logger.info(f"[{symbol}] Analysis complete: score={result['total_score']}, decision={result['decision']}")

        return result

    except Exception as e:
        logger.error(f"[{symbol}] Claude API error: {e}")
        return None


def generate_prompt_for_claude_code(
    symbol: str,
    ict_summary: dict,
    coin_info: dict,
    current_price: float,
    rank: int,
) -> str:
    """
    Claude Code 수동 실행용 프롬프트를 생성합니다.
    step3에서 이 프롬프트를 출력하면 Claude Code가 직접 분석합니다.

    Returns:
        프롬프트 문자열
    """
    depth = get_analysis_depth(rank)
    return _build_prompt(depth, symbol, ict_summary, coin_info, current_price)


def parse_and_save_response(symbol: str, raw_response: str) -> Optional[dict]:
    """
    Claude Code의 응답 텍스트를 파싱하여 JSON으로 저장합니다.

    Returns:
        파싱된 분석 결과 또는 None
    """
    result = _parse_analysis_response(raw_response, symbol)
    if result:
        _apply_scam_override(result)
        save_analysis_json(symbol, result)
        logger.info(f"[{symbol}] Saved: score={result['total_score']}, decision={result['decision']}")
    return result


def build_batch_prompt(coins_data: list[dict]) -> str:
    """
    여러 종목을 한 번에 분석하는 배치 프롬프트를 생성합니다.
    효율성이 높을 때 사용하여 API 호출 수를 줄입니다.

    Args:
        coins_data: [{"symbol": str, "ict_summary": dict, "coin_info": dict,
                       "current_price": float, "rank": int}, ...]

    Returns:
        배치 프롬프트 문자열
    """
    coin_sections = []
    for cd in coins_data:
        depth = get_analysis_depth(cd["rank"])
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

    symbols = [cd["symbol"] for cd in coins_data]

    return f"""Analyze the following {len(coins_data)} coins. For EACH coin, output a JSON object.
Output a JSON array of objects.

{chr(10).join(coin_sections)}

For each coin: technical_score(-25~25), macro_quant_score(-25~25), scam_score(-25~25).
Check scam. total_score = technical + macro_quant. long if >=10, short if <=-10, else skip.
Output JSON array only, no explanation. Include stop_loss_pct and take_profit_pct.
Each object must have a "symbol" field."""


def parse_batch_response(raw_response: str, symbols: list[str]) -> list[dict]:
    """
    배치 응답을 파싱하여 각 종목별 분석 결과를 저장합니다.

    Returns:
        파싱된 분석 결과 리스트
    """
    results = []

    # JSON 배열 추출
    try:
        json_match = re.search(r'\[[\s\S]*\]', raw_response)
        if json_match:
            parsed = json.loads(json_match.group())
            for item in parsed:
                symbol = item.get("symbol", "")
                if symbol:
                    result = _normalize_result(item, symbol)
                    _apply_scam_override(result)
                    save_analysis_json(symbol, result)
                    results.append(result)
                    logger.info(f"[{symbol}] Batch result: score={result['total_score']}, decision={result['decision']}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse batch response: {e}")

    return results


# ─── 내부 헬퍼 ─────────────────────────────────────────────

def _build_prompt(depth: str, symbol: str, ict_summary: dict, coin_info: dict, current_price: float) -> str:
    """분석 깊이에 맞는 프롬프트를 선택합니다."""
    if depth == "full":
        return build_full_analysis_prompt(symbol, ict_summary, coin_info, current_price)
    elif depth == "standard":
        return build_standard_analysis_prompt(symbol, ict_summary, coin_info, current_price)
    else:
        return build_quick_analysis_prompt(symbol, ict_summary, coin_info, current_price)


def _parse_analysis_response(raw_text: str, symbol: str) -> Optional[dict]:
    """Claude 응답에서 JSON을 추출하고 정규화합니다."""
    # JSON 블록 추출 (```json ... ``` 또는 { ... })
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
    scam = int(data.get("scam_score", 0))
    total = tech + macro

    # 점수 범위 클램핑
    tech = max(-25, min(25, tech))
    macro = max(-25, min(25, macro))
    scam = max(-25, min(25, scam))
    total = max(-50, min(50, total))

    # 결정 로직
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
        "scam_score": scam,
        "scam_reasoning": str(data.get("scam_reasoning", "")),
        "total_score": total,
        "decision": decision,
        "confidence": confidence,
        "suggested_position_pct": 0.0,  # position_sizing에서 나중에 계산
        "stop_loss_pct": float(data.get("stop_loss_pct", 3.0)),
        "take_profit_pct": float(data.get("take_profit_pct", 6.0)),
        "analysis_skipped": False,
        "skip_reason": "",
    }


def _apply_scam_override(result: dict):
    """스캠 점수가 임계값 이하면 강제 스킵합니다."""
    cfg = get_config()
    scam_cfg = cfg.get("scam_detection", {})

    if not scam_cfg.get("auto_skip_scam", True):
        return

    threshold = scam_cfg.get("scam_threshold", -15)
    if result.get("scam_score", 0) <= threshold:
        result["decision"] = "skip"
        result["skip_reason"] = f"scam detected (scam_score={result['scam_score']} <= {threshold})"
        logger.warning(f"[{result['symbol']}] SCAM OVERRIDE: {result['skip_reason']}")
