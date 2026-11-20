#!/usr/bin/env python3
"""Split Foreca's 57-course pilot into an independent cache."""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path

REPORT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/foreca-pilot.json")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/foreca-cache")

def main():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    courses = report.get("courses")
    if not isinstance(courses, dict) or len(courses) != 57:
        raise SystemExit(f"Expected 57 successful Foreca courses, found {len(courses or {})}")
    generated = report.get("generated_at")
    if not generated:
        raise SystemExit("Foreca report missing generated_at")
    OUT.mkdir(parents=True, exist_ok=True)
    folder = OUT / "courses"
    folder.mkdir(parents=True, exist_ok=True)
    index = {
        "schema_version": 1, "provider": report.get("provider"),
        "generated_at": generated, "timezone": "Asia/Taipei",
        "hourly_horizon": report.get("hourly_horizon"),
        "daily_horizon": report.get("daily_horizon"),
        "scope": "all-57-courses", "course_count": 57, "courses": {},
    }
    for name, source in sorted(courses.items()):
        key = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
        rel = f"courses/{key}.json"
        dates = {}
        for row in source.get("hourly", []):
            stamp = str(row.get("time") or "")
            if len(stamp) < 16:
                continue
            day, clock = stamp[:10], stamp[11:16]
            dates.setdefault(day, {"hourly": [], "daily": None})["hourly"].append({
                "time": f"{day}T{clock}",
                "temperature_2m": row.get("temperature"),
                "apparent_temperature": row.get("feelsLikeTemp"),
                "precipitation": row.get("precipAccum"),
                "precipitation_probability": row.get("precipProb"),
                "wind_speed_10m": row.get("windSpeed"),
                "wind_gusts_10m": row.get("windGust"),
                "wind_direction_10m": row.get("windDir"),
                "relative_humidity_2m": row.get("relHumidity"),
                "cloud_cover": row.get("cloudiness"),
                "uv_index": row.get("uvIndex"),
                "thunder_probability": row.get("thunderProb"),
                "precipitation_type": row.get("precipType"),
                "conditions": row.get("symbolPhrase"),
                "symbol": row.get("symbol"),
            })
        for row in source.get("daily", []):
            day = row.get("date")
            if not day:
                continue
            dates.setdefault(day, {"hourly": [], "daily": None})["daily"] = {
                "symbol": row.get("symbol"),
                "conditions": row.get("symbolPhrase"),
                "temperature_max": row.get("maxTemp"),
                "temperature_min": row.get("minTemp"),
                "feels_like_max": row.get("maxFeelsLikeTemp"),
                "feels_like_min": row.get("minFeelsLikeTemp"),
                "precipitation": row.get("precipAccum"),
                "precipitation_probability": row.get("precipProb"),
                "wind_speed_max": row.get("maxWindSpeed"),
                "wind_gust_max": row.get("maxWindGust"),
                "wind_direction": row.get("windDir"),
                "cloud_cover": row.get("cloudiness"),
                "humidity_max": row.get("maxRelHumidity"),
                "uv_index": row.get("uvIndex"),
                "confidence": row.get("confidence"),
                "sunrise": row.get("sunrise"),
                "sunset": row.get("sunset"),
            }
        payload_dates = {}
        for day, values in dates.items():
            providers = {"Foreca": {
                "status": "ok", "display_name": "Foreca",
                "hourly": values["hourly"], "daily": values["daily"],
            }}
            payload_dates[day] = {"providers": providers}
        payload = {
            "schema_version": 1, "provider": index["provider"],
            "generated_at": generated, "timezone": "Asia/Taipei",
            "latitude": source.get("latitude"), "longitude": source.get("longitude"),
            "hour_count": source.get("hourly_count"), "date_count": len(dates),
            "courses": {name: payload_dates},
        }
        (folder / f"{key}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        index["courses"][name] = rel
    (OUT / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"Split and validated 57 Foreca course caches; generated_at={generated}")

if __name__ == "__main__":
    main()
