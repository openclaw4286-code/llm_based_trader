from __future__ import annotations
#!/usr/bin/env python3
"""
파이프라인 1단계: 데이터 수집
  1. Gate.io에서 거래량 상위 N개 종목 조회
  2. 각 종목의 5년치 일봉 데이터 다운로드
  3. 인기종목 리스트 및 캔들 데이터를 로컬에 저장

독립 실행 가능: 여러 번 실행해도 데이터를 덮어쓰므로 안전합니다.

사용법:
  python scripts/step1_fetch_data.py
"""
import sys
import time
from pathlib import Path

# 프로젝트 루트를 모듈 경로에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils import get_config
from src.top_coins import fetch_top_coins, save_top_coins
from src.candle_fetcher import fetch_daily_candles, save_candles
from src.utils import setup_logger

logger = setup_logger("step1")


def main():
    cfg = get_config()
    n = cfg["trading"]["top_n_coins"]

    logger.info(f"=== Step 1: Fetching top {n} coins and candle data ===")

    # 1. 인기종목 조회
    logger.info("Fetching top coins by 24h volume...")
    coins = fetch_top_coins(n)

    if not coins:
        logger.error("No coins fetched. Check API connection.")
        sys.exit(1)

    save_top_coins(coins)

    # 2. 각 종목의 일봉 데이터 다운로드
    logger.info(f"Downloading daily candles for {len(coins)} coins...")
    success_count = 0
    fail_count = 0

    for coin in coins:
        symbol = coin["symbol"]
        logger.info(f"[{coin['rank']}/{len(coins)}] Fetching {symbol}...")

        try:
            df = fetch_daily_candles(symbol)
            if not df.empty:
                save_candles(symbol, df)
                success_count += 1
            else:
                logger.warning(f"[{symbol}] Empty data, skipping")
                fail_count += 1
        except Exception as e:
            logger.error(f"[{symbol}] Failed: {e}")
            fail_count += 1

        # API 레이트 리밋 방지
        time.sleep(0.5)

    logger.info(f"=== Step 1 Complete: {success_count} success, {fail_count} failed ===")


if __name__ == "__main__":
    main()
