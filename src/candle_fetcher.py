"""
캔들 데이터 다운로더 - Gate.io에서 최대 5년치 일봉 데이터를 가져와 CSV로 저장합니다.

Gate.io API 제한:
- 현물 캔들: 한 번에 최대 1000개
- 선물 캔들: 한 번에 최대 2000개

5년 = ~1825일이므로 현물은 2번, 선물은 1번 호출로 충분합니다.
"""
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from src.gateio_client import GateIOClient
from src.config_loader import get_config
from src.logger import setup_logger

logger = setup_logger("candle_fetcher")

YEARS_TO_FETCH = 5
ONE_DAY_SEC = 86400


def fetch_daily_candles(symbol: str, years: int = YEARS_TO_FETCH) -> pd.DataFrame:
    """
    Gate.io에서 일봉 데이터를 가져옵니다.
    선물 API를 먼저 시도하고, 실패하면 현물 API로 폴백합니다.

    Args:
        symbol: 거래쌍 (예: BTC_USDT)
        years: 가져올 연수

    Returns:
        DataFrame with columns: [timestamp, open, high, low, close, volume]
    """
    client = GateIOClient()
    now = int(time.time())
    start_ts = now - (years * 365 * ONE_DAY_SEC)

    all_candles = []

    # 선물 API로 시도 (한 번에 2000개까지)
    try:
        all_candles = _fetch_futures_candles(client, symbol, start_ts, now)
        logger.info(f"[{symbol}] Fetched {len(all_candles)} candles from futures API")
    except Exception as e:
        logger.warning(f"[{symbol}] Futures API failed: {e}, trying spot API")
        try:
            all_candles = _fetch_spot_candles(client, symbol, start_ts, now)
            logger.info(f"[{symbol}] Fetched {len(all_candles)} candles from spot API")
        except Exception as e2:
            logger.error(f"[{symbol}] Both APIs failed: {e2}")
            return pd.DataFrame()

    if not all_candles:
        logger.warning(f"[{symbol}] No candle data retrieved")
        return pd.DataFrame()

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)

    # 숫자 타입 변환
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    logger.info(f"[{symbol}] Final: {len(df)} daily candles from {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    return df


def _fetch_futures_candles(client: GateIOClient, symbol: str, start_ts: int, end_ts: int) -> list:
    """선물 API에서 일봉 데이터를 페이지네이션으로 가져옵니다."""
    all_data = []
    current_start = start_ts

    while current_start < end_ts:
        candles = client.get_futures_candlesticks(
            contract=symbol,
            interval="1d",
            limit=2000,
            from_ts=current_start,
            to_ts=end_ts,
        )

        if not candles:
            break

        for c in candles:
            all_data.append([
                int(c["t"]),
                float(c["o"]),
                float(c["h"]),
                float(c["l"]),
                float(c["c"]),
                float(c.get("v", 0)),
            ])

        # 다음 페이지: 마지막 캔들 타임스탬프 + 1일
        last_ts = max(int(c["t"]) for c in candles)
        current_start = last_ts + ONE_DAY_SEC

        if len(candles) < 2000:
            break

        time.sleep(0.2)  # 레이트 리밋 방지

    return all_data


def _fetch_spot_candles(client: GateIOClient, symbol: str, start_ts: int, end_ts: int) -> list:
    """현물 API에서 일봉 데이터를 페이지네이션으로 가져옵니다."""
    all_data = []
    current_start = start_ts

    while current_start < end_ts:
        candles = client.get_spot_candlesticks(
            currency_pair=symbol,
            interval="1d",
            limit=1000,
            from_ts=current_start,
            to_ts=end_ts,
        )

        if not candles:
            break

        for c in candles:
            # 현물 캔들 형식: [timestamp, volume, close, high, low, open, is_closed]
            all_data.append([
                int(c[0]),
                float(c[5]),   # open
                float(c[3]),   # high
                float(c[4]),   # low
                float(c[2]),   # close
                float(c[1]),   # volume
            ])

        last_ts = max(int(c[0]) for c in candles)
        current_start = last_ts + ONE_DAY_SEC

        if len(candles) < 1000:
            break

        time.sleep(0.2)

    return all_data


def save_candles(symbol: str, df: pd.DataFrame) -> Path:
    """캔들 데이터를 CSV로 저장합니다."""
    cfg = get_config()
    candle_dir = Path(cfg["paths"]["candles"])
    candle_dir.mkdir(parents=True, exist_ok=True)

    filepath = candle_dir / f"{symbol}.csv"
    df.to_csv(filepath, index=False)
    logger.info(f"[{symbol}] Candles saved to {filepath}")
    return filepath


def load_candles(symbol: str) -> pd.DataFrame:
    """저장된 캔들 데이터를 CSV에서 로드합니다."""
    cfg = get_config()
    candle_dir = Path(cfg["paths"]["candles"])
    filepath = candle_dir / f"{symbol}.csv"

    if not filepath.exists():
        return pd.DataFrame()

    df = pd.read_csv(filepath, parse_dates=["timestamp"])
    return df
