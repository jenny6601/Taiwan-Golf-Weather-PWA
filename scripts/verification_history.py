#!/usr/bin/env python3
"""Archive immutable 16-day forecast vintages and CWA observations; verify skill.

Storage is the dedicated verification-history branch. Forecast vintages are
captured once daily for lead days 1-16. CWA rainfall and hourly weather snapshots
are appended every ten minutes. No source forecast is overwritten.
"""
from __future__ import annotations
import concurrent.futures
import gzip
import json
import math
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median as statistics_median
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
LEADS = tuple(range(1, 17))
RETENTION_DAYS = 180
OBS_MAX_AGE_MIN = 28
WEATHER_OBS_MAX_AGE_MIN = 90
FORECAST_MAX_AGE_HOURS = 4
VC_MAX_AGE_HOURS = 36
FORECA_MAX_AGE_HOURS = 36
MIN_STATION_DISTANCE_KM = 15.0
MIN_10M_SAMPLES = 6
MIN_GLOBAL_PAIRS = 200
MIN_COURSE_PAIRS = 100
MIN_DAILY_COURSE_PAIRS = 30
MIN_GLOBAL_WET_PAIRS = 20
MIN_COURSE_WET_PAIRS = 10
MIN_FORECAST_VINTAGES = 20
MIN_COURSE_VINTAGES = 10
SOURCES = (
    "ECMWF IFS", "NOAA GFS", "DWD ICON", "JMA GSM",
    "Canada GEM", "ECMWF AIFS", "Visual Crossing", "Foreca",
)
CORE_MODEL_SOURCES = (
    "ECMWF IFS", "NOAA GFS", "DWD ICON", "JMA GSM",
    "Canada GEM", "ECMWF AIFS",
)
LIVE_URL = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather/main/data/live_observations.json"
COURSE_INDEX_URL = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/course-cache/index.json"
COURSE_BASE = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/course-cache/"
VC_INDEX_URL = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/visual-crossing-cache/index.json"
VC_BASE = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/visual-crossing-cache/"
FORECA_INDEX_URL = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/foreca-cache/index.json"
FORECA_BASE = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/foreca-cache/"
ROOT = Path(sys.argv[2] if len(sys.argv) > 2 else "history")

def get_json(url: str, timeout: int = 45) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Taiwan-Golf-Weather-Verification/1.0",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    })
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed: {url}: {last}")

def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def local_date(value: str) -> date:
    dt = parse_dt(value)
    if dt is None:
        raise ValueError(f"Invalid timestamp: {value}")
    return dt.astimezone(TZ).date()

def name_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFC", str(value)).split()).casefold()

def read_gzip_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows

def write_gzip_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)

def write_gzip_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)

def read_gzip_json(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)

def prune_old_files(folder: Path, cutoff: date):
    if not folder.exists():
        return
    for path in folder.glob("*.json*"):
        try:
            day = date.fromisoformat(path.name.split(".")[0])
            if day < cutoff:
                path.unlink()
        except Exception:
            continue

def archive_observations(now: datetime) -> dict:
    snapshot = get_json(LIVE_URL)
    generated = snapshot.get("generated_at")
    generated_dt = parse_dt(generated)
    if not generated_dt:
        raise RuntimeError("CWA live snapshot has no valid generated_at")
    age_min = (now.astimezone(timezone.utc) - generated_dt).total_seconds() / 60
    if age_min < -5 or age_min > OBS_MAX_AGE_MIN:
        raise RuntimeError(f"CWA live snapshot is stale: age={age_min:.1f} minutes")
    courses = snapshot.get("courses", {})
    if len(courses) != 57:
        raise RuntimeError(f"CWA live snapshot must contain all 57 courses; found {len(courses)}")
    coverage = snapshot.get("coverage") or {}
    if coverage and (
        coverage.get("weather_courses", 0) < 50
        or coverage.get("rainfall_courses", 0) < 50
    ):
        raise RuntimeError(f"CWA station coverage below verification threshold: {coverage}")
    day = generated_dt.astimezone(TZ).date().isoformat()
    path = ROOT / "observations" / f"{day}.jsonl.gz"
    history = read_gzip_jsonl(path)
    if any(x.get("snapshot_at") == generated for x in history):
        return {
            "status": "duplicate", "snapshot_at": generated,
            "course_count": len(courses), "coverage": coverage,
        }
    normalized = {}
    for course_id, item in courses.items():
        rain = item.get("rainfall") or {}
        station = rain.get("station") or {}
        weather = item.get("weather") or {}
        weather_station = weather.get("station") or {}
        normalized[str(course_id)] = {
            "course_id": str(course_id),
            "course_name": item.get("course_name") or item.get("name"),
            "station_code": station.get("code"),
            "station_name": station.get("name"),
            "station_observed_at": station.get("observed_at"),
            "distance_km": station.get("distance_km"),
            "quality_status": station.get("quality_status"),
            "quality_flags": station.get("quality_flags") or [],
            "precipitation_10m_mm": rain.get("precipitation_10m_mm"),
            "precipitation_1h_mm": rain.get("precipitation_1h_mm"),
            "precipitation_period": rain.get("precipitation_period"),
            "weather_station_code": weather_station.get("code"),
            "weather_station_name": weather_station.get("name"),
            "weather_station_observed_at": weather_station.get("observed_at"),
            "weather_station_distance_km": weather_station.get("distance_km"),
            "weather_station_quality_status": weather_station.get("quality_status"),
            "weather_station_observation_age_minutes": weather_station.get("observation_age_minutes"),
            "temperature_c": weather.get("temperature_c"),
            "relative_humidity_pct": weather.get("relative_humidity_pct"),
            "wind_speed_ms": weather.get("wind_speed_ms"),
            "wind_gust_ms": weather.get("wind_gust_ms"),
            "wind_direction_deg": weather.get("wind_direction_deg"),
            "weather_text": weather.get("weather_text"),
        }
    history.append({
        "snapshot_at": generated,
        "snapshot_schema_version": snapshot.get("schema_version", 1),
        "source": snapshot.get("source", "CWA"),
        "course_count": len(normalized),
        "coverage": coverage,
        "courses": normalized,
    })
    history.sort(key=lambda x: x.get("snapshot_at", ""))
    write_gzip_jsonl(path, history)
    return {
        "status": "archived", "snapshot_at": generated,
        "course_count": len(normalized), "age_minutes": round(age_min, 1),
        "coverage": coverage,
    }

def fetch_course_cache(name: str, rel: str):
    payload = get_json(COURSE_BASE + rel)
    entry = payload.get("courses", {}).get(name)
    if not isinstance(entry, dict):
        # Unicode normalization fallback for course display-name differences.
        entry = next((v for k, v in payload.get("courses", {}).items() if name_key(k) == name_key(name)), None)
    if not isinstance(entry, dict):
        raise RuntimeError(f"Course cache entry missing for {name}")
    return entry

def fetch_vc_cache(name: str, rel: str):
    payload = get_json(VC_BASE + rel)
    entry = payload.get("courses", {}).get(name)
    if not isinstance(entry, dict):
        entry = next((v for k, v in payload.get("courses", {}).items() if name_key(k) == name_key(name)), None)
    return entry if isinstance(entry, dict) else {}

