#!/usr/bin/env python3
from __future__ import annotations
"""
파이프라인 3단계 (통합): Per-Coin 분석 + 주문

각 종목에 대해 순차적으로:
  1. 프롬프트 생성 (ICT + 뉴스 + 차트 이미지 경로)
  2. Claude Code CLI 1회 호출 (해당 종목만)
  3. 응답 파싱 → 분석 JSON 저장
  4. 포지션 사이징 계산
  5. Gate.io 주문 실행 (진입/유지/청산/반전)
  6. 다음 종목으로

장점: 분석과 주문이 종목별로 즉시 처리됨. 한 종목 실패가 다른 종목에 영향 없음.
"""
import sys
import json
import os
import subprocess
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import get_config
from src.top_coins import load_top_coins
from src.file_manager import (
    get_session_id,
    save_analysis_json,
    load_all_analysis_for_session,
    get_processed_sessions,
    mark_session_processed,
)
from src.claude_analyzer import generate_prompt_for_claude_code, parse_and_save_response, _normalize_result
from src.news_fetcher import fetch_news_for_symbols, format_news_for_prompt
from src.gateio_client import GateIOClient
from src.order_executor import OrderExecutor
from src.position_sizing import kelly_criterion
from src.logger import setup_logger

logger = setup_logger("step3_per_coin")


