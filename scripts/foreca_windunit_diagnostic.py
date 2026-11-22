#!/usr/bin/env python3
"""Compare Foreca wind units for one location without writing forecast caches."""
import argparse
import json
import math
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

API_BASE = "https://weatherapi.foreca.net/api/v1/forecast"

def fetch(lat, lon, periods, unit, api_key):
    # Foreca coordinate path order is longitude,latitude.
    location = f"{lon:.5f},{lat:.5f}"
    query = urllib.parse.urlencode({
        "periods": periods,
        "tz": "Asia/Taipei",
        "tempunit": "C",
        "windunit": unit,
        "dataset": "full",
        "rounding": 0,
    })
    url = f"{API_BASE}/hourly/{location}?{query}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Taiwan-Golf-Weather-WindUnit-Diagnostic/1.0",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Never print request URL or headers; they include auth context.
        detail = exc.read().decode("utf-8", errors="replace")[:250]
        raise RuntimeError(f"Foreca HTTP {exc.code}: {detail}") from None
    rows = payload.get("forecast")
    if not isinstance(rows, list):
        raise RuntimeError(f"{unit}: API response has no forecast list")
    result = {}
    for row in rows:
        stamp = row.get("time") or row.get("datetime")
        speed = row.get("windSpeed")
        gust = row.get("windGust")
        if stamp:
            result[str(stamp)] = {
                "speed": speed if isinstance(speed, (int, float)) else None,
                "gust": gust if isinstance(gust, (int, float)) else None,
            }
    return result

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--course", default="Jiaoxi")
    p.add_argument("--lat", type=float, default=24.8389)
    p.add_argument("--lon", type=float, default=121.7389)
    p.add_argument("--periods", type=int, default=48)
    p.add_argument("--out", default="/tmp/foreca-windunit-report.json")
    a = p.parse_args()
    key = os.getenv("FORECA_API_KEY", "").strip()
    if not key:
        raise SystemExit("FORECA_API_KEY secret is missing.")
    if not (-90 <= a.lat <= 90 and -180 <= a.lon <= 180):
        raise SystemExit("Invalid latitude/longitude.")
    if not (1 <= a.periods <= 169):
        raise SystemExit("periods must be between 1 and 169.")

    kmh = fetch(a.lat, a.lon, a.periods, "KMH", key)
    ms = fetch(a.lat, a.lon, a.periods, "MS", key)
    common = sorted(set(kmh) & set(ms))
    comparisons = []
    for stamp in common:
        k, m = kmh[stamp], ms[stamp]
        if k["speed"] is None or m["speed"] is None:
            continue
        expected = m["speed"] * 3.6
        diff = k["speed"] - expected
        rel = abs(diff) / max(abs(expected), 1.0) * 100
        row = {
            "time": stamp,
            "kmh_speed": k["speed"],
            "ms_speed": m["speed"],
            "ms_times_3_6": round(expected, 3),
            "speed_abs_diff_kmh": round(abs(diff), 3),
            "speed_relative_error_pct": round(rel, 2),
        }
        if k["gust"] is not None and m["gust"] is not None:
            row.update({
                "kmh_gust": k["gust"],
                "ms_gust": m["gust"],
                "ms_gust_times_3_6": round(m["gust"] * 3.6, 3),
                "gust_abs_diff_kmh": round(abs(k["gust"] - m["gust"] * 3.6), 3),
            })
        comparisons.append(row)

    if not comparisons:
        raise RuntimeError("No overlapping hourly wind-speed values to compare.")
    errors = [x["speed_relative_error_pct"] for x in comparisons]
    median_error = statistics.median(errors)
    within_10 = sum(x <= 10 for x in errors)
    passed = len(comparisons) >= 6 and median_error <= 10 and within_10 / len(errors) >= 0.8
    report = {
        "generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
        "course": a.course,
        "latitude": a.lat,
        "longitude": a.lon,
        "requested_periods": a.periods,
        "unit_relationship": "KMH should approximately equal MS * 3.6",
        "kmh_hour_count": len(kmh),
        "ms_hour_count": len(ms),
        "matched_hour_count": len(comparisons),
        "median_speed_relative_error_pct": round(median_error, 2),
        "hours_within_10pct": within_10,
        "speed_unit_test": "PASS" if passed else "INCONCLUSIVE_OR_FAIL",
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "comparisons"}, ensure_ascii=False))
    if not passed:
        raise SystemExit("Wind unit relationship did not meet the diagnostic pass criteria.")

if __name__ == "__main__":
    main()
