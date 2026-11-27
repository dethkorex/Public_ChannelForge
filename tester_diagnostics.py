"""Field allowlists shared by private tester clients and the diagnostics receiver."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import threading
import urllib.request
from urllib.parse import urlsplit

MAX_REPORT_BYTES = 8192
PLATFORMS = frozenset({"fire-tv", "android", "windows", "linux", "roku"})
NUMBER = frozenset("""
    sdk studioCount staleGuides firstFrameMs bufferMs positionMs bandwidthEstimateBps retries
    temporaryBitrateCapBps postStartRebuffers postStartRebufferMs playerErrorCode httpError
    width height videoBitrateBps audioChannels renderedFrames droppedFrames
    decoded_frames dropped_frames position_seconds buffered_ahead_seconds
    media_ready_state variant_bps estimated_bandwidth_bps media_error_code
    recoveries_this_attempt studios channels reconnecting first_frame_ms stalls stall_ms crash_line elapsed_ms at_ms build_revision
    segment_loads segment_errors manifest_errors slowest_segment_ms slowest_manifest_ms segment_rate_bps
    live_channels viewer_sessions encoder_count encoder_starting upload_bps
""".split())
BOOLEAN = frozenset({"directoryCached", "playing", "stream_selected", "registered", "relay_constrained"})
TEXT = {
    "platform": r"fire-tv|android|windows|linux|roku",
    "app": r"\d+(?:\.\d+){1,3}",
    "model": r"[A-Za-z0-9 ._()-]{1,64}",
    "engine": r"Media3 ExoPlayer|hls\.js|native HTML video|inactive web player|Roku SceneGraph",
    "kind": r"live|movie",
    "delivery_route": r"preferred-relay|registered-route|registered-fallback",
    "trace_id": r"[a-f0-9]{32}",
    "phase": r"tune|rendition|movie",
    "http_resource": r"manifest|segment|other",
    "component": r"tv|studio",
    "state": r"Opening|Buffering|Playing|Paused|Stopped|Ended|Error|NothingSpecial|none|buffering|playing|paused|stopped|finished|error",
    "videoMime": r"video/[a-z0-9.+-]+",
    "audioMime": r"audio/[a-z0-9.+-]+",
    "report_type": r"crash",
    "crash_area": r"viewer|studio|launcher|webview|fire-tv",
    "crash_kind": r"RuntimeError|ValueError|AssertionError|OSError|MemoryError|Exception|"
                  r"TypeError|AttributeError|KeyError|PermissionError|FileNotFoundError|TimeoutError|ModuleNotFoundError|"
                  r"OutOfMemoryError|IllegalStateException|NullPointerException|IllegalArgumentException|"
                  r"SecurityException|IndexOutOfBoundsException|IOException|Error",
}
EVENT_KINDS = frozenset({"load", "first-frame", "buffering", "playing", "ended", "error", "retry", "guide"})


def report_address(value: str) -> bool:
    url = urlsplit(value)
    return bool(url.scheme == "https" and url.hostname and not (
        url.username or url.password or url.query or url.fragment
    ) and re.fullmatch(r"/[A-Za-z0-9_-]{32,64}/report", url.path))


def clean_report(report: object) -> dict:
    platform = report.get("platform") if isinstance(report, dict) else None
    if not isinstance(platform, str) or platform not in PLATFORMS:
        raise ValueError("Invalid tester report")
    clean = {key: value for key, value in report.items()
             if (key in NUMBER and type(value) in (int, float) and -1 <= value <= 10**12)
             or (key in BOOLEAN and isinstance(value, bool))
             or (key in TEXT and isinstance(value, str) and re.fullmatch(TEXT[key], value))}
    events = report.get("timeline")
    if isinstance(events, list):
        clean["timeline"] = [
            {"kind": event["kind"], "ms": event["ms"]}
            for event in events[-40:]
            if isinstance(event, dict) and isinstance(event.get("kind"), str)
            and event["kind"] in EVENT_KINDS and type(event.get("ms")) is int
            and 0 <= event["ms"] <= 10**12
        ]
    traces = report.get("stream_traces")
    if isinstance(traces, list):
        clean["stream_traces"] = [
            {key: value for key, value in item.items()
             if (key in {"trace_id", "phase"} and isinstance(value, str) and re.fullmatch(TEXT[key], value))
             or (key in {"httpError", "elapsed_ms", "at_ms"} and type(value) is int and 0 <= value < 10**14)}
            for item in traces[-24:] if isinstance(item, dict)
        ]
    return clean


def encode_report(report: object) -> bytes:
    return json.dumps(clean_report(report), allow_nan=False).encode("utf-8")


def preferences_dir() -> Path:
    root = (Path(os.environ.get("LOCALAPPDATA", Path.home())) if sys.platform == "win32"
            else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")))
    return root / "ChannelForge-Tester-Diagnostics"


def automatic_reports(agreed: bool | None = None) -> bool:
    folder = preferences_dir()
    preference = folder / "automatic-opt-in"
    if agreed is True:
        folder.mkdir(parents=True, exist_ok=True)
        preference.touch()
    elif agreed is False:
        preference.unlink(missing_ok=True)
        for area in ("viewer", "studio", "launcher", "webview"):
            (folder / f"crash-{area}.json").unlink(missing_ok=True)
    return preference.is_file()


def post_report(target: str, report: object) -> str | None:
    if not report_address(target):
        return None

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    request = urllib.request.Request(target, data=encode_report(report), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=8) as response:
            receipt = response.read(16).decode("ascii")
            if response.status == 201 and re.fullmatch(r"[0-9a-f]{10}", receipt):
                return receipt
    except (OSError, ValueError):
        pass
    return None


def install_crash_reporting(config_path: Path, area: str, version: str) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    target = config.get("report_url", "")
    pending = preferences_dir() / f"crash-{area}.json"

    def save(exception_type, trace):
        if not automatic_reports() or issubclass(exception_type, (SystemExit, KeyboardInterrupt)):
            return
        names = {"Public_ChannelForge.py", "ChannelForge_TV.py", "tester_suite_launcher.py"}
        line = 0
        while trace is not None:
            if Path(trace.tb_frame.f_code.co_filename).name in names:
                line = trace.tb_lineno
            trace = trace.tb_next
        kind = exception_type.__name__
        if not re.fullmatch(TEXT["crash_kind"], kind):
            kind = "Exception"
        payload = {"platform": "windows" if sys.platform == "win32" else "linux", "app": version,
                   "crash_area": area, "crash_kind": kind, "crash_line": line, "report_type": "crash"}
        if type(config.get("revision")) is int:
            payload["build_revision"] = config["revision"]
        staged = pending.with_suffix(".tmp")
        try:
            staged.write_bytes(encode_report(payload))
            staged.replace(pending)
        except OSError:
            pass

    old_hook = sys.excepthook
    thread_hook = threading.excepthook

    def unhandled(exception_type, exception, trace):
        save(exception_type, trace)
        old_hook(exception_type, exception, trace)

    def thread_failed(args):
        save(args.exc_type, args.exc_traceback)
        thread_hook(args)

    def flush():
        try:
            data = pending.read_bytes()
            if automatic_reports() and post_report(target, json.loads(data)) and pending.read_bytes() == data:
                pending.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    sys.excepthook = unhandled
    threading.excepthook = thread_failed
    if pending.is_file() and automatic_reports():
        threading.Thread(target=flush, name="tester-crash-report", daemon=True).start()