def call_claude_for_coin(prompt: str, claude_path: str = "claude") -> str:
    """
    단일 종목 프롬프트로 Claude Code CLI를 호출합니다.
    """
    try:
        result = subprocess.run(
            [
                claude_path,
                "--print",
                "--dangerously-skip-permissions",
                "--model", "claude-opus-4-6",
                "--output-format", "text",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,  # 5분 타임아웃
        )
        if result.returncode != 0:
            logger.error(f"Claude CLI failed (code {result.returncode}): {result.stderr[:500]}")
            return ""
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.error("Claude CLI timeout (>5min)")
        return ""
    except Exception as e:
        logger.error(f"Claude CLI error: {e}")
        return ""


def compute_position_pct(analysis: dict, running_exposure: float, max_exposure: float) -> float:
    """
    단일 종목의 포지션 비율 계산 (누적 노출 고려).
    Kelly Criterion + 제약.
    """
    cfg = get_config()
    ps_cfg = cfg["position_sizing"]

    kelly_frac = ps_cfg["kelly_fraction"]
    max_pos = ps_cfg["max_position_pct"]
    min_pos = ps_cfg["min_position_pct"]

    confidence = analysis.get("confidence", 0.5)
    total_score = analysis.get("total_score", 0)

    win_rate = 0.5 + (confidence - 0.5) * 0.3
    win_loss_ratio = 1.0 + abs(total_score) / 50.0 * 1.5

    raw_kelly = kelly_criterion(win_rate, win_loss_ratio)
    adjusted = raw_kelly * kelly_frac * 100.0

    position_pct = max(min_pos, min(max_pos, adjusted))

    # 전체 노출 한도 체크
    available = max_exposure - running_exposure
    if position_pct > available:
        if available < min_pos:
            return 0.0  # 여유 없음, 스킵
        position_pct = available

    return round(position_pct, 2)


def main():
    cfg = get_config()
    session_id = get_session_id()
    analysis_dir = Path(cfg["paths"]["analysis"])

    logger.info(f"=== Step 3 Per-Coin: Analysis & Order (session: {session_id}) ===")

    # 세션 중복 체크
    processed = get_processed_sessions()
    if session_id in processed:
        logger.warning(f"Session {session_id} already processed. Skipping.")
        return

    # 종목 로드
    coins = load_top_coins(session_id)
    if not coins:
        logger.error("No top coins found. Run step1 first.")
        sys.exit(1)

    # 이전 세션 파일 정리 (재실행 안전)
    prompts_dir = analysis_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    for old in prompts_dir.glob(f"{session_id}_*_prompt.txt"):
        old.unlink()
    for old in prompts_dir.glob(f"{session_id}_*_response.txt"):
        old.unlink()
    for old in analysis_dir.glob(f"{session_id}_*.json"):
        if old.name.endswith("_ict.json") or "_top_coins.json" in old.name:
            continue
        old.unlink()

    # 뉴스 일괄 수집 (한 번만)
    logger.info("Fetching RSS news...")
    symbols = [c["symbol"] for c in coins]
    news_by_symbol = fetch_news_for_symbols(symbols, hours=24)

    # Gate.io 클라이언트 + executor
    client = GateIOClient()
    executor = OrderExecutor()

    # 잔고 조회 (한 번만)
    try:
        account = client.get_futures_account()
        balance = float(account.get("available", 0))
    except Exception as e:
        logger.error(f"Balance fetch failed: {e}")
        sys.exit(1)
    logger.info(f"Available balance: ${balance:,.2f}")

    # 기존 포지션 일괄 조회
    existing_positions = executor._get_existing_positions()
    logger.info(f"Existing positions: {len(existing_positions)}")

    # 현재 누적 노출 계산 (기존 포지션이 이미 사용 중인 잔고 비율)
    running_exposure = 0.0
    for pos in existing_positions.values():
        margin = float(pos.get("margin", 0))
        if margin > 0 and balance > 0:
            running_exposure += (margin / balance) * 100
    running_exposure = round(running_exposure, 2)
    max_exposure = cfg["position_sizing"]["max_total_exposure_pct"]
    logger.info(f"Running exposure: {running_exposure:.1f}% / max {max_exposure}%")

    # Claude CLI 경로 확인
    claude_path = os.environ.get("CLAUDE_PATH", "claude")

    # 각 종목 처리
    total_processed = 0
    total_orders = 0

    for coin in coins:
        symbol = coin["symbol"]
        logger.info(f"\n{'='*60}")
        logger.info(f"[{coin['rank']}/{len(coins)}] Processing {symbol}")
        logger.info(f"{'='*60}")

        # ICT 요약 로드
        ict_path = analysis_dir / f"{symbol}_ict.json"
        if not ict_path.exists():
            logger.warning(f"[{symbol}] No ICT data, skipping")
            continue
        with open(ict_path, "r", encoding="utf-8") as f:
            ict_summary = json.load(f)

        # 뉴스
        news_items = news_by_symbol.get(symbol, [])
        news_text = format_news_for_prompt(news_items)

        # 차트 경로
        chart_abs = str((Path(cfg["paths"]["charts"]) / f"{symbol}.png").resolve())

        # 프롬프트 생성
        prompt = generate_prompt_for_claude_code(
            symbol=symbol,
            ict_summary=ict_summary,
            coin_info=coin,
            current_price=coin.get("last_price", 0),
            rank=coin.get("rank", 99),
            news_text=news_text,
            chart_path=chart_abs,
        )

        # 프롬프트 저장 (디버그용)
        prompt_file = prompts_dir / f"{session_id}_{symbol}_prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

        # Claude 호출
        logger.info(f"[{symbol}] Calling Claude Code...")
        start = time.time()
        raw_response = call_claude_for_coin(prompt, claude_path)
        elapsed = time.time() - start
        logger.info(f"[{symbol}] Claude responded in {elapsed:.1f}s ({len(raw_response)} bytes)")

        # 응답 저장 (디버그용)
        response_file = prompts_dir / f"{session_id}_{symbol}_response.txt"
        response_file.write_text(raw_response, encoding="utf-8")

        if not raw_response or len(raw_response) < 50:
            logger.error(f"[{symbol}] Empty/tiny response, skipping")
            continue

        # 응답 파싱
        result = parse_and_save_response(symbol, raw_response)
        if result is None:
            logger.error(f"[{symbol}] Parse failed, skipping")
            continue

        total_processed += 1
        decision = result.get("decision", "skip")
        score = result.get("total_score", 0)
        logger.info(f"[{symbol}] Decision: {decision}, score: {score}")

        # 포지션 사이징 (skip이 아니면)
        if decision in ("long", "short"):
            pct = compute_position_pct(result, running_exposure, max_exposure)
            result["suggested_position_pct"] = pct
            save_analysis_json(symbol, result)

            if pct <= 0:
                logger.warning(f"[{symbol}] Position pct = 0 (exposure cap), skipping order")
                continue

            logger.info(f"[{symbol}] Position: {pct:.2f}% (running exposure: {running_exposure:.1f}%)")

        # 주문 실행 (단일 종목)
        analysis_data = result
        existing = existing_positions.get(symbol)

        try:
            order_result = executor._process_single_coin(
                symbol=symbol,
                analysis=analysis_data,
                existing=existing,
                balance=balance,
                dry_run=False,
            )
            if order_result:
                status = order_result.get("status", "unknown")
                logger.info(f"[{symbol}] Order result: {status}")
                if status == "filled":
                    total_orders += 1
                    # 실제 진입 시 running_exposure 증가
                    if decision in ("long", "short"):
                        running_exposure += result.get("suggested_position_pct", 0)
        except Exception as e:
            logger.error(f"[{symbol}] Order execution failed: {e}")

    # 세션 마킹
    mark_session_processed(session_id)

    logger.info(f"\n{'='*60}")
    logger.info(f"COMPLETE: {total_processed} analyzed, {total_orders} new orders placed")
    logger.info(f"Final exposure: {running_exposure:.1f}%")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
