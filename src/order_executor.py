from __future__ import annotations
"""
주문 실행 엔진 - 분석 결과 JSON을 읽고 Gate.io 선물 주문을 실행합니다.

안전 장치:
  1. 중복 주문 방지: processed_sessions.json으로 이미 처리한 세션 추적
  2. 주문 전 검증: 잔고, 포지션 한도, 최소 수량 등 확인
  3. 기존 포지션 관리: 방향 전환 시 기존 포지션 정리 후 신규 진입
  4. 독립 실행: 여러 번 실행해도 같은 세션은 한 번만 주문
"""
import json
import math
from typing import Optional
from pathlib import Path

from src.gateio_client import GateIOClient
from src.config_loader import get_config
from src.file_manager import (
    get_session_id,
    load_all_analysis_for_session,
    get_processed_sessions,
    mark_session_processed,
    save_order_result,
)
from src.order_validator import (
    validate_analysis,
    validate_order_prices,
    validate_slippage,
    validate_balance,
)
from src.logger import setup_logger

logger = setup_logger("order_executor")


class OrderExecutor:
    def __init__(self):
        self.client = GateIOClient()
        self.cfg = get_config()
        self.trading_cfg = self.cfg["trading"]

    def execute_session(self, session_id: Optional[str] = None, dry_run: bool = False) -> list[dict]:
        """
        세션의 분석 결과를 기반으로 주문을 실행합니다.

        Args:
            session_id: 세션 ID (None이면 현재 세션)
            dry_run: True면 실제 주문 없이 시뮬레이션

        Returns:
            실행된 주문 결과 리스트
        """
        if session_id is None:
            session_id = get_session_id()

        # 1. 중복 실행 체크
        processed = get_processed_sessions()
        if session_id in processed:
            logger.warning(f"Session {session_id} already processed. Skipping.")
            return []

        # 2. 분석 결과 로드
        analyses = load_all_analysis_for_session(session_id)
        if not analyses:
            logger.error(f"No analyses found for session {session_id}")
            return []

        logger.info(f"Session {session_id}: {len(analyses)} analyses loaded")

        # 3. 잔고 확인
        balance = self._get_balance()
        if balance is None:
            logger.error("Could not fetch balance. Aborting.")
            return []

        logger.info(f"Available balance: ${balance:,.2f} USDT")

        # 4. 기존 포지션 조회 (전체)
        existing_positions = self._get_existing_positions()
        logger.info(f"Existing positions: {len(existing_positions)}")

        # 5. 분석 결과를 symbol → analysis 맵으로
        analysis_by_symbol = {a["symbol"]: a for a in analyses}

        # 6. 처리할 모든 종목 = 분석된 종목 ∪ 기존 포지션 보유 종목
        # (보유 중인데 이번 분석에 없는 종목도 처리해야 함)
        all_symbols = set(analysis_by_symbol.keys()) | set(existing_positions.keys())

        order_results = []
        for symbol in sorted(all_symbols):
            analysis = analysis_by_symbol.get(symbol)
            existing = existing_positions.get(symbol)

            result = self._process_single_coin(
                symbol=symbol,
                analysis=analysis,
                existing=existing,
                balance=balance,
                dry_run=dry_run,
            )
            if result:
                order_results.append(result)

        # 6. 세션 처리 완료 기록
        if not dry_run:
            mark_session_processed(session_id)

        logger.info(f"Execution complete: {len(order_results)} orders placed")
        return order_results

    def _process_single_coin(
        self,
        symbol: str,
        analysis: Optional[dict],
        existing: Optional[dict],
        balance: float,
        dry_run: bool,
    ) -> Optional[dict]:
        """
        단일 종목을 처리합니다. 모든 경우의 수를 다룹니다:

        | 기존 상태       | 새 분석     | 동작                                  |
        |---------------|-----------|--------------------------------------|
        | 없음           | long/short | 미체결 정리 → 검증 → 신규 진입            |
        | 없음           | skip       | 미체결 정리만                            |
        | 없음           | (분석없음)  | 미체결 정리만                            |
        | 같은 방향 포지션 | 같은 방향   | 유지 (불필요한 거래 방지)                  |
        | 반대 방향 포지션 | 반대 방향   | 미체결 정리 → 청산 → 검증 → 신규 진입       |
        | 보유 포지션     | skip       | 미체결 정리 → 청산 (LLM이 더이상 추천 안함)  |
        | 보유 포지션     | (분석없음)  | 유지 (효율성 필터로 분석 안된 종목)          |
        """
        # 0. 기존 포지션 분석
        existing_size = int(existing.get("size", 0)) if existing else 0
        existing_side = "long" if existing_size > 0 else "short" if existing_size < 0 else None

        # 0a. 분석 없음 + 포지션 없음 → 미체결 잔여 주문만 정리
        if not analysis and not existing_side:
            self._cleanup_pending_orders(symbol, dry_run)
            return None

        # 0b. 분석 없음 + 포지션 있음 → 효율성 필터로 분석 스킵된 경우 = 유지
        if not analysis and existing_side:
            logger.info(f"[{symbol}] Holding {existing_side} position (no new analysis)")
            return None

        decision = analysis.get("decision", "skip")
        analysis_skipped = analysis.get("analysis_skipped", False)

        # 1. 분석이 skip이거나 효율성으로 스킵됨
        if decision == "skip" or analysis_skipped:
            if existing_side:
                # 보유 중인데 더이상 추천 안함 → 청산
                logger.info(f"[{symbol}] Closing {existing_side} position (analysis: skip)")
                if not dry_run:
                    self._cleanup_pending_orders(symbol, dry_run=False)
                    self._close_position(symbol, existing_size)
                return {
                    "session_id": get_session_id(),
                    "symbol": symbol,
                    "decision": "close",
                    "side": "sell" if existing_size > 0 else "buy",
                    "size": -existing_size,
                    "status": "closed_on_skip" if not dry_run else "dry_run_close",
                    "error": "",
                }
            else:
                # 보유도 없고 skip이면 미체결만 정리
                self._cleanup_pending_orders(symbol, dry_run)
                return None

        # 2. 분석 무결성 검증 (LLM 실수 방어)
        valid, reason = validate_analysis(analysis)
        if not valid:
            logger.error(f"[{symbol}] VALIDATION FAILED: {reason} - skipping order")
            return {
                "session_id": get_session_id(), "symbol": symbol,
                "decision": decision, "status": "validation_failed",
                "error": reason, "size": 0,
            }

        position_pct = analysis.get("suggested_position_pct", 0)
        if position_pct <= 0:
            logger.info(f"[{symbol}] Position size is 0%, skipping")
            return None

        # 3. 같은 방향 포지션 보유 → 유지 (효율적)
        if existing_side == decision:
            logger.info(f"[{symbol}] Already in {decision} position (size={existing_size}), holding")
            return None

        # 4. 반대 방향 포지션 → 미체결 정리 + 청산
        if existing_side and existing_side != decision:
            logger.info(f"[{symbol}] Reversing: closing {existing_side} → entering {decision}")
            if not dry_run:
                self._cleanup_pending_orders(symbol, dry_run=False)
                self._close_position(symbol, existing_size)
        else:
            # 5. 신규 진입 전에도 좀비 미체결 주문 정리
            self._cleanup_pending_orders(symbol, dry_run)

        stop_loss_pct = analysis.get("stop_loss_pct", 3.0)
        take_profit_pct = analysis.get("take_profit_pct", 6.0)
        logger.info(f"[{symbol}] Entering: {decision}, {position_pct:.2f}% of balance")

        # 계약 정보 조회
        contract_info = self._get_contract_info(symbol)
        if not contract_info:
            logger.error(f"[{symbol}] Could not get contract info")
            return None

        # 분석 시점 가격과 현재 시장 가격 모두 조회
        analysis_price = analysis.get("premium_discount", {}).get("current_price", 0) \
                       or analysis.get("entry_price_at_analysis", 0)
        current_price = 0.0
        try:
            tickers = self.client.get_futures_tickers(contract=symbol)
            if tickers:
                current_price = float(tickers[0].get("last", 0))
        except Exception as e:
            logger.warning(f"[{symbol}] Could not fetch live price: {e}")

        if current_price <= 0:
            current_price = analysis_price

        if current_price <= 0:
            logger.error(f"[{symbol}] Could not determine current price")
            return None

        # 슬리피지 검증: 분석 시점 가격과 현재 가격 차이가 너무 크면 거부
        ok, reason = validate_slippage(analysis_price, current_price)
        if not ok:
            logger.error(f"[{symbol}] SLIPPAGE REJECTED: {reason}")
            return {
                "session_id": get_session_id(), "symbol": symbol,
                "decision": decision, "status": "slippage_rejected",
                "error": reason, "size": 0,
            }

        position_usdt = balance * (position_pct / 100.0)
        leverage = self.trading_cfg["leverage"]
        quanto_multiplier = float(contract_info.get("quanto_multiplier", 1))

        # 계약 수량 계산: (투자금 * 레버리지) / (가격 * 계약단위)
        if quanto_multiplier > 0:
            size = int((position_usdt * leverage) / (current_price * quanto_multiplier))
        else:
            size = int((position_usdt * leverage) / current_price)

        min_size = int(contract_info.get("order_size_min", 1))
        if abs(size) < min_size:
            logger.warning(f"[{symbol}] Calculated size {size} below minimum {min_size}, using minimum")
            size = min_size

        # 숏이면 음수
        if decision == "short":
            size = -size

        # 손절/익절 가격 계산
        if decision == "long":
            stop_loss_price = current_price * (1 - stop_loss_pct / 100)
            take_profit_price = current_price * (1 + take_profit_pct / 100)
        else:
            stop_loss_price = current_price * (1 + stop_loss_pct / 100)
            take_profit_price = current_price * (1 - take_profit_pct / 100)

        # SL/TP 가격 방향성 검증 (LLM이 SL/TP를 뒤바꾸는 실수 차단)
        ok, reason = validate_order_prices(decision, current_price, stop_loss_price, take_profit_price)
        if not ok:
            logger.error(f"[{symbol}] PRICE VALIDATION FAILED: {reason}")
            return {
                "session_id": get_session_id(), "symbol": symbol,
                "decision": decision, "status": "price_validation_failed",
                "error": reason, "size": 0,
            }

        # 잔고 충분성 검증 (마진 = 포지션USDT, 레버리지 적용 전)
        required_margin = position_usdt
        ok, reason = validate_balance(required_margin, balance)
        if not ok:
            logger.error(f"[{symbol}] INSUFFICIENT BALANCE: {reason}")
            return {
                "session_id": get_session_id(), "symbol": symbol,
                "decision": decision, "status": "insufficient_balance",
                "error": reason, "size": 0,
            }

        order_info = {
            "session_id": get_session_id(),
            "symbol": symbol,
            "decision": decision,
            "side": "buy" if size > 0 else "sell",
            "size": size,
            "leverage": leverage,
            "entry_price": current_price,
            "stop_loss_price": round(stop_loss_price, 6),
            "take_profit_price": round(take_profit_price, 6),
            "position_usdt": round(position_usdt, 2),
            "position_pct": position_pct,
            "order_id": "",
            "status": "pending",
            "error": "",
        }

        if dry_run:
            order_info["status"] = "dry_run"
            logger.info(f"[{symbol}] DRY RUN: {decision} {abs(size)} contracts @ ${current_price:,.4f}")
            save_order_result(order_info)
            return order_info

        # 실제 주문 실행
        try:
            # 레버리지 설정
            self.client.update_position_leverage(symbol, leverage)

            # 시장가 주문
            order_result = self.client.create_futures_order(
                contract=symbol,
                size=size,
                price=0,  # 시장가
                tif="ioc",
            )

            order_info["order_id"] = str(order_result.get("id", ""))
            order_info["status"] = order_result.get("status", "unknown")
            order_info["entry_price"] = float(order_result.get("fill_price", current_price) or current_price)

            logger.info(
                f"[{symbol}] ORDER PLACED: {decision} {abs(size)} contracts, "
                f"order_id={order_info['order_id']}, status={order_info['status']}"
            )

            # 손절 주문 (조건부 주문)
            self._place_stop_loss(symbol, size, stop_loss_price)

            # 익절 주문 (조건부 주문)
            self._place_take_profit(symbol, size, take_profit_price)

        except Exception as e:
            order_info["status"] = "failed"
            order_info["error"] = str(e)
            logger.error(f"[{symbol}] ORDER FAILED: {e}")

        save_order_result(order_info)
        return order_info

    def _get_balance(self) -> Optional[float]:
        """선물 계정 사용 가능 잔고를 조회합니다."""
        try:
            account = self.client.get_futures_account()
            return float(account.get("available", 0))
        except Exception as e:
            logger.error(f"Balance fetch error: {e}")
            return None

    def _get_existing_positions(self) -> dict:
        """현재 보유 중인 포지션을 {symbol: position_info} 형태로 반환합니다."""
        try:
            positions = self.client.get_futures_positions()
            result = {}
            for pos in positions:
                contract = pos.get("contract", "")
                size = int(pos.get("size", 0))
                if size != 0:
                    result[contract] = pos
            return result
        except Exception as e:
            logger.error(f"Position fetch error: {e}")
            return {}

    def _get_contract_info(self, symbol: str) -> Optional[dict]:
        """선물 계약 정보를 조회합니다."""
        try:
            contracts = self.client.get_futures_contracts()
            for c in contracts:
                if c.get("name") == symbol:
                    return c
            return None
        except Exception as e:
            logger.error(f"[{symbol}] Contract info error: {e}")
            return None

    def _cleanup_pending_orders(self, symbol: str, dry_run: bool = False):
        """
        해당 종목의 좀비 미체결 주문(일반 + 트리거 SL/TP)을 모두 취소합니다.
        세션마다 호출되어 잔여 주문이 누적되는 것을 방지합니다.
        """
        if dry_run:
            return
        try:
            # 일반 미체결 주문 취소
            self.client.cancel_all_futures_orders(symbol)
        except Exception as e:
            logger.debug(f"[{symbol}] No pending orders or cancel failed: {e}")

        # 트리거(조건부) 주문도 취소
        try:
            self.client._request(
                "DELETE",
                f"/futures/{self.client.settle}/price_orders",
                params={"contract": symbol},
            )
        except Exception as e:
            logger.debug(f"[{symbol}] No price-triggered orders or cancel failed: {e}")

    def _close_position(self, symbol: str, current_size: int):
        """기존 포지션을 닫습니다."""
        try:
            # 반대 방향으로 같은 수량 주문
            close_size = -current_size
            self.client.create_futures_order(
                contract=symbol,
                size=close_size,
                price=0,
                tif="ioc",
                reduce_only=True,
            )
            logger.info(f"[{symbol}] Position closed (size={current_size})")
        except Exception as e:
            logger.error(f"[{symbol}] Close position error: {e}")

    def _place_stop_loss(self, symbol: str, entry_size: int, stop_price: float):
        """손절 주문을 설정합니다."""
        try:
            # Gate.io price triggered order를 사용
            close_size = -entry_size
            trigger_body = {
                "initial": {
                    "contract": symbol,
                    "size": close_size,
                    "price": "0",  # 시장가로 실행
                    "tif": "ioc",
                    "reduce_only": True,
                },
                "trigger": {
                    "strategy_type": 0,  # by price
                    "price_type": 0,  # latest deal price
                    "price": str(stop_price),
                    "rule": 2 if entry_size > 0 else 1,  # 1: >=, 2: <=
                },
            }
            self.client._request(
                "POST",
                f"/futures/{self.client.settle}/price_orders",
                body=trigger_body,
            )
            logger.info(f"[{symbol}] Stop loss set at ${stop_price:,.4f}")
        except Exception as e:
            logger.warning(f"[{symbol}] Stop loss order failed: {e}")

    def _place_take_profit(self, symbol: str, entry_size: int, tp_price: float):
        """익절 주문을 설정합니다."""
        try:
            close_size = -entry_size
            trigger_body = {
                "initial": {
                    "contract": symbol,
                    "size": close_size,
                    "price": "0",
                    "tif": "ioc",
                    "reduce_only": True,
                },
                "trigger": {
                    "strategy_type": 0,
                    "price_type": 0,
                    "price": str(tp_price),
                    "rule": 1 if entry_size > 0 else 2,  # 롱: >= tp, 숏: <= tp
                },
            }
            self.client._request(
                "POST",
                f"/futures/{self.client.settle}/price_orders",
                body=trigger_body,
            )
            logger.info(f"[{symbol}] Take profit set at ${tp_price:,.4f}")
        except Exception as e:
            logger.warning(f"[{symbol}] Take profit order failed: {e}")

    def close_all_positions(self):
        """모든 포지션을 닫습니다 (비상용)."""
        positions = self._get_existing_positions()
        for symbol, pos in positions.items():
            size = int(pos.get("size", 0))
            if size != 0:
                logger.info(f"[{symbol}] Emergency closing position (size={size})")
                self._close_position(symbol, size)

    def cancel_all_pending_orders(self):
        """모든 미체결 주문을 취소합니다 (비상용)."""
        positions = self._get_existing_positions()
        for symbol in positions:
            try:
                self.client.cancel_all_futures_orders(symbol)
                logger.info(f"[{symbol}] All pending orders cancelled")
            except Exception as e:
                logger.error(f"[{symbol}] Cancel orders error: {e}")
