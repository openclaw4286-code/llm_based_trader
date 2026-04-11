from __future__ import annotations
"""
유틸리티 모듈 - 설정 로더, 로거, 세션/파일 관리를 한 파일에 통합.

구성:
  - Config: load_config(), get_config(), reload_config()
  - Logger: setup_logger()
  - Session: get_session_id(), SCHEDULE_HOURS
  - File management: save/load analysis JSON, order results,
                     processed sessions (중복 주문 방지)
"""
import os
import sys
import json
import logging
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

# 스케줄 시각 (ICT Kill Zone 기반, KST)
SCHEDULE_HOURS = [4, 9, 13, 16, 21]


# ─── Config ────────────────────────────────────────────────

_config_cache: Optional[dict] = None


def load_config() -> dict:
    """config.yaml을 읽어 딕셔너리로 반환합니다. 경로는 절대경로로 변환."""
    load_dotenv(PROJECT_ROOT / ".env")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    cfg["gateio_api_key"] = os.getenv("GATEIO_API_KEY", "")
    cfg["gateio_api_secret"] = os.getenv("GATEIO_API_SECRET", "")

    for key, rel in cfg.get("paths", {}).items():
        cfg["paths"][key] = str(PROJECT_ROOT / rel)

    return cfg


def get_config() -> dict:
    """캐시된 설정을 반환합니다. 최초 호출 시 로드합니다."""
    global _config_cache
    if _config_cache is None:
        _config_cache = load_config()
    return _config_cache


def reload_config() -> dict:
    """설정을 강제 리로드합니다."""
    global _config_cache
    _config_cache = load_config()
    return _config_cache


# ─── Logger ────────────────────────────────────────────────

def setup_logger(name: str = "trader") -> logging.Logger:
    """이름별 로거를 생성합니다. 콘솔 INFO + 파일 DEBUG 동시 출력."""
    cfg = get_config()
    log_dir = Path(cfg["paths"]["logs"])
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    today = datetime.now().strftime("%Y-%m-%d")
    fh = logging.FileHandler(log_dir / f"{name}_{today}.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger


# ─── Session ID ────────────────────────────────────────────

def get_session_id() -> str:
    """현재 세션 ID를 YYYYMMDD_HH 형식으로 반환합니다.

    스케줄: 04, 09, 13, 16, 21시 (실제 launchd는 :30에 실행).
    00~03시는 전날 21시 세션에 속합니다.
    """
    now = datetime.now()
    hour = now.hour

    candidates = [h for h in SCHEDULE_HOURS if h <= hour]
    if candidates:
        schedule_hour = max(candidates)
        return now.strftime(f"%Y%m%d_{schedule_hour:02d}")
    prev_day = now - timedelta(days=1)
    return prev_day.strftime(f"%Y%m%d_{SCHEDULE_HOURS[-1]:02d}")


# ─── Analysis JSON 파일 ────────────────────────────────────

def save_analysis_json(symbol: str, data: dict) -> Path:
    """분석 결과 JSON을 저장합니다 (session_id + symbol 접두)."""
    cfg = get_config()
    analysis_dir = Path(cfg["paths"]["analysis"])
    analysis_dir.mkdir(parents=True, exist_ok=True)

    session_id = get_session_id()
    data["session_id"] = session_id
    data["symbol"] = symbol
    data["timestamp"] = datetime.now().isoformat()

    filepath = analysis_dir / f"{session_id}_{symbol}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return filepath


def load_analysis_json(symbol: str, session_id: Optional[str] = None) -> Optional[dict]:
    """분석 결과 JSON을 로드합니다."""
    cfg = get_config()
    analysis_dir = Path(cfg["paths"]["analysis"])

    if session_id is None:
        session_id = get_session_id()

    filepath = analysis_dir / f"{session_id}_{symbol}.json"
    if not filepath.exists():
        return None

    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def load_all_analysis_for_session(session_id: Optional[str] = None) -> list[dict]:
    """현재 세션의 분석 결과(Claude 판단)만 로드합니다.

    ICT JSON(`_ict.json`)과 top_coins.json 등은 제외합니다.
    분석 결과는 반드시 'symbol'과 'decision' 필드를 가져야 합니다.
    """
    cfg = get_config()
    analysis_dir = Path(cfg["paths"]["analysis"])

    if session_id is None:
        session_id = get_session_id()

    results = []
    for filepath in sorted(analysis_dir.glob(f"{session_id}_*.json")):
        if filepath.name.endswith("_ict.json") or filepath.name.endswith("_top_coins.json"):
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "symbol" in data and "decision" in data:
                results.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return results


# ─── 주문 결과 + 세션 처리 기록 ─────────────────────────────

def save_order_result(order_data: dict) -> Path:
    """세션별 주문 결과를 누적 저장합니다."""
    cfg = get_config()
    orders_dir = Path(cfg["paths"]["orders"])
    orders_dir.mkdir(parents=True, exist_ok=True)

    session_id = get_session_id()
    filepath = orders_dir / f"{session_id}_orders.json"

    existing = []
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            existing = json.load(f)

    existing.append({
        **order_data,
        "executed_at": datetime.now().isoformat(),
    })

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    return filepath


def get_processed_sessions() -> set:
    """이미 주문이 실행된 세션 ID 목록을 반환합니다 (중복 주문 방지)."""
    cfg = get_config()
    orders_dir = Path(cfg["paths"]["orders"])
    processed_file = orders_dir / "processed_sessions.json"

    if not processed_file.exists():
        return set()

    with open(processed_file, "r", encoding="utf-8") as f:
        return set(json.load(f))


def mark_session_processed(session_id: str):
    """세션을 처리 완료로 기록합니다."""
    cfg = get_config()
    orders_dir = Path(cfg["paths"]["orders"])
    orders_dir.mkdir(parents=True, exist_ok=True)
    processed_file = orders_dir / "processed_sessions.json"

    processed = get_processed_sessions()
    processed.add(session_id)

    with open(processed_file, "w", encoding="utf-8") as f:
        json.dump(sorted(processed), f, indent=2)
