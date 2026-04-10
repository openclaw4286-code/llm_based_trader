from __future__ import annotations
#!/usr/bin/env python3
"""
파이프라인 3단계: Claude 분석

두 가지 실행 모드:
  1. --api     : Anthropic API를 직접 호출하여 자동 분석 (ANTHROPIC_API_KEY 필요)
  2. --manual  : 각 종목의 프롬프트를 파일로 출력 → Claude Code가 수동 분석

효율성 파라미터에 따라:
  - 불필요한 종목은 조기 스킵 (이전 스캠, 변동 없음 등)
  - 분석 깊이 조절 (full/standard/quick)
  - 높은 효율성에서는 배치 분석 (여러 종목 한 번에)

독립 실행 가능: 여러 번 실행해도 같은 세션의 JSON을 덮어쓰므로 안전합니다.

사용법:
  python scripts/step3_claude_analysis.py --api           # API 자동 분석
  python scripts/step3_claude_analysis.py --manual        # 프롬프트 출력
  python scripts/step3_claude_analysis.py --api --batch   # 배치 모드 (효율적)
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import get_config
from src.top_coins import load_top_coins
from src.file_manager import get_session_id, save_analysis_json, load_all_analysis_for_session
from src.claude_analyzer import (
    generate_prompt_for_claude_code,
    build_batch_prompt,
    parse_batch_response,
)
from src.position_sizing import calculate_position_sizes, calculate_cash_reserve
from src.gateio_client import GateIOClient
from src.logger import setup_logger

logger = setup_logger("step3")


def get_prev_session_id() -> str:
    """이전 세션 ID를 계산합니다 (6시간 전)."""
    from src.file_manager import SCHEDULE_HOURS
    now = datetime.now()
    prev = now - timedelta(hours=6)
    hour = prev.hour
    candidates = [h for h in SCHEDULE_HOURS if h <= hour]
    if candidates:
        schedule_hour = max(candidates)
        return prev.strftime(f"%Y%m%d_{schedule_hour:02d}")
    prev_day = prev - timedelta(days=1)
    return prev_day.strftime(f"%Y%m%d_{SCHEDULE_HOURS[-1]:02d}")


def run_api_mode(batch: bool = False):
    """API 모드: Anthropic API로 자동 분석합니다."""
    cfg = get_config()
    session_id = get_session_id()
    prev_session_id = get_prev_session_id()

    logger.info(f"=== Step 3: Claude Analysis - API Mode (session: {session_id}) ===")

    coins = load_top_coins(session_id)
    if not coins:
        logger.error("No top coins found. Run step1 and step2 first.")
        sys.exit(1)

    # 효율성 필터링
    coins_to_analyze = []
    skipped = []

    for coin in coins:
        should, reason = should_analyze(
            symbol=coin["symbol"],
            rank=coin["rank"],
            price_change_24h_pct=coin.get("price_change_24h_pct", 0),
            volume_24h_usdt=coin.get("volume_24h_usdt", 0),
            prev_session_id=prev_session_id,
        )

        if should:
            coins_to_analyze.append(coin)
        else:
            logger.info(f"[{coin['symbol']}] SKIPPED: {reason}")
            # 스킵된 종목은 스킵 사유와 함께 저장
            save_analysis_json(coin["symbol"], {
                "technical_score": 0,
                "technical_reasoning": "",
                "macro_quant_score": 0,
                "macro_quant_reasoning": "",
                "scam_score": 0,
                "scam_reasoning": "",
                "total_score": 0,
                "decision": "skip",
                "confidence": 0.0,
                "suggested_position_pct": 0.0,
                "stop_loss_pct": 0.0,
                "take_profit_pct": 0.0,
                "analysis_skipped": True,
                "skip_reason": reason,
            })
            skipped.append(coin["symbol"])

    logger.info(f"Analyzing {len(coins_to_analyze)} coins, skipped {len(skipped)}")

    # 배치 모드 또는 개별 모드
    if batch and len(coins_to_analyze) > 1:
        _run_batch_analysis(coins_to_analyze, session_id)
    else:
        _run_individual_analysis(coins_to_analyze, session_id)

    # 포지션 사이징 계산
    _calculate_positions(session_id)


def _run_individual_analysis(coins: list[dict], session_id: str):
    """각 종목을 개별적으로 분석합니다."""
    cfg = get_config()
    analysis_dir = Path(cfg["paths"]["analysis"])

    for coin in coins:
        symbol = coin["symbol"]

        # ICT 분석 요약 로드
        ict_path = analysis_dir / f"{session_id}_{symbol}_ict.json"
        if not ict_path.exists():
            logger.warning(f"[{symbol}] No ICT analysis found, skipping")
            continue

        with open(ict_path, "r", encoding="utf-8") as f:
            ict_summary = json.load(f)

        result = analyze_coin_api(
            symbol=symbol,
            ict_summary=ict_summary,
            coin_info=coin,
            current_price=coin.get("last_price", 0),
            rank=coin.get("rank", 99),
        )

        if result is None:
            logger.error(f"[{symbol}] Analysis failed")


def _run_batch_analysis(coins: list[dict], session_id: str):
    """여러 종목을 배치로 분석합니다 (API 호출 절약)."""
    import os
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic package not installed")
        return

    cfg = get_config()
    analysis_dir = Path(cfg["paths"]["analysis"])

    # 배치 데이터 준비
    batch_data = []
    for coin in coins:
        symbol = coin["symbol"]
        ict_path = analysis_dir / f"{session_id}_{symbol}_ict.json"
        if not ict_path.exists():
            continue

        with open(ict_path, "r", encoding="utf-8") as f:
            ict_summary = json.load(f)

        batch_data.append({
            "symbol": symbol,
            "ict_summary": ict_summary,
            "coin_info": coin,
            "current_price": coin.get("last_price", 0),
            "rank": coin.get("rank", 99),
        })

    if not batch_data:
        return

    # 5개씩 배치 처리 (프롬프트가 너무 길어지지 않도록)
    batch_size = 5
    from src.prompts import SYSTEM_PROMPT

    for i in range(0, len(batch_data), batch_size):
        chunk = batch_data[i:i + batch_size]
        symbols = [cd["symbol"] for cd in chunk]
        logger.info(f"Batch analyzing: {', '.join(symbols)}")

        prompt = build_batch_prompt(chunk)

        try:
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            raw_text = response.content[0].text.strip()
            results = parse_batch_response(raw_text, symbols)
            logger.info(f"Batch complete: {len(results)} results parsed")

        except Exception as e:
            logger.error(f"Batch analysis failed: {e}")
            # 폴백: 개별 분석
            logger.info("Falling back to individual analysis...")
            _run_individual_analysis(
                [c for c in coins if c["symbol"] in symbols],
                session_id,
            )


def _calculate_positions(session_id: str):
    """포지션 사이징을 계산하고 결과를 업데이트합니다."""
    analyses = load_all_analysis_for_session(session_id)
    if not analyses:
        logger.warning("No analyses found for position sizing")
        return

    # 잔고 조회
    try:
        client = GateIOClient()
        account = client.get_futures_account()
        balance = float(account.get("available", 0))
        logger.info(f"Available balance: ${balance:,.2f}")
    except Exception as e:
        logger.warning(f"Could not fetch balance: {e}, using 0")
        balance = 0.0

    if balance > 0:
        analyses = calculate_position_sizes(analyses, balance)
        cash_info = calculate_cash_reserve(analyses)
        logger.info(
            f"Position sizing: {cash_info['num_positions']} positions, "
            f"exposure={cash_info['total_exposure_pct']:.1f}%, "
            f"cash={cash_info['cash_reserve_pct']:.1f}% "
            f"({'OK' if cash_info['meets_minimum'] else 'BELOW MINIMUM'})"
        )

        # 업데이트된 분석 결과 저장
        for a in analyses:
            save_analysis_json(a["symbol"], a)


def run_manual_mode():
    """Manual 모드: Claude Code용 프롬프트 파일을 생성합니다."""
    cfg = get_config()
    session_id = get_session_id()
    prev_session_id = get_prev_session_id()
    analysis_dir = Path(cfg["paths"]["analysis"])

    logger.info(f"=== Step 3: Claude Analysis - Manual Mode (session: {session_id}) ===")

    coins = load_top_coins(session_id)
    if not coins:
        logger.error("No top coins found. Run step1 and step2 first.")
        sys.exit(1)

    # 프롬프트 저장 디렉토리
    prompts_dir = analysis_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    # 같은 세션의 이전 파일 정리 (재실행 시 옛 분석 결과가 남아있는 문제 방지)
    for old_prompt in prompts_dir.glob(f"{session_id}_*_prompt.txt"):
        old_prompt.unlink()
    for old_analysis in analysis_dir.glob(f"{session_id}_*.json"):
        # ICT JSON과 top_coins는 step2에서 생성한 것이므로 보존
        if old_analysis.name.endswith("_ict.json") or "_top_coins.json" in old_analysis.name:
            continue
        old_analysis.unlink()
        logger.debug(f"Cleaned old analysis: {old_analysis.name}")

    generated = 0
    for coin in coins:
        symbol = coin["symbol"]

        # ICT 요약 로드
        ict_path = analysis_dir / f"{session_id}_{symbol}_ict.json"
        if not ict_path.exists():
            logger.warning(f"[{symbol}] No ICT data")
            continue

        with open(ict_path, "r", encoding="utf-8") as f:
            ict_summary = json.load(f)

        prompt = generate_prompt_for_claude_code(
            symbol=symbol,
            ict_summary=ict_summary,
            coin_info=coin,
            current_price=coin.get("last_price", 0),
            rank=coin.get("rank", 99),
        )

        # 프롬프트를 파일로 저장
        prompt_path = prompts_dir / f"{session_id}_{symbol}_prompt.txt"
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)

        generated += 1

    # 종합 프롬프트 파일 (Claude Code에 한 번에 입력 가능)
    master_prompt_path = prompts_dir / f"{session_id}_master_prompt.txt"
    _generate_master_prompt(coins, session_id, analysis_dir, master_prompt_path)

    logger.info(f"=== Step 3 Manual Mode Complete: {generated} prompts generated ===")
    logger.info(f"Master prompt: {master_prompt_path}")


def _generate_master_prompt(coins: list, session_id: str, analysis_dir: Path, output_path: Path):
    """Claude Code에 입력할 종합 프롬프트를 생성합니다."""
    from src.prompts import SYSTEM_PROMPT

    sections = [SYSTEM_PROMPT, "\n\n===== ANALYZE THE FOLLOWING COINS =====\n"]

    for coin in coins:
        symbol = coin["symbol"]
        ict_path = analysis_dir / f"{session_id}_{symbol}_ict.json"
        if not ict_path.exists():
            continue

        prompt_path = analysis_dir / "prompts" / f"{session_id}_{symbol}_prompt.txt"
        if not prompt_path.exists():
            continue

        with open(prompt_path, "r", encoding="utf-8") as f:
            sections.append(f"\n\n{'='*60}\n{f.read()}")

    sections.append(f"\n\n{'='*60}")
    sections.append(
        "\nFor each coin above, output the analysis JSON. "
        "Output a JSON array where each element has a 'symbol' field."
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sections))


def main():
    parser = argparse.ArgumentParser(description="Step 3: Claude Analysis")
    parser.add_argument("--api", action="store_true", help="Use Anthropic API directly")
    parser.add_argument("--manual", action="store_true", help="Generate prompts for Claude Code")
    parser.add_argument("--batch", action="store_true", help="Use batch mode (with --api)")
    args = parser.parse_args()

    if args.api:
        run_api_mode(batch=args.batch)
    elif args.manual:
        run_manual_mode()
    else:
        # 기본: API 키가 있으면 API 모드, 없으면 Manual 모드
        import os
        if os.getenv("ANTHROPIC_API_KEY"):
            run_api_mode(batch=args.batch)
        else:
            run_manual_mode()


if __name__ == "__main__":
    main()
