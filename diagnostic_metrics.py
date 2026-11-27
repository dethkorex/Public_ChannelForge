"""Field allowlists for playback and health diagnostics."""
from __future__ import annotations

import json
import re

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
    relay_cache_hits relay_cache_misses slowest_relay_upstream_ms slowest_studio_response_ms
    first_progress_ms
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
    "delivery_path": r"managed-vps|legacy-tunnel|local|direct|unknown",
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


def playback_response_headers(headers) -> dict[str, str]:
    """Forward only recognized, non-identifying playback metadata to the desktop player."""
    clean = {}
    relay = headers.get("X-ChannelForge-Relay", "")
    cache = headers.get("X-ChannelForge-Cache", "")
    if relay == "vps":
        clean["X-ChannelForge-Relay"] = relay
    if cache in {"HIT", "MISS", "EXPIRED", "STALE", "UPDATING", "REVALIDATED", "BYPASS"}:
        clean["X-ChannelForge-Cache"] = cache
    for name, pattern in (("X-ChannelForge-Upstream-Header-Time", r"\d{1,3}\.\d{3}"),
                          ("X-ChannelForge-Studio-Response-Ms", r"\d{1,6}")):
        value = headers.get(name, "")
        if re.fullmatch(pattern, value):
            clean[name] = value
    return clean


def clean_report(report: object) -> dict:
    platform = report.get("platform") if isinstance(report, dict) else None
    if not isinstance(platform, str) or platform not in PLATFORMS:
        raise ValueError("Invalid diagnostics report")
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
