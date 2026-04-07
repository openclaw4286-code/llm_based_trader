from __future__ import annotations
"""
파일 관리자 - JSON 파일 읽기/쓰기, 세션 ID 관리, 멱등성 보장을 담당합니다.

핵심 설계:
- Claude Code가 실행될 때마다 기존 JSON을 덮어씀 (중복 실행 안전)
- 주문 모듈은 처리한 세션 ID를 기록해 같은 분석을 두 번 주문하지 않음
"""
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config_loader import get_config


def get_session_id() -> str:
    """현재 세션 ID를 생성합니다 (YYYYMMDD_HH 형식)."""
    now = datetime.now()
    # 가장 가까운 스케줄 시간으로 반올림 (0, 6, 12, 18)
    hour = now.hour
    schedule_hour = max(h for h in [0, 6, 12, 18] if h <= hour)
    return now.strftime(f"%Y%m%d_{schedule_hour:02d}")


def save_analysis_json(symbol: str, data: dict) -> Path:
    """분석 결과를 JSON으로 저장합니다. 같은 세션이면 덮어씁니다."""
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
    """현재 세션의 모든 분석 결과를 로드합니다."""
    cfg = get_config()
    analysis_dir = Path(cfg["paths"]["analysis"])

    if session_id is None:
        session_id = get_session_id()

    results = []
    for filepath in sorted(analysis_dir.glob(f"{session_id}_*.json")):
        with open(filepath, "r", encoding="utf-8") as f:
            results.append(json.load(f))

    return results


def save_order_result(order_data: dict) -> Path:
    """주문 결과를 저장합니다."""
    cfg = get_config()
    orders_dir = Path(cfg["paths"]["orders"])
    orders_dir.mkdir(parents=True, exist_ok=True)

    session_id = get_session_id()
    filepath = orders_dir / f"{session_id}_orders.json"

    # 기존 주문 기록 로드 (있으면)
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
    """이미 주문이 실행된 세션 ID 목록을 반환합니다."""
    cfg = get_config()
    orders_dir = Path(cfg["paths"]["orders"])
    processed_file = orders_dir / "processed_sessions.json"

    if not processed_file.exists():
        return set()

    with open(processed_file, "r", encoding="utf-8") as f:
        return set(json.load(f))


def mark_session_processed(session_id: str):
    """세션을 처리 완료로 기록합니다 (중복 주문 방지)."""
    cfg = get_config()
    orders_dir = Path(cfg["paths"]["orders"])
    orders_dir.mkdir(parents=True, exist_ok=True)
    processed_file = orders_dir / "processed_sessions.json"

    processed = get_processed_sessions()
    processed.add(session_id)

    with open(processed_file, "w", encoding="utf-8") as f:
        json.dump(sorted(processed), f, indent=2)


def compute_file_hash(filepath: str) -> str:
    """파일 해시를 계산합니다 (변경 감지용)."""
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()
