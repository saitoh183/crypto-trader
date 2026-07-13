#!/usr/bin/env python3
"""Summarize paper scan outcome instrumentation for a review window.

Reads the JSONL file written by scripts/scan_webhook.py and answers the
reliability questions operators care about: how many scans ran, how many posted,
how many were intentionally quiet, how many failed, and whether alert fingerprints
repeated inside the review window.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_OUTCOME_LOG = Path("/home/saitoh183/projects/crypto/team/SCAN_OUTCOMES.jsonl")


def parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def summarize(records: list[dict[str, Any]], *, hours: int, now: datetime) -> dict[str, Any]:
    cutoff = now - timedelta(hours=hours)
    window_records = []
    for record in records:
        timestamp = parse_utc(record.get("recorded_at_utc") or record.get("generated_at_utc"))
        if timestamp is not None and timestamp >= cutoff:
            window_records.append(record)

    outcome_counts = Counter(str(record.get("outcome_type", "unknown")) for record in window_records)
    posted_count = sum(1 for record in window_records if bool(record.get("posted_to_discord")))
    failure_count = outcome_counts.get("workflow_failure", 0)
    quiet_count = outcome_counts.get("quiet_hold_no_post", 0)
    duplicate_records = [record for record in window_records if bool(record.get("duplicate_alert_candidate"))]
    fingerprints = Counter(
        str(record.get("alert_fingerprint", ""))
        for record in window_records
        if record.get("alert_fingerprint")
    )
    repeated_fingerprints = {
        fingerprint: count for fingerprint, count in fingerprints.items() if count > 1
    }

    return {
        "window_hours": hours,
        "cutoff_utc": cutoff.isoformat().replace("+00:00", "Z"),
        "records_in_window": len(window_records),
        "posted_to_discord": posted_count,
        "intentionally_quiet": quiet_count,
        "workflow_failures": failure_count,
        "failed_or_retried": failure_count,
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "duplicate_alert_candidates": len(duplicate_records),
        "repeated_alert_fingerprints": repeated_fingerprints,
        "latest_recorded_at_utc": max(
            (
                str(record.get("recorded_at_utc"))
                for record in window_records
                if record.get("recorded_at_utc")
            ),
            default=None,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Review crypto scan outcome instrumentation")
    parser.add_argument("--outcome-log", type=Path, default=DEFAULT_OUTCOME_LOG)
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    now = datetime.now(UTC).replace(microsecond=0)
    summary = summarize(load_records(args.outcome_log), hours=args.hours, now=now)

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    print(f"Scan outcome review window: last {summary['window_hours']}h since {summary['cutoff_utc']}")
    print(f"Total recorded scans/outcomes: {summary['records_in_window']}")
    print(f"Posted to Discord: {summary['posted_to_discord']}")
    print(f"Intentionally quiet HOLD/no-post: {summary['intentionally_quiet']}")
    print(f"Workflow failures/retries: {summary['failed_or_retried']}")
    print("Outcome counts:")
    for outcome_type, count in summary["outcome_counts"].items():
        print(f"  {outcome_type}: {count}")
    print(f"Duplicate alert candidates: {summary['duplicate_alert_candidates']}")
    if summary["repeated_alert_fingerprints"]:
        print("Repeated alert fingerprints:")
        for fingerprint, count in summary["repeated_alert_fingerprints"].items():
            print(f"  {fingerprint}: {count}")


if __name__ == "__main__":
    main()
