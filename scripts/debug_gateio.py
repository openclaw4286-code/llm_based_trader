#!/usr/bin/env python3
"""Gate.io API 키 디버그 - 공식 예제 패턴 그대로 raw 호출."""
from __future__ import annotations
import time
import hashlib
import hmac
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests

KEY = os.getenv("GATEIO_API_KEY", "").strip()
SECRET = os.getenv("GATEIO_API_SECRET", "").strip()

print(f"KEY length: {len(KEY)}")
print(f"SECRET length: {len(SECRET)}")
print(f"KEY first 4: {KEY[:4]}")
print(f"KEY last 4:  {KEY[-4:]}")

if not KEY or not SECRET:
    print("ERROR: Missing key or secret in .env")
    sys.exit(1)


def gen_sign(method, url, query_string=None, payload_string=None):
    t = time.time()
    m = hashlib.sha512()
    m.update((payload_string or "").encode("utf-8"))
    hashed_payload = m.hexdigest()
    s = "%s\n%s\n%s\n%s\n%s" % (method, url, query_string or "", hashed_payload, t)
    sign = hmac.new(SECRET.encode("utf-8"), s.encode("utf-8"), hashlib.sha512).hexdigest()
    return {"KEY": KEY, "Timestamp": str(t), "SIGN": sign}


host = "https://api.gateio.ws"
prefix = "/api/v4"
url_path = "/futures/usdt/accounts"
url = prefix + url_path

print(f"\nCalling: {host}{url}")
sign_headers = gen_sign("GET", url)
sign_headers["Accept"] = "application/json"
sign_headers["Content-Type"] = "application/json"

response = requests.get(host + url, headers=sign_headers, timeout=15)
print(f"Status: {response.status_code}")
print(f"Body:   {response.text[:500]}")
