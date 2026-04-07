#!/usr/bin/env python3
"""
Claude Code의 응답 파일을 받아 각 종목별 분석 JSON으로 저장합니다.

run_pipeline.sh가 호출하는 흐름:
  1. step3 --manual 이 master_prompt.txt 생성
  2. claude -p "$(cat master_prompt.txt)" > claude_response.txt
  3. 이 스크립트가 claude_response.txt를 읽어 data/analysis/<session>_<symbol>.json 저장
  4. 그 후 step4가 실행되어 주문

여러 번 실행해도 같은 세션의 JSON을 덮어쓰므로 안전합니다.

사용법:
  python scripts/parse_claude_response.py <response_file>
"""
import sys
import json
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.claude_analyzer import parse_batch_response, parse_and_save_response
from src.file_manager import get_session_id, load_all_analysis_for_session
from src.position_sizing import calculate_position_sizes, calculate_cash_reserve
from src.file_manager import save_analysis_json
from src.gateio_client import GateIOClient
from src.logger import setup_logger

logger = setup_logger("parse_claude")


def main():
    if len(sys.argv) < 2:
        print("Usage: parse_claude_response.py <response_file>")
        sys.exit(1)

    response_path = Path(sys.argv[1])
    if not response_path.exists():
        logger.error(f"Response file not found: {response_path}")
        sys.exit(1)

    raw = response_path.read_text(encoding="utf-8")
    session_id = get_session_id()
    logger.info(f"Parsing Claude response for session {session_id}")

    # 배치 응답(JSON 배열) 우선 시도
    array_match = re.search(r'\[\s*\{[\s\S]*\}\s*\]', raw)
    results = []
    if array_match:
        try:
            results = parse_batch_response(array_match.group(), [])
            logger.info(f"Parsed {len(results)} results from JSON array")
        except Exception as e:
            logger.warning(f"Batch parse failed: {e}")

    # 폴백: 개별 객체들 추출 (symbol 필드 기준)
    if not results:
        for obj_match in re.finditer(r'\{[^{}]*"symbol"[^{}]*\}', raw):
            try:
                data = json.loads(obj_match.group())
                symbol = data.get("symbol", "")
                if symbol:
                    parse_and_save_response(symbol, obj_match.group())
                    results.append(data)
            except Exception:
                continue
        logger.info(f"Parsed {len(results)} individual results")

    if not results:
        logger.error("No valid analysis JSON found in response")
        sys.exit(1)

    # 포지션 사이징 계산
    analyses = load_all_analysis_for_session(session_id)
    try:
        client = GateIOClient()
        balance = float(client.get_futures_account().get("available", 0))
    except Exception as e:
        logger.warning(f"Balance fetch failed: {e}")
        balance = 0.0

    if balance > 0 and analyses:
        analyses = calculate_position_sizes(analyses, balance)
        info = calculate_cash_reserve(analyses)
        logger.info(
            f"Positions: {info['num_positions']}, exposure={info['total_exposure_pct']:.1f}%, "
            f"cash={info['cash_reserve_pct']:.1f}%"
        )
        for a in analyses:
            save_analysis_json(a["symbol"], a)

    logger.info("Parse complete")


if __name__ == "__main__":
    main()
