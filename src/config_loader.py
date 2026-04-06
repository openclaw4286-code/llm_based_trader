"""
설정 파일 로더 - config.yaml과 .env를 읽어 전역 설정 객체를 제공합니다.
"""
import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def load_config() -> dict:
    """config.yaml을 읽어 딕셔너리로 반환합니다."""
    load_dotenv(PROJECT_ROOT / ".env")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 환경변수 주입
    cfg["gateio_api_key"] = os.getenv("GATEIO_API_KEY", "")
    cfg["gateio_api_secret"] = os.getenv("GATEIO_API_SECRET", "")

    # 경로를 절대경로로 변환
    for key, rel in cfg.get("paths", {}).items():
        cfg["paths"][key] = str(PROJECT_ROOT / rel)

    return cfg


# 싱글턴 캐시
_config_cache = None


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