def fetch_foreca_cache(name: str, rel: str):
    payload = get_json(FORECA_BASE + rel)
    entry = payload.get("courses", {}).get(name)
    if not isinstance(entry, dict):
        entry = next((v for k, v in payload.get("courses", {}).items() if name_key(k) == name_key(name)), None)
    return entry if isinstance(entry, dict) else {}

def archive_forecast_vintage(now: datetime) -> dict:
    issue_date = now.astimezone(TZ).date()
    out_path = ROOT / "forecasts" / f"{issue_date.isoformat()}.json.gz"
    previous_vintage = None
    if out_path.exists():
        try:
            previous_vintage = read_gzip_json(out_path)
            if (
                len(previous_vintage.get("courses", {})) == 57
                and previous_vintage.get("schema_version") == 2
                and previous_vintage.get("hour_alignment_version") == 3
                and previous_vintage.get("lead_days") == list(LEADS)
            ):
                return {"status": "already_archived", "issue_date": issue_date.isoformat()}
        except Exception:
            pass
        out_path.unlink(missing_ok=True)

    course_index = get_json(COURSE_INDEX_URL)
    course_generated = parse_dt(course_index.get("generated_at"))
    if not course_generated:
        raise RuntimeError("Course forecast index missing generated_at")
    age_h = (now.astimezone(timezone.utc) - course_generated).total_seconds() / 3600
    if age_h < -1 or age_h > FORECAST_MAX_AGE_HOURS:
        raise RuntimeError(f"Course forecast cache is stale: age={age_h:.1f} hours")
    names = course_index.get("courses", {})
    if len(names) != 57:
        raise RuntimeError(f"Course forecast index must contain all 57 courses; found {len(names)}")
    model_names = course_index.get("model_names", [])
    if len(model_names) < 6 or "ECMWF AIFS" not in model_names:
        raise RuntimeError(f"Expected six model names, found {model_names}")

    vc_index = {}
    vc_age_h = None
    try:
        vc_index = get_json(VC_INDEX_URL)
        vc_generated = parse_dt(vc_index.get("generated_at"))
        if vc_generated:
            vc_age_h = (now.astimezone(timezone.utc) - vc_generated).total_seconds() / 3600
        if not vc_generated or vc_age_h < -1 or vc_age_h > VC_MAX_AGE_HOURS:
            vc_index = {}
    except Exception as exc:
        print(f"Visual Crossing unavailable for this vintage: {exc}")

    foreca_index = {}
    foreca_age_h = None
    try:
        foreca_index = get_json(FORECA_INDEX_URL)
        foreca_generated = parse_dt(foreca_index.get("generated_at"))
        if foreca_generated:
            foreca_age_h = (now.astimezone(timezone.utc) - foreca_generated).total_seconds() / 3600
        if not foreca_generated or foreca_age_h < -1 or foreca_age_h > FORECA_MAX_AGE_HOURS:
            foreca_index = {}
    except Exception as exc:
        print(f"Foreca unavailable for this vintage: {exc}")
    if previous_vintage and previous_vintage.get("hour_alignment_version") == 2 and not foreca_index:
        write_gzip_json(out_path, previous_vintage)
        return {"status": "awaiting_fresh_foreca_cache", "issue_date": issue_date.isoformat()}

    live = get_json(LIVE_URL)
    course_id_by_name = {
        name_key(v.get("course_name") or v.get("name") or ""): str(k)
        for k, v in live.get("courses", {}).items()
    }
    course_items = list(names.items())
    vc_names = vc_index.get("courses", {}) if vc_index else {}
    vc_name_lookup = {name_key(k): (k, v) for k, v in vc_names.items()}
    foreca_names = foreca_index.get("courses", {}) if foreca_index else {}
    foreca_name_lookup = {name_key(k): (k, v) for k, v in foreca_names.items()}
    valid_dates = { (issue_date + timedelta(days=lead)).isoformat(): lead for lead in LEADS }
    archive = {
        "schema_version": 2,
        "hour_alignment_version": 3,
        "source_hour_alignment": {
            "Open-Meteo": "preceding-hour",
            "Visual Crossing": "timestamped-hour-period",
            "Foreca": "preceding-hour",
        },
        "issued_at": now.astimezone(TZ).isoformat(),
        "issue_date": issue_date.isoformat(),
        "timezone": "Asia/Taipei",
        "lead_days": list(LEADS),
        "forecast_cache_generated_at": course_index.get("generated_at"),
        "model_generated_at": course_index.get("model_generated_at", {}),
        "model_names": model_names,
        "model_metadata": course_index.get("model_metadata", {}),
        "visual_crossing_generated_at": vc_index.get("generated_at") if vc_index else None,
        "visual_crossing_age_hours": round(vc_age_h, 2) if vc_age_h is not None else None,
        "course_count": len(course_items),
        "sources": list(model_names) + (["Visual Crossing"] if vc_index else []) + (["Foreca"] if foreca_index else []),
        "courses": {},
        "errors": [],
    }

    def one_course(item):
        name, rel = item
        entry = fetch_course_cache(name, rel)
        vc_rel_item = vc_name_lookup.get(name_key(name))
        try:
            vc_entry = fetch_vc_cache(name, vc_rel_item[1]) if vc_rel_item else {}
        except Exception as exc:
            vc_entry = {}
            print(f"Visual Crossing cache unavailable for {name}: {exc}")
        valid = {}
        for valid_date, lead in valid_dates.items():
            day = entry.get("dates", {}).get(valid_date, {})
            model_rows = {}
            for model in model_names:
                rows = day.get("modelRows", {}).get(model, [])
                model_rows[model] = [
                    {
                        "time": str(r.get("time", ""))[:5],
                        "weather_time": str(r.get("time", ""))[:5],
                        "precip_mm": r.get("mm"),
                        "precip_probability_pct": r.get("rain"),
                        "temp_c": r.get("temp"),
                        "feel_c": r.get("feel"),
                        "wind_kmh": r.get("wind"),
                        "gust_kmh": r.get("gust"),
                        "humidity_pct": r.get("rh"),
                        "wind_dir_deg": r.get("dir"),
                        "cloud_pct": r.get("cloud"),
                        "uv_index": r.get("uv"),
                        "weather_code": r.get("code"),
                    }
                    for r in rows
                    if r.get("available") and str(r.get("time", ""))[:5] in {f"{h:02d}:00" for h in range(6, 19)}
                ]
            vc_day = vc_entry.get(valid_date, {})
            vc_provider = (vc_day.get("providers") or {}).get("Visual Crossing", {})
            vc_rows = vc_provider.get("hourly", []) if vc_provider.get("status") == "ok" else []
            if vc_rows:
                model_rows["Visual Crossing"] = [
                    {
                        "time": str(r.get("time", ""))[11:16],
                        "weather_time": f"{(int(str(r.get('time', ''))[11:13]) + 1):02d}:00",
                        "precip_mm": r.get("precipitation"),
                        "precip_probability_pct": r.get("precipitation_probability"),
                        "temp_c": r.get("temperature_2m"),
                        "feel_c": r.get("apparent_temperature"),
                        "wind_kmh": r.get("wind_speed_10m"),
                        "gust_kmh": r.get("wind_gusts_10m"),
                        "humidity_pct": r.get("relative_humidity_2m"),
                        "wind_dir_deg": r.get("wind_direction_10m"),
                        "cloud_pct": r.get("cloud_cover"),
                        "uv_index": r.get("uv_index"),
                        "weather_code": r.get("weather_code"),
                    }
                    for r in vc_rows
                    if str(r.get("time", ""))[11:13].isdigit()
                    and 5 <= int(str(r.get("time", ""))[11:13]) <= 17
                ]
            foreca_rel_item = foreca_name_lookup.get(name_key(name))
            try:
                foreca_entry = fetch_foreca_cache(name, foreca_rel_item[1]) if foreca_rel_item else {}
            except Exception as exc:
                foreca_entry = {}
                print(f"Foreca cache unavailable for {name}: {exc}")
            foreca_day = foreca_entry.get(valid_date, {})
            foreca_provider = (foreca_day.get("providers") or {}).get("Foreca", {})
            foreca_rows = foreca_provider.get("hourly", []) if foreca_provider.get("status") == "ok" else []
            if foreca_rows:
                model_rows["Foreca"] = [
                    {"time": str(r.get("time", ""))[11:16],
                     "weather_time": str(r.get("time", ""))[11:16],
                     "precip_mm": r.get("precipitation"),
                     "precip_probability_pct": r.get("precipitation_probability"),
                     "temp_c": r.get("temperature_2m"),
                     "feel_c": r.get("apparent_temperature"),
                     "wind_kmh": r.get("wind_speed_10m"),
                     "gust_kmh": r.get("wind_gusts_10m"),
                     "humidity_pct": r.get("relative_humidity_2m"),
                     "wind_dir_deg": r.get("wind_direction_10m"),
                     "cloud_pct": r.get("cloud_cover"),
                     "uv_index": r.get("uv_index"),
                     "weather_code": r.get("weather_code")}
                    for r in foreca_rows
                    if str(r.get("time", ""))[11:13].isdigit()
                    and 6 <= int(str(r.get("time", ""))[11:13]) <= 18
                ]
            valid[valid_date] = {"lead_days": lead, "sources": model_rows}
        course_id = course_id_by_name.get(name_key(name))
        return name, {"course_id": course_id, "valid_dates": valid}

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(one_course, item) for item in course_items]
        for future in concurrent.futures.as_completed(futures):
            try:
                name, value = future.result()
                archive["courses"][name] = value
            except Exception as exc:
                archive["errors"].append(str(exc))
    if len(archive["courses"]) != 57:
        raise RuntimeError(f"Forecast vintage incomplete: {len(archive['courses'])}/{len(course_items)} courses; refusing partial archive")
    model_coverage = {
        model: sum(
            1 for course in archive["courses"].values()
            if any(day.get("sources", {}).get(model) for day in course["valid_dates"].values())
        )
        for model in model_names
    }
    archive["model_course_coverage"] = model_coverage
    archive["visual_crossing_course_coverage"] = sum(
        1 for course in archive["courses"].values()
        if any(day.get("sources", {}).get("Visual Crossing") for day in course["valid_dates"].values())
    )
    archive["foreca_course_coverage"] = sum(
        1 for course in archive["courses"].values()
        if any(day.get("sources", {}).get("Foreca") for day in course["valid_dates"].values())
    )
    write_gzip_json(out_path, archive)
    return {
        "status": "archived",
        "issue_date": issue_date.isoformat(),
        "courses": len(archive["courses"]),
        "model_coverage": model_coverage,
        "visual_crossing_coverage": archive["visual_crossing_course_coverage"],
        "forecast_cache_age_hours": round(age_h, 2),
    }

