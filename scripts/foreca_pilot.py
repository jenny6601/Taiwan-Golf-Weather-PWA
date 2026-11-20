#!/usr/bin/env python3
"""Collect independent Foreca hourly and daily forecasts for Taiwan's 57 golf courses.

API credentials are read only from FORECA_API_KEY. Never print request headers,
tokens, or full authenticated URLs. Output is a private workflow artifact until
the Foreca licence permits broader distribution.
"""
from __future__ import annotations
import concurrent.futures
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
INDEX_URL = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/course-cache/index.json"
CACHE_BASE = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/course-cache/"
API_BASE = "https://weatherapi.foreca.net/api/v1/forecast"
API_KEY = os.getenv("FORECA_API_KEY", "").strip()
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/foreca-pilot.json"

def get_json(url: str, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Taiwan-Golf-Weather-Foreca-Pilot/1.0",
        "Accept": "application/json",
        **(headers or {}),
    })
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After")
            # Do not print response URLs or request headers; error bodies may
            # contain provider diagnostics but should never contain our token.
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            last = RuntimeError(f"Foreca HTTP {exc.code}: {detail}")
            if exc.code not in (429, 500, 502, 503, 504):
                break
            delay = min(float(retry_after), 30) if retry_after and retry_after.isdigit() else (2 ** attempt)
        except Exception as exc:
            last = exc
            delay = 2 ** attempt
        if attempt < 3:
            time.sleep(delay)
    raise RuntimeError(str(last))

def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

def load_courses():
    index = get_json(INDEX_URL)
    refs = index.get("courses", {})
    if len(refs) != 57:
        raise RuntimeError(f"Expected 57 course references, found {len(refs)}")
    courses = []
    for name, rel in refs.items():
        cache = get_json(CACHE_BASE + rel)
        entry = cache.get("courses", {}).get(name)
        if not isinstance(entry, dict):
            raise RuntimeError(f"Course cache entry missing: {name}")
        lat, lon = number(entry.get("lat")), number(entry.get("lon"))
        if lat is None or lon is None:
            raise RuntimeError(f"Course coordinates missing: {name}")
        courses.append({"name": name, "lat": lat, "lon": lon})
    return index, courses

def foreca(location, endpoint, periods):
    query = urllib.parse.urlencode({
        "periods": periods, "tz": "Asia/Taipei", "tempunit": "C",
        "windunit": "KMH", "dataset": "full", "rounding": 0,
    })
    url = f"{API_BASE}/{endpoint}/{location}?{query}"
    return get_json(url, {"Authorization": f"Bearer {API_KEY}"})

def fetch_course(course):
    name, lat, lon = course["name"], course["lat"], course["lon"]
    # Foreca coordinate path order is longitude,latitude.
    location = f"{lon:.5f},{lat:.5f}"
    hourly_doc = foreca(location, "hourly", 169)
    daily_doc = foreca(location, "daily", 15)
    hourly = hourly_doc.get("forecast", [])
    daily = daily_doc.get("forecast", [])
    if len(hourly) < 100:
        raise RuntimeError(f"Incomplete hourly forecast for {name}: {len(hourly)} records")
    if len(daily) < 7:
        raise RuntimeError(f"Incomplete daily forecast for {name}: {len(daily)} records")
    return {
        "latitude": lat, "longitude": lon,
        "hourly_count": len(hourly), "daily_count": len(daily),
        "hourly": hourly, "daily": daily,
    }

def main():
    if not API_KEY:
        raise SystemExit("FORECA_API_KEY GitHub Secret is missing.")
    source_index, courses = load_courses()
    now = datetime.now(TZ)
    report = {
        "schema_version": 1,
        "provider": "Foreca Point Forecasts API",
        "generated_at": now.isoformat(),
        "timezone": "Asia/Taipei",
        "hourly_horizon": "up to 7 days",
        "daily_horizon": "up to 14 days",
        "scope": "all-57-courses",
        "source_course_cache_generated_at": source_index.get("generated_at"),
        "course_count_expected": 57,
        "successful_courses": 0,
        "courses": {}, "errors": [],
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_course, c): c for c in courses}
        for future in concurrent.futures.as_completed(futures):
            course = futures[future]
            try:
                report["courses"][course["name"]] = future.result()
                c = report["courses"][course["name"]]
                print(f"OK {course['name']}: {c['hourly_count']} hourly / {c['daily_count']} daily")
            except Exception as exc:
                report["errors"].append({"course": course["name"], "error": str(exc)})
                print(f"ERROR {course['name']}: {exc}", file=sys.stderr)
    report["successful_courses"] = len(report["courses"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps({
        "provider": report["provider"],
        "courses_ok": report["successful_courses"], "courses_expected": 57,
        "hourly_records": sum(c["hourly_count"] for c in report["courses"].values()),
        "daily_records": sum(c["daily_count"] for c in report["courses"].values()),
        "errors": report["errors"], "output": OUT,
    }, ensure_ascii=False))
    if report["successful_courses"] != 57:
        raise SystemExit("Foreca pilot incomplete; refusing to publish a partial nationwide cache.")

if __name__ == "__main__":
    main()
