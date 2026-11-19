#!/usr/bin/env python3
"""Split the 57-course Visual Crossing report into a public per-course cache."""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path

REPORT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/visual-crossing-pilot.json")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/visual-crossing-cache")

def main():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    courses = report.get("courses")
    if not isinstance(courses, dict) or len(courses) != 57:
        raise SystemExit(f"Expected 57 successful Visual Crossing courses, found {len(courses or {})}")
    generated_at = report.get("generated_at")
    if not generated_at:
        raise SystemExit("Visual Crossing report missing generated_at")
    OUT.mkdir(parents=True, exist_ok=True)
    course_dir = OUT / "courses"
    course_dir.mkdir(parents=True, exist_ok=True)
    index = {
        "schema_version": 1,
        "provider": report.get("provider", "Visual Crossing Timeline Weather API"),
        "generated_at": generated_at,
        "timezone": report.get("timezone", "Asia/Taipei"),
        "forecast_horizon": report.get("forecast_horizon", "up to 15 days"),
        "scope": "all-57-courses",
        "api_key_mode": report.get("api_key_mode"),
        "course_count": len(courses),
        "total_query_cost_reported": sum(
            c.get("query_cost") or 0 for c in courses.values()
            if isinstance(c.get("query_cost"), (int, float))
        ),
        "courses": {},
    }
    for name, source in sorted(courses.items()):
        key = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
        rel = f"courses/{key}.json"
        dates = {}
        for day in source.get("days", []):
            date = day.get("date")
            if not date:
                continue
            hourly = []
            for h in day.get("hours", []):
                hourly.append({
                    "time": h.get("time"),
                    "temperature_2m": h.get("temp_c"),
                    "apparent_temperature": h.get("feelslike_c"),
                    "precipitation": h.get("precip_mm"),
                    "precipitation_probability": h.get("precip_probability_pct"),
                    "wind_speed_10m": h.get("wind_kmh"),
                    "wind_gusts_10m": h.get("gust_kmh"),
                    "wind_direction_10m": h.get("wind_direction_deg"),
                    "relative_humidity_2m": h.get("humidity_pct"),
                    "cloud_cover": h.get("cloud_cover_pct"),
                    "uv_index": h.get("uv_index"),
                    "conditions": h.get("conditions"),
                    "icon": h.get("icon"),
                })
            if hourly:
                dates[date] = {
                    "providers": {
                        "Visual Crossing": {
                            "status": "ok",
                            "display_name": "Visual Crossing",
                            "query_cost": source.get("query_cost"),
                            "hourly": hourly,
                        }
                    }
                }
        payload = {
            "schema_version": 1,
            "provider": index["provider"],
            "generated_at": generated_at,
            "timezone": source.get("timezone", "Asia/Taipei"),
            "latitude": source.get("latitude"),
            "longitude": source.get("longitude"),
            "date_count": source.get("date_count"),
            "hour_count": source.get("hour_count"),
            "courses": {name: dates},
        }
        (course_dir / f"{key}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        index["courses"][name] = rel
    (OUT / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Split and validated {len(courses)} Visual Crossing course caches; generated_at={generated_at}")

if __name__ == "__main__":
    main()