class Score:
    def __init__(self):
        self.n = 0
        self.abs_error = 0.0
        self.squared_error = 0.0
        self.bias = 0.0
        self.wet_abs_error = 0.0
        self.wet_squared_error = 0.0
        self.wet_sample_count = 0
        self.probability_wet_sample_count = 0
        self.vintages = set()
        self.probability_vintages = set()
        self.hit = self.miss = self.false_alarm = self.correct_negative = 0
        self.brier_sum = 0.0
        self.prob_n = 0
    def add(self, forecast, actual, probability, vintage_id=None):
        if vintage_id is not None:
            self.vintages.add(str(vintage_id))
        self.n += 1
        error = forecast - actual
        self.abs_error += abs(error)
        self.squared_error += error * error
        self.bias += error
        if actual >= 0.1:
            self.wet_abs_error += abs(error)
            self.wet_squared_error += error * error
            self.wet_sample_count += 1
        event_actual = actual >= 0.1
        event_forecast = forecast >= 0.1
        if event_forecast and event_actual: self.hit += 1
        elif event_actual: self.miss += 1
        elif event_forecast: self.false_alarm += 1
        else: self.correct_negative += 1
        if probability is not None:
            if vintage_id is not None:
                self.probability_vintages.add(str(vintage_id))
            if event_actual:
                self.probability_wet_sample_count += 1
            p = max(0.0, min(1.0, probability / 100.0))
            y = 1.0 if event_actual else 0.0
            self.brier_sum += (p - y) ** 2
            self.prob_n += 1
    def result(self):
        n = self.n
        csi_den = self.hit + self.miss + self.false_alarm
        pod_den = self.hit + self.miss
        far_den = self.hit + self.false_alarm
        return {
            "sample_count": n,
            "MAE_mm": round(self.abs_error / n, 3) if n else None,
            "RMSE_mm": round(math.sqrt(self.squared_error / n), 3) if n else None,
            "Bias_mm": round(self.bias / n, 3) if n else None,
            "wet_sample_count": self.wet_sample_count,
            "distinct_vintage_count": len(self.vintages),
            "probability_wet_sample_count": self.probability_wet_sample_count,
            "probability_vintage_count": len(self.probability_vintages),
            "MAE_wet_mm": round(self.wet_abs_error / self.wet_sample_count, 3) if self.wet_sample_count else None,
            "RMSE_wet_mm": round(math.sqrt(self.wet_squared_error / self.wet_sample_count), 3) if self.wet_sample_count else None,
            "Weight_error_mm": round(
                0.5 * (self.abs_error / n) + 0.5 * (self.wet_abs_error / self.wet_sample_count), 3
            ) if n and self.wet_sample_count >= 20 else None,
            "POD": round(self.hit / pod_den, 4) if pod_den else None,
            "FAR": round(self.false_alarm / far_den, 4) if far_den else None,
            "CSI": round(self.hit / csi_den, 4) if csi_den else None,
            "hit": self.hit, "miss": self.miss,
            "false_alarm": self.false_alarm, "correct_negative": self.correct_negative,
            "probability_sample_count": self.prob_n,
            "Brier_score": round(self.brier_sum / self.prob_n, 4) if self.prob_n else None,
        }

