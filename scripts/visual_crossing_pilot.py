#!/usr/bin/env python3
"""Fetch Visual Crossing 15-day hourly forecasts for all 57 Taiwan golf courses.

The API key is optional because anonymous access is supported under provider
fair-use limits. A configured GitHub Actions secret is preferred for reliable
scheduled collection.
"""
from __future__ import annotations
import concurrent.futures
import gzip
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
INDEX_URL = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/course-cache/index.json"
CACHE_BASE = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/course-cache/"
API_BASE = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/"
API_KEY = os.getenv("VISUAL_CROSSING_API_KEY", "").strip()
OUT = sys.argv[1] if len(sys.argv) > 1 else "visual-crossing-pilot.json"
ELEMENTS = ",".join([
    "datetime", "temp", "feelslike", "humidity", "precip", "precipprob",
    "windspeed", "windgust", "winddir", "cloudcover", "uvindex", "conditions", "icon"
])

def get_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Taiwan-Golf-Weather-PWA-VisualCrossing/2.0",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    last = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                payload = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    payload = gzip.decompress(payload)
                return json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
            last = RuntimeError(f"Visual Crossing HTTP {exc.code}: {detail}")
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
        cache = get_json(CACHE_BASE + rel)
        entry = cache.get("courses", {}).get(name)
        if not isinstance(entry, dict):
            raise RuntimeError(f"Course cache entry missing: {name}")
        lat, lon = number(entry.get("lat")), number(entry.get("lon"))
        if lat is None or lon is None:
            raise RuntimeError(f"Course coordinates missing: {name}")
        courses.append({"name": name, "lat": lat, "lon": lon})
    return index, courses

def fetch_course(course):
    name, lat, lon = course["name"], course["lat"], course["lon"]
    location = f"{lat:.5f},{lon:.5f}"
    params = {
        "unitGroup": "metric",
        "include": "hours",
        "elements": ELEMENTS,
        "contentType": "json",
        "source": "llms",
    }
    if API_KEY:
        params["key"] = API_KEY
    url = API_BASE + urllib.parse.quote(location, safe=",") + "?" + urllib.parse.urlencode(params)
    data = get_json(url)

    timezone = data.get("timezone") or "Asia/Taipei"
    days = []
    for day in data.get("days", []):
        day_date = day.get("datetime")
        if not day_date:
            continue
        hours = []
        for hour in day.get("hours", []):
            clock = hour.get("datetime")
            if not clock:
                continue
            hours.append({
                "time": f"{day_date}T{str(clock)[:5]}",
                "temp_c": number(hour.get("temp")),
                "feelslike_c": number(hour.get("feelslike")),
                "humidity_pct": number(hour.get("humidity")),
                "precip_mm": number(hour.get("precip")),
                "precip_probability_pct": number(hour.get("precipprob")),
                "wind_kmh": number(hour.get("windspeed")),
                "gust_kmh": number(hour.get("windgust")),
                "wind_direction_deg": number(hour.get("winddir")),
                "cloud_cover_pct": number(hour.get("cloudcover")),
                "uv_index": number(hour.get("uvindex")),
                "conditions": hour.get("conditions"),
                "icon": hour.get("icon"),
            })
        days.append({"date": day_date, "hour_count": len(hours), "hours": hours})

    total_hours = sum(day["hour_count"] for day in days)
    if len(days) < 14 or total_hours < 300:
        raise RuntimeError(
            f"Incomplete forecast for {name}: {len(days)} dates / {total_hours} hourly records"
        )
    return {
        "latitude": lat,
        "longitude": lon,
        "timezone": timezone,
        "resolved_address": data.get("resolvedAddress"),
        "description": data.get("description"),
        "query_cost": data.get("queryCost"),
        "date_count": len(days),
        "hour_count": total_hours,
        "days": days,
    }

def main():
    source_index, courses = load_courses()
    now = datetime.now(TZ)
    report = {
        "schema_version": 2,
        "provider": "Visual Crossing Timeline Weather API",
        "generated_at": now.isoformat(),
        "timezone": "Asia/Taipei",
        "forecast_horizon": "up to 15 days",
        "scope": "all-57-courses",
        "source_course_cache_generated_at": source_index.get("generated_at"),
        "api_key_mode": "registered-key" if API_KEY else "anonymous-fair-use",
        "course_count_expected": 57,
        "courses": {},
        "errors": [],
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_course, c): c for c in courses}
        for future in concurrent.futures.as_completed(futures):
            course = futures[future]
            try:
                report["courses"][course["name"]] = future.result()
                c = report["courses"][course["name"]]
                print(f"OK {course['name']}: {c['date_count']} dates, {c['hour_count']} hours, queryCost={c['query_cost']}")
            except Exception as exc:
                report["errors"].append({"course": course["name"], "error": str(exc)})
                print(f"ERROR {course['name']}: {exc}", file=sys.stderr)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, separators=(",", ":"))

    total_query_cost = sum(
        number(c.get("query_cost")) or 0 for c in report["courses"].values()
    )
    summary = {
        "provider": report["provider"],
        "courses_ok": len(report["courses"]),
        "courses_expected": 57,
        "total_query_cost_reported": total_query_cost,
        "api_key_mode": report["api_key_mode"],
        "errors": report["errors"],
        "output": OUT,
    }
    print(json.dumps(summary, ensure_ascii=False))
    if len(report["courses"]) != 57:
        raise SystemExit("Visual Crossing pilot incomplete; refusing to publish a partial nationwide cache.")

if __name__ == "__main__":
    main()
