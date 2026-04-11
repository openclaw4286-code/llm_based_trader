from __future__ import annotations
"""
경제 캘린더 수집 - 거시경제 이벤트 일정을 가져와 Claude 프롬프트에 반영합니다.

데이터 소스: faireconomy.media (Forex Factory 미러, 무료, XML 피드)
  - 이번 주 이벤트: ff_calendar_thisweek.xml
  - 다음 주 이벤트: ff_calendar_nextweek.xml

High-impact 이벤트만 필터링 (Red folder):
  - FOMC 금리 결정
  - CPI/PPI (인플레이션)
  - NFP (Non-Farm Payrolls)
  - GDP
  - ECB/BOJ/BOE 정책 발표
"""
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from src.utils import setup_logger

logger = setup_logger("economic_calendar")

FEED_THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

# 캐시 (한 파이프라인 실행 동안 재사용)
_cache: Optional[list[dict]] = None
_cache_time: float = 0
CACHE_TTL_SECONDS = 1800  # 30분


def fetch_economic_events(
    hours_ahead: int = 72,
    hours_behind: int = 24,
    high_impact_only: bool = True,
) -> list[dict]:
    """
    Forex Factory 미러에서 경제 이벤트를 수집합니다.

    Args:
        hours_ahead: 앞으로 몇 시간 내 이벤트
        hours_behind: 지난 몇 시간 내 이벤트 (최근에 나온 결과도 시장에 영향)
        high_impact_only: High impact(Red) 이벤트만

    Returns:
        [{"title", "country", "datetime", "impact", "forecast", "previous", "actual"}, ...]
    """
    global _cache, _cache_time

    now_ts = time.time()
    if _cache is not None and (now_ts - _cache_time) < CACHE_TTL_SECONDS:
        logger.info(f"Using cached economic events ({len(_cache)})")
        return _filter_events(_cache, hours_ahead, hours_behind, high_impact_only)

    all_events = []
    try:
        logger.debug(f"Fetching {FEED_THIS_WEEK}")
        resp = requests.get(FEED_THIS_WEEK, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for event_el in root.findall("event"):
            title = event_el.findtext("title", default="").strip()
            country = event_el.findtext("country", default="").strip()
            date_str = event_el.findtext("date", default="").strip()
            time_str = event_el.findtext("time", default="").strip()
            impact = event_el.findtext("impact", default="").strip()
            forecast = event_el.findtext("forecast", default="").strip()
            previous = event_el.findtext("previous", default="").strip()
            actual = event_el.findtext("actual", default="").strip()

            dt = _parse_event_datetime(date_str, time_str)
            if dt is None:
                continue

            all_events.append({
                "title": title,
                "country": country,
                "datetime": dt,
                "impact": impact,
                "forecast": forecast,
                "previous": previous,
                "actual": actual,
            })
    except Exception as e:
        logger.warning(f"Failed to fetch {FEED_THIS_WEEK}: {e}")

    logger.info(f"Fetched {len(all_events)} total economic events")
    _cache = all_events
    _cache_time = now_ts

    return _filter_events(all_events, hours_ahead, hours_behind, high_impact_only)


def _parse_event_datetime(date_str: str, time_str: str) -> Optional[datetime]:
    """
    Forex Factory 형식의 날짜+시각을 datetime으로 파싱.
    예: date="10-16-2025", time="8:30am"

    Forex Factory는 EST/EDT 기준이므로 EST(UTC-5)로 간주하여 UTC로 변환.
    """
    if not date_str:
        return None
    try:
        if time_str and time_str.lower() not in ("all day", "tentative", ""):
            dt_str = f"{date_str} {time_str}"
            dt_naive = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p")
        else:
            dt_naive = datetime.strptime(date_str, "%m-%d-%Y")
        # EST(UTC-5)로 해석 후 UTC로 변환 (여름철 EDT UTC-4는 무시, ~1시간 오차 허용)
        est = timezone(timedelta(hours=-5))
        dt_est = dt_naive.replace(tzinfo=est)
        return dt_est.astimezone(timezone.utc)
    except Exception as e:
        logger.debug(f"Parse failed: {date_str} {time_str}: {e}")
        return None


def _filter_events(
    events: list[dict],
    hours_ahead: int,
    hours_behind: int,
    high_impact_only: bool,
) -> list[dict]:
    """이벤트를 시간/영향도로 필터링합니다."""
    now = datetime.now(timezone.utc)
    future_cutoff = now + timedelta(hours=hours_ahead)
    past_cutoff = now - timedelta(hours=hours_behind)

    filtered = []
    for e in events:
        dt = e["datetime"]
        if dt < past_cutoff:
            continue
        if dt > future_cutoff:
            continue
        if high_impact_only and e["impact"].lower() != "high":
            continue
        filtered.append(e)

    filtered.sort(key=lambda x: x["datetime"])
    return filtered


def format_events_for_prompt(events: list[dict], limit: int = 12) -> str:
    """경제 이벤트를 프롬프트 텍스트로 포매팅합니다."""
    if not events:
        return "(no high-impact economic events in the window)"

    now = datetime.now(timezone.utc)
    lines = []
    for e in events[:limit]:
        dt = e["datetime"]
        dt_str = dt.strftime("%Y-%m-%d %H:%M UTC")
        country = e["country"] or "?"
        title = e["title"]

        # 과거/미래 표시
        diff_hours = (dt - now).total_seconds() / 3600
        if diff_hours < 0:
            time_label = f"({abs(diff_hours):.0f}h ago, RELEASED)"
        else:
            time_label = f"(in {diff_hours:.0f}h)"

        actual = f" actual={e.get('actual','')}" if e.get("actual") else ""
        fc = f" fc={e['forecast']}" if e["forecast"] else ""
        prev = f" prev={e['previous']}" if e["previous"] else ""
        lines.append(f"  [{dt_str}] {time_label} {country}: {title}{actual}{fc}{prev}")

    return "\n".join(lines)