class ContinuousScore:
    """Continuous-variable forecast skill against quality-controlled CWA observations."""
    def __init__(self):
        self.n = 0
        self.abs_error = 0.0
        self.squared_error = 0.0
        self.bias = 0.0
        self.vintages = set()

    def add(self, forecast, actual, vintage_id=None):
        error = float(forecast) - float(actual)
        self.n += 1
        self.abs_error += abs(error)
        self.squared_error += error * error
        self.bias += error
        if vintage_id is not None:
            self.vintages.add(str(vintage_id))

    def result(self):
        return {
            "sample_count": self.n,
            "distinct_vintage_count": len(self.vintages),
            "MAE": round(self.abs_error / self.n, 3) if self.n else None,
            "RMSE": round(math.sqrt(self.squared_error / self.n), 3) if self.n else None,
            "Bias": round(self.bias / self.n, 3) if self.n else None,
        }


def build_actual_hourly(now: datetime):
    cutoff = now.astimezone(TZ).date() - timedelta(days=60)
    samples = defaultdict(dict)
    obs_dir = ROOT / "observations"
    if not obs_dir.exists():
        return {}
    for path in sorted(obs_dir.glob("*.jsonl.gz")):
        try:
            day = date.fromisoformat(path.name.split(".")[0])
        except Exception:
            continue
        if day < cutoff:
            continue
        for snapshot in read_gzip_jsonl(path):
            # Only use the new direct-CWA nationwide snapshot format for
            # verification; older static seed snapshots are retained but excluded.
            if snapshot.get("snapshot_schema_version", 1) < 2:
                continue
            for course_id, item in snapshot.get("courses", {}).items():
                name = item.get("course_name") or course_id
                station = item.get("station_code")
                stamp = parse_dt(item.get("station_observed_at"))
                distance = item.get("distance_km")
                if not station or not stamp or item.get("quality_status") != "passed":
                    continue
                if distance is None or float(distance) > MIN_STATION_DISTANCE_KM:
                    continue
                local = stamp.astimezone(TZ)
                # A 10-minute accumulation belongs to the hour ending at its observation time.
                hour = (local - timedelta(minutes=1)).hour
                valid_date = local.date().isoformat()
                key = (name_key(name), valid_date, hour, str(station))
                ts_key = stamp.isoformat()
                val10 = item.get("precipitation_10m_mm")
                val1h = item.get("precipitation_1h_mm")
                samples[key][ts_key] = {
                    "at": local,
                    "mm10": float(val10) if isinstance(val10, (int, float)) and val10 >= 0 else None,
                    "mm1h": float(val1h) if isinstance(val1h, (int, float)) and val1h >= 0 else None,
                }
    actual = defaultdict(dict)
    for (course, valid_date, hour, station), values in samples.items():
        valid = [v for v in values.values() if v["mm10"] is not None]
        if len(valid) >= MIN_10M_SAMPLES:
            amount = sum(v["mm10"] for v in valid)
            actual[course][(valid_date, hour)] = {
                "precip_mm": amount, "sample_count": len(valid),
                "station_code": station, "method": "sum_10min",
            }
        else:
            # Fallback to the latest rolling 1-hour accumulation within 15 minutes
            # of the end of the target hour. It is not summed with 10-minute values.
            end = datetime.fromisoformat(valid_date).replace(tzinfo=TZ) + timedelta(hours=hour + 1)
            candidates = [
                v for v in values.values()
                if v["mm1h"] is not None and abs((v["at"] - end).total_seconds()) <= 15 * 60
            ]
            if candidates:
                chosen = min(candidates, key=lambda v: abs((v["at"] - end).total_seconds()))
                actual[course][(valid_date, hour)] = {
                    "precip_mm": chosen["mm1h"], "sample_count": 1,
                    "station_code": station, "method": "rolling_1h_fallback",
                }
    return actual

def build_actual_weather_hourly(now: datetime):
    """Build point-in-time CWA weather observations keyed to the local observation hour."""
    cutoff = now.astimezone(TZ).date() - timedelta(days=60)
    samples = defaultdict(dict)
    obs_dir = ROOT / "observations"
    if not obs_dir.exists():
        return {}
    for path in sorted(obs_dir.glob("*.jsonl.gz")):
        try:
            day = date.fromisoformat(path.name.split(".")[0])
        except Exception:
            continue
        if day < cutoff:
            continue
        for snapshot in read_gzip_jsonl(path):
            if snapshot.get("snapshot_schema_version", 1) < 2:
                continue
            for course_id, item in snapshot.get("courses", {}).items():
                name = item.get("course_name") or course_id
                station = item.get("weather_station_code")
                stamp = parse_dt(item.get("weather_station_observed_at"))
                distance = item.get("weather_station_distance_km")
                age = item.get("weather_station_observation_age_minutes")
                if not station or not stamp or item.get("weather_station_quality_status") != "passed":
                    continue
                if distance is None or float(distance) > MIN_STATION_DISTANCE_KM:
                    continue
                if age is not None and float(age) > WEATHER_OBS_MAX_AGE_MIN:
                    continue
                local = stamp.astimezone(TZ)
                valid_date = local.date().isoformat()
                key = (name_key(name), valid_date, local.hour)
                ts_key = f"{station}|{stamp.isoformat()}"
                samples[key][ts_key] = {
                    "observed_at": stamp,
                    "station_code": str(station),
                    "distance_km": float(distance),
                    "temperature_c": item.get("temperature_c"),
                    "relative_humidity_pct": item.get("relative_humidity_pct"),
                    "wind_speed_kmh": (
                        float(item["wind_speed_ms"]) * 3.6
                        if isinstance(item.get("wind_speed_ms"), (int, float)) else None
                    ),
                    "wind_gust_kmh": (
                        float(item["wind_gust_ms"]) * 3.6
                        if isinstance(item.get("wind_gust_ms"), (int, float)) else None
                    ),
                    "wind_direction_deg": item.get("wind_direction_deg"),
                }
    actual = defaultdict(dict)
    for (course, valid_date, hour), rows in samples.items():
        # Prefer the closest quality-controlled station; break ties with the latest observation.
        chosen = min(
            rows.values(),
            key=lambda row: (row["distance_km"], -row["observed_at"].timestamp()),
        )
        actual[course][(valid_date, hour)] = chosen
    return actual


