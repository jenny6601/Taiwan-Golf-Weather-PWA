#!/usr/bin/env python3
"""Read-only schema audit for the live forecast caches and CWA observations."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verification_history import (
    CORE_MODEL_SOURCES,
    COURSE_BASE,
    COURSE_INDEX_URL,
    FORECA_BASE,
    FORECA_INDEX_URL,
    LIVE_URL,
    VC_BASE,
    VC_INDEX_URL,
    name_key,
)


def fetch_json(url: str, attempts: int = 3) -> dict:
    last_error = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": "Taiwan-Golf-Weather-Source-Audit/1.0"})
            with urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Unable to read {url}: {last_error}")


def main() -> None:
    course_index = fetch_json(COURSE_INDEX_URL)
    vc_index = fetch_json(VC_INDEX_URL)
    foreca_index = fetch_json(FORECA_INDEX_URL)
    live = fetch_json(LIVE_URL)

    course_names = list((course_index.get("courses") or {}).keys())
    live_courses = list((live.get("courses") or {}).values())
    live_names = [item.get("course_name") for item in live_courses if item.get("course_name")]
    normalized_live = {name_key(name) for name in live_names}
    matched_names = [name for name in course_names if name_key(name) in normalized_live]

    if len(course_names) != 57 or len(live_courses) != 57:
        raise SystemExit(f"Expected 57 courses; forecast index={len(course_names)}, CWA={len(live_courses)}")
    if len(matched_names) != 57:
        raise SystemExit(f"Course-name mapping incomplete: {len(matched_names)}/57")

    sample_name = course_names[0]
    course_path = course_index["courses"][sample_name]
    course_file = fetch_json(COURSE_BASE + course_path)
    course_entry = course_file["courses"][sample_name]
    course_date = max(course_entry["dates"])
    model_rows = course_entry["dates"][course_date]["modelRows"]
    present_core = [model for model in CORE_MODEL_SOURCES if model in model_rows]
    if len(present_core) < 5:
        raise SystemExit(f"Core model coverage below 5/6 for {sample_name} at {course_date}: {present_core}")
    core_row = next(
        row for model in present_core
        for row in model_rows.get(model, [])
        if row.get("time") == "10:00"
    )
    required_core = {"temp", "feel", "mm", "wind", "gust", "rh"}
    if not required_core.issubset(core_row):
        raise SystemExit(f"Core forecast row fields missing: {sorted(required_core - set(core_row))}")

    provider_checks = []
    for label, index, base, provider_name in (
        ("Visual Crossing", vc_index, VC_BASE, "Visual Crossing"),
        ("Foreca", foreca_index, FORECA_BASE, "Foreca"),
    ):
        provider_path = index["courses"][sample_name]
        provider_file = fetch_json(base + provider_path)
        provider_course = provider_file["courses"][sample_name]
        usable_dates = []
        for candidate_date, candidate_day in provider_course.items():
            candidate_provider = (candidate_day.get("providers") or {}).get(provider_name) or {}
            candidate_hourly = candidate_provider.get("hourly") or []
            if candidate_provider.get("status") == "ok" and candidate_hourly:
                usable_dates.append((candidate_date, candidate_provider, candidate_hourly))
        if not usable_dates:
            raise SystemExit(f"{label} provider cache has no usable hourly date for {sample_name}")
        provider_date, provider, hourly = max(usable_dates, key=lambda item: item[0])
        required_provider = {
            "temperature_2m", "apparent_temperature", "precipitation",
            "precipitation_probability", "wind_speed_10m", "wind_gusts_10m",
            "relative_humidity_2m",
        }
        if not required_provider.issubset(hourly[0]):
            raise SystemExit(f"{label} fields missing: {sorted(required_provider - set(hourly[0]))}")
        provider_checks.append(f"{label}: latest usable {provider_date}, {len(hourly)} hourly rows across {len(usable_dates)} usable dates")

    weather_pass = 0
    for item in live_courses:
        weather = item.get("weather") or {}
        station = weather.get("station") or {}
        if (
            station.get("quality_status") == "passed"
            and isinstance(station.get("distance_km"), (int, float))
            and station["distance_km"] <= 15
            and isinstance(station.get("observation_age_minutes"), (int, float))
            and station["observation_age_minutes"] <= 90
            and isinstance(weather.get("temperature_c"), (int, float))
            and isinstance(weather.get("wind_speed_ms"), (int, float))
        ):
            weather_pass += 1
    if weather_pass < 50:
        raise SystemExit(f"CWA hourly weather observation coverage too low: {weather_pass}/57")

    print("LIVE SOURCE SCHEMA AUDIT: PASS")
    print(f"Course index and CWA names: {len(matched_names)}/57 matched")
    print(f"Core Open-Meteo models at {course_date}: {len(present_core)}/6 present; required hourly fields present")
    for result in provider_checks:
        print(result)
    print(f"CWA quality-passed weather stations within 15 km and 90 min: {weather_pass}/57")
    print(f"CWA source generated_at: {live.get('generated_at')}")


if __name__ == "__main__":
    main()
