#!/usr/bin/env python3
"""Fetch Visual Crossing 15-day hourly forecasts for five Taiwan golf-course pilots.

The API key is optional: anonymous access is supported subject to provider fair-use
limits. If configured, VISUAL_CROSSING_API_KEY is sent as a query parameter.
"""
from __future__ import annotations
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
COURSES = {
    "礁溪": "9f35112bceaa34e7",
    "東方": "07d249bc77494442",
    "美麗華": "54a4a424eb9351c2",
    "林口": "9194898a8728899b",
    "大溪": "08fb9cffedd9df7d",
}
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
        headers={"User-Agent": "Taiwan-Golf-Weather-PWA-VisualCrossing-Pilot/1.0",
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"Visual Crossing HTTP {exc.code}: {detail}") from exc

def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

def fetch_course(name: str, cache_key: str) -> dict:
    cache = get_json(CACHE_BASE + f"courses/{cache_key}.json")
    entry = cache.get("courses", {}).get(name)
    if not isinstance(entry, dict):
        raise RuntimeError(f"Course cache entry missing: {name}")
    lat, lon = number(entry.get("lat")), number(entry.get("lon"))
    if lat is None or lon is None:
        raise RuntimeError(f"Course coordinates missing: {name}")

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
            dt = hour.get("datetime")
            if not dt:
                continue
            hours.append({
                "time": dt,
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
    now = datetime.now(TZ)
    report = {
        "schema_version": 1,
        "provider": "Visual Crossing Timeline Weather API",
        "generated_at": now.isoformat(),
        "timezone": "Asia/Taipei",
        "forecast_horizon": "up to 15 days",
        "scope": "five-course-pilot",
        "api_key_mode": "registered-key" if API_KEY else "anonymous-fair-use",
        "courses": {},
        "errors": [],
    }
    for name, key in COURSES.items():
        try:
            report["courses"][name] = fetch_course(name, key)
            c = report["courses"][name]
            print(f"OK {name}: {c['date_count']} dates, {c['hour_count']} hours, queryCost={c['query_cost']}")
        except Exception as exc:
            report["errors"].append({"course": name, "error": str(exc)})
            print(f"ERROR {name}: {exc}", file=sys.stderr)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    summary = {
        "provider": report["provider"],
        "courses_ok": len(report["courses"]),
        "courses_expected": len(COURSES),
        "errors": report["errors"],
        "output": OUT,
    }
    print(json.dumps(summary, ensure_ascii=False))
    if len(report["courses"]) != len(COURSES):
        raise SystemExit("Pilot incomplete: not all five courses returned valid 15-day hourly data.")

if __name__ == "__main__":
    main()