def build_daily_verification_pairs(source, hours, valid_date, course_key, actual_rain, actual_weather):
    """Pair source hourly rows with CWA observations for 06:00-18:00 daily aggregates."""
    pairs = {
        "rain": [],
        "temperature": [],
        "wind": [],
        "gust": [],
        "humidity": [],
    }
    for row in hours:
        try:
            forecast_hour = int(str(row.get("time", ""))[:2])
        except Exception:
            forecast_hour = -1
        if source == "Visual Crossing":
            rain_hour = forecast_hour if 5 <= forecast_hour <= 17 else None
        else:
            rain_hour = forecast_hour - 1 if 6 <= forecast_hour <= 18 else None
        if rain_hour is not None:
            observed_rain = actual_rain.get(course_key, {}).get((valid_date, rain_hour))
            forecast_mm = row.get("precip_mm")
            if (
                observed_rain
                and isinstance(forecast_mm, (int, float))
                and math.isfinite(forecast_mm)
            ):
                probability = row.get("precip_probability_pct")
                if not isinstance(probability, (int, float)) or not math.isfinite(probability):
                    probability = None
                pairs["rain"].append((float(forecast_mm), float(observed_rain["precip_mm"]), probability))

        weather_time = str(row.get("weather_time") or row.get("time") or "")
        try:
            weather_hour = int(weather_time[:2])
        except Exception:
            continue
        if not 6 <= weather_hour <= 18:
            continue
        observed_weather = actual_weather.get(course_key, {}).get((valid_date, weather_hour))
        if not observed_weather:
            continue
        for key, forecast_key, actual_key in (
            ("temperature", "temp_c", "temperature_c"),
            ("wind", "wind_kmh", "wind_speed_kmh"),
            ("gust", "gust_kmh", "wind_gust_kmh"),
            ("humidity", "humidity_pct", "relative_humidity_pct"),
        ):
            forecast_value = row.get(forecast_key)
            actual_value = observed_weather.get(actual_key)
            if (
                isinstance(forecast_value, (int, float)) and math.isfinite(forecast_value)
                and isinstance(actual_value, (int, float)) and math.isfinite(actual_value)
            ):
                pairs[key].append((float(forecast_value), float(actual_value)))
    return pairs


def normalized_weights(scores: dict, metric_key: str, minimum_n: int, prior_strength: float, cap: float = 0.35, minimum_vintages: int = MIN_FORECAST_VINTAGES):
    is_probability = metric_key == "Brier_score"
    # The caller normalizes both amount and probability metrics to sample_count.
    count_key = "sample_count"
    wet_key = "probability_wet_sample_count" if is_probability else "wet_sample_count"
    vintage_key = "probability_vintage_count" if is_probability else "distinct_vintage_count"
    wet_minimum = MIN_GLOBAL_WET_PAIRS if minimum_n == MIN_GLOBAL_PAIRS else MIN_COURSE_WET_PAIRS
    eligible = {
        name: row for name, row in scores.items()
        if row.get(count_key, 0) >= minimum_n
        and row.get(wet_key, 0) >= wet_minimum
        and row.get(vintage_key, 0) >= minimum_vintages
        and isinstance(row.get(metric_key), (int, float))
    }
    if len(eligible) < 3:
        return None
    raw = {name: 1.0 / max(float(row[metric_key]), 0.02) for name, row in eligible.items()}
    total = sum(raw.values())
    empirical = {name: value / total for name, value in raw.items()}
    prior = 1.0 / len(eligible)
    out = {}
    for name, row in eligible.items():
        n = row.get(count_key, 0)
        shrink = n / (n + prior_strength)
        out[name] = prior * (1 - shrink) + empirical[name] * shrink
    for _ in range(5):
        changed = False
        for name in out:
            if out[name] > cap:
                excess = out[name] - cap
                out[name] = cap
                others = [k for k in out if k != name]
                denom = sum(out[k] for k in others)
                if denom:
                    for k in others: out[k] += excess * out[k] / denom
                changed = True
        if not changed: break
    total = sum(out.values())
    return {k: round(v / total, 5) for k, v in out.items()}

def normalized_continuous_weights(
    scores: dict, minimum_n: int, minimum_vintages: int, prior_strength: int, cap: float = 0.35
):
    """Create bounded inverse-MAE weights for core models only."""
    eligible = {
        name: row for name, row in scores.items()
        if name in CORE_MODEL_SOURCES
        and row.get("sample_count", 0) >= minimum_n
        and row.get("distinct_vintage_count", 0) >= minimum_vintages
        and isinstance(row.get("MAE"), (int, float))
    }
    if len(eligible) < 3:
        return None
    raw = {name: 1.0 / max(float(row["MAE"]), 0.05) for name, row in eligible.items()}
    total = sum(raw.values())
    empirical = {name: value / total for name, value in raw.items()}
    prior = 1.0 / len(eligible)
    out = {}
    for name, row in eligible.items():
        shrink = row["sample_count"] / (row["sample_count"] + prior_strength)
        out[name] = prior * (1 - shrink) + empirical[name] * shrink
    for _ in range(5):
        changed = False
        for name in out:
            if out[name] > cap:
                excess = out[name] - cap
                out[name] = cap
                others = [key for key in out if key != name]
                denominator = sum(out[key] for key in others)
                if denominator:
                    for key in others:
                        out[key] += excess * out[key] / denominator
                changed = True
        if not changed:
            break
    total = sum(out.values())
    return {key: round(value / total, 5) for key, value in out.items()}


