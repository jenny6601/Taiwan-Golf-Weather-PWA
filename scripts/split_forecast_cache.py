#!/usr/bin/env python3
"""Split the public nationwide forecast cache into one small JSON file per course."""
from __future__ import annotations
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://raw.githubusercontent.com/jenny6601/Taiwan-Golf-Weather/main/data/forecast_cache.json"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "course-cache")
OUT.mkdir(parents=True, exist_ok=True)

req = urllib.request.Request(SOURCE, headers={"User-Agent": "Taiwan-Golf-Weather-PWA-cache-splitter/1.0"})
with urllib.request.urlopen(req, timeout=90) as response:
    cache = json.load(response)

courses = cache.get("courses")
if not isinstance(courses, dict) or not courses:
    raise SystemExit("Source forecast cache has no courses.")
generated_at = cache.get("generated_at")
if not generated_at:
    raise SystemExit("Source forecast cache is missing generated_at.")
if len(courses) < 50:
    raise SystemExit(f"Source cache unexpectedly contains only {len(courses)} courses; refusing to publish an incomplete snapshot.")

course_dir = OUT / "courses"
course_dir.mkdir(parents=True, exist_ok=True)
index = {
    "schema_version": 1,
    "generated_at": generated_at,
    "timezone": cache.get("timezone", "Asia/Taipei"),
    "course_count": len(courses),
    "model_names": cache.get("model_names", []),
    "model_metadata": cache.get("model_metadata", {}),
    "model_generated_at": cache.get("model_generated_at", {}),
    "courses": {},
}
for name, entry in sorted(courses.items(), key=lambda item: item[0]):
    key = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
    rel = f"courses/{key}.json"
    payload = {
        "schema_version": cache.get("schema_version", 1),
        "generated_at": generated_at,
        "timezone": cache.get("timezone", "Asia/Taipei"),
        "course_count": 1,
        "model_names": cache.get("model_names", []),
        "model_metadata": cache.get("model_metadata", {}),
        "model_generated_at": cache.get("model_generated_at", {}),
        "courses": {name: entry},
    }
    (OUT / rel).write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    index["courses"][name] = rel

(OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"Validated and split {len(courses)} courses from source cache generated_at={generated_at}")
