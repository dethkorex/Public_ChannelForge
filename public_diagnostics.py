"""Queue and deliver non-identifying Alpha health measurements."""

import json
from pathlib import Path
import re
import sys
import threading
import urllib.request

from diagnostic_metrics import clean_report


REPORT_URL = "https://channelforge-directory.fresh-anorak.workers.dev/v1/diagnostics/exit"


class PublicDiagnostics:
    def __init__(self, folder: Path, component: str, version: str):
        self.pending = folder / f"alpha-{component}-pending.json"
        self.component = component
        self.version = version
        self.lock = threading.Lock()
        self.sent_exit = False
        if self.pending.is_file():
            threading.Thread(target=self.flush, daemon=True, name="alpha-health-retry").start()

    def submit(self, metrics=None, *, failure=None):
        with self.lock:
            if self.sent_exit:
                return
            self.sent_exit = True
        platform = "windows" if sys.platform == "win32" else "linux"
        report = clean_report({**(metrics or {}), "platform": platform, "app": self.version, "component": self.component})
        for field in ("model", "trace_id", "stream_traces", "timeline", "position_seconds", "positionMs", "at_ms", "crash_line"):
            report.pop(field, None)
        report["report_type"] = "exit" if failure is None else "crash"
        if failure is not None:
            kind = type(failure).__name__
            report["crash_kind"] = kind if re.fullmatch(r"RuntimeError|ValueError|OSError|MemoryError", kind) else "Exception"
        for key in ("video_bps", "movie_sessions", "uptime_seconds"):
            value = (metrics or {}).get(key)
            if type(value) is int and 0 <= value <= 10**12:
                report[key] = value
        sharing = (metrics or {}).get("sharing")
        if sharing in {"live", "retrying", "connecting", "idle", "private"}:
            report["sharing"] = sharing
        try:
            self.pending.parent.mkdir(parents=True, exist_ok=True)
            temp = self.pending.with_suffix(".writing")
            temp.write_text(json.dumps(report, allow_nan=False), encoding="utf-8")
            temp.replace(self.pending)
        except OSError:
            return
        self.flush()

    def flush(self):
        try:
            data = self.pending.read_bytes()
            request = urllib.request.Request(REPORT_URL, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=2) as response:
                if response.status == 204 and self.pending.read_bytes() == data:
                    self.pending.unlink(missing_ok=True)
        except OSError:
            pass
