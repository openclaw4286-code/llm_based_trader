from __future__ import annotations
"""
인기종목 수집 - Gate.io에서 시가총액(= 가격 × 거래량 proxy) 기준 상위 N개 USDT 선물 종목을 선별합니다.

Gate.io 선물 API는 직접적인 시총 필드를 제공하지 않으므로,
mark_price × volume_24h_base 를 시총 대용값(proxy)으로 사용합니다.
금/은 등 상품 선물도 포함됩니다.

스테이블코인 및 래핑 토큰은 제외합니다.
"""
import json
from datetime import datetime
from pathlib import Path

from src.gateio_client import GateIOClient
from src.config_loader import get_config
from src.file_manager import get_session_id
from src.logger import setup_logger

logger = setup_logger("top_coins")

# 거래 대상에서 제외할 종목 (스테이블코인, 래핑 토큰 등)
EXCLUDE_SYMBOLS = {
    "USDC_USDT", "TUSD_USDT", "BUSD_USDT", "DAI_USDT", "USDP_USDT",
    "FDUSD_USDT", "PYUSD_USDT", "USDD_USDT", "GUSD_USDT",
    "WBTC_USDT", "WETH_USDT", "STETH_USDT", "CBETH_USDT",
    "RETH_USDT", "WBETH_USDT",
}


def fetch_top_coins(n: int = 20) -> list[dict]:
    """
    시가총액 proxy 기준 상위 N개 USDT 선물 종목을 반환합니다.
    금(XAU), 은(XAG) 등 상품 선물도 포함됩니다.

    시총 proxy = mark_price × volume_24h_base (Gate.io가 직접 시총을 안 줘서 대용)
    대형 코인(BTC, ETH)은 가격 × 거래량이 압도적이라 자연스럽게 상위에 옵니다.

    Returns:
        [{"symbol": "BTC_USDT", "rank": 1, "volume_24h_usdt": ..., "market_cap_proxy": ..., ...}, ...]
    """
    client = GateIOClient()

    # 선물 티커에서 조회
    tickers = client.get_futures_tickers()

    usdt_tickers = []
    for t in tickers:
        contract = t.get("contract", "")
        if not contract.endswith("_USDT"):
            continue
        if contract in EXCLUDE_SYMBOLS:
            continue

        volume_quote = float(t.get("volume_24h_quote", 0) or 0)
        volume_base = float(t.get("volume_24h_base", 0) or 0)
        last_price = float(t.get("last", 0) or 0)
        mark_price = float(t.get("mark_price", 0) or 0)
        change_pct = float(t.get("change_percentage", 0) or 0)

        if last_price <= 0:
            continue

        # 시총 proxy: mark_price × base volume (가격 높고 거래 많은 종목 = 대형)
        price = mark_price if mark_price > 0 else last_price
        market_cap_proxy = price * volume_base if volume_base > 0 else volume_quote

        usdt_tickers.append({
            "symbol": contract,
            "volume_24h_usdt": volume_quote,
            "market_cap_proxy": market_cap_proxy,
            "last_price": last_price,
            "price_change_24h_pct": change_pct,
        })

    # 시총 proxy 기준 내림차순 정렬
    usdt_tickers.sort(key=lambda x: x["market_cap_proxy"], reverse=True)

    # 상위 N개 선택 및 순위 부여
    top = usdt_tickers[:n]
    for i, coin in enumerate(top):
        coin["rank"] = i + 1

    logger.info(f"Top {len(top)} coins by market cap proxy fetched")
    for coin in top[:5]:
        logger.info(f"  #{coin['rank']} {coin['symbol']}: mcap_proxy=${coin['market_cap_proxy']:,.0f}")

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
