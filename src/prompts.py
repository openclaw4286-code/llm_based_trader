from __future__ import annotations
"""
Claude Code 분석용 프롬프트 템플릿

Claude Code가 각 종목을 분석할 때 사용하는 프롬프트를 생성합니다.
분석 깊이(full/standard/quick)에 따라 프롬프트 크기를 조절하여 토큰을 절약합니다.
"""

SYSTEM_PROMPT = """You are a professional crypto trading analyst. You perform two types of analysis and output structured JSON.

SCORING RULES:
- Technical Score: -25 to +25 (based on ICT chart analysis)
- Macro/Quantitative Score: -25 to +25 (based on news, SNS, whitepaper, fundamentals)
- Total = Technical + Macro/Quant
- If total >= 10: decision = "long"
- If total <= -10: decision = "short"
- Otherwise: decision = "skip"

SCAM DETECTION (MANDATORY):
- Always assess if the coin is a potential scam
- Check for: fake team, plagiarized whitepaper, pump-and-dump patterns, rug pull risk, no real utility, suspicious tokenomics
- scam_score: -25 (definite scam) to +25 (completely safe)
- If scam_score <= -15: force decision = "skip" regardless of other scores

OUTPUT FORMAT (strict JSON, no markdown):
{
  "technical_score": <int -25 to 25>,
  "technical_reasoning": "<brief reasoning>",
  "macro_quant_score": <int -25 to 25>,
  "macro_quant_reasoning": "<brief reasoning>",
  "scam_score": <int -25 to 25>,
  "scam_reasoning": "<brief reasoning>",
  "total_score": <int>,
  "decision": "long" | "short" | "skip",
  "confidence": <float 0.0 to 1.0>,
  "stop_loss_pct": <float, suggested stop loss % from entry>,
  "take_profit_pct": <float, suggested take profit % from entry>
}"""


def build_full_analysis_prompt(
    symbol: str,
    ict_summary: dict,
    coin_info: dict,
    current_price: float,
) -> str:
    """풀 분석 프롬프트를 생성합니다 (efficiency_level 0~3)."""
    ict_data = _format_ict_summary(ict_summary)
    coin_data = _format_coin_info(coin_info, current_price)

    return f"""Analyze {symbol} for a trading decision.

== COIN INFO ==
{coin_data}

== ICT TECHNICAL ANALYSIS (Daily Chart) ==
{ict_data}

== YOUR TASK ==
1. TECHNICAL ANALYSIS (-25 to +25):
   - Evaluate market structure (BOS/CHoCH trend direction)
   - Assess order block proximity (is price near an active bullish/bearish OB?)
   - Check FVG status (unfilled gaps as potential targets/support)
   - Analyze liquidity levels (where is liquidity sitting? likely sweep targets?)
   - Determine premium/discount zone (buy in discount, sell in premium)
   - OTE zone alignment (is price in optimal trade entry range?)

2. MACRO/QUANTITATIVE ANALYSIS (-25 to +25):
   - Assess the project's fundamentals and real-world utility
   - Consider recent news sentiment and market narrative
   - Evaluate social media sentiment and community activity
   - Review tokenomics and supply dynamics
   - Consider broader crypto market conditions (BTC dominance, total market cap trend)
   - Assess on-chain metrics if applicable (TVL, active addresses, transaction volume)

3. SCAM DETECTION (MANDATORY):
   - Is the team doxxed and credible?
   - Is the whitepaper original with genuine technical merit?
   - Are there signs of pump-and-dump or rug pull?
   - Is the trading volume organic or potentially wash traded?
   - Does the token have real utility or is it purely speculative?

4. POSITION MANAGEMENT:
   - Suggest stop_loss_pct based on nearest support/OB level
   - Suggest take_profit_pct based on nearest resistance/liquidity level

Respond with ONLY the JSON object, no explanation."""


def build_standard_analysis_prompt(
    symbol: str,
    ict_summary: dict,
    coin_info: dict,
    current_price: float,
) -> str:
    """표준 분석 프롬프트를 생성합니다 (efficiency_level 4~6)."""
    summary = ict_summary.get("summary", {})
    pd_info = ict_summary.get("premium_discount", {})
    coin_data = _format_coin_info_brief(coin_info, current_price)

    return f"""Analyze {symbol} for trading. Be concise.

{coin_data}

ICT Summary: trend={summary.get('current_trend','?')}, zone={summary.get('premium_discount_zone','?')}, fib={summary.get('fib_level',0):.3f}
Active OBs: {summary.get('active_bullish_obs',0)} bullish, {summary.get('active_bearish_obs',0)} bearish
Active FVGs: {summary.get('active_bullish_fvgs',0)} bullish, {summary.get('active_bearish_fvgs',0)} bearish
Unswept liquidity: {summary.get('unswept_buy_side_liq',0)} buy-side, {summary.get('unswept_sell_side_liq',0)} sell-side
Near OTE: {summary.get('near_ote', False)}
BOS(30d): {summary.get('recent_bos_bullish',0)} bullish, {summary.get('recent_bos_bearish',0)} bearish
CHoCH(30d): {summary.get('recent_choch_count',0)}, last direction: {summary.get('last_choch_direction','none')}

Score technical(-25~25), macro/quant(-25~25), scam(-25~25). Check if scam. Output JSON only."""


