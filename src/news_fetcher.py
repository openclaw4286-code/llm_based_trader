from __future__ import annotations
"""
뉴스 수집 - 암호화폐 뉴스 RSS 피드에서 최근 기사를 수집합니다.

각 종목별로 최근 24시간 내 뉴스 헤드라인을 매칭하여 Claude 프롬프트에 포함시킵니다.
"""
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.logger import setup_logger

logger = setup_logger("news_fetcher")

# 주요 암호화폐 RSS 피드
RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/.rss/full/",
    "https://cryptonews.com/news/feed/",
]

# 심볼 → 검색 키워드 매핑 (대문자 매칭)
# 심볼 코드 자체 + 프로젝트 이름을 함께 검색
SYMBOL_KEYWORDS = {
    "BTC_USDT": ["BTC", "Bitcoin"],
    "ETH_USDT": ["ETH", "Ethereum", "Ether"],
    "SOL_USDT": ["SOL", "Solana"],
    "XRP_USDT": ["XRP", "Ripple"],
    "DOGE_USDT": ["DOGE", "Dogecoin"],
    "ADA_USDT": ["ADA", "Cardano"],
    "AVAX_USDT": ["AVAX", "Avalanche"],
    "BNB_USDT": ["BNB", "Binance Coin"],
    "SUI_USDT": ["SUI"],
    "PEPE_USDT": ["PEPE"],
    "SIREN_USDT": ["SIREN"],
    "FARTCOIN_USDT": ["FARTCOIN", "Fartcoin"],
    "HYPE_USDT": ["HYPE", "Hyperliquid"],
    "ENA_USDT": ["ENA", "Ethena"],
    "ZEC_USDT": ["ZEC", "Zcash"],
    "TAO_USDT": ["TAO", "Bittensor"],
    "XAU_USDT": ["XAU", "Gold", "gold price"],
    "XAG_USDT": ["XAG", "Silver", "silver price"],
    "XAUT_USDT": ["XAUT", "Tether Gold"],
    "XTI_USDT": ["XTI", "WTI", "crude oil"],
    "XBR_USDT": ["XBR", "Brent"],
    "BR_USDT": ["BR"],
    "ARIA_USDT": ["ARIA"],
    "MAGMA_USDT": ["MAGMA"],
    "RAVE_USDT": ["RAVE"],
    "BULLA_USDT": ["BULLA"],
}

# RSS 캐시 (파이프라인 한 번 실행 동안은 재사용)
_rss_cache: Optional[list[dict]] = None
_rss_cache_time: float = 0
CACHE_TTL_SECONDS = 600  # 10분


def _get_keywords_for_symbol(symbol: str) -> list[str]:
    """심볼에 해당하는 검색 키워드를 반환합니다."""
    if symbol in SYMBOL_KEYWORDS:
        return SYMBOL_KEYWORDS[symbol]
    # 폴백: 심볼 코드에서 _USDT 제거
    base = symbol.replace("_USDT", "")
    return [base]


def fetch_all_news(hours: int = 24, max_per_feed: int = 30) -> list[dict]:
    """
    모든 RSS 피드에서 최근 N시간 내 기사를 수집합니다.

    Returns:
        [{"title": str, "link": str, "published": datetime, "source": str, "summary": str}, ...]
    """
    global _rss_cache, _rss_cache_time

    now = time.time()
    if _rss_cache is not None and (now - _rss_cache_time) < CACHE_TTL_SECONDS:
        logger.info(f"Using cached news ({len(_rss_cache)} articles)")
        return _rss_cache

    try:
        import feedparser
    except ImportError:
        logger.error("feedparser not installed. Run: pip install feedparser")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    all_news = []

    for feed_url in RSS_FEEDS:
        try:
            logger.debug(f"Fetching {feed_url}")
            feed = feedparser.parse(feed_url)
            source = feed.feed.get("title", feed_url)

            count = 0
            for entry in feed.entries[:max_per_feed]:
                # 발행 시각 파싱
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    except Exception:
                        pass

                if published is None:
                    continue

                # 시간 필터
                if published < cutoff:
                    continue

                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "")[:300]  # 요약은 300자로 자름

                all_news.append({
                    "title": title,
                    "link": link,
                    "published": published,
                    "source": source,
                    "summary": summary,
                })
                count += 1

            logger.info(f"  {source}: {count} recent articles")
        except Exception as e:
            logger.warning(f"Failed to fetch {feed_url}: {e}")
            continue

    # 최신순 정렬
    all_news.sort(key=lambda x: x["published"], reverse=True)

    _rss_cache = all_news
    _rss_cache_time = now

    logger.info(f"Total recent news ({hours}h): {len(all_news)}")
    return all_news


def match_news_for_symbol(
    news_list: list[dict],
    symbol: str,
    limit: int = 5,
) -> list[dict]:
    """
    심볼과 관련된 뉴스만 필터링합니다.

    제목에 심볼 키워드가 포함된 것만 매칭 (대소문자 무시, 단어 경계 확인).
    """
    keywords = _get_keywords_for_symbol(symbol)
    matched = []

    for news in news_list:
        title = news["title"]
        title_upper = title.upper()

        for kw in keywords:
            kw_upper = kw.upper()
            # 단어 경계 매칭 (BTC가 ABTCABC 같은 곳에 매칭되지 않도록)
            if re.search(r"\b" + re.escape(kw_upper) + r"\b", title_upper):
                matched.append(news)
                break

        if len(matched) >= limit:
            break

    return matched


def format_news_for_prompt(news_items: list[dict]) -> str:
    """뉴스를 프롬프트용 텍스트로 포매팅합니다."""
    if not news_items:
        return "(no recent news found)"

    lines = []
    for news in news_items:
        pub = news["published"].strftime("%Y-%m-%d %H:%M UTC")
        title = news["title"]
        source = news["source"]
        lines.append(f"  [{pub}] {title} ({source})")

    return "\n".join(lines)


def fetch_news_for_symbols(symbols: list[str], hours: int = 24) -> dict[str, list[dict]]:
    """
    심볼 리스트에 대해 한 번에 뉴스를 수집하고 매칭합니다.

    Returns:
        {symbol: [news1, news2, ...], ...}
    """
    all_news = fetch_all_news(hours)
    result = {}
    for symbol in symbols:
        matched = match_news_for_symbol(all_news, symbol)
        result[symbol] = matched
        if matched:
            logger.info(f"[{symbol}] {len(matched)} matched news")
    return result
