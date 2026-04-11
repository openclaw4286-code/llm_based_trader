from __future__ import annotations
"""
Claude Code 분석용 프롬프트 템플릿

Claude Code가 각 종목을 분석할 때 사용하는 프롬프트를 생성합니다.
모든 종목을 매 세션마다 풀 분석합니다 (보유 종목 포함).
"""

SYSTEM_PROMPT = """You are a professional crypto trading analyst. You perform two types of analysis and output structured JSON.

SCORING RULES:
- Technical Score: -25 to +25 (based on ICT chart analysis)
- Macro/Quantitative Score: -25 to +25 (based on news, SNS, fundamentals)
- Total = Technical + Macro/Quant
- If total >= 10: decision = "long"
- If total <= -10: decision = "short"
- Otherwise: decision = "skip"

NOTE: Do NOT suggest stop_loss or take_profit. SL/TP is calculated separately from ICT structures.

OUTPUT FORMAT (strict JSON, no markdown):
{
  "technical_score": <int -25 to 25>,
  "technical_reasoning": "<brief reasoning>",
  "macro_quant_score": <int -25 to 25>,
  "macro_quant_reasoning": "<brief reasoning>",
  "total_score": <int>,
  "decision": "long" | "short" | "skip",
  "confidence": <float 0.0 to 1.0>
}"""


def build_full_analysis_prompt(
    symbol: str,
    ict_summary: dict,
    coin_info: dict,
    current_price: float,
    news_text: str = "",
    chart_path: str = "",
    econ_text: str = "",
) -> str:
    """풀 분석 프롬프트를 생성합니다."""
    ict_data = _format_ict_summary(ict_summary)
    coin_data = _format_coin_info(coin_info, current_price)

    news_section = ""
    if news_text and news_text != "(no recent news found)":
        news_section = f"\n== RECENT NEWS (last 24h, from RSS feeds) ==\n{news_text}\n"

    econ_section = ""
    if econ_text and econ_text != "(no high-impact economic events in the window)":
        econ_section = (
            f"\n== UPCOMING ECONOMIC CALENDAR (next 72h, high-impact only) ==\n"
            f"{econ_text}\n"
            f"(These are global macro events that can cause volatility "
            f"in crypto/risk assets. Consider proximity to these events.)\n"
        )

    chart_section = ""
    if chart_path:
        chart_section = (
            f"\n== ICT CHART IMAGE ==\n"
            f"Chart file: {chart_path}\n"
            f"READ THIS IMAGE using the Read tool before scoring. Visually verify OB, FVG, "
            f"Liquidity, BOS/CHoCH markers, and current price position on the chart. "
            f"Use the visual information together with the numerical ICT summary below.\n"
        )

    return f"""Analyze {symbol} for a trading decision.

== COIN INFO ==
{coin_data}
{chart_section}
== ICT TECHNICAL ANALYSIS (Daily Chart — numerical summary) ==
{ict_data}
{news_section}{econ_section}
== YOUR TASK ==
1. TECHNICAL ANALYSIS (-25 to +25):
   - FIRST: Read the chart image file (if provided above) and visually assess the structure
   - Cross-reference the visual patterns with the numerical ICT summary
   - Evaluate market structure (BOS/CHoCH trend direction)
   - Assess order block proximity (is price near an active bullish/bearish OB?)
   - Check FVG status (unfilled gaps as potential targets/support)
   - Analyze liquidity levels (where is liquidity sitting? likely sweep targets?)
   - Determine premium/discount zone (buy in discount, sell in premium)
   - OTE zone alignment (is price in optimal trade entry range?)

2. MACRO/QUANTITATIVE ANALYSIS (-25 to +25):
   - PRIORITIZE the RECENT NEWS section above if present (freshest signal)
   - CONSIDER UPCOMING ECONOMIC CALENDAR events (FOMC, CPI, NFP, etc.):
     * If a high-impact event is within 6 hours, REDUCE conviction (expect volatility)
     * Dollar-strengthening events (hawkish Fed) are bearish for crypto
     * Dollar-weakening events (dovish Fed, bad CPI) are bullish for crypto
   - Assess news sentiment and market narrative
   - Evaluate tokenomics and supply dynamics
   - Consider broader crypto market conditions (BTC dominance, total market cap trend)
   - Assess on-chain metrics if applicable (TVL, active addresses, transaction volume)

Respond with ONLY the JSON object, no explanation."""


# build_standard_analysis_prompt과 build_quick_analysis_prompt은
# build_full_analysis_prompt과 동일하게 사용 (efficiency 삭제됨)
build_standard_analysis_prompt = build_full_analysis_prompt
build_quick_analysis_prompt = build_full_analysis_prompt


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
    for ob in obs[-10:]:
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
        f"Rank: #{coin_info.get('rank', '?')}",
    ]
    return "\n".join(lines)
