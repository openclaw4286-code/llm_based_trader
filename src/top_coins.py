from __future__ import annotations
"""
인기종목 수집 - CoinGecko 기반 진짜 시가총액 상위 N개 + Gate.io 상품 선물(금/은/원유).

1. CoinGecko에서 시총 상위 크립토 조회 (순환공급량 × 가격 = 진짜 시총)
2. Gate.io 선물에 존재하는 종목만 필터링
3. 상품 선물(XAU=금, XAG=은, XAUT=테더골드, XTI=WTI, XBR=브렌트)은 항상 포함
4. 스테이블코인, 래핑 토큰은 제외

폴백: CoinGecko API 실패 시 이전 proxy 방식 사용.
"""
import json
import requests
from datetime import datetime
from pathlib import Path

from src.gateio_client import GateIOClient
from src.utils import get_config, get_session_id, setup_logger

logger = setup_logger("top_coins")

# 거래 대상에서 제외할 종목 (스테이블코인, 래핑 토큰 등)
EXCLUDE_SYMBOLS = {
    "USDC_USDT", "TUSD_USDT", "BUSD_USDT", "DAI_USDT", "USDP_USDT",
    "FDUSD_USDT", "PYUSD_USDT", "USDD_USDT", "GUSD_USDT",
    "WBTC_USDT", "WETH_USDT", "STETH_USDT", "CBETH_USDT",
    "RETH_USDT", "WBETH_USDT",
}

# CoinGecko 심볼 → Gate.io 심볼 매핑이 다른 경우 (보통 동일하지만 예외 처리)
SYMBOL_OVERRIDE = {
    # "iota": "IOTA_USDT",  # CoinGecko는 소문자, Gate.io는 대문자
}

# 항상 포함할 상품 선물 (크립토 시총과 무관)
ALWAYS_INCLUDE_COMMODITIES = [
    "XAU_USDT",   # 금
    "XAG_USDT",   # 은
]

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"