def verify_and_weight(now: datetime) -> dict:
    forecasts_dir = ROOT / "forecasts"
    actual = build_actual_hourly(now)
    actual_weather = build_actual_weather_hourly(now)
    global_scores = {str(lead): defaultdict(Score) for lead in LEADS}
    course_scores = defaultdict(lambda: {str(lead): defaultdict(Score) for lead in LEADS})
    variable_specs = {
        "temperature_c": ("temp_c", "temperature_c", 1.0, "°C"),
        "wind_speed_kmh": ("wind_kmh", "wind_speed_kmh", 1.0, "km/h"),
        "wind_gust_kmh": ("gust_kmh", "wind_gust_kmh", 1.0, "km/h"),
        "relative_humidity_pct": ("humidity_pct", "relative_humidity_pct", 1.0, "%"),
    }
    global_variable_scores = {
        variable: {str(lead): defaultdict(ContinuousScore) for lead in LEADS}
        for variable in variable_specs
    }
    course_variable_scores = defaultdict(lambda: {
        variable: {str(lead): defaultdict(ContinuousScore) for lead in LEADS}
        for variable in variable_specs
    })
    daily_variable_specs = {
        "temperature_high_c": ("temperature", "max", "°C"),
        "temperature_low_c": ("temperature", "min", "°C"),
        "wind_speed_kmh": ("wind", "median", "km/h"),
        "wind_gust_kmh": ("gust", "max", "km/h"),
        "relative_humidity_pct": ("humidity", "median", "%"),
    }
    global_daily_variable_scores = {
        variable: {str(lead): defaultdict(ContinuousScore) for lead in LEADS}
        for variable in daily_variable_specs
    }
    course_daily_variable_scores = defaultdict(lambda: {
        variable: {str(lead): defaultdict(ContinuousScore) for lead in LEADS}
        for variable in daily_variable_specs
    })
    global_daily_rain_scores = {str(lead): defaultdict(Score) for lead in LEADS}
    course_daily_rain_scores = defaultdict(lambda: {str(lead): defaultdict(Score) for lead in LEADS})
    forecast_files = sorted(forecasts_dir.glob("*.json.gz")) if forecasts_dir.exists() else []
    cutoff = now.astimezone(TZ).date() - timedelta(days=60)
    today = now.astimezone(TZ).date()
    evaluated_vintages = 0
    for path in forecast_files:
        try:
            archive = read_gzip_json(path)
            issue_date = date.fromisoformat(archive["issue_date"])
        except Exception:
            continue
        if issue_date < cutoff:
            continue
        for course_name, course in archive.get("courses", {}).items():
            course_key = name_key(course_name)
            for valid_date, day in course.get("valid_dates", {}).items():
                if valid_date >= today.isoformat():
                    continue
                lead = str(day.get("lead_days"))
                if lead not in global_scores:
                    continue
                vintage_id = archive.get("issue_date", path.stem.split(".")[0])
                for source, hours in day.get("sources", {}).items():
                    for row in hours:
                        try:
                            forecast_hour = int(str(row.get("time", ""))[:2])
                            if source == "Visual Crossing":
                                if not 5 <= forecast_hour <= 17:
                                    continue
                                # Visual Crossing precipitation is the amount for
                                # the timestamped hourly period (05:00 = 05:00-06:00).
                                hour = forecast_hour
                            else:
                                if not 6 <= forecast_hour <= 18:
                                    continue
                                # Open-Meteo precipitation/probability are preceding-hour
                                # values (06:00 verifies against 05:00-06:00).
                                hour = forecast_hour - 1
                        except Exception:
                            continue
                        vintage_id = archive.get("issue_date", path.stem.split(".")[0])
                        observed = actual.get(course_key, {}).get((valid_date, hour))
                        if observed:
                            forecast_mm = row.get("precip_mm")
                            actual_mm = observed["precip_mm"]
                            if isinstance(forecast_mm, (int, float)) and math.isfinite(forecast_mm):
                                prob = row.get("precip_probability_pct")
                                if not isinstance(prob, (int, float)) or not math.isfinite(prob):
                                    prob = None
                                global_scores[lead][source].add(float(forecast_mm), float(actual_mm), prob, vintage_id)
                                course_scores[course_name][lead][source].add(float(forecast_mm), float(actual_mm), prob, vintage_id)

                        # Temperature, wind, gust and humidity are point forecasts, so compare
                        # them at their valid timestamp rather than the preceding rain interval.
                        weather_time = str(row.get("weather_time") or row.get("time") or "")
                        try:
                            weather_hour = int(weather_time[:2])
                            if not 0 <= weather_hour <= 23:
                                continue
                        except Exception:
                            continue
                        observed_weather = actual_weather.get(course_key, {}).get((valid_date, weather_hour))
                        if not observed_weather:
                            continue
                        for variable, (forecast_key, actual_key, scale, _unit) in variable_specs.items():
                            forecast_value = row.get(forecast_key)
                            actual_value = observed_weather.get(actual_key)
                            if (
                                isinstance(forecast_value, (int, float)) and math.isfinite(forecast_value)
                                and isinstance(actual_value, (int, float)) and math.isfinite(actual_value)
                            ):
                                actual_scaled = float(actual_value) * scale
                                global_variable_scores[variable][lead][source].add(float(forecast_value), actual_scaled, vintage_id)
                                course_variable_scores[course_name][variable][lead][source].add(float(forecast_value), actual_scaled, vintage_id)

                # Independently verify the daily 06:00-18:00 aggregates used by the 16-day panel.
                for source, hours in day.get("sources", {}).items():
                    daily_pairs = build_daily_verification_pairs(
                        source, hours, valid_date, course_key, actual, actual_weather
                    )
                    rain_pairs = daily_pairs["rain"]
                    if len(rain_pairs) >= 9:
                        rain_forecast = sum(pair[0] for pair in rain_pairs)
                        rain_actual = sum(pair[1] for pair in rain_pairs)
                        probabilities = [pair[2] for pair in rain_pairs if pair[2] is not None]
                        daily_probability = max(probabilities) if probabilities else None
                        global_daily_rain_scores[lead][source].add(
                            rain_forecast, rain_actual, daily_probability, vintage_id
                        )
                        course_daily_rain_scores[course_name][lead][source].add(
                            rain_forecast, rain_actual, daily_probability, vintage_id
                        )
                    for variable, (pair_key, statistic, _unit) in daily_variable_specs.items():
                        values = daily_pairs[pair_key]
                        minimum_hours = 6 if pair_key == "gust" else 9
                        if len(values) < minimum_hours:
                            continue
                        forecast_values = [pair[0] for pair in values]
                        actual_values = [pair[1] for pair in values]
                        if statistic == "max":
                            forecast_daily = max(forecast_values)
                            actual_daily = max(actual_values)
                        elif statistic == "min":
                            forecast_daily = min(forecast_values)
                            actual_daily = min(actual_values)
                        else:
                            forecast_daily = statistics_median(forecast_values)
                            actual_daily = statistics_median(actual_values)
                        global_daily_variable_scores[variable][lead][source].add(
                            forecast_daily, actual_daily, vintage_id
                        )
                        course_daily_variable_scores[course_name][variable][lead][source].add(
                            forecast_daily, actual_daily, vintage_id
                        )
        evaluated_vintages += 1

    metrics = {
        "schema_version": 3,
        "generated_at": now.astimezone(TZ).isoformat(),
        "verification_source": "CWA rainfall and hourly weather observations",
        "verification_window_days": 60,
        "station_distance_limit_km": MIN_STATION_DISTANCE_KM,
        "rain_event_threshold_mm": 0.1,
        "forecast_hours": "06:00-18:00",
        "evaluated_vintages": evaluated_vintages,
        "minimum_evaluated_vintages_for_weights": MIN_FORECAST_VINTAGES,
        "minimum_global_wet_samples_for_weights": MIN_GLOBAL_WET_PAIRS,
        "minimum_course_wet_samples_for_weights": MIN_COURSE_WET_PAIRS,
        "minimum_course_vintages_for_weights": MIN_COURSE_VINTAGES,
        "minimum_global_variable_samples_for_weights": MIN_GLOBAL_PAIRS,
        "minimum_global_variable_vintages_for_weights": MIN_FORECAST_VINTAGES,
        "minimum_course_variable_samples_for_weights": MIN_COURSE_PAIRS,
        "minimum_course_variable_vintages_for_weights": MIN_COURSE_VINTAGES,
        "minimum_daily_course_samples_for_weights": MIN_DAILY_COURSE_PAIRS,
        "lead_days": {},
        "by_course": {},
        "variables": {},
        "daily_variables": {},
    }
    weights = {
        "schema_version": 3,
        "generated_at": now.astimezone(TZ).isoformat(),
        "status": "warming_up",
        "method": "Hourly and daily rainfall: balanced dry/wet inverse-error with wet-event and vintage gates. Hourly and daily core temperature/wind/gust/humidity: inverse-MAE weights with vintage gates. Equal-prior shrinkage and 0.35 maximum source weight.",
        "minimum_evaluated_vintages": MIN_FORECAST_VINTAGES,
        "minimum_global_wet_samples": MIN_GLOBAL_WET_PAIRS,
        "minimum_course_wet_samples": MIN_COURSE_WET_PAIRS,
        "minimum_course_vintages": MIN_COURSE_VINTAGES,
        "minimum_global_variable_samples": MIN_GLOBAL_PAIRS,
        "minimum_global_variable_vintages": MIN_FORECAST_VINTAGES,
        "minimum_course_variable_samples": MIN_COURSE_PAIRS,
        "minimum_course_variable_vintages": MIN_COURSE_VINTAGES,
        "minimum_daily_course_samples": MIN_DAILY_COURSE_PAIRS,
        "verification_source": "CWA rainfall and hourly weather observations",
        "lead_days": {},
        "by_course": {},
        "variables": {},
        "daily_variables": {},
    }
    active_leads = 0
    active_variable_leads = 0
    active_daily_variable_leads = 0
    active_daily_rain_leads = 0
    for lead in LEADS:
        key = str(lead)
        rows = {source: score.result() for source, score in global_scores[key].items()}
        metrics["lead_days"][key] = rows
        if evaluated_vintages >= MIN_FORECAST_VINTAGES:
            amount_weights = normalized_weights(rows, "Weight_error_mm", MIN_GLOBAL_PAIRS, 2000)
            prob_scores = {
                source: {
                    "sample_count": row.get("probability_sample_count", 0),
                    "probability_wet_sample_count": row.get("probability_wet_sample_count", 0),
                    "probability_vintage_count": row.get("probability_vintage_count", 0),
                    "Brier_score": row.get("Brier_score"),
                }
                for source, row in rows.items()
            }
            probability_weights = normalized_weights(prob_scores, "Brier_score", MIN_GLOBAL_PAIRS, 2000, 0.35)
        else:
            amount_weights = None
            probability_weights = None
        if amount_weights or probability_weights:
            active_leads += 1
        weights["lead_days"][key] = {
            "sample_count_by_source": {source: row.get("sample_count", 0) for source, row in rows.items()},
            "amount_weights": amount_weights,
            "probability_weights": probability_weights,
            "weighting_active": bool(amount_weights or probability_weights),
            "amount_weighting_active": bool(amount_weights),
            "probability_weighting_active": bool(probability_weights),
        }

    for variable, (_forecast_key, _actual_key, _scale, unit) in variable_specs.items():
        variable_leads = {}
        for lead in LEADS:
            key = str(lead)
            variable_leads[key] = {
                source: score.result()
                for source, score in global_variable_scores[variable][key].items()
            }
        variable_by_course = {}
        for course_name, course_vars in course_variable_scores.items():
            variable_by_course[course_name] = {
                str(lead): {
                    source: score.result()
                    for source, score in course_vars[variable][str(lead)].items()
                }
                for lead in LEADS
            }
        metrics["variables"][variable] = {
            "unit": unit,
            "lead_days": variable_leads,
            "by_course": variable_by_course,
        }
        global_variable_weights = {}
        course_variable_weights = {}
        for lead in LEADS:
            key = str(lead)
            if evaluated_vintages >= MIN_FORECAST_VINTAGES:
                global_weights = normalized_continuous_weights(
                    variable_leads[key], MIN_GLOBAL_PAIRS, MIN_FORECAST_VINTAGES, 2000
                )
            else:
                global_weights = None
            global_variable_weights[key] = global_weights
            if global_weights:
                active_variable_leads += 1
            for course_name, course_rows in variable_by_course.items():
                if evaluated_vintages < MIN_FORECAST_VINTAGES:
                    continue
                local_weights = normalized_continuous_weights(
                    course_rows[key], MIN_COURSE_PAIRS, MIN_COURSE_VINTAGES, 500
                )
                if local_weights:
                    course_variable_weights.setdefault(course_name, {})[key] = local_weights
        weights["variables"][variable] = {
            "lead_days": global_variable_weights,
            "by_course": course_variable_weights,
        }

    daily_continuous_metrics = {}
    daily_continuous_weights = {}
    for variable, (_pair_key, _statistic, unit) in daily_variable_specs.items():
        lead_rows = {}
        course_rows = {}
        for lead in LEADS:
            key = str(lead)
            lead_rows[key] = {
                source: score.result()
                for source, score in global_daily_variable_scores[variable][key].items()
            }
        for course_name, course_vars in course_daily_variable_scores.items():
            course_rows[course_name] = {
                str(lead): {
                    source: score.result()
                    for source, score in course_vars[variable][str(lead)].items()
                }
                for lead in LEADS
            }
        daily_continuous_metrics[variable] = {
            "unit": unit,
            "lead_days": lead_rows,
            "by_course": course_rows,
        }
        global_weights_by_lead = {}
        course_weights_by_course = {}
        for lead in LEADS:
            key = str(lead)
            if evaluated_vintages >= MIN_FORECAST_VINTAGES:
                global_weights = normalized_continuous_weights(
                    lead_rows[key], MIN_GLOBAL_PAIRS, MIN_FORECAST_VINTAGES, 500
                )
            else:
                global_weights = None
            global_weights_by_lead[key] = global_weights
            if global_weights:
                active_daily_variable_leads += 1
            for course_name, local_leads in course_rows.items():
                if evaluated_vintages < MIN_FORECAST_VINTAGES:
                    continue
                local_weights = normalized_continuous_weights(
                    local_leads[key], MIN_DAILY_COURSE_PAIRS, MIN_COURSE_VINTAGES, 250
                )
                if local_weights:
                    course_weights_by_course.setdefault(course_name, {})[key] = local_weights
        daily_continuous_weights[variable] = {
            "lead_days": global_weights_by_lead,
            "by_course": course_weights_by_course,
        }

    daily_rain_metrics = {}
    daily_rain_by_course = {}
    daily_rain_weights = {}
    for lead in LEADS:
        key = str(lead)
        daily_rain_metrics[key] = {
            source: score.result()
            for source, score in global_daily_rain_scores[key].items()
        }
    for course_name, course_leads in course_daily_rain_scores.items():
        daily_rain_by_course[course_name] = {
            str(lead): {
                source: score.result()
                for source, score in course_leads[str(lead)].items()
            }
            for lead in LEADS
        }
    for lead in LEADS:
        key = str(lead)
        core_rows = {
            source: row for source, row in daily_rain_metrics[key].items()
            if source in CORE_MODEL_SOURCES
        }
        if evaluated_vintages >= MIN_FORECAST_VINTAGES:
            amount_weights = normalized_weights(core_rows, "Weight_error_mm", MIN_GLOBAL_PAIRS, 2000)
            probability_scores = {
                source: {
                    "sample_count": row.get("probability_sample_count", 0),
                    "probability_wet_sample_count": row.get("probability_wet_sample_count", 0),
                    "probability_vintage_count": row.get("probability_vintage_count", 0),
                    "Brier_score": row.get("Brier_score"),
                }
                for source, row in core_rows.items()
            }
            probability_weights = normalized_weights(
                probability_scores, "Brier_score", MIN_GLOBAL_PAIRS, 2000, 0.35
            )
        else:
            amount_weights = None
            probability_weights = None
        if amount_weights or probability_weights:
            active_daily_rain_leads += 1
        daily_rain_weights[key] = {
            "amount_weights": amount_weights,
            "probability_weights": probability_weights,
        }
    daily_rain_course_weights = {}
    for course_name, course_leads in daily_rain_by_course.items():
        local_out = {}
        for lead in LEADS:
            key = str(lead)
            core_rows = {
                source: row for source, row in course_leads[key].items()
                if source in CORE_MODEL_SOURCES
            }
            if evaluated_vintages < MIN_FORECAST_VINTAGES:
                continue
            amount_weights = normalized_weights(
                core_rows, "Weight_error_mm", MIN_DAILY_COURSE_PAIRS, 500,
                minimum_vintages=MIN_COURSE_VINTAGES,
            )
            probability_scores = {
                source: {
                    "sample_count": row.get("probability_sample_count", 0),
                    "probability_wet_sample_count": row.get("probability_wet_sample_count", 0),
                    "probability_vintage_count": row.get("probability_vintage_count", 0),
                    "Brier_score": row.get("Brier_score"),
                }
                for source, row in core_rows.items()
            }
            probability_weights = normalized_weights(
                probability_scores, "Brier_score", MIN_DAILY_COURSE_PAIRS, 500, 0.35,
                minimum_vintages=MIN_COURSE_VINTAGES,
            )
            if amount_weights or probability_weights:
                local_out[key] = {
                    "amount_weights": amount_weights,
                    "probability_weights": probability_weights,
                }
        if local_out:
            daily_rain_course_weights[course_name] = local_out

    metrics["daily_variables"] = daily_continuous_metrics
    metrics["daily_variables"]["rain_mm"] = {
        "unit": "mm",
        "lead_days": daily_rain_metrics,
        "by_course": daily_rain_by_course,
    }
    weights["daily_variables"] = daily_continuous_weights
    weights["daily_variables"]["rain_mm"] = {
        "lead_days": daily_rain_weights,
        "by_course": daily_rain_course_weights,
    }

    for course_name, leads in course_scores.items():
        course_out = {}
        course_weight_out = {}
        for lead in LEADS:
            key = str(lead)
            rows = {source: score.result() for source, score in leads[key].items()}
            course_out[key] = rows
            local_weights = normalized_weights(rows, "Weight_error_mm", MIN_COURSE_PAIRS, 500, minimum_vintages=MIN_COURSE_VINTAGES) if evaluated_vintages >= MIN_FORECAST_VINTAGES else None
            if local_weights:
                course_weight_out[key] = local_weights
        metrics["by_course"][course_name] = course_out
        if course_weight_out:
            weights["by_course"][course_name] = course_weight_out

    if active_leads or active_variable_leads or active_daily_variable_leads or active_daily_rain_leads:
        weights["status"] = "active"
    report = {
        "generated_at": metrics["generated_at"],
        "status": weights["status"],
        "evaluated_vintages": evaluated_vintages,
        "actual_course_hours": sum(len(v) for v in actual.values()),
        "actual_weather_course_hours": sum(len(v) for v in actual_weather.values()),
        "verified_variables": list(variable_specs),
        "minimum_evaluated_vintages_for_weights": MIN_FORECAST_VINTAGES,
        "minimum_global_wet_samples_for_weights": MIN_GLOBAL_WET_PAIRS,
        "minimum_course_wet_samples_for_weights": MIN_COURSE_WET_PAIRS,
        "minimum_course_vintages_for_weights": MIN_COURSE_VINTAGES,
        "minimum_global_variable_samples_for_weights": MIN_GLOBAL_PAIRS,
        "minimum_global_variable_vintages_for_weights": MIN_FORECAST_VINTAGES,
        "minimum_course_variable_samples_for_weights": MIN_COURSE_PAIRS,
        "minimum_course_variable_vintages_for_weights": MIN_COURSE_VINTAGES,
        "active_variable_weight_leads": active_variable_leads,
        "active_daily_variable_weight_leads": active_daily_variable_leads,
        "active_daily_rain_weight_leads": active_daily_rain_leads,
        "lead_days": {
            key: {
                "sources": len(metrics["lead_days"][key]),
                "active_amount_weights": weights["lead_days"][key]["weighting_active"],
                "scores": metrics["lead_days"][key],
            }
            for key in map(str, LEADS)
        },
    }
    ROOT.joinpath("verification", "metrics").mkdir(parents=True, exist_ok=True)
    ROOT.joinpath("verification", "reports").mkdir(parents=True, exist_ok=True)
    (ROOT / "verification" / "metrics" / "latest.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ROOT / "verification" / "weights.json").write_text(
        json.dumps(weights, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path = ROOT / "verification" / "reports" / f"{today.isoformat()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report

def main():
    now = datetime.now(TZ)
    ROOT.mkdir(parents=True, exist_ok=True)
    result = {"run_at": now.isoformat()}
    try:
        result["observation_archive"] = archive_observations(now)
    except Exception as exc:
        result["observation_archive"] = {"status": "error", "error": str(exc)}
        print(f"Observation archive warning: {exc}")

    # One immutable forecast vintage per Taiwan local date, captured after
    # the scheduled 07:25 local forecast cache refresh.
    if (now.hour > 8 or (now.hour == 8 and now.minute >= 10)):
        try:
            result["forecast_archive"] = archive_forecast_vintage(now)
        except Exception as exc:
            result["forecast_archive"] = {"status": "error", "error": str(exc)}
            print(f"Forecast archive warning: {exc}")

    # Recompute verification once daily after yesterday's 06:00-18:00 CWA
    # observations are complete. Re-run on later ten-minute cycles if needed.
    if (now.hour > 8 or (now.hour == 8 and now.minute >= 45)):
        today_report = ROOT / "verification" / "reports" / f"{now.date().isoformat()}.json"
        if not today_report.exists():
            try:
                result["verification"] = verify_and_weight(now)
            except Exception as exc:
                result["verification"] = {"status": "error", "error": str(exc)}
                print(f"Verification warning: {exc}")

    cutoff = now.date() - timedelta(days=RETENTION_DAYS)
    prune_old_files(ROOT / "observations", cutoff)
    prune_old_files(ROOT / "forecasts", cutoff)
    weights_path = ROOT / "verification" / "weights.json"
    try:
        weights_doc = json.loads(weights_path.read_text(encoding="utf-8"))
    except Exception:
        weights_doc = {"status": "warming_up"}
    forecast_dates = sorted(
        p.name.split(".")[0] for p in (ROOT / "forecasts").glob("*.json.gz")
    ) if (ROOT / "forecasts").exists() else []
    observation_result = result.get("observation_archive") or {}
    status_doc = {
        "generated_at": now.isoformat(),
        "latest_observation_snapshot_at": observation_result.get("snapshot_at"),
        "observation_archive": observation_result,
        "latest_forecast_archive_date": forecast_dates[-1] if forecast_dates else None,
        "verification_status": weights_doc.get("status", "warming_up"),
        "active_lead_days": [
            lead for lead, item in weights_doc.get("lead_days", {}).items()
            if item.get("weighting_active")
        ],
        "retention_days": RETENTION_DAYS,
    }
    (ROOT / "verification").mkdir(parents=True, exist_ok=True)
    (ROOT / "verification" / "status.json").write_text(
        json.dumps(status_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
