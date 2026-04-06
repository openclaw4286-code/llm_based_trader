"""
로깅 설정 - 콘솔 + 파일 로깅을 제공합니다.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

from src.config_loader import get_config


def setup_logger(name: str = "trader") -> logging.Logger:
    """이름별 로거를 생성합니다. 콘솔과 파일에 동시 출력합니다."""
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

    # 콘솔 핸들러
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # 파일 핸들러 (날짜별)
    today = datetime.now().strftime("%Y-%m-%d")
    fh = logging.FileHandler(log_dir / f"{name}_{today}.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger
