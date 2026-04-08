#!/usr/bin/env python3
"""
주문 없이 시뮬레이션 테스트
==========================
실제 주문 API를 호출하지 않고, 각 종목이 어떤 주문으로 나갈지 미리 계산해서 출력합니다.
validate_analysis, validate_order_prices, validate_slippage 등의 검증도 모두 통과 여부 표시.

사용법:
  python scripts/test_orders.py
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gateio_client import GateIOClient
from src.config_loader import get_config
from src.file_manager import get_session_id, load_all_analysis_for_session
from src.order_validator import (
    validate_analysis,
    validate_order_prices,
    validate_slippage,
    validate_balance,
)
from src.logger import setup_logger

logger = setup_logger("test_orders")


def main():
    cfg = get_config()
    trading_cfg = cfg["trading"]
    leverage = trading_cfg["leverage"]

    session_id = get_session_id()
    print(f"\n{'='*70}")
    print(f"  TEST ORDERS (no real API calls) - Session: {session_id}")
    print(f"{'='*70}\n")

    # 분석 결과 로드
    analyses = load_all_analysis_for_session(session_id)
    if not analyses:
        print("ERROR: No analyses found. Run step3/parse_claude_response first.")
        sys.exit(1)

    tradeable = [a for a in analyses if a.get("decision") in ("long", "short")]
    print(f"Loaded {len(analyses)} analyses, {len(tradeable)} tradeable\n")

    # Gate.io 연결
    client = GateIOClient()

    # 잔고 조회
    try:
        account = client.get_futures_account()
        balance = float(account.get("available", 0))
    except Exception as e:
        print(f"ERROR: balance fetch failed: {e}")
        sys.exit(1)

    print(f"Available Balance: ${balance:,.2f} USDT")
    print(f"Leverage: {leverage}x")
    print(f"{'─'*70}\n")

    # 계약 정보 캐시 (한 번에 조회)
    try:
        all_contracts = client.get_futures_contracts()
        contract_map = {c["name"]: c for c in all_contracts}
    except Exception as e:
        print(f"ERROR: contract list fetch failed: {e}")
        sys.exit(1)

    # 기존 포지션 조회
    try:
        positions = client.get_futures_positions()
        existing_positions = {
            p["contract"]: p for p in positions if int(p.get("size", 0)) != 0
        }
    except Exception as e:
        print(f"WARNING: positions fetch failed: {e}")
        existing_positions = {}

    print(f"Existing positions: {len(existing_positions)}\n")
    if existing_positions:
        for sym, pos in existing_positions.items():
            size = int(pos.get("size", 0))
            side = "LONG" if size > 0 else "SHORT"
            print(f"  {sym}: {side} {abs(size)}")
        print()

    # 각 tradeable 종목 시뮬레이션
    print(f"{'─'*70}")
    print(f"{'SYMBOL':<15} {'DIR':<6} {'POS%':>6} {'SIZE':>8} {'PRICE':>12} {'NOTIONAL':>10} {'STATUS':<30}")
    print(f"{'─'*70}")

    summary = {"ok": 0, "validation_failed": 0, "insufficient_balance": 0, "price_failed": 0, "contract_failed": 0}

    for a in tradeable:
        symbol = a["symbol"]
        decision = a["decision"]
        position_pct = a.get("suggested_position_pct", 0)
        sl_pct = a.get("stop_loss_pct", 3.0)
        tp_pct = a.get("take_profit_pct", 6.0)

        # 1. 분석 검증
        valid, reason = validate_analysis(a)
        if not valid:
            _print_row(symbol, decision, position_pct, "-", "-", "-", f"VALIDATION FAIL: {reason[:40]}")
            summary["validation_failed"] += 1
            continue

        if position_pct <= 0:
            _print_row(symbol, decision, position_pct, "-", "-", "-", "SKIP: pos=0%")
            continue

        # 2. 계약 정보
        contract_info = contract_map.get(symbol)
        if not contract_info:
            _print_row(symbol, decision, position_pct, "-", "-", "-", "NO CONTRACT INFO")
            summary["contract_failed"] += 1
            continue

        # 3. 현재 가격
        try:
            tickers = client.get_futures_tickers(contract=symbol)
            current_price = float(tickers[0].get("last", 0)) if tickers else 0
        except Exception as e:
            _print_row(symbol, decision, position_pct, "-", "-", "-", f"PRICE FAIL: {e}"[:40])
            summary["price_failed"] += 1
            continue

        if current_price <= 0:
            _print_row(symbol, decision, position_pct, "-", "-", "-", "PRICE=0")
            summary["price_failed"] += 1
            continue

        # 4. 슬리피지 체크
        analysis_price = a.get("premium_discount", {}).get("current_price", 0) or current_price
        ok, reason = validate_slippage(analysis_price, current_price)
        if not ok:
            _print_row(symbol, decision, position_pct, "-", f"${current_price:.4f}", "-", f"SLIPPAGE: {reason[:30]}")
            continue

        # 5. 수량 계산
        position_usdt = balance * (position_pct / 100.0)
        quanto_multiplier = float(contract_info.get("quanto_multiplier", 1) or 1)

        notional = position_usdt * leverage
        if quanto_multiplier > 0:
            raw_size = notional / (current_price * quanto_multiplier)
        else:
            raw_size = notional / current_price

        size = int(raw_size)
        min_size = int(contract_info.get("order_size_min", 1))
        size_rounded_to_min = False

        if abs(size) < min_size:
            size = min_size
            size_rounded_to_min = True

        if decision == "short":
            size = -size

        # 6. SL/TP 가격 계산
        if decision == "long":
            sl_price = current_price * (1 - sl_pct / 100)
            tp_price = current_price * (1 + tp_pct / 100)
        else:
            sl_price = current_price * (1 + sl_pct / 100)
            tp_price = current_price * (1 - tp_pct / 100)

        # 7. SL/TP 방향 검증
        ok, reason = validate_order_prices(decision, current_price, sl_price, tp_price)
        if not ok:
            _print_row(symbol, decision, position_pct, str(size), f"${current_price:.4f}", f"${notional:.2f}", f"PRICE VALID FAIL")
            summary["validation_failed"] += 1
            continue

        # 8. 잔고 체크
        required_margin = position_usdt
        ok, reason = validate_balance(required_margin, balance)
        if not ok:
            _print_row(symbol, decision, position_pct, str(size), f"${current_price:.4f}", f"${notional:.2f}", "INSUFFICIENT BAL")
            summary["insufficient_balance"] += 1
            continue

        # 최종 시뮬 출력
        status_msg = "READY"
        if size_rounded_to_min:
            status_msg += " (min_size)"
        if existing_positions.get(symbol):
            existing_size = int(existing_positions[symbol].get("size", 0))
            existing_side = "long" if existing_size > 0 else "short"
            if existing_side == decision:
                status_msg = f"HOLD (already {existing_side})"
            else:
                status_msg = f"REVERSE ({existing_side}→{decision})"

        _print_row(symbol, decision, position_pct, str(size), f"${current_price:.4f}", f"${notional:.2f}", status_msg)
        summary["ok"] += 1

        # SL/TP 세부
        print(f"{'':>15}  entry=${current_price:,.6f}  SL=${sl_price:,.6f} ({sl_pct}%)  TP=${tp_price:,.6f} ({tp_pct}%)  min={min_size}")

    print(f"{'─'*70}\n")
    print(f"SUMMARY:")
    print(f"  READY:                {summary['ok']}")
    print(f"  Validation failed:    {summary['validation_failed']}")
    print(f"  Insufficient balance: {summary['insufficient_balance']}")
    print(f"  Price fetch failed:   {summary['price_failed']}")
    print(f"  Contract missing:     {summary['contract_failed']}")
    print()


def _print_row(symbol, direction, pct, size, price, notional, status):
    print(f"{symbol:<15} {direction:<6} {pct:>5.1f}% {str(size):>8} {str(price):>12} {str(notional):>10}  {status}")


if __name__ == "__main__":
    main()
