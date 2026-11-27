"""Summarize recent ChannelForge reports without printing individual records."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tester_diagnostics import MAX_REPORT_BYTES, clean_report


def summarize(root: Path, since: datetime) -> list[dict]:
    groups = defaultdict(list)
    for path in root.glob("*/*/*.json"):
        try:
            if path.stat().st_size > MAX_REPORT_BYTES * 2:
                continue
            row = json.loads(path.read_bytes())
            timestamp = datetime.fromisoformat(row["received_utc"])
            if timestamp.tzinfo is None or timestamp < since:
                continue
            report = clean_report(row)
        except (OSError, ValueError, TypeError, KeyError):
            continue
        groups[(report["platform"], report.get("app", "unknown"))].append(report)

    summary = []
    for (platform, version), reports in sorted(groups.items()):
        starts = [
            value for report in reports
            if (value := report.get("first_frame_ms", report.get("firstFrameMs", -1))) >= 0
        ]
        errors = Counter(
            value for report in reports if (value := report.get("httpError", 0)) >= 400
        )
        crashes = Counter(
            (report.get("crash_area", "unknown"), report.get("crash_kind", "unknown"))
            for report in reports if report.get("report_type") == "crash"
        )
        summary.append({
            "platform": platform, "version": version, "reports": len(reports),
            "median_first_frame_ms": round(statistics.median(starts)) if starts else None,
            "http_errors": dict(errors.most_common()),
            "crashes": [{"component": area, "kind": kind, "reports": count}
                        for (area, kind), count in crashes.most_common()],
        })
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    if args.days < 1 or not args.reports.is_dir():
        parser.error("Select an existing report directory and a positive number of days.")
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    print(json.dumps(summarize(args.reports, since), indent=2))
