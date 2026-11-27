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
    studio_trace_ids = set()
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
        if report.get("component") == "studio":
            studio_trace_ids.update(trace["trace_id"] for trace in report.get("stream_traces", []) if "trace_id" in trace)

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
        routes = Counter(report["delivery_path"] for report in reports if "delivery_path" in report)
        stalls = [report.get("stall_ms", report.get("postStartRebufferMs", 0)) for report in reports]
        timings = {}
        for field in ("slowest_segment_ms", "slowest_relay_upstream_ms", "slowest_studio_response_ms"):
            values = [value for report in reports if (value := report.get(field, -1)) > 0]
            timings[field] = round(statistics.median(values)) if values else None
        summary.append({
            "platform": platform, "version": version, "reports": len(reports),
            "median_first_frame_ms": round(statistics.median(starts)) if starts else None,
            "playback_routes": dict(routes),
            "reports_with_stalls": sum(ms > 0 for ms in stalls),
            "reports_matched_to_studio_trace": sum(report.get("trace_id") in studio_trace_ids for report in reports),
            "median_slowest_timings_ms": timings,
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
