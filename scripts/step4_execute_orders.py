#!/usr/bin/env python3
"""
파이프라인 4단계: 주문 실행

분석 결과 JSON을 읽어 Gate.io 선물 주문을 실행합니다.

안전 장치:
  - 이미 처리된 세션은 자동 스킵 (중복 주문 방지)
  - --dry-run으로 시뮬레이션 가능
  - --close-all로 모든 포지션 비상 종료
  - 실행 전 잔고/포지션 요약 출력

독립 실행 가능: 여러 번 실행해도 같은 세션은 한 번만 주문합니다.

사용법:
  python scripts/step4_execute_orders.py              # 실제 주문
  python scripts/step4_execute_orders.py --dry-run     # 시뮬레이션
  python scripts/step4_execute_orders.py --close-all   # 모든 포지션 종료
  python scripts/step4_execute_orders.py --status       # 현재 상태 조회
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config_loader import get_config
from src.order_executor import OrderExecutor
from src.file_manager import (
    get_session_id,
    load_all_analysis_for_session,
    get_processed_sessions,
)
from src.logger import setup_logger

logger = setup_logger("step4")


def show_status():
    """현재 계정 상태와 포지션을 출력합니다."""
    executor = OrderExecutor()

    # 잔고
    balance = executor._get_balance()
    if balance is not None:
        print(f"\n{'='*50}")
        print(f"  Available Balance: ${balance:,.2f} USDT")
        print(f"{'='*50}")

    # 기존 포지션
    positions = executor._get_existing_positions()
    if positions:
        print(f"\n  Active Positions ({len(positions)}):")
        print(f"  {'Symbol':<15} {'Side':<8} {'Size':<10} {'Entry':<12} {'PnL':<12}")
        print(f"  {'-'*57}")
        for symbol, pos in positions.items():
            size = int(pos.get("size", 0))
            side = "LONG" if size > 0 else "SHORT"
            entry = float(pos.get("entry_price", 0))
            pnl = float(pos.get("unrealised_pnl", 0))
            pnl_str = f"${pnl:+,.2f}"
            print(f"  {symbol:<15} {side:<8} {abs(size):<10} ${entry:<11,.4f} {pnl_str}")
    else:
        print("\n  No active positions")

    # 처리된 세션
    processed = get_processed_sessions()
    if processed:
        print(f"\n  Processed Sessions (last 5):")
        for s in sorted(processed)[-5:]:
            print(f"    - {s}")

    # 현재 세션 분석 결과 요약
    session_id = get_session_id()
    analyses = load_all_analysis_for_session(session_id)
    if analyses:
        longs = [a for a in analyses if a.get("decision") == "long"]
        shorts = [a for a in analyses if a.get("decision") == "short"]
        skips = [a for a in analyses if a.get("decision") == "skip"]
        print(f"\n  Current Session ({session_id}) Analysis:")
        print(f"    Long:  {len(longs)} coins")
        for a in longs:
            print(f"      {a['symbol']}: score={a.get('total_score',0)}, {a.get('suggested_position_pct',0):.1f}%")
        print(f"    Short: {len(shorts)} coins")
        for a in shorts:
            print(f"      {a['symbol']}: score={a.get('total_score',0)}, {a.get('suggested_position_pct',0):.1f}%")
        print(f"    Skip:  {len(skips)} coins")

    already = session_id in processed
    print(f"\n  Session {session_id} processed: {'YES (will skip)' if already else 'NO (ready to execute)'}")
    print()


def execute_orders(dry_run: bool = False):
    """분석 결과 기반으로 주문을 실행합니다."""
    session_id = get_session_id()
    logger.info(f"=== Step 4: Order Execution (session: {session_id}, dry_run={dry_run}) ===")

    # 실행 전 상태 요약
    analyses = load_all_analysis_for_session(session_id)
    if not analyses:
        logger.error(f"No analyses for session {session_id}. Run step3 first.")
        sys.exit(1)

    tradeable = [a for a in analyses if a.get("decision") in ("long", "short")]
    logger.info(f"Tradeable: {len(tradeable)} / {len(analyses)} total")

    for a in tradeable:
        logger.info(
            f"  {a['symbol']}: {a['decision']} (score={a.get('total_score',0)}, "
            f"pos={a.get('suggested_position_pct',0):.1f}%, "
            f"SL={a.get('stop_loss_pct',0):.1f}%, TP={a.get('take_profit_pct',0):.1f}%)"
        )

    # 주문 실행
    executor = OrderExecutor()
    results = executor.execute_session(session_id=session_id, dry_run=dry_run)

    # 결과 요약
    if results:
        print(f"\n{'='*60}")
        print(f"  ORDER EXECUTION SUMMARY {'(DRY RUN)' if dry_run else ''}")
        print(f"{'='*60}")
        for r in results:
            status_emoji = "OK" if r["status"] not in ("failed",) else "FAIL"
            print(
                f"  [{status_emoji}] {r['symbol']}: {r['decision']} "
                f"{abs(r['size'])} contracts @ ${r.get('entry_price',0):,.4f} "
                f"(${r.get('position_usdt',0):,.2f})"
            )
        print(f"{'='*60}\n")
    else:
        logger.info("No orders executed (session already processed or no tradeable positions)")


def close_all():
    """비상 종료: 모든 포지션 닫기 + 미체결 주문 취소."""
    logger.warning("=== EMERGENCY: Closing all positions ===")
    executor = OrderExecutor()
    executor.cancel_all_pending_orders()
    executor.close_all_positions()
    logger.info("All positions closed and pending orders cancelled")


def main():
    parser = argparse.ArgumentParser(description="Step 4: Order Execution")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without placing real orders")
    parser.add_argument("--close-all", action="store_true", help="Emergency: close all positions")
    parser.add_argument("--status", action="store_true", help="Show current account status")
    parser.add_argument("--session", type=str, default=None, help="Override session ID")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.close_all:
        close_all()
    else:
        execute_orders(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