def _fetch_coingecko_top(n: int = 50) -> list[dict]:
    """
    CoinGecko에서 시총 상위 N개 코인을 가져옵니다.

    Returns:
        [{"symbol": "BTC", "market_cap": 1e12, "current_price": 70000, ...}, ...]
    """
    try:
        resp = requests.get(
            COINGECKO_URL,
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": n,
                "page": 1,
                "sparkline": "false",
            },
            timeout=15,
            headers={"User-Agent": "llm-trader/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"CoinGecko: fetched {len(data)} top coins by market cap")
        return data
    except Exception as e:
        logger.warning(f"CoinGecko fetch failed: {e}")
        return []


def fetch_top_coins(n: int = 20) -> list[dict]:
    """
    CoinGecko 시총 상위 N개 + 상품 선물(금/은)을 반환합니다.

    절차:
      1. CoinGecko에서 상위 50개 조회 (시총 순)
      2. 각 코인의 심볼을 Gate.io 선물 형식으로 변환 (예: BTC → BTC_USDT)
      3. Gate.io 선물에 존재하고 EXCLUDE 목록에 없는 것만 선택
      4. 상위 (n - 상품 개수)개 + 상품 선물을 합쳐서 반환

    Returns:
        [{"symbol": "BTC_USDT", "rank": 1, "market_cap_usd": ..., "last_price": ..., ...}, ...]
    """
    client = GateIOClient()

    # Gate.io 선물 티커 전체 조회 (가격/변동율 참고용)
    try:
        gate_tickers = client.get_futures_tickers()
    except Exception as e:
        logger.error(f"Gate.io futures tickers fetch failed: {e}")
        return []

    gate_info = {}  # contract → ticker data
    for t in gate_tickers:
        contract = t.get("contract", "")
        if contract.endswith("_USDT"):
            gate_info[contract] = t

    # 1) CoinGecko 시총 상위 조회
    coingecko_coins = _fetch_coingecko_top(n=100)  # 여유분 포함

    if not coingecko_coins:
        logger.warning("CoinGecko failed, falling back to volume proxy")
        return _fallback_volume_proxy(gate_tickers, n)

    # 2) 각 코인을 Gate.io 심볼로 매핑
    result = []
    seen_symbols = set()

    for cg in coingecko_coins:
        symbol_raw = (cg.get("symbol") or "").upper()
        if not symbol_raw:
            continue

        # Gate.io 선물 심볼 추정
        gate_symbol = SYMBOL_OVERRIDE.get(symbol_raw.lower(), f"{symbol_raw}_USDT")

        if gate_symbol in EXCLUDE_SYMBOLS or gate_symbol in seen_symbols:
            continue
        if gate_symbol not in gate_info:
            # Gate.io 선물에 해당 종목 없음
            continue

        # Gate.io에서 실시간 가격/변동 가져옴
        tk = gate_info[gate_symbol]
        last_price = float(tk.get("last", 0) or 0)
        volume_quote = float(tk.get("volume_24h_quote", 0) or 0)
        change_pct = float(tk.get("change_percentage", 0) or 0)

        if last_price <= 0:
            continue

        result.append({
            "symbol": gate_symbol,
            "market_cap_usd": float(cg.get("market_cap", 0) or 0),
            "last_price": last_price,
            "volume_24h_usdt": volume_quote,
            "price_change_24h_pct": change_pct,
            "coingecko_rank": cg.get("market_cap_rank", 999),
        })
        seen_symbols.add(gate_symbol)

    # 3) 상품 선물 추가 (항상 포함)
    commodities_added = 0
    for com_symbol in ALWAYS_INCLUDE_COMMODITIES:
        if com_symbol in seen_symbols or com_symbol not in gate_info:
            continue
        tk = gate_info[com_symbol]
        last_price = float(tk.get("last", 0) or 0)
        if last_price <= 0:
            continue
        result.append({
            "symbol": com_symbol,
            "market_cap_usd": 0,  # 상품은 시총 개념이 다름
            "last_price": last_price,
            "volume_24h_usdt": float(tk.get("volume_24h_quote", 0) or 0),
            "price_change_24h_pct": float(tk.get("change_percentage", 0) or 0),
            "coingecko_rank": 0,
            "is_commodity": True,
        })
        seen_symbols.add(com_symbol)
        commodities_added += 1

    # 4) 상품 제외하고 시총 순 정렬 → 상위 (n - commodities)개 선택 → 상품 끝에 추가
    cryptos = [c for c in result if not c.get("is_commodity")]
    commodities = [c for c in result if c.get("is_commodity")]

    cryptos.sort(key=lambda x: x["market_cap_usd"], reverse=True)
    selected_cryptos = cryptos[:max(1, n - len(commodities))]

    final = selected_cryptos + commodities
    for i, coin in enumerate(final):
        coin["rank"] = i + 1

    logger.info(f"Top {len(final)} coins selected (crypto: {len(selected_cryptos)}, commodities: {len(commodities)})")
    for coin in final[:8]:
        mcap = coin.get("market_cap_usd", 0)
        label = "commodity" if coin.get("is_commodity") else f"${mcap/1e9:.1f}B mcap"
        logger.info(f"  #{coin['rank']} {coin['symbol']}: {label}")

    return final


def _fallback_volume_proxy(tickers: list, n: int) -> list[dict]:
    """CoinGecko 실패 시 Gate.io 거래량 기반 폴백."""
    usdt_tickers = []
    for t in tickers:
        contract = t.get("contract", "")
        if not contract.endswith("_USDT") or contract in EXCLUDE_SYMBOLS:
            continue
        last_price = float(t.get("last", 0) or 0)
        volume_quote = float(t.get("volume_24h_quote", 0) or 0)
        if last_price <= 0:
            continue
        usdt_tickers.append({
            "symbol": contract,
            "volume_24h_usdt": volume_quote,
            "last_price": last_price,
            "price_change_24h_pct": float(t.get("change_percentage", 0) or 0),
            "market_cap_usd": 0,
        })
    usdt_tickers.sort(key=lambda x: x["volume_24h_usdt"], reverse=True)
    top = usdt_tickers[:n]
    for i, coin in enumerate(top):
        coin["rank"] = i + 1
    return top


def save_top_coins(coins: list[dict]) -> Path:
    """인기종목 리스트를 JSON으로 저장합니다."""
    cfg = get_config()
    analysis_dir = Path(cfg["paths"]["analysis"])
    analysis_dir.mkdir(parents=True, exist_ok=True)

    session_id = get_session_id()
    data = {
        "session_id": session_id,
        "fetched_at": datetime.now().isoformat(),
        "coins": coins,
    }

    filepath = analysis_dir / f"{session_id}_top_coins.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"Top coins saved to {filepath}")
    return filepath


def load_top_coins(session_id: str = None) -> list[dict]:
    """저장된 인기종목 리스트를 로드합니다."""
    cfg = get_config()
    analysis_dir = Path(cfg["paths"]["analysis"])

    if session_id is None:
        session_id = get_session_id()

    filepath = analysis_dir / f"{session_id}_top_coins.json"
    if not filepath.exists():
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("coins", [])
