#!/usr/bin/env python3
"""Archive immutable forecast vintages and CWA rainfall observations; verify skill.

Storage is the dedicated verification-history branch. Forecast vintages are
captured once daily for lead days 1,3,5,7,10,14. CWA course rainfall snapshots
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
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
LEADS = (1, 3, 5, 7, 10, 14)
RETENTION_DAYS = 180
OBS_MAX_AGE_MIN = 28
FORECAST_MAX_AGE_HOURS = 4
VC_MAX_AGE_HOURS = 36
MIN_STATION_DISTANCE_KM = 15.0
MIN_10M_SAMPLES = 6
MIN_GLOBAL_PAIRS = 200
MIN_COURSE_PAIRS = 40
SOURCES = (
    "ECMWF IFS", "NOAA GFS", "DWD ICON", "JMA GSM",
    "Canada GEM", "ECMWF AIFS", "Visual Crossing",
)
LIVE_URL = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather/main/data/live_observations.json"
COURSE_INDEX_URL = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/course-cache/index.json"
COURSE_BASE = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/course-cache/"
VC_INDEX_URL = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/visual-crossing-cache/index.json"
VC_BASE = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather-PWA/visual-crossing-cache/"
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
    day = generated_dt.astimezone(TZ).date().isoformat()
    path = ROOT / "observations" / f"{day}.jsonl.gz"
    history = read_gzip_jsonl(path)
    if any(x.get("snapshot_at") == generated for x in history):
        return {"status": "duplicate", "snapshot_at": generated, "course_count": len(courses)}
    normalized = {}
    for course_id, item in courses.items():
        rain = item.get("rainfall") or {}
        station = rain.get("station") or {}
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
        }
    history.append({
        "snapshot_at": generated,
        "source": snapshot.get("source", "CWA"),
        "course_count": len(normalized),
        "courses": normalized,
    })
    history.sort(key=lambda x: x.get("snapshot_at", ""))
    write_gzip_jsonl(path, history)
    return {"status": "archived", "snapshot_at": generated, "course_count": len(normalized), "age_minutes": round(age_min, 1)}

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

def archive_forecast_vintage(now: datetime) -> dict:
    issue_date = now.astimezone(TZ).date()
    out_path = ROOT / "forecasts" / f"{issue_date.isoformat()}.json.gz"
    if out_path.exists():
        try:
            previous_vintage = read_gzip_json(out_path)
            if len(previous_vintage.get("courses", {})) == 57:
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

    live = get_json(LIVE_URL)
    course_id_by_name = {
        name_key(v.get("course_name") or v.get("name") or ""): str(k)
        for k, v in live.get("courses", {}).items()
    }
    course_items = list(names.items())
    vc_names = vc_index.get("courses", {}) if vc_index else {}
    vc_name_lookup = {name_key(k): (k, v) for k, v in vc_names.items()}
    valid_dates = { (issue_date + timedelta(days=lead)).isoformat(): lead for lead in LEADS }
    archive = {
        "schema_version": 1,
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
        "sources": list(model_names) + (["Visual Crossing"] if vc_index else []),
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
                        "precip_mm": r.get("mm"),
                        "precip_probability_pct": r.get("rain"),
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
                        "precip_mm": r.get("precipitation"),
                        "precip_probability_pct": r.get("precipitation_probability"),
                    }
                    for r in vc_rows
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
        self.hit = self.miss = self.false_alarm = self.correct_negative = 0
        self.brier_sum = 0.0
        self.prob_n = 0
    def add(self, forecast, actual, probability):
        self.n += 1
        error = forecast - actual
        self.abs_error += abs(error)
        self.squared_error += error * error
        self.bias += error
        event_actual = actual >= 0.1
        event_forecast = forecast >= 0.1
        if event_forecast and event_actual: self.hit += 1
        elif event_actual: self.miss += 1
        elif event_forecast: self.false_alarm += 1
        else: self.correct_negative += 1
        if probability is not None:
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
            "POD": round(self.hit / pod_den, 4) if pod_den else None,
            "FAR": round(self.false_alarm / far_den, 4) if far_den else None,
            "CSI": round(self.hit / csi_den, 4) if csi_den else None,
            "hit": self.hit, "miss": self.miss,
            "false_alarm": self.false_alarm, "correct_negative": self.correct_negative,
            "probability_sample_count": self.prob_n,
            "Brier_score": round(self.brier_sum / self.prob_n, 4) if self.prob_n else None,
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

def normalized_weights(scores: dict, metric_key: str, minimum_n: int, prior_strength: float, cap: float = 0.35):
    eligible = {
        name: row for name, row in scores.items()
        if row.get("sample_count", 0) >= minimum_n and isinstance(row.get(metric_key), (int, float))
    }
    if len(eligible) < 3:
        return None
    raw = {name: 1.0 / max(float(row[metric_key]), 0.02) for name, row in eligible.items()}
    total = sum(raw.values())
    empirical = {name: value / total for name, value in raw.items()}
    prior = 1.0 / len(eligible)
    out = {}
    for name, row in eligible.items():
        n = row.get("sample_count", 0)
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

def verify_and_weight(now: datetime) -> dict:
    forecasts_dir = ROOT / "forecasts"
    actual = build_actual_hourly(now)
    global_scores = {str(lead): defaultdict(Score) for lead in LEADS}
    course_scores = defaultdict(lambda: {str(lead): defaultdict(Score) for lead in LEADS})
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
                for source, hours in day.get("sources", {}).items():
                    for row in hours:
                        try:
                            hour = int(str(row.get("time", ""))[:2])
                            if not 6 <= hour <= 18:
                                continue
                        except Exception:
                            continue
                        observed = actual.get(course_key, {}).get((valid_date, hour))
                        if not observed:
                            continue
                        forecast_mm = row.get("precip_mm")
                        actual_mm = observed["precip_mm"]
                        if not isinstance(forecast_mm, (int, float)) or not math.isfinite(forecast_mm):
                            continue
                        prob = row.get("precip_probability_pct")
                        if not isinstance(prob, (int, float)) or not math.isfinite(prob):
                            prob = None
                        global_scores[lead][source].add(float(forecast_mm), float(actual_mm), prob)
                        course_scores[course_name][lead][source].add(float(forecast_mm), float(actual_mm), prob)
        evaluated_vintages += 1

    metrics = {
        "schema_version": 1,
        "generated_at": now.astimezone(TZ).isoformat(),
        "verification_source": "CWA rainfall observations",
        "verification_window_days": 60,
        "station_distance_limit_km": MIN_STATION_DISTANCE_KM,
        "rain_event_threshold_mm": 0.1,
        "forecast_hours": "06:00-18:00",
        "evaluated_vintages": evaluated_vintages,
        "lead_days": {},
        "by_course": {},
    }
    weights = {
        "schema_version": 1,
        "generated_at": now.astimezone(TZ).isoformat(),
        "status": "warming_up",
        "method": "inverse-error with equal-prior shrinkage and 0.35 maximum source weight",
        "verification_source": "CWA rainfall observations",
        "lead_days": {},
        "by_course": {},
    }
    active_leads = 0
    for lead in LEADS:
        key = str(lead)
        rows = {source: score.result() for source, score in global_scores[key].items()}
        metrics["lead_days"][key] = rows
        amount_weights = normalized_weights(rows, "MAE_mm", MIN_GLOBAL_PAIRS, 2000)
        prob_scores = {
            source: {
                "sample_count": row.get("probability_sample_count", 0),
                "Brier_score": row.get("Brier_score"),
            }
            for source, row in rows.items()
        }
        probability_weights = normalized_weights(prob_scores, "Brier_score", MIN_GLOBAL_PAIRS, 2000, 0.4)
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

    for course_name, leads in course_scores.items():
        course_out = {}
        course_weight_out = {}
        for lead in LEADS:
            key = str(lead)
            rows = {source: score.result() for source, score in leads[key].items()}
            course_out[key] = rows
            local_weights = normalized_weights(rows, "MAE_mm", MIN_COURSE_PAIRS, 2000)
            if local_weights:
                course_weight_out[key] = local_weights
        metrics["by_course"][course_name] = course_out
        if course_weight_out:
            weights["by_course"][course_name] = course_weight_out

    if active_leads:
        weights["status"] = "active"
    report = {
        "generated_at": metrics["generated_at"],
        "status": weights["status"],
        "evaluated_vintages": evaluated_vintages,
        "actual_course_hours": sum(len(v) for v in actual.values()),
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
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
