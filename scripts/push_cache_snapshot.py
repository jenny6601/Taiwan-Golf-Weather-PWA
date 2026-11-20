#!/usr/bin/env python3
"""Publish a cache snapshot without overwriting a newer concurrent snapshot.

The candidate index.json must have a timezone-aware generated_at value.
Existing cache branches are intentionally snapshot branches, so the update
uses force-with-lease rather than an unguarded force push.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check)


def timestamp(path: Path) -> datetime:
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get("generated_at")
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path}: generated_at is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{path}: generated_at must include a timezone")
    return parsed


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: push_cache_snapshot.py <cache-branch>", file=sys.stderr)
        return 2
    branch = sys.argv[1]
    if not branch or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_/" for c in branch):
        print("Invalid branch name", file=sys.stderr)
        return 2

    candidate = Path("index.json")
    if not candidate.is_file():
        print("Candidate index.json is missing; refusing to publish", file=sys.stderr)
        return 1
    candidate_time = timestamp(candidate)
    destination = f"refs/heads/{branch}"

    for attempt in range(1, 4):
        listed = run(["git", "ls-remote", "--heads", "origin", destination])
        expected_sha = listed.stdout.split()[0] if listed.stdout.strip() else ""

        if expected_sha:
            fetched = run(["git", "fetch", "--no-tags", "--depth=1", "origin", destination], check=False)
            if fetched.returncode:
                print(f"Attempt {attempt}: unable to fetch current {branch}: {fetched.stderr[-1000:]}", file=sys.stderr)
                time.sleep(attempt)
                continue
            previous = run(["git", "show", "FETCH_HEAD:index.json"], check=False)
            if previous.returncode:
                print(f"Existing {branch} has no readable index.json; refusing to overwrite", file=sys.stderr)
                return 1
            old = Path("/tmp/cache-previous-index.json")
            old.write_text(previous.stdout, encoding="utf-8")
            previous_time = timestamp(old)
            if candidate_time <= previous_time:
                print(
                    f"Refusing stale cache publication: candidate={candidate_time.isoformat()} "
                    f"existing={previous_time.isoformat()} branch={branch}",
                    file=sys.stderr,
                )
                return 1

        lease = f"--force-with-lease={destination}:{expected_sha}"
        pushed = run(["git", "push", lease, "origin", f"HEAD:{destination}"], check=False)
        if pushed.returncode == 0:
            print(f"Published {branch}: generated_at={candidate_time.isoformat()} (attempt {attempt})")
            return 0

        print(f"Attempt {attempt}: guarded push rejected; rechecking remote head", file=sys.stderr)
        time.sleep(attempt)

    print(f"Unable to publish {branch} after 3 guarded attempts; existing cache preserved", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