def build_quick_analysis_prompt(
    symbol: str,
    ict_summary: dict,
    coin_info: dict,
    current_price: float,
) -> str:
    """빠른 분석 프롬프트를 생성합니다 (efficiency_level 7~10)."""
    summary = ict_summary.get("summary", {})
    coin_data = _format_coin_info_brief(coin_info, current_price)

    return f"""Quick analysis for {symbol}. {coin_data}
Trend: {summary.get('current_trend','?')}, Zone: {summary.get('premium_discount_zone','?')}, Fib: {summary.get('fib_level',0):.3f}, Near OTE: {summary.get('near_ote',False)}
Score technical(-25~25), macro/quant(-25~25), scam(-25~25). JSON only."""


# ─── 헬퍼 ─────────────────────────────────────────────────

def _format_ict_summary(ict_summary: dict) -> str:
    """ICT 요약을 텍스트로 포매팅합니다."""
    summary = ict_summary.get("summary", {})
    pd_info = ict_summary.get("premium_discount", {})
    lines = []

    lines.append(f"Current Trend: {summary.get('current_trend', 'unknown')}")
    lines.append(f"BOS (30d): {summary.get('recent_bos_bullish', 0)} bullish, {summary.get('recent_bos_bearish', 0)} bearish")
    lines.append(f"CHoCH (30d): {summary.get('recent_choch_count', 0)} (last: {summary.get('last_choch_direction', 'none')})")
    lines.append(f"Zone: {pd_info.get('zone', '?')} (Fib: {pd_info.get('fib_level', 0):.4f})")
    lines.append(f"Current Price: {pd_info.get('current_price', 0)}")
    lines.append(f"Swing Range: {pd_info.get('swing_low', 0)} ~ {pd_info.get('swing_high', 0)}")
    lines.append(f"OTE Zone: {pd_info.get('ote_bottom', 0)} ~ {pd_info.get('ote_top', 0)}")
    lines.append(f"Near OTE: {summary.get('near_ote', False)}")

    lines.append(f"\nActive Order Blocks: {summary.get('active_bullish_obs', 0)} bullish, {summary.get('active_bearish_obs', 0)} bearish")
    obs = ict_summary.get("active_order_blocks", [])
    for ob in obs[-10:]:  # 최근 10개만
        lines.append(f"  - {ob['type']} OB: {ob['bottom']:.6f} ~ {ob['top']:.6f}")

    lines.append(f"\nActive FVGs: {summary.get('active_bullish_fvgs', 0)} bullish, {summary.get('active_bearish_fvgs', 0)} bearish")
    fvgs = ict_summary.get("active_fvgs", [])
    for fvg in fvgs[-10:]:
        lines.append(f"  - {fvg['type']} FVG: {fvg['bottom']:.6f} ~ {fvg['top']:.6f}")

    lines.append(f"\nUnswept Liquidity: {summary.get('unswept_buy_side_liq', 0)} buy-side, {summary.get('unswept_sell_side_liq', 0)} sell-side")
    liqs = ict_summary.get("unswept_liquidity", [])
    for liq in liqs[-10:]:
        lines.append(f"  - {liq['type']}: {liq['price']:.6f} ({liq['touches']} touches)")

    lines.append(f"\nRecent Structure Changes:")
    structs = ict_summary.get("recent_structures", [])
    for s in structs[-5:]:
        lines.append(f"  - {s['type']} {s['direction']} at {s['price']:.6f}")

    return "\n".join(lines)


def _format_coin_info(coin_info: dict, current_price: float) -> str:
    """코인 정보를 텍스트로 포매팅합니다."""
    lines = [
        f"Symbol: {coin_info.get('symbol', '?')}",
        f"Current Price: ${current_price:,.6f}",
        f"24h Volume (USDT): ${coin_info.get('volume_24h_usdt', 0):,.0f}",
        f"24h Price Change: {coin_info.get('price_change_24h_pct', 0):.2f}%",
        f"Volume Rank: #{coin_info.get('rank', '?')}",
    ]
    return "\n".join(lines)


def _format_coin_info_brief(coin_info: dict, current_price: float) -> str:
    """코인 정보를 간결하게 포매팅합니다."""
    return (
        f"Price: ${current_price:,.6f}, "
        f"Vol24h: ${coin_info.get('volume_24h_usdt', 0):,.0f}, "
        f"Change24h: {coin_info.get('price_change_24h_pct', 0):.2f}%, "
        f"Rank: #{coin_info.get('rank', '?')}"
    )
