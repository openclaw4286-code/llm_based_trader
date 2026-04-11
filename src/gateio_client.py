from __future__ import annotations
"""
Gate.io API 클라이언트 - 현물/선물 계정 정보, 주문, 시세 조회를 담당합니다.

Gate.io API v4를 사용합니다.
- 현물(Spot): 거래량 조회, 시세 조회
- 선물(Futures): 잔고, 포지션, 주문
"""
import time
import hashlib
import hmac
import json
from urllib.parse import urlencode
from typing import Optional

import requests

from src.utils import get_config, setup_logger

logger = setup_logger("gateio")

BASE_URL = "https://api.gateio.ws"
API_PREFIX = "/api/v4"


class GateIOClient:
    def __init__(self):
        cfg = get_config()
        self.api_key = cfg["gateio_api_key"]
        self.api_secret = cfg["gateio_api_secret"]
        self.settle = "usdt"  # USDT 선물

    # ─── 인증 ─────────────────────────────────────────────

    def _gen_sign(self, method: str, url: str, query_string: str = "", body: str = "") -> dict:
        """Gate.io API v4 서명을 생성합니다."""
        t = str(int(time.time()))
        hashed_body = hashlib.sha512(body.encode("utf-8")).hexdigest()
        sign_string = f"{method}\n{url}\n{query_string}\n{hashed_body}\n{t}"
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            sign_string.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        return {
            "KEY": self.api_key,
            "Timestamp": t,
            "SIGN": signature,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        auth: bool = True,
    ) -> dict | list:
        """API 요청을 보냅니다."""
        url = f"{API_PREFIX}{endpoint}"
        full_url = f"{BASE_URL}{url}"
        query_string = urlencode(params) if params else ""
        body_str = json.dumps(body) if body else ""

        headers = {}
        if auth:
            headers = self._gen_sign(method, url, query_string, body_str)
        else:
            headers = {"Content-Type": "application/json"}

        try:
            resp = requests.request(
                method,
                full_url,
                params=params,
                data=body_str if body else None,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"API HTTP error: {e} - {resp.text}")
            raise
        except Exception as e:
            logger.error(f"API request error: {e}")
            raise

    # ─── 현물 (Spot) - 공개 API ──────────────────────────

    def get_spot_tickers(self) -> list[dict]:
        """모든 현물 거래쌍의 티커 정보를 조회합니다."""
        return self._request("GET", "/spot/tickers", auth=False)

    def get_spot_candlesticks(
        self,
        currency_pair: str,
        interval: str = "1d",
        limit: Optional[int] = None,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
    ) -> list[list]:
        """
        현물 캔들스틱 데이터를 조회합니다.

        Gate.io 규칙: from+to를 동시에 주면 limit을 제외해야 합니다.

        Returns:
            [[timestamp, volume, close, high, low, open, ...], ...]
        """
        params = {
            "currency_pair": currency_pair,
            "interval": interval,
        }
        if from_ts and to_ts:
            params["from"] = from_ts
            params["to"] = to_ts
        elif from_ts:
            params["from"] = from_ts
            if limit:
                params["limit"] = limit
        else:
            params["limit"] = limit or 1000

        return self._request("GET", "/spot/candlesticks", params=params, auth=False)

    # ─── 선물 (Futures) ──────────────────────────────────

    def get_futures_account(self) -> dict:
        """선물 계정 잔고를 조회합니다."""
        return self._request("GET", f"/futures/{self.settle}/accounts")

    def get_futures_positions(self) -> list[dict]:
        """현재 보유 중인 선물 포지션을 조회합니다."""
        return self._request("GET", f"/futures/{self.settle}/positions")

    def get_futures_contracts(self) -> list[dict]:
        """사용 가능한 선물 계약 목록을 조회합니다."""
        return self._request("GET", f"/futures/{self.settle}/contracts", auth=False)

    def get_futures_tickers(self, contract: Optional[str] = None) -> list[dict]:
        """선물 티커 정보를 조회합니다."""
        params = {}
        if contract:
            params["contract"] = contract
        return self._request("GET", f"/futures/{self.settle}/tickers", params=params, auth=False)

    def get_futures_candlesticks(
        self,
        contract: str,
        interval: str = "1d",
        limit: Optional[int] = None,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
    ) -> list[dict]:
        """
        선물 캔들스틱 데이터를 조회합니다.

        Gate.io 규칙: from+to를 동시에 주면 limit을 제외해야 합니다.

        Returns:
            [{"t": timestamp, "v": volume, "c": close, "h": high, "l": low, "o": open}, ...]
        """
        params = {
            "contract": contract,
            "interval": interval,
        }
        if from_ts and to_ts:
            params["from"] = from_ts
            params["to"] = to_ts
        elif from_ts:
            params["from"] = from_ts
            if limit:
                params["limit"] = limit
        else:
            params["limit"] = limit or 1000

        return self._request(
            "GET", f"/futures/{self.settle}/candlesticks", params=params, auth=False
        )

    def create_futures_order(
        self,
        contract: str,
        size: int,
        price: float = 0,
        tif: str = "ioc",
        reduce_only: bool = False,
    ) -> dict:
        """
        선물 주문을 생성합니다.

        Args:
            contract: 계약명 (예: BTC_USDT)
            size: 계약 수량 (양수=롱, 음수=숏)
            price: 지정가 (0이면 시장가)
            tif: 주문 유형 (gtc, ioc, poc, fok)
            reduce_only: 포지션 축소만 허용

        Returns:
            주문 결과 딕셔너리
        """
        body = {
            "contract": contract,
            "size": size,
            "price": str(price),
            "tif": tif,
            "reduce_only": reduce_only,
        }

        # 시장가 주문일 때 price="0"으로 설정
        if price == 0:
            body["price"] = "0"
            body["tif"] = "ioc"

        return self._request("POST", f"/futures/{self.settle}/orders", body=body)

    def update_position_leverage(
        self, contract: str, leverage: int, cross_leverage_limit: int = 0
    ) -> dict:
        """포지션의 레버리지를 설정합니다."""
        params = {
            "contract": contract,
            "leverage": str(leverage),
        }
        if cross_leverage_limit > 0:
            params["cross_leverage_limit"] = str(cross_leverage_limit)

        return self._request("POST", f"/futures/{self.settle}/positions/{contract}/leverage", params=params)

    def update_position_margin(self, contract: str, margin_mode: str = "isolated") -> dict:
        """포지션의 마진 모드를 설정합니다."""
        params = {"contract": contract, "change": "0"}
        return self._request(
            "POST", f"/futures/{self.settle}/positions/{contract}/margin", params=params
        )

    def set_dual_mode(self, dual_mode: bool = False) -> dict:
        """듀얼 모드(헤지 모드) 설정합니다. False = 단방향 모드."""
        return self._request(
            "POST",
            f"/futures/{self.settle}/dual_mode",
            body={"dual_mode": dual_mode},
        )

    def get_futures_orders(self, contract: str, status: str = "open") -> list[dict]:
        """선물 주문 목록을 조회합니다."""
        params = {"contract": contract, "status": status}
        return self._request("GET", f"/futures/{self.settle}/orders", params=params)

    def cancel_futures_order(self, order_id: str) -> dict:
        """선물 주문을 취소합니다."""
        return self._request("DELETE", f"/futures/{self.settle}/orders/{order_id}")

    def cancel_all_futures_orders(self, contract: str) -> list[dict]:
        """특정 계약의 모든 미체결 주문을 취소합니다."""
        params = {"contract": contract}
        return self._request("DELETE", f"/futures/{self.settle}/orders", params=params)
