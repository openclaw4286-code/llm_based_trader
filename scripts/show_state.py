#!/usr/bin/env python3
"""
Gate.io 현재 상태 조회 - 잔고, 포지션, 미체결 주문, 조건부(SL/TP) 주문을 모두 출력합니다.

주문 없이 조회만 합니다. 언제 실행해도 안전합니다.

사용법:
  python scripts/show_state.py
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gateio_client import GateIOClient


def main():
    c = GateIOClient()

    print("\n" + "=" * 70)
    print("  Gate.io Account State")
    print("=" * 70)

    # ─── 1. 잔고 ──────────────────────────────────────
    try:
        acc = c.get_futures_account()
        total = float(acc.get("total", 0))
        available = float(acc.get("available", 0))
        unrealised = float(acc.get("unrealised_pnl", 0))
        position_margin = float(acc.get("position_margin", 0))
        order_margin = float(acc.get("order_margin", 0))

        print(f"\n[ BALANCE ]")
        print(f"  Total:            ${total:>12,.4f} USDT")
        print(f"  Available:        ${available:>12,.4f} USDT")
        print(f"  Position Margin:  ${position_margin:>12,.4f} USDT")
        print(f"  Order Margin:     ${order_margin:>12,.4f} USDT")
        print(f"  Unrealised PnL:   ${unrealised:>12,.4f} USDT")
    except Exception as e:
        print(f"\n[ BALANCE ] ERROR: {e}")

    # ─── 2. 포지션 ────────────────────────────────────
    try:
        positions = c.get_futures_positions()
        active = [p for p in positions if int(p.get("size", 0)) != 0]

        print(f"\n[ POSITIONS ] ({len(active)} active)")
        if active:
            print(f"  {'Symbol':<15} {'Side':<6} {'Size':>8} {'Entry':>14} {'Mark':>14} {'PnL':>12} {'Lev':>5}")
            print(f"  {'-'*76}")
            for p in active:
                symbol = p.get("contract", "?")
                size = int(p.get("size", 0))
                side = "LONG" if size > 0 else "SHORT"
                entry = float(p.get("entry_price", 0) or 0)
                mark = float(p.get("mark_price", 0) or 0)
                pnl = float(p.get("unrealised_pnl", 0) or 0)
                lev = p.get("leverage", "?")
                print(f"  {symbol:<15} {side:<6} {abs(size):>8} ${entry:>13,.6f} ${mark:>13,.6f} ${pnl:>+11,.4f} {lev:>4}x")
        else:
            print("  (none)")
    except Exception as e:
        print(f"\n[ POSITIONS ] ERROR: {e}")

    # ─── 3. 미체결 일반 주문 ────────────────────────────
    try:
        orders = c._request("GET", "/futures/usdt/orders", params={"status": "open"})
        print(f"\n[ OPEN ORDERS ] ({len(orders)} pending)")
        if orders:
            print(f"  {'Symbol':<15} {'Size':>8} {'Price':>14} {'TIF':<6} {'ReduceOnly':<12} {'OrderID':<20}")
            print(f"  {'-'*80}")
            for o in orders:
                print(f"  {o.get('contract',''):<15} {str(o.get('size','')):>8} {str(o.get('price','')):>14} {str(o.get('tif','')):<6} {str(o.get('is_reduce_only', False)):<12} {str(o.get('id','')):<20}")
        else:
            print("  (none)")
    except Exception as e:
        print(f"\n[ OPEN ORDERS ] ERROR: {e}")

    # ─── 4. 조건부(트리거) 주문 ──────────────────────────
    try:
        triggers = c._request("GET", "/futures/usdt/price_orders", params={"status": "open"})
        print(f"\n[ TRIGGER ORDERS (SL/TP) ] ({len(triggers)} pending)")
        if triggers:
            print(f"  {'Symbol':<15} {'Dir':<8} {'Size':>8} {'TriggerPx':>14} {'Rule':<12} {'ReduceOnly':<12} {'ID':<12}")
            print(f"  {'-'*85}")
            for t in triggers:
                init = t.get("initial", {}) or {}
                trig = t.get("trigger", {}) or {}
                symbol = init.get("contract", "?")
                size = init.get("size", 0)
                tprice = trig.get("price", "?")
                rule_n = int(trig.get("rule", 0))
                rule_str = "price >=" if rule_n == 1 else "price <=" if rule_n == 2 else f"rule={rule_n}"
                size_int = int(size) if size else 0
                direction = "CLOSE_LONG" if size_int < 0 else "CLOSE_SHORT" if size_int > 0 else "?"
                reduce_only = init.get("reduce_only", False)
                oid = t.get("id", "?")
                print(f"  {symbol:<15} {direction:<8} {str(size):>8} {str(tprice):>14} {rule_str:<12} {str(reduce_only):<12} {str(oid):<12}")

            # 심볼별 트리거 개수 집계 (좀비 주문 탐지용)
            from collections import Counter
            symbol_counts = Counter(t.get("initial", {}).get("contract", "?") for t in triggers)
            multi = {s: n for s, n in symbol_counts.items() if n >= 2}
            if multi:
                print(f"\n  [!] Multiple triggers per symbol (possible zombies):")
                for s, n in multi.items():
                    print(f"      {s}: {n} triggers")
        else:
            print("  (none)")
    except Exception as e:
        print(f"\n[ TRIGGER ORDERS ] ERROR: {e}")

    print()


if __name__ == "__main__":
    main()
