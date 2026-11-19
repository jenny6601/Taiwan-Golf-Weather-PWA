#!/usr/bin/env python3
"""Build a separate 57-course ECMWF AIFS cache using Open-Meteo.

AIFS Single is natively 6-hourly. Open-Meteo exposes an hourly interpolated
series; precipitation probability, wind gusts and UV are intentionally null
because they are not native AIFS Single fields.
"""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
INDEX_URL = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/course-cache/index.json"
CACHE_BASE = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/course-cache/"
API_URL = "https://api.open-meteo.com/v1/forecast"
MODEL_ID = "ecmwf_aifs025_single"
HOURLY_FIELDS = ",".join([
    "temperature_2m", "relative_humidity_2m", "apparent_temperature",
    "precipitation", "weather_code", "wind_speed_10m",
    "wind_direction_10m", "cloud_cover",
])
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "aifs-cache")

def get_json(url: str, timeout: int = 60) -> dict:
    last = None
    for attempt in range(2):
        req = urllib.request.Request(url, headers={
            "User-Agent": "Taiwan-Golf-Weather-PWA-AIFS/1.0",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last = exc
            if attempt == 0:
                time.sleep(1.5)
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
        payload = get_json(CACHE_BASE + rel)
        entry = payload.get("courses", {}).get(name)
        if not isinstance(entry, dict):
            raise RuntimeError(f"Course cache entry missing: {name}")
        lat, lon = number(entry.get("lat")), number(entry.get("lon"))
        if lat is None or lon is None:
            raise RuntimeError(f"Invalid coordinates for {name}")
        courses.append({"name": name, "lat": lat, "lon": lon})
    return index, courses

def fetch_course(course):
    params = urllib.parse.urlencode({
        "latitude": course["lat"],
        "longitude": course["lon"],
        "models": MODEL_ID,
        "timezone": "Asia/Taipei",
        "forecast_days": 15,
        "wind_speed_unit": "kmh",
        "hourly": HOURLY_FIELDS,
    })
    data = get_json(API_URL + "?" + params)
    hourly = data.get("hourly") or {}
    times = hourly.get("time")
    if not isinstance(times, list) or not times:
        raise RuntimeError("AIFS response missing hourly.time")
    rows_by_date = {}
    for i, stamp in enumerate(times):
        if len(stamp) < 16:
            continue
        date, clock = stamp[:10], stamp[11:16]
        hour = int(clock[:2])
        if not 6 <= hour <= 18:
            continue
        def val(key):
            values = hourly.get(key)
            return number(values[i]) if isinstance(values, list) and i < len(values) else None
        rows_by_date.setdefault(date, []).append({
            "time": clock,
            "available": True,
            "temp": val("temperature_2m"),
            "feel": val("apparent_temperature"),
            "rain": None,
            "mm": val("precipitation"),
            "code": val("weather_code"),
            "wind": val("wind_speed_10m"),
            "gust": None,
            "dir": val("wind_direction_10m"),
            "rh": val("relative_humidity_2m"),
            "cloud": val("cloud_cover"),
            "uv": None,
        })
    valid_dates = {d: rows for d, rows in rows_by_date.items() if len(rows) >= 10}
    if len(valid_dates) < 14:
        raise RuntimeError(f"Incomplete AIFS horizon: {len(valid_dates)} dates")
    return {
        "lat": course["lat"],
        "lon": course["lon"],
        "dates": {
            date: {
                "modelRows": {"ECMWF AIFS": rows},
                "forecastSource": "ECMWF AIFS Single（原始6小時；Open-Meteo逐時內插）",
            }
            for date, rows in valid_dates.items()
        },
    }

def main():
    source_index, courses = load_courses()
    now = datetime.now(TZ).isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    course_dir = OUT / "courses"
    course_dir.mkdir(parents=True, exist_ok=True)
    index = {
        "schema_version": 1,
        "provider": "ECMWF AIFS Single via Open-Meteo",
        "model_id": MODEL_ID,
        "generated_at": now,
        "source_course_cache_generated_at": source_index.get("generated_at"),
        "timezone": "Asia/Taipei",
        "course_count": len(courses),
        "forecast_horizon_days": 15,
        "native_temporal_resolution": "6-hourly",
        "display_temporal_resolution": "hourly-interpolated-by-Open-Meteo",
        "limitations": ["no_native_precipitation_probability", "no_native_wind_gusts", "no_uv_index"],
        "courses": {},
        "errors": [],
    }
    successes = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_course, c): c for c in courses}
        for future in concurrent.futures.as_completed(futures):
            course = futures[future]
            try:
                successes[course["name"]] = future.result()
                print(f"OK {course['name']}: {len(successes[course['name']]['dates'])} forecast dates")
            except Exception as exc:
                index["errors"].append({"course": course["name"], "error": str(exc)})
                print(f"ERROR {course['name']}: {exc}")
    for name, entry in sorted(successes.items()):
        key = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
        rel = f"courses/{key}.json"
        payload = {
            "schema_version": 1,
            "provider": index["provider"],
            "model_id": MODEL_ID,
            "generated_at": now,
            "timezone": "Asia/Taipei",
            "native_temporal_resolution": "6-hourly",
            "display_temporal_resolution": "hourly-interpolated-by-Open-Meteo",
            "limitations": index["limitations"],
            "courses": {name: entry},
        }
        (course_dir / f"{key}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        index["courses"][name] = rel
    index["course_count"] = len(successes)
    (OUT / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(json.dumps({
        "courses_ok": len(successes),
        "courses_expected": 57,
        "errors": index["errors"],
        "output": str(OUT),
        "generated_at": now,
    }, ensure_ascii=False))
    if len(successes) != 57:
        raise SystemExit("AIFS cache incomplete; refusing to publish a partial 57-course cache.")

if __name__ == "__main__":
    main()
