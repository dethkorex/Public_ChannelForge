"""Public_ChannelForge: local-first personal channel guide builder."""

from __future__ import annotations

import argparse
import atexit
import base64
import concurrent.futures
import hashlib
import gzip
import io
import html
import importlib.util
import itertools
import json
import mimetypes
import os
import queue
import re
import secrets
import select
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import wave
import zlib
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple
from xml.etree import ElementTree as ET

from linux_runtime import apply_linux_update, can_apply_linux_update, clean_linux_update_cache, restart_linux_app, linux_install_command, linux_release_asset, start_webview
from collections import deque
from release_metadata import read_latest_release, version_tuple
import studio_branding
from windows_distribution import clean_windows_update_cache, published_hash, relay_process_job, verified_windows_setup, windows_setup_asset, windows_update_cache

APP_NAME = "Public_ChannelForge"
APP_TITLE = "ChannelForge Studio"
VERSION = "4.5.8"
DISCOVERY_VERSION = VERSION.partition("-")[0]
HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_PUBLIC_MEDIA_PORT = 8790
LEGACY_RELAY_HOST_KEY = "localhost.run ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILVqOuSMnyeGDVO1lG6EaG5In/dXABCchhmHKkuRU2s9"
RELAY_INSTANCE = secrets.token_hex(20)
RELAY_MEDIA_SAMPLE = secrets.token_bytes(512 * 1024)
RELAY_MEDIA_CHALLENGES: dict[str, int] = {}
BROADCAST_ROUTE = re.compile(r"/broadcast/([a-zA-Z0-9_-]+)/(?:([a-f0-9]{24})/(economy|balanced|hd)|(master|firetv))\.m3u8")
LIVE_VIEWER_ID = re.compile(r"[a-f0-9]{32}")
CHANNEL_RATING_LEVELS = {
    "TV-Y": 1, "TV-Y7": 2, "TV-Y7-FV": 2, "G": 3, "TV-G": 3, "PG": 4, "TV-PG": 4,
    "PG-13": 5, "TV-14": 6, "R": 7, "TV-MA": 8, "NC-17": 9,
}
LIVE_VIEWER_TTL = 25
LIVE_VIEWER_GRACE = 3
LIVE_LEGACY_IDLE = 45
MOVIE_VIEWER_TTL = 45
CHANNEL_SAVE_BODY_BYTES = 32 * 1024 * 1024
STUDIO_START = time.monotonic()
STUDIO_TRAFFIC = {}
STUDIO_TRAFFIC_LOCK = threading.Lock()
STREAM_TRACES = deque(maxlen=128)
LIVE_PLAYOUT = {}
BROADCAST_DEFAULT = (720, 2500)
BROADCAST_HEIGHTS = (270, 480, 720)
EXPORT_CHANNEL_IDS = None
EXPORT_REFRESHED_AT = float("-inf")
GUIDE_HOURS = 12
DEFAULT_CHANNEL_ARTWORK = {"logo_asset_id": "builtin-studio-studio-logo",
                           "background_asset_id": "builtin-studio-studio-guide-background",
                           "corner_bug_asset_id": "builtin-studio-studio-corner-bug"}
RELAY_JOB = None
CHECKED_RELAY_PORTS = set()
DEFAULT_DIRECTORY_URL = "https://channelforge-directory.fresh-anorak.workers.dev"
OLD_DIRECTORY_URL = "https://directory.channelforge.tv"
RELAY_SLOT_PATH = r"/studio/[a-f0-9]{16}"
MEDIA_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".mpg", ".mpeg", ".wmv", ".flv"}
POSTER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def app_dir() -> Path:
    override = os.environ.get("PUBLIC_CHANNELFORGE_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / (APP_NAME)
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / ("public-channelforge")


DATA_DIR = app_dir()
CONFIG_FILE = DATA_DIR / "config.json"
JELLYFIN_FILE = DATA_DIR / "jellyfin-connections.json"
CHANNELS_FILE = DATA_DIR / "channels.json"
VAULT_CATALOG_FILE = DATA_DIR / "rewind-cinema.json"
EXPORT_DIR = DATA_DIR / "exports"
PLUGINS_DIR = DATA_DIR / "plugins"
ASSETS_DIR = DATA_DIR / "assets"
BACKUP_DIR = DATA_DIR / "backups"
POSTER_CACHE_DIR = DATA_DIR / "poster-cache"
WEB_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "web"
CREATOR_ICON = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / "channel-forge-creator-icon.png"
LOCK = threading.RLock()
# Bound the lock pool while allowing different channels to start in parallel.
ENCODER_START_LOCKS = tuple(threading.Lock() for _ in range(32))
NEXT_PROGRAM_SLOT = threading.BoundedSemaphore(1)
NEXT_SOURCE_SLOTS = threading.BoundedSemaphore(2)
POSTER_CACHE_LOCKS = tuple(threading.Lock() for _ in range(32))
HLS_PROXY: dict[str, tuple[str, float]] = {}
HLS_PROXY_CHANNELS: dict[str, str] = {}
UPDATE_JOBS: dict[str, dict] = {}
PLAYBACK_ERRORS: dict[str, tuple[float, str]] = {}
PLAYBACK_ERROR_COOLDOWN = 30
FFMPEG_SESSIONS: dict[str, dict] = {}
BROADCAST_EVENTS = deque(maxlen=100)
SOURCE_FAILURES = {}
ENCODER_STARTING: dict[str, int] = {}
ENCODER_GENERATION = 0
ENCODER_LAUNCHES = {}
def bounded_environment_int(name, default, low, high):
    try:
        return max(low, min(high, int(os.environ.get(name, default))))
    except ValueError:
        return default


MAX_ENCODERS = bounded_environment_int("CHANNELFORGE_MAX_ENCODERS", min(6, max(2, 1 + (os.cpu_count() or 2) // 2)), 1, 16)
STREAM_CACHE_LIMIT = bounded_environment_int("CHANNELFORGE_CACHE_MIB", 3072, 256, 8192) * 1024**2
STREAM_FREE_RESERVE = 1024**3
STREAM_ROLLING_BUDGET = 128 * 1024**2
STREAM_PRESSURE_SINCE = None
STREAM_DIRECTORY_NAME = re.compile(r"[a-f0-9]{20}(?:-[a-f0-9]{12})?")


class EncoderCapacityError(ValueError):
    """A Creator cannot accept another encoder until local capacity recovers."""


class MovieEncodingError(OSError):
    """A movie encoder failed before finishing the source."""

LOCAL_LIBRARY_CACHE: dict[str, tuple[float, list[dict]]] = {}
AUDIO_SELECTION_CACHE: dict[str, tuple[int, bool]] = {}
ENCODER_FILE = r"(?:index(?:_(?:economy|balanced|hd))?\.m3u8|segment(?:(?:economy|balanced|hd)_)?\d+\.ts)"
DEINTERLACE_VIDEO = "bwdif=mode=send_frame:parity=auto:deint=interlaced"
AAC_FORMAT = ["-c:a", "aac", "-profile:a", "aac_low", "-ac", "2", "-ar", "48000", "-b:a", "64k"]
PLEX_RESOURCE_CACHE: dict[str, object] = {"expires": 0.0, "servers": {}}
STREAM_DIR = DATA_DIR / "streams"
TUNNEL_LOCK = threading.RLock()
TUNNEL_URL = ""
LEGACY_RELAY_PROCESS: subprocess.Popen | None = None
LEGACY_RELAY_ORIGIN = ""
PUBLISH_LOCK = threading.RLock()
SYNCED_CHANNEL_NUMBERS = None


class CommunityRelayProfile(NamedTuple):
    origin: str
    ssh_host: str
    ssh_user: str
    ssh_port: int
    remote_port: int
    host_key: str
    funnel_host: str = ""
    funnel_port: int = 8443


class CommunityRelayConnection(NamedTuple):
    profile: CommunityRelayProfile
    local_port: int
    process: subprocess.Popen


COMMUNITY_RELAY: CommunityRelayConnection | None = None
SHUTTING_DOWN = threading.Event()
PUBLISH_CHANGED = threading.Event()
SHARING_STATUS = "idle"
REGISTERED_ORIGIN = ""
HEARTBEAT_STATUS = {"last_success": 0, "last_attempt": 0, "failures": 0}
MOVIE_SCAN_STATUS = {"state": "idle"}
MOVIE_SCAN_LOCK = threading.RLock()
PUBLIC_MEDIA_SERVER: ThreadingHTTPServer | None = None
PUBLIC_MEDIA_PORT = DEFAULT_PUBLIC_MEDIA_PORT
PUBLIC_MEDIA_LOCK = threading.RLock()
APP_WINDOW = None
ALPHA_REPORTS = None
UPDATE_EXITING = False
def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        for attempt in range(7):
            try:
                temporary.replace(path)
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 6:
                    raise
                # Windows readers may keep the destination open briefly.
                time.sleep(0.02 * (2 ** attempt))
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path, fallback: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def github_bytes(url: str, limit: int = 100_000_000) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "github.com", "objects.githubusercontent.com"}:
        raise ValueError("The update URL was not an approved GitHub address.")
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": f"{APP_NAME}/{VERSION}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        size = int(response.headers.get("Content-Length", "0") or 0)
        if size > limit:
            raise ValueError("The update file was unexpectedly large.")
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("The update file exceeded the safety limit.")
        return data


def github_file(url: str, destination: Path, limit: int, progress) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "github.com", "objects.githubusercontent.com"}:
        raise ValueError("The update URL was not an approved GitHub address.")
    request = urllib.request.Request(url, headers={"Accept": "application/octet-stream", "User-Agent": f"{APP_NAME}/{VERSION}"})
    with urllib.request.urlopen(request, timeout=45) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length", "0") or 0)
        if total > limit:
            raise ValueError("The update file was unexpectedly large.")
        downloaded = 0
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            downloaded += len(chunk)
            if downloaded > limit:
                raise ValueError("The update file exceeded the safety limit.")
            output.write(chunk)
            progress("downloading", downloaded, total)


def latest_release() -> dict:
    payload = read_latest_release(f"{APP_NAME}/{VERSION}")
    tag = str(payload.get("tag_name", ""))
    assets = [{"name": str(item.get("name", "")), "url": str(item.get("browser_download_url", "")), "size": int(item.get("size", 0) or 0)} for item in payload.get("assets", [])]
    return {"current_version": VERSION, "latest_version": tag.lstrip("v"), "update_available": version_tuple(tag) > version_tuple(VERSION), "release_url": str(payload.get("html_url", "")), "published_at": str(payload.get("published_at", "")), "notes": str(payload.get("body", ""))[:4000], "assets": assets}


def update_asset_name(release: dict) -> tuple[str, str]:
    names = {item["name"] for item in release["assets"]}
    version = release["latest_version"]
    if sys.platform == "win32":
        return windows_setup_asset(names, "ChannelForge_Creator_Setup.exe"), "SHA256SUMS.txt"
    candidate = linux_release_asset(names, version, Path(__file__).resolve().parent)
    if candidate not in names:
        raise ValueError("The latest release does not contain an installer for this platform.")
    return candidate, "SHA256SUMS-Linux.txt"


def download_verified_update(progress=None) -> dict:
    report_progress = progress is not None
    progress = progress or (lambda _stage, _downloaded=0, _total=0: None)
    progress("checking", 0, 0)
    release = latest_release()
    if not release["update_available"]:
        raise ValueError("Public ChannelForge is already up to date.")
    asset_name, checksum_name = update_asset_name(release)
    by_name = {item["name"]: item for item in release["assets"]}
    if asset_name not in by_name or checksum_name not in by_name:
        raise ValueError("The release is missing its installer or checksum file.")
    progress("checksum", 0, 0)
    checksum_text = github_bytes(by_name[checksum_name]["url"], 1_000_000).decode("utf-8", "replace")
    expected = published_hash(checksum_text, asset_name)
    if sys.platform == "win32":
        def download_setup(path):
            if report_progress:
                github_file(by_name[asset_name]["url"], path, 200_000_000, progress)
            else:
                path.write_bytes(github_bytes(by_name[asset_name]["url"], 200_000_000))

        destination = verified_windows_setup(windows_update_cache(), release["latest_version"], expected, download_setup, progress)
        progress("complete", destination.stat().st_size, destination.stat().st_size)
        return {"version": release["latest_version"], "asset": asset_name, "path": str(destination), "sha256": expected,
                "command": f'Run the verified installer: "{destination}"', "can_launch": True}
    update_dir = DATA_DIR / "updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    destination = update_dir / asset_name
    temporary = destination.with_suffix(destination.suffix + ".part")
    if report_progress:
        github_file(by_name[asset_name]["url"], temporary, 150_000_000, progress)
        progress("verifying", temporary.stat().st_size, temporary.stat().st_size)
        actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
    else:
        data = github_bytes(by_name[asset_name]["url"], 150_000_000)
        temporary.write_bytes(data)
        actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError("The downloaded installer failed SHA-256 verification.")
    temporary.replace(destination)
    command = linux_install_command(destination, Path(__file__).resolve().parent, expected)
    progress("complete", destination.stat().st_size, destination.stat().st_size)
    return {"version": release["latest_version"], "asset": asset_name, "path": str(destination), "sha256": actual, "command": command, "can_launch": sys.platform.startswith("linux") and can_apply_linux_update(destination, Path(__file__).resolve().parent)}


def launch_verified_windows_update(installer: str) -> None:
    """Apply packaged Windows updates in place without reopening setup pages."""
    command = [installer]
    if getattr(sys, "frozen", False):
        command.extend(["--silent-update", "--target-exe", str(Path(sys.executable).resolve()), "--wait-pid", str(os.getpid())])
    subprocess.Popen(command, cwd=str(Path(installer).parent), creationflags=0x08000000 if sys.platform == "win32" else 0)


def exit_for_update(shutdown) -> None:
    """Release the web server, tray host, and executable before replacement."""
    global UPDATE_EXITING
    UPDATE_EXITING = True
    SHUTTING_DOWN.set()
    set_community_relay(None)
    stop_legacy_relay()
    shutdown()
    if APP_WINDOW is not None:
        APP_WINDOW.destroy()


def apply_creator_update(result, shutdown):
    if sys.platform.startswith("linux"):
        try:
            apply_linux_update(Path(result["path"]), result["sha256"], Path(__file__).resolve().parent)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result.update(can_launch=False, install_error=str(exc))
            return
        def restart():
            stop_public_tunnel()
            stop_legacy_relay()
            set_community_relay(None)
            stop_ffmpeg_sessions()
            restart_linux_app()
        threading.Timer(1.5, restart).start()
    else:
        threading.Timer(0.8, lambda: launch_verified_windows_update(result["path"])).start()
        threading.Timer(1.5, lambda: exit_for_update(shutdown)).start()


def start_update_job(launch: bool, shutdown) -> str:
    job_id = secrets.token_urlsafe(18)
    with LOCK:
        UPDATE_JOBS[job_id] = {"status": "running", "stage": "starting", "downloaded": 0, "total": 0, "percent": 0}

    def worker() -> None:
        def report(stage: str, downloaded: int = 0, total: int = 0) -> None:
            percent = round(downloaded * 100 / total, 1) if total else (100 if stage == "complete" else 0)
            with LOCK:
                UPDATE_JOBS[job_id].update({"stage": stage, "downloaded": downloaded, "total": total, "percent": percent})
        try:
            result = download_verified_update(report)
            if launch and result["can_launch"]:
                report("installing")
                apply_creator_update(result, shutdown)
            with LOCK:
                UPDATE_JOBS[job_id].update({"status": "complete", "result": result, "stage": "complete", "percent": 100})
        except Exception as exc:
            with LOCK:
                UPDATE_JOBS[job_id].update({"status": "error", "stage": "error", "error": str(exc)})

    threading.Thread(target=worker, daemon=True, name=f"channel-forge-update-{job_id[:6]}").start()
    return job_id


def config(public: bool = False) -> dict:
    value = load_json(CONFIG_FILE, {})
    if not isinstance(value, dict):
        value = {}
    directory_url = str(value.get("directory_url", "")).strip().rstrip("/")
    legacy_directory = directory_url == OLD_DIRECTORY_URL
    if legacy_directory or not directory_url:
        directory_url = DEFAULT_DIRECTORY_URL
    result = {
        "plex_url": str(value.get("plex_url", "http://127.0.0.1:32400")).rstrip("/"),
        "plex_token": str(value.get("plex_token", "")),
        "guide_hours": GUIDE_HOURS,
        "port": max(1024, min(65535, int(value.get("port", DEFAULT_PORT)))),
        "bind_host": str(value.get("bind_host", HOST)).strip() or HOST,
        "public_url": str(value.get("public_url", "")).strip().rstrip("/"),
        "community_relay_origin": str(value.get("community_relay_origin", "")).strip().rstrip("/"),
        "managed_relay": value.get("managed_relay") is True,
        "public_tunnel": bool(value.get("public_tunnel", False)),
        "plex_profile": value.get("plex_profile", {}) if isinstance(value.get("plex_profile", {}), dict) else {},
        "client_identifier": str(value.get("client_identifier", "")) or secrets.token_hex(16),
        "local_libraries": value.get("local_libraries", []) if isinstance(value.get("local_libraries", []), list) else [],
        "directory_url": directory_url,
        "creator_id": "" if legacy_directory else str(value.get("creator_id", "")),
        "creator_key": "" if legacy_directory else str(value.get("creator_key", "")),
        "creator_profile": value.get("creator_profile", {}) if isinstance(value.get("creator_profile", {}), dict) else {},
        "pending_creator_profile": value.get("pending_creator_profile", {}) if isinstance(value.get("pending_creator_profile", {}), dict) else {},
        "auto_publish_channels": bool(value.get("auto_publish_channels", True)),
    }
    if public:
        result["jellyfin_connections"] = jellyfin_connections(public=True)
        result["publishing_pending"] = bool(result.pop("pending_creator_profile"))
        result["plex_token"] = ""
        result["token_saved"] = bool(value.get("plex_token"))
        result["creator_published"] = bool(result["creator_id"] and result["creator_key"])
        result["creator_key"] = ""
        community = COMMUNITY_RELAY
        result["community_relay_active"] = bool(community and community.profile.origin == result["public_url"]
                                               and community.process.poll() is None)
        result["tunnel_active"] = result["community_relay_active"] or bool(TUNNEL_URL or LEGACY_RELAY_ORIGIN)
        result["sharing_status"] = SHARING_STATUS
        result["legacy_relay"] = is_legacy_relay(result["public_url"])
        result["directory_heartbeat"] = HEARTBEAT_STATUS.copy()
        result["movie_scan"] = MOVIE_SCAN_STATUS
        result.pop("client_identifier", None)
    return result


def local_libraries(settings: dict | None = None) -> list[dict]:
    """Return valid user-configured folders without walking them."""
    values = (settings or config()).get("local_libraries", [])
    result = []
    for value in values:
        if not isinstance(value, dict):
            continue
        library_id = str(value.get("id", ""))
        name = str(value.get("name", "")).strip()
        path = Path(str(value.get("path", ""))).expanduser()
        if re.fullmatch(r"[a-f0-9]{12}", library_id) and name and path.is_absolute():
            result.append({"id": library_id, "key": f"local:{library_id}", "title": name[:100], "type": "local", "path": str(path)})
    return result


def save_local_library(name: str, raw_path: str) -> dict:
    if not raw_path.strip():
        raise ValueError("Enter the full path to a local media folder.")
    path = Path(raw_path.strip()).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("Choose an existing local media folder.")
    old = config()
    existing = local_libraries(old)
    if any(Path(item["path"]).resolve() == path for item in existing):
        raise ValueError("That folder is already a local library.")
    item = {"id": secrets.token_hex(6), "name": (name.strip() or path.name or "Local media")[:100], "path": str(path)}
    raw = load_json(CONFIG_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    raw["local_libraries"] = [{"id": entry["id"], "name": entry["title"], "path": entry["path"]} for entry in existing] + [item]
    atomic_json(CONFIG_FILE, raw)
    LOCAL_LIBRARY_CACHE.clear()
    return {**item, "key": f"local:{item['id']}", "title": item["name"], "type": "local"}


def delete_local_library(library_id: str) -> None:
    raw = load_json(CONFIG_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    values = raw.get("local_libraries", [])
    if not isinstance(values, list):
        values = []
    updated = [item for item in values if isinstance(item, dict) and item.get("id") != library_id]
    if len(updated) == len(values):
        raise ValueError("Local library not found.")
    raw["local_libraries"] = updated
    atomic_json(CONFIG_FILE, raw)


def directory_request(method: str, path: str, payload: dict | None = None, key: str = "", *, timeout=20) -> dict:
    settings = config()
    base = settings["directory_url"]
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("The Creator Directory must use HTTPS.")
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": f"{APP_NAME}/{VERSION}"}
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    if key:
        request.add_unredirected_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(65537)
            if len(data) > 65536:
                raise ValueError("Directory response exceeded the limit.")
            value = json.loads(data)
            return value if isinstance(value, dict) else {}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(65536)).get("error", "")
        except Exception:
            detail = ""
        raise ValueError(detail or f"Creator Directory returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError("Could not reach the hosted Creator Directory.") from exc


def registration_key(directory_url: str) -> str:
    """Keep a lost first registration response from stranding a published slug."""
    namespace = hashlib.sha256(directory_url.encode()).hexdigest()[:24]
    path = DATA_DIR / f"directory-registration-{namespace}.json"
    with LOCK:
        key = load_json(path, {})
        if not isinstance(key, dict) or key.get("directory") != directory_url or not re.fullmatch(r"[a-f0-9]{64}", str(key.get("secret", ""))):
            key = {"directory": directory_url, "secret": secrets.token_hex(32)}
            temporary = path.with_suffix(f".{secrets.token_hex(8)}.tmp")
            try:
                with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as secret:
                    json.dump(key, secret)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return key["secret"]


def channel_number_key(value: str) -> str:
    return re.sub(r"\.0+(?=[0-9])", ".", re.sub(r"^0+(?=[0-9])", "", value.strip().lower()), count=1)


def public_channel_numbers(values: list[dict]) -> list[dict]:
    return [{"id": channel["id"], "number": channel["number"]} for channel in values]


def sync_public_channel_numbers(settings: dict) -> None:
    global SYNCED_CHANNEL_NUMBERS
    if (settings.get("directory_url") != DEFAULT_DIRECTORY_URL or not settings.get("auto_publish_channels")
            or not settings.get("creator_id") or not settings.get("creator_key")):
        return
    numbers = public_channel_numbers(channels())
    signature = (settings["creator_id"], tuple(sorted((row["id"], row["number"]) for row in numbers)))
    if signature != SYNCED_CHANNEL_NUMBERS:
        path = f'/v1/creators/{settings["creator_id"]}/channel-numbers'
        directory_request("POST", path, {"mode": "sync", "channels": numbers}, settings["creator_key"], timeout=5)
        SYNCED_CHANNEL_NUMBERS = signature


def publish_creator(payload: dict, *, origin: str | None = None, community_relay_origin: str | None = None,
                    preserve_pending: bool = False) -> dict:
    settings = config()
    public_url = origin if origin is not None else settings["public_url"]
    origin = urllib.parse.urlsplit(public_url)
    if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.query or origin.fragment
            or (origin.path and not re.fullmatch(RELAY_SLOT_PATH, origin.path))):
        raise ValueError("Enter the public HTTPS origin for ChannelForge's read-only media server.")
    if origin.hostname == "trycloudflare.com" or origin.hostname.endswith(".trycloudflare.com"):
        raise ValueError("Cloudflare Quick Tunnels are for development. Use a public HTTPS host that supports video delivery.")
    logo_asset_id = str(payload.get("logo_asset_id", "")).strip()
    selected_logo = next((asset for asset in asset_manifest() if asset.get("id") == logo_asset_id), None)
    logo_url = public_url + "/user-assets/" + urllib.parse.quote(selected_logo["filename"]) if selected_logo else str(payload.get("logo_url", "")).strip()[:1000]
    profile = {
        "name": str(payload.get("name", "")).strip()[:80],
        "slug": str(payload.get("slug", "")).strip()[:60],
        "description": str(payload.get("description", "")).strip()[:400],
        "guide_url": public_url + "/exports/guide.xml",
        "playlist_url": public_url + "/exports/channels.m3u",
        "status_url": public_url + "/api/public/channels",
        "vault_url": public_url + "/api/public/vault",
        "creator_version": DISCOVERY_VERSION,
        "logo_url": logo_url,
        "logo_asset_id": logo_asset_id if selected_logo else "",
    }
    if not profile["name"]:
        raise ValueError("Enter a creator or studio name.")
    directory_profile = ({**profile, "channel_numbers": public_channel_numbers(channels())}
                         if settings["directory_url"] == DEFAULT_DIRECTORY_URL else profile)
    if settings["creator_id"] and settings["creator_key"]:
        result = directory_request("PUT", f'/v1/creators/{settings["creator_id"]}', directory_profile, settings["creator_key"])
        creator_id, creator_key = settings["creator_id"], settings["creator_key"]
    else:
        result = directory_request("POST", "/v1/creators", directory_profile, registration_key(settings["directory_url"]))
        creator_id, creator_key = str(result.get("creator", {}).get("id", "")), str(result.get("api_key", ""))
        if not creator_id or not creator_key:
            raise ValueError("The Creator Directory did not return publishing credentials.")
    profile["heartbeat_supported"] = bool(result.get("heartbeat_supported"))
    with LOCK:
        raw = load_json(CONFIG_FILE, {})
        if not isinstance(raw, dict):
            raw = {}
        raw.update({"directory_url": settings["directory_url"], "creator_id": creator_id,
                    "creator_key": creator_key, "creator_profile": profile, "public_url": public_url})
        if not preserve_pending:
            raw["pending_creator_profile"] = {}
        if community_relay_origin is not None:
            raw["community_relay_origin"] = community_relay_origin
        atomic_json(CONFIG_FILE, raw)
    return result.get("creator", {"id": creator_id, **profile})


def unpublish_creator() -> None:
    with PUBLISH_LOCK:
        settings = config()
        if settings["creator_id"] and settings["creator_key"]:
            directory_request("DELETE", f'/v1/creators/{settings["creator_id"]}', None, settings["creator_key"])
        with LOCK:
            raw = load_json(CONFIG_FILE, {})
            if isinstance(raw, dict):
                raw.update({"creator_id": "", "creator_key": "", "creator_profile": {}, "community_relay_origin": "", "managed_relay": False,
                            "pending_creator_profile": {}, "auto_publish_channels": False})
                atomic_json(CONFIG_FILE, raw)
        set_community_relay(None)
        stop_legacy_relay()
    PUBLISH_CHANGED.set()


def publish_studio(payload: dict) -> dict:
    global REGISTERED_ORIGIN, SHARING_STATUS, TUNNEL_URL
    if not str(payload.get("name", "")).strip():
        raise ValueError("Enter a creator or studio name.")
    with PUBLISH_LOCK:
        previous = load_json(CONFIG_FILE, {})
        if not isinstance(previous, dict):
            previous = {}
        changes = {}
        if payload.get("directory_url"):
            new_directory = str(payload["directory_url"]).strip().rstrip("/")
            if new_directory != str(previous.get("directory_url", DEFAULT_DIRECTORY_URL)).rstrip("/"):
                changes.update({"creator_id": "", "creator_key": ""})
            changes["directory_url"] = new_directory
        requested = str(payload.get("public_url", "")).strip().rstrip("/")
        default_directory = (changes.get("directory_url") or previous.get("directory_url") or DEFAULT_DIRECTORY_URL) == DEFAULT_DIRECTORY_URL
        saved = str(previous.get("public_url", ""))
        first_publish = (default_directory and not previous.get("creator_id") and not requested and not saved
                         and not (DATA_DIR / "community-relay.json").is_file())
        managed = payload.get("managed_relay", first_publish or previous.get("managed_relay")) is True
        if managed and changes.get("creator_id") == "":
            raise ValueError("Use this Studio’s registered directory when enabling the ChannelForge relay.")
        if not managed and previous.get("managed_relay") is True:
            if requested == saved:
                requested = ""
            saved = ""
        origin = enrolled_relay_origin(previous) if managed else studio_publication_origin(requested, saved)
        relay = community_relay_profile(origin)
        funnel = relay is None and is_funnel_origin(origin)
        if managed and relay is None:
            raise ValueError("The directory did not assign a valid Studio transport.")
        already_serving = PUBLIC_MEDIA_SERVER is not None
        candidate = None
        if not funnel:
            start_public_media_server(automatic=relay is not None)
        try:
            with stage_media_funnel(origin) if funnel else nullcontext():
                if relay is not None:
                    candidate = connect_community_relay(relay)
                    if not relay.funnel_host and candidate is not COMMUNITY_RELAY and not relay_transfers_media(relay.origin, hd=True):
                        raise ValueError("The community relay is reachable but could not deliver a 720p test segment in time.")
                if SHUTTING_DOWN.is_set():
                    raise InterruptedError("Creator is shutting down.")
                changes.update({"public_url": origin, "public_tunnel": funnel,
                                "auto_publish_channels": True, "community_relay_origin": relay.origin if relay else "",
                                "managed_relay": managed})
                with LOCK:
                    previous = load_json(CONFIG_FILE, {})
                    atomic_json(CONFIG_FILE, {**previous, **changes})
                generate_exports()
                creator = publish_creator(payload)
        except Exception:
            if candidate is not None and candidate is not COMMUNITY_RELAY:
                stop_relay_process(candidate.process)
            with LOCK:
                current = load_json(CONFIG_FILE, {})
                for field, attempted in changes.items():
                    if current.get(field) == attempted:
                        if field in previous:
                            current[field] = previous[field]
                        else:
                            current.pop(field, None)
                atomic_json(CONFIG_FILE, current)
            generate_exports()
            if not already_serving:
                stop_public_media_server()
            raise
        if funnel:
            with TUNNEL_LOCK:
                TUNNEL_URL = origin
        else:
            stop_public_tunnel()
        stop_legacy_relay()
        set_community_relay(candidate)
        REGISTERED_ORIGIN = origin
        SHARING_STATUS = "live"
        return creator


def can_use_managed_relay(settings: dict) -> bool:
    origin = str(settings.get("public_url", ""))
    directory = str(settings.get("directory_url", "")).strip().rstrip("/") or DEFAULT_DIRECTORY_URL
    return bool(directory == DEFAULT_DIRECTORY_URL and settings.get("creator_id") and settings.get("creator_key")
                and not settings.get("managed_relay") and (not origin or is_legacy_relay(origin) or is_funnel_origin(origin)))


def queue_channel_publication(channel: dict, enabled: bool | None) -> bool:
    """Remember a requested publication so the background service can retry it."""
    with LOCK:
        raw = load_json(CONFIG_FILE, {})
        if not isinstance(raw, dict):
            raw = {}
        if enabled is None:
            enabled = raw.get("auto_publish_channels", True)
            if not enabled or raw.get("creator_id") or raw.get("pending_creator_profile"):
                return False
        raw["auto_publish_channels"] = enabled
        queued = bool(enabled and not raw.get("creator_id"))
        if queued and not raw.get("pending_creator_profile"):
            plex = raw.get("plex_profile") or {}
            raw["pending_creator_profile"] = {
                "name": plex.get("server_name") or channel["name"],
                "slug": "studio-" + secrets.token_hex(6),
            }
        elif not enabled:
            raw["pending_creator_profile"] = {}
        atomic_json(CONFIG_FILE, raw)
    if queued:
        PUBLISH_CHANGED.set()
    return queued


def queue_existing_publication(has_movies: bool = False) -> None:
    settings = config()
    if not settings["auto_publish_channels"] or settings["creator_profile"] or settings["pending_creator_profile"]:
        return
    channel = next((channel for channel in channels() if channel.get("items")), None)
    if channel:
        queue_channel_publication(channel, None)
    elif has_movies or VAULT_CATALOG_FILE.is_file() and cached_plex_movies():
        queue_channel_publication({"name": "Rewind Cinema"}, None)


def channels() -> list[dict]:
    with LOCK:
        value = load_json(CHANNELS_FILE, [])
        if not isinstance(value, list):
            return []
        missing_ids = [channel for channel in value if not channel.get("id")]
        for channel in missing_ids:
            channel["id"] = secrets.token_hex(8)
        legacy = [channel for channel in value if channel.get("items") and not (channel.get("repeat_on_air") or channel.get("air_until"))]
        if legacy:
            for channel in legacy:
                channel["air_until"] = current_run_end(channel).isoformat()
        if legacy or missing_ids:
            atomic_json(CHANNELS_FILE, value)
        return value


def lineup_duration(channel, *, items=None):
    if items is None:
        items = timeline_items(channel)
    return timedelta(milliseconds=sum(max(1000, int(item.get("duration_ms", 1800000))) for item in items))


def current_run_end(channel):
    anchor = parse_timestamp(channel.get("schedule_anchor", ""))
    duration = lineup_duration(channel)
    cycles = max(0, (datetime.now(timezone.utc) - anchor) // duration) if duration else 0
    return anchor + (cycles + 1) * duration


def run_finished(channel, moment=None):
    return bool(not channel.get("repeat_on_air") and channel.get("air_until")
                and (moment or datetime.now(timezone.utc)) >= parse_timestamp(channel["air_until"]))


def update_channel_run(channel_id, action):
    with LOCK:
        values = channels()
        channel = next((row for row in values if row["id"] == channel_id), None)
        if not channel:
            raise ValueError("Channel not found.")
        finished = run_finished(channel)
        if action == "shuffle" and channel.get("playback_mode") == "custom":
            raise ValueError("Maximum customization uses the exact order saved in the channel builder.")
        if action == "shuffle":
            previous_order = scheduled_items(channel)
            for _ in range(16):
                channel["shuffle_seed"] = secrets.token_hex(8)
                if scheduled_items(channel) != previous_order:
                    break
        if (action == "reair" and finished) or (action == "shuffle" and not finished):
            now = datetime.now(timezone.utc)
            channel["schedule_anchor"] = now.isoformat()
            channel["repeat_on_air"] = True
            channel["air_until"] = ""
        if action == "shuffle" or (action == "reair" and finished):
            atomic_json(CHANNELS_FILE, values)
        PLAYBACK_ERRORS.pop(channel_id, None)
        generate_exports(values)
        return channel


def backup_state(reason: str) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    settings = config()
    snapshot = {"created_at": datetime.now(timezone.utc).isoformat(), "reason": reason, "channels": channels(), "assets": asset_manifest(),
                "studio_settings": {"name": settings["creator_profile"].get("name", ""),
                                    **{k: settings[k] for k in ("guide_hours", "port", "auto_publish_channels", "public_tunnel")}}}
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    atomic_json(BACKUP_DIR / name, snapshot)
    # A burst of edits must not discard every restore point from earlier days.
    owned = sorted(p for p in BACKUP_DIR.glob("*.json") if re.fullmatch(r"\d{8}T\d{12}Z\.json", p.name))
    first_per_day = {}
    for path in owned:
        first_per_day.setdefault(path.name[:8], path)
    keep = set(owned[-20:]) | set(list(first_per_day.values())[-30:])
    for stale in owned:
        if stale not in keep:
            stale.unlink(missing_ok=True)


def asset_manifest() -> list[dict]:
    manifest = load_json(ASSETS_DIR / "manifest.json", [])
    return manifest if isinstance(manifest, list) else []


def safe_asset_id(value: str) -> str:
    cleaned = "".join(char for char in value.lower() if char.isalnum() or char in "-_").strip("-_")
    return cleaned[:60] or secrets.token_hex(6)


def save_asset_record(record: dict, content: bytes) -> dict:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    values = asset_manifest()
    stem = safe_asset_id(record.get("name", "asset"))
    asset_id = f"{stem}-{secrets.token_hex(3)}"
    extension = record["extension"]
    filename = f"{asset_id}.{extension}"
    (ASSETS_DIR / filename).write_bytes(content)
    item = {
        "id": asset_id, "name": str(record.get("name", "Asset"))[:100],
        "kind": str(record.get("kind", "logo"))[:30], "filename": filename,
        "content_type": record["content_type"], "created_at": datetime.now(timezone.utc).isoformat(),
    }
    values.append(item)
    atomic_json(ASSETS_DIR / "manifest.json", values)
    return item


def install_default_assets() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    values = asset_manifest()
    active_legacy = {
        channel.get(field, "") for channel in channels()
        for field in ("logo_asset_id", "background_asset_id", "corner_bug_asset_id")
    }
    active_legacy.add(config()["creator_profile"].get("logo_asset_id", ""))
    kinds = ("logo", "guide-background", "corner-bug")
    new_ids = {f"builtin-studio-{style}-{kind}" for style in studio_branding.STYLES for kind in kinds}
    new_ids.update(f"builtin-ambience-{sound}" for sound in studio_branding.AMBIENCE)
    keep = []
    for item in values:
        asset_id = item.get("id")
        if not item.get("built_in") or asset_id in new_ids:
            keep.append(item)
        elif asset_id in active_legacy:
            keep.append({**item, "retired": True})
        else:
            name = Path(str(item.get("filename", ""))).name
            if name.startswith("builtin-"):
                (ASSETS_DIR / name).unlink(missing_ok=True)
    known = {item["id"] for item in keep}
    for style, (label, _, _) in studio_branding.STYLES.items():
        for kind in kinds:
            asset_id = f"builtin-studio-{style}-{kind}"
            filename = asset_id + ".png"
            path = ASSETS_DIR / filename
            if not path.is_file():
                studio_branding.default_art(style, kind).save(path, optimize=True)
            if asset_id not in known:
                keep.append({"id": asset_id, "name": f"{label} {kind.replace('-', ' ').title()}",
                             "kind": kind, "filename": filename, "content_type": "image/png", "built_in": True})
    for sound, label in studio_branding.AMBIENCE.items():
        asset_id = f"builtin-ambience-{sound}"
        filename = asset_id + ".wav"
        path = ASSETS_DIR / filename
        if not path.is_file():
            studio_branding.write_ambience(path, sound)
        if asset_id not in known:
            keep.append({"id": asset_id, "name": label, "kind": "ambience", "filename": filename,
                         "content_type": "audio/wav", "built_in": True})
    if keep != values:
        atomic_json(ASSETS_DIR / "manifest.json", keep)


def generate_asset(payload: dict) -> dict:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("Asset name is required.")
    kind = str(payload.get("kind", "logo"))
    if kind not in {"logo", "guide-background", "corner-bug"}:
        raise ValueError("Invalid asset type.")
    primary = str(payload.get("primary", "#68e0b8"))
    secondary = str(payload.get("secondary", "#6da9ff"))
    for color in (primary, secondary):
        if len(color) != 7 or not color.startswith("#") or any(char not in "0123456789abcdefABCDEF" for char in color[1:]):
            raise ValueError("Choose valid colors.")
    dimensions = {"logo": (1200, 675), "guide-background": (1920, 1080), "corner-bug": (600, 600)}
    width, height = dimensions[kind]
    radius = 90 if kind == "corner-bug" else 48
    label = html.escape(name)
    subtitle = {"logo": "CHANNEL", "guide-background": "NOW PLAYING", "corner-bug": "LIVE"}[kind]
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{primary}"/><stop offset="1" stop-color="{secondary}"/></linearGradient></defs>
<rect width="100%" height="100%" rx="{radius}" fill="#08111f"/><circle cx="{width*.82}" cy="{height*.18}" r="{min(width,height)*.32}" fill="url(#g)" opacity=".3"/>
<rect x="{width*.06}" y="{height*.1}" width="{width*.02}" height="{height*.8}" rx="12" fill="url(#g)"/>
<text x="{width*.13}" y="{height*.48}" fill="#f5f7fb" font-family="Arial,sans-serif" font-size="{max(42,min(128,width//10))}" font-weight="800">{label}</text>
<text x="{width*.13}" y="{height*.62}" fill="{primary}" font-family="Arial,sans-serif" font-size="{max(24,min(48,width//24))}" font-weight="700" letter-spacing="8">{subtitle}</text></svg>'''
    return save_asset_record({"name": name, "kind": kind, "extension": "svg", "content_type": "image/svg+xml"}, svg.encode("utf-8"))


def upload_asset(payload: dict) -> dict:
    name, kind = str(payload.get("name", "")).strip(), str(payload.get("kind", "logo"))
    data_url = str(payload.get("data", ""))
    allowed = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/svg+xml": "svg"}
    if not name or kind not in {"logo", "guide-background", "corner-bug"}:
        raise ValueError("Enter a name and valid asset type.")
    if not data_url.startswith("data:") or ";base64," not in data_url:
        raise ValueError("Choose a valid image file.")
    header, encoded = data_url.split(",", 1)
    content_type = header[5:].split(";", 1)[0]
    if content_type not in allowed:
        raise ValueError("Use PNG, JPEG, WebP, or SVG artwork.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError("The image data was invalid.") from exc
    if not content or len(content) > 8_000_000:
        raise ValueError("Artwork must be smaller than 8 MB.")
    if content_type == "image/svg+xml":
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise ValueError("The SVG file was invalid.") from exc
        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1].lower()
            if tag in {"script", "foreignobject", "iframe", "object", "embed"}:
                raise ValueError("SVG files cannot contain scripts or embedded documents.")
            for attribute, value in node.attrib.items():
                key = attribute.rsplit("}", 1)[-1].lower()
                if key.startswith("on") or (key == "href" and str(value).lower().startswith(("http:", "https:", "javascript:"))):
                    raise ValueError("SVG files cannot contain event handlers or external links.")
    return save_asset_record({"name": name, "kind": kind, "extension": allowed[content_type], "content_type": content_type}, content)


def upload_ambience(payload: dict) -> dict:
    name = str(payload.get("name", "")).strip()
    header, separator, encoded = str(payload.get("data", "")).partition(";base64,")
    if not name or not separator or not header.startswith("data:audio/"):
        raise ValueError("Choose an audio file and give it a name.")
    original = base64.b64decode(encoded, validate=True)
    if not original or len(original) > 3_500_000:
        raise ValueError("Ambience files must be smaller than 3.5 MB.")
    rate = studio_branding.AMBIENCE_RATE
    limit = studio_branding.AMBIENCE_SECONDS * rate
    with tempfile.TemporaryDirectory(prefix="channelforge-ambience-") as folder:
        source, target = Path(folder) / "audio-input", Path(folder) / "ambience.wav"
        source.write_bytes(original)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run([ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin",
                                 "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:a:0",
                                 "-af", "volume=0.25,alimiter=limit=0.3:level=0", "-ar", str(rate), "-ac", "1",
                                 "-t", "25.05", "-c:a", "pcm_s16le", str(target)],
                                capture_output=True, timeout=12, creationflags=flags)
        if result.returncode or not target.is_file():
            raise ValueError("Studio could not open that audio file.")
        with wave.open(str(target), "rb") as audio:
            samples = audio.getnframes()
            if not 0 < samples <= limit:
                raise ValueError("Choose a clip no longer than 25 seconds.")
            frames = audio.readframes(samples)
        data = io.BytesIO()
        with wave.open(data, "wb") as output:
            output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            output.writeframes(frames + bytes((limit - samples) * 2))
    with LOCK:
        return save_asset_record({"name": name, "kind": "ambience", "extension": "wav",
                                  "content_type": "audio/wav"}, data.getvalue())


def channel_branding_poster(program: dict):
    source = program.get("source", "plex")
    if source == "local":
        try:
            return studio_branding.open_artwork(local_image(program["section"], program["id"])[0])
        except (KeyError, OSError, ValueError):
            return None
    fields = ("series_thumb", "thumb") if program.get("series") else ("thumb", "series_thumb")
    for poster in dict.fromkeys(str(program.get(field) or "") for field in fields):
        if not poster:
            continue
        try:
            if source == "jellyfin" and jellyfin_id(poster):
                connection, _ = jellyfin_section(program["section"])
                data, _ = jellyfin_request(connection, f"/Items/{poster}/Images/Primary?maxWidth=384", image=True)
            elif source == "plex" and re.fullmatch(r"/library/metadata/\d+/thumb(?:/\d+)?", poster):
                data, _ = plex_binary(poster, program.get("plex_server_id", ""), "poster")
            else:
                continue
        except (KeyError, OSError, ValueError):
            continue
        image = studio_branding.open_artwork(data)
        if image is not None:
            return image
    return None


def render_channel_branding_card(branding: dict, channel: dict, program: dict):
    assets = {record["id"]: record for record in asset_manifest()}
    logo = None
    for field in ("corner_bug_asset_id", "logo_asset_id"):
        asset = assets.get(channel.get(field))
        if not asset or asset.get("content_type") not in {"image/png", "image/jpeg", "image/webp"}:
            continue
        try:
            with (ASSETS_DIR / Path(asset["filename"]).name).open("rb") as source:
                data = source.read(8_000_001)
            if len(data) <= 8_000_000:
                logo = studio_branding.open_artwork(data, "RGBA")
        except (KeyError, OSError, ValueError):
            continue
        if logo is not None:
            break
    return studio_branding.render_card(branding, channel, program, channel_branding_poster(program), logo)


def ambience_file(branding):
    selection = branding["ambience"]
    asset_id = "builtin-ambience-" + selection if selection in studio_branding.AMBIENCE else selection
    asset = next((item for item in asset_manifest() if item["id"] == asset_id and item.get("kind") == "ambience"), None)
    if asset:
        path = ASSETS_DIR / Path(asset["filename"]).name
        if path.is_file():
            return path
    return None


def delete_asset(asset_id: str) -> None:
    with LOCK:
        values = asset_manifest()
        match = next((item for item in values if item.get("id") == asset_id), None)
        if not match:
            raise ValueError("Asset not found.")
        if match.get("built_in"):
            raise ValueError("Built-in starter assets cannot be deleted.")
        path = ASSETS_DIR / Path(match["filename"]).name
        if path.exists():
            path.unlink()
        atomic_json(ASSETS_DIR / "manifest.json", [item for item in values if item.get("id") != asset_id])


def plex_request(path: str, settings: dict | None = None) -> ET.Element:
    settings = settings or config()
    if not settings["plex_token"]:
        raise ValueError("Enter a Plex token in Setup first.")
    url = settings["plex_url"] + path
    separator = "&" if "?" in url else "?"
    url += separator + urllib.parse.urlencode({"X-Plex-Token": settings["plex_token"]})
    request = urllib.request.Request(url, headers={"Accept": "application/xml", "X-Plex-Product": APP_NAME})
    try:
        with urllib.request.urlopen(request, timeout=float(settings.get("plex_timeout", 12))) as response:
            return ET.fromstring(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError("Plex rejected the token. Check the server URL and token.") from exc
        raise ValueError(f"Plex returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f"Could not reach Plex at {settings['plex_url']}.") from exc
    except ET.ParseError as exc:
        raise ValueError("Plex returned an unexpected response.") from exc


def plex_resource_servers(force: bool = False) -> dict[str, dict]:
    """Discover Plex servers shared with the signed-in account.

    Each resource carries its own access token. Those tokens stay in this
    process and are never included in API responses, channels, or exports.
    """
    settings = config()
    account_token = str(settings.get("plex_token", ""))
    if not account_token:
        return {}
    with LOCK:
        if not force and float(PLEX_RESOURCE_CACHE.get("expires", 0.0)) > time.time():
            return dict(PLEX_RESOURCE_CACHE.get("servers", {}))
    query = urllib.parse.urlencode({"includeHttps": "1", "includeRelay": "1", "includeIPv6": "1"})
    request = urllib.request.Request(
        "https://clients.plex.tv/api/v2/resources?" + query,
        headers={
            "Accept": "application/json", "X-Plex-Product": APP_NAME,
            "X-Plex-Version": VERSION, "X-Plex-Client-Identifier": persistent_client_identifier(),
            "X-Plex-Token": account_token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError("Could not load Plex libraries shared with this account.") from exc
    resources = payload if isinstance(payload, list) else payload.get("resources", []) if isinstance(payload, dict) else []
    servers: dict[str, dict] = {}
    for resource in resources:
        if not isinstance(resource, dict) or "server" not in str(resource.get("provides", "")).split(","):
            continue
        owned = resource.get("owned") in {True, 1, "1", "true"}
        token = str(resource.get("accessToken", "") or "")
        machine_id = str(resource.get("clientIdentifier") or resource.get("machineIdentifier") or "").strip()
        if owned or not token or not machine_id:
            continue
        connections = [entry for entry in resource.get("connections", []) if isinstance(entry, dict) and str(entry.get("uri", "")).startswith(("http://", "https://"))]
        if not connections:
            continue
        # A shared server's own LAN address is usually not reachable from this
        # household. Prefer its public direct address, then Plex relay, with
        # the private/LAN address as the final fallback.
        connections.sort(key=lambda entry: (
            not bool(entry.get("local")), not bool(entry.get("relay")),
            str(entry.get("uri", "")).startswith("https://"),
            ":" not in str(entry.get("address", "")),
        ), reverse=True)
        urls = list(dict.fromkeys(str(entry["uri"]).rstrip("/") for entry in connections))
        servers[machine_id] = {
            "id": machine_id, "name": str(resource.get("name") or "Shared Plex Server")[:120],
            "url": urls[0], "urls": urls, "token": token, "owned": False,
        }
    with LOCK:
        PLEX_RESOURCE_CACHE.update({"expires": time.time() + 600, "servers": servers})
    return dict(servers)


def plex_credentials(server_id: str = "") -> dict:
    if not server_id:
        settings = config()
        return {"url": settings["plex_url"], "token": settings["plex_token"], "name": settings.get("plex_profile", {}).get("server_name", "My Plex Server")}
    server = plex_resource_servers().get(server_id)
    if not server:
        raise ValueError("That shared Plex server is no longer available to this account.")
    return server


def plex_server_request(path: str, server_id: str = "") -> ET.Element:
    if not server_id:
        return plex_request(path)
    credentials = plex_credentials(server_id)
    urls = credentials.get("urls", [credentials["url"]])

    def request_url(url: str) -> tuple[str, ET.Element]:
        return url, plex_request(path, {"plex_url": url, "plex_token": credentials["token"], "plex_timeout": 4})

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(urls)), thread_name_prefix="plex-shared-connection")
    futures = [pool.submit(request_url, url) for url in urls]
    error: ValueError | None = None
    try:
        for future in concurrent.futures.as_completed(futures):
            try:
                url, root = future.result()
                with LOCK:
                    cached = PLEX_RESOURCE_CACHE.get("servers", {}).get(server_id)
                    if cached:
                        cached["url"] = url
                return root
            except ValueError as exc:
                error = exc
    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
    raise error or ValueError("That shared Plex server is offline.")


def plex_binary(path: str, server_id: str = "", size: str = "") -> tuple[bytes, str]:
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Invalid Plex image path.")
    cache_key = hashlib.sha256(f"{server_id}:{path}".encode()).hexdigest()
    width = {"mini": 128, "poster": 384, "backdrop": 960}.get(size)
    with POSTER_CACHE_LOCKS[int(cache_key[:2], 16) % len(POSTER_CACHE_LOCKS)]:
        thumbnail = POSTER_CACHE_DIR / f"{cache_key}-{width}.jpg"
        if width and thumbnail.is_file():
            return thumbnail.read_bytes(), "image/jpeg"
        data, content_type = load_plex_image(path, server_id, cache_key)
        if not width:
            return data, content_type
        try:
            from PIL import Image, ImageOps
        except ImportError:
            return data, content_type
        try:
            with Image.open(io.BytesIO(data)) as source:
                if source.width * source.height > 25_000_000:
                    return data, content_type
                image = ImageOps.exif_transpose(source)
                image.thumbnail((width, width * 3 // 2), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.convert("RGB").save(output, "JPEG", quality=78, optimize=True)
            temporary = thumbnail.with_suffix(".tmp")
            temporary.write_bytes(output.getvalue())
            temporary.replace(thumbnail)
            return output.getvalue(), "image/jpeg"
        except (OSError, ValueError, Image.DecompressionBombError):
            return data, content_type


def load_plex_image(path: str, server_id: str, cache_key: str) -> tuple[bytes, str]:
    cache_file, cache_meta = POSTER_CACHE_DIR / f"{cache_key}.img", POSTER_CACHE_DIR / f"{cache_key}.json"
    if cache_file.is_file() and cache_meta.is_file():
        metadata = load_json(cache_meta, {})
        return cache_file.read_bytes(), str(metadata.get("content_type", "image/jpeg"))
    credentials = plex_credentials(server_id)
    if not credentials["token"]:
        raise ValueError("Enter a Plex token in Setup first.")
    separator = "&" if "?" in path else "?"
    url = credentials["url"] + path + separator + urllib.parse.urlencode({"X-Plex-Token": credentials["token"]})
    request = urllib.request.Request(url, headers={"Accept": "image/*", "X-Plex-Product": APP_NAME})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read(12_000_001)
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/") or len(data) > 12_000_000:
                raise ValueError("Plex did not return an image.")
            POSTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            temporary = cache_file.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(cache_file)
            atomic_json(cache_meta, {"content_type": content_type, "cached_at": int(time.time())})
            return data, content_type
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError("Could not load that Plex image.") from exc


def plex_profile(root: ET.Element) -> dict:
    return {
        "server_name": root.get("friendlyName", "Plex Media Server"),
        "username": root.get("myPlexUsername", ""),
        "version": root.get("version", ""),
        "signed_in": root.get("myPlexSigninState", "") == "ok" or bool(root.get("myPlexUsername")),
    }


def normalize_plex_connection(raw_url: str, supplied_token: str = "") -> tuple[str, str]:
    """Extract X-Plex-Token from a pasted Plex URL without retaining it in the URL."""
    raw_url = raw_url.strip()
    parsed = urllib.parse.urlsplit(raw_url)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    token = supplied_token.strip()
    for key in list(query):
        if key.lower() == "x-plex-token":
            if not token and query[key]:
                token = query[key][0].strip()
            del query[key]
    clean_query = urllib.parse.urlencode(query, doseq=True)
    clean_path = parsed.path.rstrip("/")
    if clean_path.endswith("/web/index.html"):
        clean_path = clean_path[:-len("/web/index.html")]
    clean_url = urllib.parse.urlunsplit((parsed.scheme or "http", parsed.netloc, clean_path, clean_query, "")).rstrip("/")
    if not parsed.netloc:
        raise ValueError("Enter a complete Plex URL such as http://127.0.0.1:32400.")
    return clean_url, token


def persistent_client_identifier() -> str:
    raw = load_json(CONFIG_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    identifier = str(raw.get("client_identifier", "")).strip()
    if not identifier:
        identifier = secrets.token_hex(16)
        raw["client_identifier"] = identifier
        atomic_json(CONFIG_FILE, raw)
    return identifier


def plex_tv_json(url: str, method: str = "GET") -> dict:
    if not url.startswith("https://plex.tv/api/"):
        raise ValueError("Invalid Plex authentication URL.")
    identifier = persistent_client_identifier()
    request = urllib.request.Request(url, data=b"" if method == "POST" else None, method=method, headers={"Accept": "application/json", "X-Plex-Product": APP_NAME, "X-Plex-Version": VERSION, "X-Plex-Client-Identifier": identifier})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError("Could not communicate with Plex sign-in.") from exc


def start_plex_signin() -> dict:
    pin = plex_tv_json("https://plex.tv/api/v2/pins?strong=true", "POST")
    pin_id, code = int(pin.get("id", 0)), str(pin.get("code", ""))
    if not pin_id or not code:
        raise ValueError("Plex did not create a valid sign-in request.")
    identifier = persistent_client_identifier()
    fragment = urllib.parse.urlencode({"clientID": identifier, "code": code, "context[device][product]": APP_NAME})
    return {"pin_id": pin_id, "auth_url": "https://app.plex.tv/auth#?" + fragment, "expires_in": int(pin.get("expiresIn", 900) or 900)}


def start_plex_signin_external() -> dict:
    """Open Plex in the real system browser so Linux WebKit never blocks sign-in."""
    result = start_plex_signin()
    def open_browser() -> None:
        try:
            webbrowser.open(result["auth_url"], new=2, autoraise=True)
        except Exception:
            pass
    threading.Thread(target=open_browser, daemon=True, name="channel-forge-plex-browser").start()
    return {**result, "opened_external": True}


def discover_plex_server(token: str) -> str:
    identifier = persistent_client_identifier()
    url = "https://plex.tv/api/resources?" + urllib.parse.urlencode({"includeHttps": "1", "includeRelay": "1", "X-Plex-Token": token, "X-Plex-Client-Identifier": identifier})
    request = urllib.request.Request(url, headers={"Accept": "application/xml", "X-Plex-Product": APP_NAME})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            root = ET.fromstring(response.read())
    except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
        raise ValueError("Plex sign-in succeeded, but no server list could be loaded.") from exc
    candidates = []
    for device in root.findall("Device"):
        provides = set(str(device.get("provides", "")).split(","))
        if "server" not in provides:
            continue
        for connection in device.findall("Connection"):
            uri = str(connection.get("uri", "")).rstrip("/")
            if uri:
                score = (connection.get("local") == "1") * 4 + (connection.get("relay") != "1") * 2 + uri.startswith("https://")
                candidates.append((score, uri))
    if not candidates:
        raise ValueError("No accessible Plex Media Server was found for this account.")
    return max(candidates, key=lambda item: item[0])[1]


def poll_plex_signin(pin_id: int) -> dict:
    pin = plex_tv_json(f"https://plex.tv/api/v2/pins/{int(pin_id)}")
    token = str(pin.get("authToken", "") or "")
    if not token:
        return {"complete": False}
    plex_url = discover_plex_server(token)
    old = config()
    updated = {**old, "plex_url": plex_url, "plex_token": token, "client_identifier": persistent_client_identifier()}
    root = plex_request("/", updated)
    updated["plex_profile"] = plex_profile(root)
    atomic_json(CONFIG_FILE, updated)
    generate_exports()
    sync_plex_movies_async()
    return {"complete": True, "config": config(True)}


def plex_section_parts(section: str) -> tuple[str, str]:
    if section.startswith("plex:"):
        _, server_id, section_id = section.split(":", 2)
        if not server_id or not section_id.isdigit():
            raise ValueError("Invalid shared Plex library section.")
        return server_id, section_id
    if not section.isdigit():
        raise ValueError("Invalid Plex library section.")
    return "", section


def plex_libraries(server_id: str = "") -> list[dict]:
    root = plex_server_request("/library/sections", server_id)
    server_name = plex_credentials(server_id).get("name", "Shared Plex Server") if server_id else ""
    return [
        {
            "key": f"plex:{server_id}:{node.get('key', '')}" if server_id else node.get("key", ""),
            "title": f"{node.get('title', 'Untitled')} — shared by {server_name}" if server_id else node.get("title", "Untitled"),
            "type": node.get("type", "unknown"), "uuid": node.get("uuid", ""), "thumb": node.get("thumb", ""),
            "server_id": server_id, "server_name": server_name, "shared": bool(server_id),
        }
        for node in root.findall("Directory") if node.get("key")
    ]


def media_search_tags(tags, genres, countries) -> list[str]:
    values = list(dict.fromkeys(tag.strip()[:120] for tag in tags if isinstance(tag, str) and tag.strip()))
    genre_names = {str(genre).strip().casefold() for genre in genres}
    animated = bool(genre_names & {"animation", "anime", "アニメ"})
    japanese = any(str(country).strip().casefold() in {"jp", "japan", "日本"} for country in countries)
    explicit_anime = bool(genre_names & {"anime", "アニメ"})
    if animated and japanese and not any(tag.casefold() == "anime" for tag in values):
        values.append("Anime")
    return values


def plex_items(section: str) -> list[dict]:
    server_id, section_id = plex_section_parts(section)
    libraries = plex_libraries(server_id) if server_id else plex_libraries()
    library = next((item for item in libraries if item["key"] == section), None)
    if not library:
        raise ValueError("Plex library was not found.")

    def credit_names(node, tag, limit=24):
        return [entry.get("tag", "").strip()[:120] for entry in node.findall(tag)[:limit] if entry.get("tag", "").strip()]

    def provider_tags(node):
        return credit_names(node, "Label") + credit_names(node, "Collection")

    # TV libraries must expose playable episodes, but Plex normally keeps genres,
    # the premiere year, and the content rating on the non-playable show folder.
    # Load both views and inherit that metadata so Smart Channels can filter whole
    # series instead of seeing thousands of genre-less episodes.
    show_metadata: dict[str, dict] = {}
    show_metadata_by_title: dict[str, dict] = {}
    if library["type"] == "show":
        show_root = plex_server_request(f"/library/sections/{section_id}/all?type=2", server_id)
        for show in list(show_root):
            title = str(show.get("title", "")).strip()
            metadata = {
                "rating_key": str(show.get("ratingKey", "")),
                "year": show.get("year", ""),
                "content_rating": show.get("contentRating", ""),
                "rating": show.get("rating", ""),
                "genres": sorted({genre.get("tag", "").strip() for genre in show.findall("Genre") if genre.get("tag", "").strip()}),
                "tags": provider_tags(show),
                "countries": credit_names(show, "Country"),
                "actors": credit_names(show, "Role"),
                "directors": credit_names(show, "Director"),
                "summary": show.get("summary", "")[:600],
                "thumb": show.get("thumb", ""),
                "art": show.get("art", ""),
            }
            if metadata["rating_key"]:
                show_metadata[metadata["rating_key"]] = metadata
            if title:
                show_metadata_by_title[title.casefold()] = metadata
        root = plex_server_request(f"/library/sections/{section_id}/all?type=4", server_id)
    else:
        root = plex_server_request(f"/library/sections/{section_id}/all", server_id)
    result = []
    for node in list(root):
        rating_key = node.get("ratingKey")
        if not rating_key:
            continue
        media_node = node.find("Media")
        part_node = node.find("./Media/Part")
        audio_streams = [stream for stream in node.findall("./Media/Part/Stream") if stream.get("streamType") == "2"]
        english_audio = next((stream for stream in audio_streams if str(stream.get("languageCode", "")).casefold() in {"en", "eng"} or str(stream.get("language", "")).casefold().startswith("english")), None)
        selected_audio = next((stream for stream in audio_streams if stream.get("selected") == "1"), None)
        preferred_audio = english_audio if english_audio is not None else selected_audio if selected_audio is not None else audio_streams[0] if audio_streams else None
        series_title = node.get("grandparentTitle", "")
        parent_metadata = show_metadata.get(str(node.get("grandparentRatingKey", ""))) or show_metadata_by_title.get(str(series_title).casefold(), {})
        episode_genres = sorted({genre.get("tag", "").strip() for genre in node.findall("Genre") if genre.get("tag", "").strip()})
        genres = list(dict.fromkeys(episode_genres + parent_metadata.get("genres", [])))
        tags = provider_tags(node)
        result.append({
            "id": rating_key,
            "title": (f'{node.get("grandparentTitle", "")} · S{int(node.get("parentIndex", "0") or 0):02d}E{int(node.get("index", "0") or 0):02d} · {node.get("title", "Untitled")}' if node.get("type") == "episode" else node.get("title", "Untitled")),
            "year": parent_metadata.get("year") or node.get("year", ""),
            "release_date": node.get("originallyAvailableAt", ""),
            "release_year": node.get("year", ""),
            "type": node.get("type", "video"),
            "duration_ms": int(node.get("duration", "1800000") or 1800000),
            "added_at": node.get("addedAt", ""),
            "key": node.get("key", f"/library/metadata/{rating_key}"),
            "thumb": node.get("thumb", ""),
            "series_thumb": node.get("grandparentThumb", "") or parent_metadata.get("thumb", ""),
            "series_art": node.get("grandparentArt", "") or parent_metadata.get("art", ""),
            "art": node.get("art", "") or node.get("grandparentArt", "") or parent_metadata.get("art", ""),
            "summary": (node.get("summary", "") or parent_metadata.get("summary", ""))[:600],
            "program_summary": node.get("summary", "")[:600],
            "content_rating": node.get("contentRating", "") or parent_metadata.get("content_rating", ""),
            "rating": node.get("rating", "") or parent_metadata.get("rating", ""),
            "genres": genres,
            "tags": media_search_tags(tags + parent_metadata.get("tags", []),
                                      genres, credit_names(node, "Country") + parent_metadata.get("countries", [])),
            "program_tags": tags,
            "program_aliases": [str(node.get(key, ""))[:120] for key in ("originalTitle", "titleSort") if node.get(key)],
            "actors": list(dict.fromkeys(credit_names(node, "Role") + parent_metadata.get("actors", []))),
            "directors": list(dict.fromkeys(credit_names(node, "Director") + parent_metadata.get("directors", []))),
            "series": series_title,
            "season": int(node.get("parentIndex", "0") or 0),
            "episode": int(node.get("index", "0") or 0),
            "part_key": part_node.get("key", "") if part_node is not None else "",
            "container": media_node.get("container", "") if media_node is not None else "",
            "video_codec": media_node.get("videoCodec", "") if media_node is not None else "",
            "height": int(media_node.get("height") or 0) if media_node is not None else 0,
            "audio_codec": media_node.get("audioCodec", "") if media_node is not None else "",
            "audio_stream_index": audio_streams.index(preferred_audio) if preferred_audio is not None else 0,
            "audio_stream_id": preferred_audio.get("id", "") if preferred_audio is not None else "",
            "audio_language": (preferred_audio.get("languageCode", "") or preferred_audio.get("language", "")) if preferred_audio is not None else "",
            "plex_server_id": server_id,
        })
    return result


def refresh_plex_movie_catalog() -> dict:
    with MOVIE_SCAN_LOCK:
        return scan_plex_movies()


def scan_plex_movies() -> dict:
    """Cache every movie library visible to the signed-in Plex account.

    Plex Home/shared-library grants are account scoped, so Rewind Cinema uses
    the same aggregate library discovery as Channel Builder. A temporarily
    offline shared server is skipped without hiding movies from healthy
    servers, and its credentials are never exposed in the public vault feed.
    """
    libraries = [library for library in all_libraries(include_jellyfin=False) if library.get("source") != "local" and library.get("type") == "movie"]
    movies, failed, failed_sections = [], [], set()

    def load_library(library: dict) -> tuple[dict, list[dict]]:
        return library, plex_items(str(library["key"]))

    if libraries:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(libraries)), thread_name_prefix="plex-rewind-library") as pool:
            jobs = {pool.submit(load_library, library): library for library in libraries}
            for job in concurrent.futures.as_completed(jobs):
                library = jobs[job]
                try:
                    loaded_library, items = job.result()
                    source = "plex-shared" if loaded_library.get("shared") or loaded_library.get("source") == "plex-shared" else "plex"
                    movies.extend({
                        **item, "source": source, "section": str(loaded_library["key"]),
                        "library": loaded_library.get("title", "Movies"),
                    } for item in items if item.get("type") == "movie")
                except (OSError, ValueError) as exc:
                    failed.append({"library": library.get("title", "Movies"), "error": str(exc)})
                    failed_sections.add(str(library["key"]))
    if failed_sections:
        movies.extend(item for item in cached_plex_movies() if str(item.get("section")) in failed_sections)
    movies.sort(key=lambda item: (str(item.get("title", "")).casefold(), str(item.get("year", ""))))
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "items": movies}
    atomic_json(VAULT_CATALOG_FILE, payload)
    return {
        "movies": len(movies), "libraries": len(libraries),
        "shared_movies": sum(1 for item in movies if item.get("source") == "plex-shared"),
        "failed_libraries": failed, "updated_at": payload["updated_at"],
    }


def cached_plex_movies() -> list[dict]:
    try:
        changed = VAULT_CATALOG_FILE.stat()
    except OSError:
        return []
    return read_indexed_plex_movies(VAULT_CATALOG_FILE, changed.st_mtime_ns, changed.st_size)


@lru_cache(maxsize=2)
def read_indexed_plex_movies(path: Path, _modified: int, _length: int) -> list[dict]:
    payload = load_json(path, {})
    items = payload.get("items", []) if isinstance(payload, dict) else []
    return items if isinstance(items, list) else []


def sync_plex_movies_async() -> None:
    if not config().get("plex_token"):
        return
    def worker() -> None:
        global MOVIE_SCAN_STATUS
        if not MOVIE_SCAN_LOCK.acquire(blocking=False):
            return
        try:
            MOVIE_SCAN_STATUS = {"state": "scanning"}
            result = refresh_plex_movie_catalog()
            MOVIE_SCAN_STATUS = {"state": "ready", "movies": result["movies"]}
            queue_existing_publication(has_movies=bool(result["movies"]))
            print(f'Rewind Cinema indexed {result["movies"]} Plex movies.')
        except Exception as exc:
            MOVIE_SCAN_STATUS = {"state": "retry"}
            print(f"Rewind Cinema sync failed: {exc}")
        finally:
            MOVIE_SCAN_LOCK.release()
    threading.Thread(target=worker, daemon=True, name="channel-forge-rewind-sync").start()


JELLYFIN_ID = re.compile(r"[a-f0-9]{32}")
JELLYFIN_HEADERS = 'MediaBrowser Client="ChannelForge", Device="Creator", DeviceId="' + hashlib.sha256(str(DATA_DIR).encode()).hexdigest()[:32] + f'", Version="{VERSION}"'


def jellyfin_id(value) -> str:
    value = str(value or "").replace("-", "").lower()
    return value if JELLYFIN_ID.fullmatch(value) else ""


def jellyfin_connections(*, public=False) -> list[dict]:
    values = load_json(JELLYFIN_FILE, [])
    if not isinstance(values, list):
        return []
    fields = ("id", "url", "name", "username", "user_id", "token")
    return [({key: row[key] for key in fields[:4]} if public else row) for row in values
            if isinstance(row, dict) and all(isinstance(row.get(key), str) for key in fields)]


def jellyfin_connection(connection_id: str) -> dict:
    match = next((row for row in jellyfin_connections() if row["id"] == connection_id), None)
    if match is None:
        raise ValueError("This Jellyfin connection is unavailable. Reconnect it in Sources & sharing.")
    return match


def jellyfin_authorization(connection: dict) -> str:
    return f'{JELLYFIN_HEADERS}, Token="{connection["token"]}"' if connection.get("token") else JELLYFIN_HEADERS


def jellyfin_request(connection, path, *, payload=None, image=False, missing_ok=False):
    request = urllib.request.Request(connection["url"] + path, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={"Accept": "image/*" if image else "application/json", "Content-Type": "application/json"})
    # A reverse proxy redirect must not carry this server's token to another origin.
    request.add_unredirected_header("Authorization", jellyfin_authorization(connection))
    try:
        with urllib.request.urlopen(request, timeout=7) as response:
            limit = 12_000_000 if image else 6 * 1024**2
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError("Jellyfin returned too much data.")
            if image:
                content_type = response.headers.get_content_type()
                if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                    raise ValueError("Jellyfin did not return a supported image.")
                return data, content_type
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("Expected a Jellyfin JSON object.")
            return result
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 404 and missing_ok:
            return None
        if exc.code in (401, 403):
            raise ValueError("Jellyfin rejected the sign-in. Check the account or reconnect it.") from None
        raise ValueError(f"Jellyfin returned HTTP {exc.code}.") from None
    except (OSError, ValueError) as exc:
        raise ValueError("Jellyfin did not return a usable response. Check the server address and try again.") from exc


def connect_jellyfin(payload: dict) -> dict:
    url = str(payload.get("url", "")).strip().rstrip("/")
    address = urllib.parse.urlsplit(url)
    if address.scheme not in {"http", "https"} or not address.hostname or address.username or address.password or address.query or address.fragment or re.search(r"[\x00-\x20]", url):
        raise ValueError("Enter a Jellyfin server address such as http://192.168.1.20:8096.")
    username = str(payload.get("username", "")).strip()
    if not username:
        raise ValueError("Enter your Jellyfin username.")
    response = jellyfin_request({"url": url}, "/Users/AuthenticateByName", payload={"Username": username, "Pw": str(payload.get("password", ""))})
    user = response.get("User")
    user = user if isinstance(user, dict) else {}
    user_id = jellyfin_id(user.get("Id"))
    token = str(response.get("AccessToken", ""))
    if not user_id or not re.fullmatch(r"[A-Za-z0-9_.~-]{1,256}", token):
        raise ValueError("Jellyfin did not provide a usable user session.")
    server = jellyfin_id(response.get("ServerId")) or url
    identity = hashlib.sha256(f"{server}:{user_id}".encode()).hexdigest()[:24]
    connection = {"id": identity, "url": url, "name": str(payload.get("name", "")).strip()[:100] or address.hostname,
                  "username": username, "user_id": user_id, "token": token}
    with LOCK:
        values = {row["id"]: row for row in jellyfin_connections()}
        values[identity] = connection
        atomic_json(JELLYFIN_FILE, list(values.values()))
    return {key: connection[key] for key in ("id", "url", "name", "username")}


def jellyfin_libraries(connection):
    result = jellyfin_request(connection, "/UserViews?" + urllib.parse.urlencode({"userId": connection["user_id"]}), missing_ok=True)
    if result is None and jellyfin_id(connection["user_id"]):
        result = jellyfin_request(connection, f'/Users/{connection["user_id"]}/Views')
    return [{"key": f'jellyfin:{connection["id"]}:{identity}', "title": f'{row.get("Name", "Library")} · {connection["name"]}',
             "type": {"movies": "movie", "tvshows": "show"}.get(row.get("CollectionType"), "mixed"), "source": "jellyfin"}
            for row in (result or {}).get("Items", []) or [] if isinstance(row, dict) and (identity := jellyfin_id(row.get("Id")))
            and row.get("CollectionType") in (None, "movies", "tvshows", "homevideos", "mixed", "boxsets")]


def jellyfin_section(section: str) -> tuple[dict, str]:
    parts = section.split(":")
    if len(parts) != 3 or parts[0] != "jellyfin" or not (parent_id := jellyfin_id(parts[2])):
        raise ValueError("Jellyfin library was not found.")
    return jellyfin_connection(parts[1]), parent_id


def jellyfin_items(section):
    connection, parent_id = jellyfin_section(section)
    query = {"userId": connection["user_id"], "parentId": parent_id, "recursive": "true", "includeItemTypes": "Movie,Episode,Video,Series",
             "fields": "Overview,Genres,People,MediaStreams,Tags,ProductionLocations", "limit": 200, "sortBy": "SortName"}
    result = []
    series_metadata = {}
    offset = 0
    while True:
        page = jellyfin_request(connection, "/Items?" + urllib.parse.urlencode({**query, "startIndex": offset}))
        rows = page.get("Items") or []
        if not isinstance(rows, list):
            raise ValueError("Jellyfin returned an invalid media catalog.")
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity = jellyfin_id(row.get("Id"))
            kind = str(row.get("Type", "Video")).lower()
            if identity and kind == "series":
                series_metadata[identity] = (row.get("Genres") or [], row.get("Tags") or [], row.get("ProductionLocations") or [],
                                             row.get("OriginalTitle"))
                continue
            duration = int(row.get("RunTimeTicks") or 0) // 10000
            if not identity or not duration:
                continue
            art = row.get("ImageTags") or {}
            series_id = jellyfin_id(row.get("SeriesId"))
            series = row.get("SeriesName") or ""
            video = next((stream for stream in (row.get("MediaStreams") or []) if stream.get("Type") == "Video"), {})
            title = row.get("Name") or "Untitled"
            season, episode = int(row.get("ParentIndexNumber") or 0), int(row.get("IndexNumber") or 0)
            if kind == "episode" and series:
                title = f"{series} · S{season:02d}E{episode:02d} · {title}"
            result.append({"id": identity, "title": title, "section": section, "source": "jellyfin",
                           "type": kind, "duration_ms": duration, "year": row.get("ProductionYear") or "", "summary": row.get("Overview") or "",
                           "release_date": row.get("PremiereDate", ""), "release_year": row.get("ProductionYear") or "",
                           "program_tags": (row.get("Tags") or [])[:96],
                           "program_aliases": [str(row["OriginalTitle"])[:120]] if row.get("OriginalTitle") else [],
                           "added_at": row.get("DateCreated", ""),
                           "thumb": identity if art.get("Primary") else "", "series_thumb": series_id if row.get("SeriesPrimaryImageTag") else "",
                           "art": identity if row.get("BackdropImageTags") else "",
                           "series_art": jellyfin_id(row.get("ParentBackdropItemId")) if row.get("ParentBackdropImageTags") else "",
                           "series": series, "series_id": series_id, "season": season,
                           "episode": episode, "genres": row.get("Genres") or [], "content_rating": row.get("OfficialRating") or "",
                           "tags": media_search_tags(row.get("Tags") or [], row.get("Genres") or [], row.get("ProductionLocations") or []),
                                          "actors": [person["Name"] for person in (row.get("People") or []) if person.get("Type") == "Actor" and person.get("Name")],
                           "directors": [person["Name"] for person in (row.get("People") or []) if person.get("Type") == "Director" and person.get("Name")],
                           "video_codec": video.get("Codec") or "", "height": video.get("Height") or 0})
        offset += len(rows)
        if not rows or offset >= int(page.get("TotalRecordCount") or offset):
            break
    for item in result:
        inherited = series_metadata.get(item["series_id"])
        if inherited:
            genres, tags, countries, original_title = inherited
            item["genres"] = list(dict.fromkeys(item["genres"] + genres))
            item["tags"] = media_search_tags(item["tags"] + tags, item["genres"], countries)
    return result


def all_libraries(*, include_shared: bool = True, include_jellyfin: bool = True) -> list[dict]:
    local = [{"key": item["key"], "title": item["title"], "type": "local", "source": "local"} for item in local_libraries()]
    result = []
    if config()["plex_token"]:
        try:
            result.extend({**item, "source": "plex"} for item in plex_libraries())
        except ValueError:
            pass
        shared_servers = {}
        if include_shared:
            try:
                shared_servers = plex_resource_servers()
            except ValueError:
                pass
        if shared_servers:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(shared_servers)), thread_name_prefix="plex-shared-library") as pool:
                jobs = {pool.submit(plex_libraries, server_id): server_id for server_id in shared_servers}
                for job in concurrent.futures.as_completed(jobs):
                    try:
                        result.extend({**item, "source": "plex-shared"} for item in job.result())
                    except ValueError:
                        continue
    connections = jellyfin_connections() if include_jellyfin else []
    if connections:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(connections))) as pool:
            jobs = [pool.submit(jellyfin_libraries, connection) for connection in connections]
            for job in jobs:
                try:
                    result.extend(job.result())
                except (OSError, ValueError):
                    continue
    if config()["plex_token"] and not result and not local:
        raise ValueError("No connected media libraries could be reached.")
    return result + local


def local_library(section: str) -> dict:
    library_id = section.split(":", 1)[1] if section.startswith("local:") else ""
    match = next((item for item in local_libraries() if item["id"] == library_id), None)
    if not match:
        raise ValueError("Local library was not found.")
    return match


def local_poster(path: Path, root: Path) -> Path | None:
    candidates = [path.with_suffix(extension) for extension in POSTER_EXTENSIONS]
    candidates += [path.parent / f"poster{extension}" for extension in POSTER_EXTENSIONS]
    candidates += [path.parent / f"folder{extension}" for extension in POSTER_EXTENSIONS]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
            if resolved.is_file():
                return resolved
        except (OSError, ValueError):
            continue
    return None


def local_duration_ms(path: Path) -> int:
    """Read duration with ffprobe when available; use a safe guide fallback otherwise."""
    try:
        ffmpeg = Path(ffmpeg_path())
    except ValueError:
        return 1_800_000
    probe_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    probe = ffmpeg.with_name(probe_name)
    executable = str(probe) if probe.is_file() else shutil.which("ffprobe")
    if not executable:
        return 1_800_000
    try:
        completed = subprocess.run(
            [executable, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=12, creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        return max(1000, min(86_400_000, int(float(completed.stdout.strip()) * 1000)))
    except (OSError, ValueError, subprocess.SubprocessError):
        return 1_800_000


@lru_cache(maxsize=20_000)
def cached_local_duration(path: Path, modified_ns: int, size: int) -> int:
    return local_duration_ms(path)


def local_items(section: str) -> list[dict]:
    cached = LOCAL_LIBRARY_CACHE.get(section)
    if cached and cached[0] > time.time() - 60:
        return cached[1]
    library = local_library(section)
    root = Path(library["path"]).resolve()
    result = []
    try:
        files = sorted((path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS), key=lambda path: str(path).lower())
    except OSError as exc:
        raise ValueError("ChannelForge could not read that local library.") from exc
    for path in files[:20_000]:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root)
            info = resolved.stat()
        except (OSError, ValueError):
            continue
        identity = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:24]
        poster = local_poster(resolved, root)
        title = resolved.stem.replace("_", " ").replace(".", " ").strip()
        date_tag = re.search(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b", resolved.stem)
        year_tag = re.search(r"\(((?:19|20)\d{2})\)", resolved.stem)
        result.append({
            "id": f"local-{identity}", "title": title, "year": "", "type": "video",
            "release_date": date_tag[0] if date_tag else "", "release_year": year_tag[1] if year_tag else "",
            "duration_ms": cached_local_duration(resolved, info.st_mtime_ns, info.st_size), "key": f"local:{identity}",
            "thumb": f"local:{identity}" if poster else "", "summary": str(relative.parent) if relative.parent != Path(".") else "Local media",
            "art": f"local:{identity}" if poster else "",
            "content_rating": "", "rating": "", "genres": [], "series": relative.parts[0] if len(relative.parts) > 1 else "",
            "season": 0, "episode": 0, "part_key": "", "container": resolved.suffix.lstrip("."),
            "video_codec": "", "audio_codec": "", "source": "local", "file_path": str(resolved),
            "audio_stream_index": 0, "audio_stream_id": "", "audio_language": "",
        })
    LOCAL_LIBRARY_CACHE[section] = (time.time(), result)
    return result


def library_items(section: str, *, prefer_index: bool = False) -> list[dict]:
    if section.startswith("local:"):
        return local_items(section)
    if section.startswith("jellyfin:"):
        return jellyfin_items(section)
    if prefer_index and config().get("plex_token"):
        indexed = [item for item in cached_plex_movies() if item.get("section") == section]
        if indexed:
            return indexed
    return plex_items(section)


def local_image(section: str, media_id: str) -> tuple[bytes, str]:
    library = local_library(section)
    root = Path(library["path"]).resolve()
    item = next((entry for entry in local_items(section) if entry["id"] == media_id), None)
    if not item:
        raise ValueError("Local poster was not found.")
    poster = local_poster(Path(item["file_path"]), root)
    if not poster:
        raise ValueError("Local poster was not found.")
    content_type = mimetypes.guess_type(poster.name)[0] or "application/octet-stream"
    if not content_type.startswith("image/") or poster.stat().st_size > 12_000_000:
        raise ValueError("Local poster is invalid or larger than 12 MB.")
    return poster.read_bytes(), content_type


def upload_local_media(handler: BaseHTTPRequestHandler, section: str, filename: str) -> dict:
    if handler.client_address[0] not in {"127.0.0.1", "::1"}:
        raise ValueError("Movie uploads are allowed only from the computer running ChannelForge.")
    library = local_library(section)
    original = Path(urllib.parse.unquote(filename)).name
    safe_name = re.sub(r"[^A-Za-z0-9._() -]+", "_", original).strip(" .")
    if not safe_name or Path(safe_name).suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError("Choose a supported video file.")
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length < 1 or length > 20 * 1024 * 1024 * 1024:
        raise ValueError("Movie uploads must be between 1 byte and 20 GB.")
    folder = Path(library["path"]).resolve() / "ChannelForge Uploads"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / safe_name
    if destination.exists():
        destination = folder / f"{destination.stem}-{secrets.token_hex(3)}{destination.suffix}"
    temporary = destination.with_suffix(destination.suffix + ".uploading")
    remaining = length
    try:
        with temporary.open("wb") as output:
            while remaining:
                chunk = handler.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("The upload ended before the complete movie was received.")
                output.write(chunk)
                remaining -= len(chunk)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    LOCAL_LIBRARY_CACHE.pop(section, None)
    return {"name": destination.name, "size": destination.stat().st_size, "library": library["title"]}


def media_rating_level(value) -> int:
    rating = re.sub(r"^(?:US|USA)[: -]+", "", str(value or "").strip().upper())
    return CHANNEL_RATING_LEVELS.get(rating, 0)


def broadcast_settings(channel: dict) -> tuple[int, int]:
    height = channel.get("broadcast_height", BROADCAST_DEFAULT[0])
    bitrate = channel.get("broadcast_kbps", BROADCAST_DEFAULT[1])
    if type(height) is not int or height not in BROADCAST_HEIGHTS:
        raise ValueError("Choose a broadcast quality of 270p, 480p, or 720p.")
    if type(bitrate) is not int or not 250 <= bitrate <= BROADCAST_DEFAULT[1]:
        raise ValueError("Choose a broadcast bitrate from 250 to 2500 kbps.")
    return height, bitrate


def clean_channel(payload: dict, existing_id: str = "") -> dict:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("Channel name is required.")
    number = str(payload.get("number", "")).strip()
    channel_rating = str(payload.get("channel_rating") or "").strip().upper()
    ceiling = CHANNEL_RATING_LEVELS.get(channel_rating, 0)
    if channel_rating and not ceiling:
        raise ValueError("Choose a supported channel rating.")
    if not number:
        raise ValueError("Channel number is required.")
    broadcast_height, broadcast_kbps = broadcast_settings(payload)
    items = payload.get("items", [])
    if not isinstance(items, list) or not items:
        raise ValueError("Select at least one media title.")
    section = str(payload.get("section", ""))[:700]
    normalized = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            continue
        normalized.append({
            "id": str(item["id"]), "title": str(item.get("title", "Untitled"))[:300],
            "section": str(item.get("section") or section)[:700],
            "year": str(item.get("year", ""))[:10], "type": str(item.get("type", "video"))[:30],
            "release_date": original_release_date(item.get("release_date")),
            "release_year": str(item.get("release_year") or "")[:4],
            "duration_ms": max(1000, min(86400000, int(item.get("duration_ms", 1800000)))),
            "key": str(item.get("key", f"/library/metadata/{item['id']}"))[:500],
            "thumb": str(item.get("thumb", ""))[:500], "series_thumb": str(item.get("series_thumb", ""))[:500],
            "series_art": str(item.get("series_art", ""))[:500],
            "art": str(item.get("art", ""))[:500], "summary": str(item.get("summary", ""))[:600],
            "content_rating": str(item.get("content_rating", ""))[:40], "rating": str(item.get("rating", ""))[:20],
            "genres": [str(value)[:80] for value in item.get("genres", []) if str(value).strip()][:20],
            "tags": [str(value)[:120] for value in item.get("tags", []) if str(value).strip()][:50],
            "series": str(item.get("series", ""))[:200],
            "series_id": jellyfin_id(item.get("series_id")) if item.get("source") == "jellyfin" else str(item.get("series_id") or "")[:128],
            "season": max(0, int(item.get("season", 0) or 0)), "episode": max(0, int(item.get("episode", 0) or 0)),
            "part_key": str(item.get("part_key", ""))[:500], "container": str(item.get("container", ""))[:30],
            "video_codec": str(item.get("video_codec", ""))[:30], "audio_codec": str(item.get("audio_codec", ""))[:30],
            "height": min(16384, max(0, int(item.get("height") or 0))),
            "audio_stream_index": max(0, int(item.get("audio_stream_index", 0) or 0)),
            "audio_stream_id": str(item.get("audio_stream_id", ""))[:40], "audio_language": str(item.get("audio_language", ""))[:40],
            "source": item["source"] if item.get("source") in {"local", "jellyfin"} else "plex",
            "file_path": str(item.get("file_path", ""))[:4000] if item.get("source") == "local" else "",
            "plex_server_id": str(item.get("plex_server_id", ""))[:300] if item.get("source") != "local" else "",
        })
    if not normalized:
        raise ValueError("The selected titles were invalid.")
    if ceiling and any(not 0 < media_rating_level(item["content_rating"]) <= ceiling for item in normalized):
        raise ValueError(f"A {channel_rating} channel can only contain titles with a known rating of {channel_rating} or lower.")
    mode = str(payload.get("playback_mode", "ordered"))
    return {
        "id": existing_id or secrets.token_hex(8), "name": name[:100], "number": number[:12],
        "section": section, "items": normalized, "channel_rating": channel_rating,
        "broadcast_height": broadcast_height, "broadcast_kbps": broadcast_kbps,
        **{field: str(payload.get(field, "" if existing_id else asset_id))[:80]
           for field, asset_id in DEFAULT_CHANNEL_ARTWORK.items()},
        "branding": studio_branding.settings(payload.get("branding")),
        "playback_mode": mode if mode in {"custom", "ordered", "shuffle", "shuffle_in_order", "marathon", "franchise"} else "ordered",
        **({"franchise_timeline_version": FRANCHISE_TIMELINE_VERSION,
            "franchise_order": "story" if payload.get("franchise_order") == "story" else "release"} if mode == "franchise" else {}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "schedule_anchor": str(payload.get("schedule_anchor") or datetime.now(timezone.utc).isoformat()),
    }


def save_channel(payload: dict, channel_id: str = "") -> dict:
    global SYNCED_CHANNEL_NUMBERS
    with PUBLISH_LOCK:
        payload = dict(payload)
        if not channel_id:
            payload.setdefault("branding", {"enabled": True})
        values = channels()
        previous = next((value for value in values if value.get("id") == channel_id), None)
        if channel_id and not previous:
            raise ValueError("Channel not found.")
        if previous:
            if not payload.get("schedule_anchor"):
                payload["schedule_anchor"] = previous.get("schedule_anchor")
            payload.setdefault("channel_rating", previous.get("channel_rating", ""))
            height, bitrate = broadcast_settings(previous)
            payload.setdefault("broadcast_height", height)
            payload.setdefault("broadcast_kbps", bitrate)
            payload.setdefault("branding", previous.get("branding"))
        item = clean_channel(payload, channel_id)
        number = channel_number_key(item["number"])
        changed = not previous or number != channel_number_key(previous["number"])
        if changed and any(row["id"] != channel_id and channel_number_key(row["number"]) == number for row in values):
            raise ValueError(f'Channel {item["number"]} is already used in this Studio. Choose a different number.')
        settings = config()
        if changed and settings["directory_url"] == DEFAULT_DIRECTORY_URL:
            if settings["auto_publish_channels"] and settings["creator_id"] and settings["creator_key"]:
                path = f'/v1/creators/{settings["creator_id"]}/channel-numbers'
                # Reconcile on the next heartbeat even if the reserve response is lost.
                SYNCED_CHANNEL_NUMBERS = None
                directory_request("POST", path, {"mode": "reserve", "channels": public_channel_numbers([item])},
                                  settings["creator_key"], timeout=5)
            elif payload.get("publish_worldwide") is True:
                path = "/v1/channel-number?" + urllib.parse.urlencode({"number": item["number"]})
                if not directory_request("GET", path, timeout=5).get("available"):
                    raise ValueError(f'Channel {item["number"]} is already reserved in the public guide. Choose a different number.')
        saved = write_channel(payload, channel_id, item)
        if changed:
            try:
                sync_public_channel_numbers(settings)
            except ValueError:
                PUBLISH_CHANGED.set()
        return saved


def write_channel(payload: dict, channel_id: str, item: dict) -> dict:
    with LOCK:
        backup_state("edit channel" if channel_id else "create channel")
        values = channels()
        previous = next((value for value in values if value.get("id") == channel_id), None) if channel_id else None
        if channel_id and previous:
            restore = payload.get("restore_preset") is True
            mode = item["playback_mode"]
            if (mode == previous.get("playback_mode") == "franchise" and not restore
                    and item["franchise_order"] == previous.get("franchise_order", "release")):
                # Keep the running schedule stable until the creator reapplies its preset.
                item["franchise_timeline_version"] = previous.get("franchise_timeline_version", 1)
            manual_order = payload.get("lineup_reordered") is True and mode in {"ordered", "marathon", "franchise"}
            if previous.get("shuffle_seed") and not (restore or manual_order) and mode == previous.get("playback_mode", "ordered"):
                item["shuffle_seed"] = previous["shuffle_seed"]
            item["repeat_on_air"] = not run_finished(previous)
            item["air_until"] = "" if item["repeat_on_air"] else previous["air_until"]
            if "branding" not in payload:
                item["branding"] = studio_branding.settings(previous.get("branding"))
            if restore and not run_finished(previous):
                now = datetime.now(timezone.utc)
                item["schedule_anchor"] = now.isoformat()
        else:
            item["repeat_on_air"] = True
            item["air_until"] = ""
            requested_branding = payload.get("branding")
            if not isinstance(requested_branding, dict) or "ambience" not in requested_branding:
                item["branding"]["ambience"] = studio_branding.DEFAULT_AMBIENCE
            item["branding"].update(enabled=True, seconds=studio_branding.DEFAULT_CARD_SECONDS)
        if channel_id:
            index = next((i for i, value in enumerate(values) if value.get("id") == channel_id), None)
            if index is None:
                raise ValueError("Channel not found.")
            values[index] = item
        else:
            values.append(item)
        atomic_json(CHANNELS_FILE, values)
        generate_exports(values)
        plex_posters = {(str(media.get("thumb", "")), str(media.get("plex_server_id", ""))) for media in item["items"] if media["source"] == "plex" and media.get("thumb")}
        if plex_posters:
            def cache_posters() -> None:
                for path, server_id in plex_posters:
                    try:
                        plex_binary(path, server_id)
                    except ValueError:
                        continue
            threading.Thread(target=cache_posters, daemon=True, name="channel-forge-poster-cache").start()
        return item


def delete_channel(channel_id: str) -> None:
    with PUBLISH_LOCK:
        remove_channel(channel_id)
        try:
            sync_public_channel_numbers(config())
        except ValueError:
            PUBLISH_CHANGED.set()


def remove_channel(channel_id: str) -> None:
    with LOCK:
        backup_state("delete channel")
        values = channels()
        updated = [item for item in values if item.get("id") != channel_id]
        if len(values) == len(updated):
            raise ValueError("Channel not found.")
        atomic_json(CHANNELS_FILE, updated)
        generate_exports(updated)


def reorder_channels(ids: list[str]) -> list[dict]:
    with LOCK:
        backup_state("reorder channels")
        values = channels()
        existing = {item["id"]: item for item in values}
        if set(ids) != set(existing) or len(ids) != len(existing):
            raise ValueError("Channel order did not match the saved channels.")
        ordered = [existing[channel_id] for channel_id in ids]
        atomic_json(CHANNELS_FILE, ordered)
        generate_exports(ordered)
        return ordered


def xmltv_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def scheduled_slot(items, index, start, stop, offset_ms, availability):
    """Keep the channel clock anchored when selected media becomes unavailable."""
    def present(item):
        if SOURCE_FAILURES.get(media_source_identity(item), 0) > time.monotonic():
            return False
        if item.get("source") == "jellyfin":
            if "jellyfin_connections" not in availability:
                availability["jellyfin_connections"] = {row["id"] for row in jellyfin_connections()}
            parts = str(item.get("section", "")).split(":")
            return len(parts) == 3 and parts[0] == "jellyfin" and parts[1] in availability["jellyfin_connections"]
        if item.get("source") != "local":
            return True
        path = item.get("file_path", "")
        if path not in availability:
            availability[path] = bool(path) and Path(path).is_file()
        return availability[path]

    item = items[index]
    if item.get("source") == "interstitial":
        for step in range(1, len(items)):
            upcoming = items[(index + step) % len(items)]
            if upcoming.get("source") != "interstitial" and present(upcoming):
                item = {**item, "next_program": upcoming, "title": "Up Next · " + studio_branding.programme_title(upcoming)}
                break
    if not present(item):
        # Fill only the missing slot with the upcoming playable media. A viewer
        # joining halfway uses the same UTC offsets as one already watching.
        replacements = []
        cycle_ms = 0
        for step in range(1, len(items)):
            candidate = items[(index + step) % len(items)]
            if candidate.get("source") != "interstitial" and present(candidate):
                duration = max(1000, int(candidate.get("duration_ms", 1800000)))
                replacements.append((candidate, duration))
                cycle_ms += duration
                if cycle_ms > offset_ms:
                    break
        elapsed = offset_ms % cycle_ms if cycle_ms else 0
        cursor = start + timedelta(milliseconds=offset_ms - elapsed)
        for candidate, duration in replacements:
            if elapsed < duration:
                return {"item": candidate, "substitute_for": item.get("title", ""),
                        "index": index, "slot_start": start, "start": cursor,
                        "stop": min(stop, cursor + timedelta(milliseconds=duration)), "offset_ms": elapsed}
            cursor += timedelta(milliseconds=duration)
            elapsed -= duration
    return {"item": item, "index": index, "start": start, "stop": stop, "offset_ms": offset_ms}


def media_source_identity(item):
    return (item.get("source") or "plex", item.get("plex_server_id", ""), item.get("section", ""), item.get("file_path", ""), item.get("id", ""))


def quarantine_unplayable_source(item):
    with LOCK:
        if len(SOURCE_FAILURES) >= 256:
            SOURCE_FAILURES.pop(next(iter(SOURCE_FAILURES)))
        SOURCE_FAILURES[media_source_identity(item)] = time.monotonic() + 300


def schedule_position(channel: dict, moment: datetime | None = None, *, items: list[dict] | None = None) -> dict:
    """Resolve a deterministic 24/7 channel position for any UTC moment."""
    moment = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if items is None:
        items = timeline_items(channel)
    if not items:
        raise ValueError("This channel has no scheduled titles.")
    durations = [max(1000, int(item.get("duration_ms", 1_800_000))) for item in items]
    cycle_ms = sum(durations)
    anchor = parse_timestamp(str(channel.get("schedule_anchor", "")))
    elapsed_ms = (moment - anchor) // timedelta(milliseconds=1)
    cycles, within = divmod(elapsed_ms, cycle_ms)
    cursor = anchor + timedelta(milliseconds=cycles * cycle_ms)
    for index, duration in enumerate(durations):
        if within < duration:
            start = cursor
            stop = start + timedelta(milliseconds=duration)
            return scheduled_slot(items, index, start, stop, within, {})
        within -= duration
        cursor += timedelta(milliseconds=duration)
    raise RuntimeError("Could not resolve channel schedule.")


def schedule_window(channel: dict, start: datetime, end: datetime, *, items: list[dict] | None = None) -> list[dict]:
    if items is None:
        items = timeline_items(channel)
    if channel.get("air_until") and not channel.get("repeat_on_air"):
        finish = parse_timestamp(channel["air_until"])
        end = min(end, finish)
        start = max(start, finish - lineup_duration(channel, items=items))
        if start >= end:
            return []
    position = schedule_position(channel, start, items=items)
    cursor = position.get("slot_start", position["start"])
    index = position["index"]
    result = []
    availability = {}
    while cursor < end:
        item = items[index]
        stop = cursor + timedelta(milliseconds=max(1000, int(item.get("duration_ms", 1_800_000))))
        sub_start = max(start, cursor)
        while sub_start < min(stop, end):
            offset = (sub_start - cursor) // timedelta(milliseconds=1)
            slot = scheduled_slot(items, index, cursor, stop, offset, availability)
            result.append(slot)
            sub_start = slot["stop"]
        cursor, index = stop, (index + 1) % len(items)
    return result


FRANCHISE_TIMELINE_VERSION = 2
FRANCHISE_TIMELINES = {
    "alien": {
        "prometheus": 0, "alien covenant": 1, "alien": 2, "alien earth": 3,
        "aliens": 4, "alien 3": 5, "alien resurrection": 6,
    },
    "it": {"welcome to derry": 0, "it": 1, "it chapter two": 2, "it chapter 2": 2},
}
STAR_WARS_TIMELINE = {
    "the acolyte": 10, "the phantom menace": 20, "attack of the clones": 30,
    "the clone wars": 40, "tales of the jedi": 50, "revenge of the sith": 60,
    "tales of the empire": 70, "tales of the underworld": 80, "the bad batch": 90,
    "maul shadow lord": 100, "solo": 110, "obi wan kenobi": 120, "andor": 130,
    "rebels": 140, "rogue one": 150, "a new hope": 160, "the empire strikes back": 170,
    "return of the jedi": 180, "the mandalorian": 190, "the book of boba fett": 200,
    "ahsoka": 210, "skeleton crew": 220, "the mandalorian and grogu": 230,
    "resistance": 240, "the force awakens": 250, "the last jedi": 260, "the rise of skywalker": 270,
}
STAR_WARS_EPISODE_RANKS = (20, 30, 60, 160, 170, 180, 250, 260, 270)
STAR_WARS_EPISODES = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix")


def normalized_title(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def original_release_date(value: object) -> str:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat() if len(text) == 10 else ""
    except ValueError:
        return ""


def release_sort_date(item: dict) -> str:
    premiere = original_release_date(item.get("release_date"))
    if not premiere:
        year = str(item.get("release_year") or (item.get("year") if not item.get("series") else "") or "")
        return year + "-12-31" if re.fullmatch(r"(?:18|19|20|21)\d{2}", year) else "9999-12-31"
    return premiere


def star_wars_timeline_rank(title: str, *, show: bool) -> int | None:
    name = re.sub(r" (?:19|20)\d{2}$", "", normalized_title(title))
    if name == "star wars" and not show:
        return 160  # The original film is sometimes listed under its theatrical title.
    if name in {"solo", "rebels", "resistance"}:
        return None  # These bare titles are ambiguous without the franchise name.
    prefixed = name.startswith("star wars ")
    name = name.removeprefix("star wars ").removesuffix(" a star wars story")
    episode = re.search(r"\bepisode ([ivx]+|[1-9])\b", name)
    if episode and not show:
        name = (name[:episode.start()] + name[episode.end():]).strip()
        if prefixed:
            token = episode[1]
            numbers = tuple(str(number) for number in range(1, 10))
            for labels in (STAR_WARS_EPISODES, numbers):
                if token in labels:
                    return STAR_WARS_EPISODE_RANKS[labels.index(token)]
    rank = STAR_WARS_TIMELINE.get(name)
    return rank - 1 if name == "the clone wars" and not show else rank


def franchise_family(value: object) -> str:
    title = normalized_title(value)
    if title in {"alien", "aliens", "prometheus"} or title.startswith(("alien ", "aliens ", "prometheus ")):
        return "alien"
    if title == "it" or title.startswith("it chapter") or "welcome to derry" in title:
        return "it"
    # A conservative fallback groups obvious numbered/subtitled installments
    # without merging unrelated one-word titles.
    root = re.split(r"\s+(?:chapter|part|season)\s+|\s*:\s*|\s+\d+$", title, maxsplit=1)[0].strip()
    return root if len(root) >= 4 else title


def franchise_block_order(family: str, title: str, year: object, original: int) -> tuple:
    normalized = normalized_title(title)
    timeline = FRANCHISE_TIMELINES.get(family, {})
    exact = timeline.get(normalized)
    if exact is None:
        exact = next((rank for name, rank in timeline.items() if normalized.startswith(name + " ")), None)
    parsed_year = int(year) if str(year).isdigit() else 9999
    return (0, exact, original) if exact is not None else (1, parsed_year, normalized, original)


def timeline_items(channel: dict) -> list[dict]:
    programmes = scheduled_items(channel)
    return studio_branding.with_interstitials(programmes, studio_branding.settings(channel.get("branding")),
                                             looping=bool(channel.get("repeat_on_air")))


def scheduled_items(channel: dict) -> list[dict]:
    """Return a stable lineup for ordered, shuffled, or series-marathon playback."""
    items = list(channel.get("items", []))
    mode = channel.get("playback_mode", "ordered")
    seed = str(channel.get("shuffle_seed") or channel.get("id", "channel"))
    def item_identity(item: dict) -> str:
        source = str(item.get("plex_server_id") or item.get("file_path") or "")
        return f'{source}:{item.get("id", "")}' if source else str(item.get("id", ""))

    def series_group(item: dict):
        return (item.get("series_id") or str(item.get("series", "")).strip().casefold(),
                item.get("source") or "plex", item.get("plex_server_id", ""), item.get("section", ""))

    def distinct_items():
        seen = set()
        for original, item in enumerate(items):
            key = media_source_identity(item)
            if item.get("id") and key in seen:
                continue
            seen.add(key)
            yield original, item

    stable_key = lambda item: hashlib.sha256(f"{seed}:{item_identity(item)}".encode()).hexdigest()
    if mode == "shuffle" or (mode == "ordered" and channel.get("shuffle_seed")):
        return sorted(items, key=stable_key)
    if mode == "franchise":
        updated_timeline = channel.get("franchise_timeline_version") == FRANCHISE_TIMELINE_VERSION
        if updated_timeline and channel.get("franchise_order") == "release":
            release_entries = []
            series_positions = {}
            for index, item in distinct_items():
                premiere = release_sort_date(item)
                position = series_positions.setdefault((premiere, series_group(item)), index) if item.get("series") else index
                release_entries.append(((premiere, position, item.get("season", 0), item.get("episode", 0), index), item))
            return [item for _, item in sorted(release_entries, key=lambda entry: entry[0])]
        # Build mixed movie/show blocks. A show's episodes stay in strict
        # season/episode order, while movies and shows are placed on one smart
        # franchise timeline. This keeps examples such as Alien -> Alien:
        # Earth -> Aliens and Welcome to Derry -> It -> It Chapter Two intact.
        blocks = {}
        for original, item in distinct_items():
            item_id = item_identity(item)
            series = str(item.get("series", "")).strip()
            title = series or str(item.get("title") or item_id)
            block_id = ("show", series_group(item)) if series else ("movie", original)
            block = blocks.setdefault(block_id, {"title": title, "year": item.get("year", ""), "original": original,
                                                 "show": bool(series), "items": []})
            block["items"].append(item)
            if str(item.get("year", "")).isdigit() and (not str(block["year"]).isdigit() or int(item["year"]) < int(block["year"])):
                block["year"] = item["year"]
        families: dict[str, list[dict]] = {}
        family_order: dict[str, int] = {}
        for block in blocks.values():
            family = franchise_family(block["title"])
            block["story_rank"] = star_wars_timeline_rank(block["title"], show=block["show"]) if updated_timeline else None
            if updated_timeline and (block["story_rank"] is not None or normalized_title(block["title"]).startswith("star wars ")):
                family = "star wars"
            families.setdefault(family, []).append(block)
            family_order[family] = min(family_order.get(family, block["original"]), block["original"])
        result = []
        family_names = sorted(families, key=family_order.get)
        if channel.get("shuffle_seed"):
            family_names.sort(key=lambda name: hashlib.sha256(f"{seed}:{name}".encode()).digest())
        for family in family_names:
            if updated_timeline and family == "star wars":
                ordered = sorted(families[family], key=lambda block: (block["story_rank"] is None,
                                 block["story_rank"] or 0, block["original"]))
            else:
                ordered = sorted(families[family], key=lambda block: franchise_block_order(family, block["title"], block["year"], block["original"]))
            for block in ordered:
                result.extend(sorted(block["items"], key=lambda value: (int(value.get("season", 0)), int(value.get("episode", 0)), str(value.get("title", "")))))
        return result
    if mode in {"shuffle_in_order", "marathon"}:
        groups = {}
        group_titles = {}
        for original, item in distinct_items():
            title = str(item.get("title") or item.get("id"))
            series = str(item.get("series") or "").strip()
            group = ("show", series_group(item)) if series else ("movie", original)
            groups.setdefault(group, []).append(item)
            group_titles.setdefault(group, series or title)
        group_names = list(groups)
        if mode == "shuffle_in_order" or channel.get("shuffle_seed"):
            group_names.sort(key=lambda group: hashlib.sha256(f"{seed}:{group_titles[group]}".encode()).hexdigest())
        ordered_groups = {
            name: sorted(groups[name], key=lambda value: (int(value.get("season", 0)), int(value.get("episode", 0)), str(value.get("title", ""))))
            for name in group_names
        }
        if mode == "shuffle_in_order":
            # Rotate between the shuffled shows, advancing each show's episodes
            # in sequence. This keeps variety without scrambling episode order or
            # repeating the same episode/show twice when another show is available.
            pending = deque(iter(ordered_groups[name]) for name in group_names)
            result = []
            while pending:
                group = pending.popleft()
                episode = next(group, None)
                if episode is not None:
                    result.append(episode)
                    pending.append(group)
            return result
        return [item for name in group_names for item in ordered_groups[name]]
    return items


def server_base(settings: dict | None = None) -> str:
    settings = settings or config()
    if settings.get("public_url"):
        return settings["public_url"]
    host = settings.get("bind_host", HOST)
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings['port']}"


def relay_address(event: str) -> str:
    try:
        value = json.loads(event)
    except (ValueError, TypeError):
        return ""
    if not isinstance(value, dict) or value.get("type") != "run.localhost.tunnel.registered":
        return ""
    address = value.get("subject", "")
    return "https://" + address if isinstance(address, str) and re.fullmatch(r"[a-z0-9-]+\.app\.lhr\.life", address) else ""


def openssh_tool(name: str) -> str | None:
    executable = name + ".exe" if sys.platform == "win32" else name
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "openssh" / executable
    return str(bundled) if getattr(sys, "frozen", False) and bundled.is_file() else shutil.which(name)


def community_relay_profile(origin: str) -> CommunityRelayProfile | None:
    return next(iter(community_relay_candidates(origin)), None)


def parse_community_relay_profile(settings: dict) -> CommunityRelayProfile:
    origin = str(settings.get("origin", "")).strip().rstrip("/")
    host = str(settings.get("ssh_host", "")).strip().lower()
    user = str(settings.get("ssh_user", "")).strip()
    ssh_port, remote_port = settings.get("ssh_port", 22), settings.get("remote_port")
    key = str(settings.get("host_key", "")).split()
    funnel_host = str(settings.get("funnel_host", "")).strip().lower()
    funnel_port = settings.get("funnel_port", 8443)
    try:
        blob = base64.b64decode(key[1], validate=True) if len(key) == 2 and key[0] == "ssh-ed25519" else b""
        address = urllib.parse.urlsplit(origin)
        valid_origin = (address.scheme == "https" and address.hostname and not (
            address.username or address.password or address.query or address.fragment
        ) and (not address.path or re.fullmatch(RELAY_SLOT_PATH, address.path)) and address.port != 0)
    except ValueError as exc:
        raise ValueError("Invalid community relay HTTPS origin or SSH host key.") from exc
    if (not valid_origin or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", host)
            or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", user)
            or type(ssh_port) is not int or not 1 <= ssh_port <= 65535
            or type(remote_port) is not int or not 1024 <= remote_port <= 65535
            or len(blob) != 51 or not blob.startswith(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20")):
        raise ValueError("Check the community relay hostname, account, assigned port, and Ed25519 host key.")
    if (funnel_host and not re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+\.ts\.net", funnel_host)
            or type(funnel_port) is not int or funnel_port not in {443, 8443, 10000}):
        raise ValueError("Provide a Tailscale Funnel hostname and a supported TLS port.")
    return CommunityRelayProfile(origin, host, user, ssh_port, remote_port, " ".join(key), funnel_host, funnel_port)


def community_relay_candidates(origin: str) -> tuple[CommunityRelayProfile, ...]:
    if not origin:
        return ()
    settings = load_json(DATA_DIR / "community-relay.json", {})
    if not isinstance(settings, dict) or str(settings.get("origin", "")).strip().rstrip("/") != origin:
        enrolled = load_json(DATA_DIR / "managed-community-relay.json", {})
        settings = enrolled.get("profile") if isinstance(enrolled, dict) else None
        if not isinstance(settings, dict) or settings.get("origin") != origin:
            return ()
        studio_id, _, _ = relay_registration(config())
        if enrolled.get("creator_id") != studio_id:
            return ()
    primary = parse_community_relay_profile(settings)
    backup = settings.get("backup")
    if not isinstance(backup, dict):
        return (primary,)
    try:
        alternate = parse_community_relay_profile(backup)
    except ValueError:
        return (primary,)
    if alternate.origin == primary.origin or (alternate.ssh_host, alternate.ssh_port, alternate.remote_port) == (
            primary.ssh_host, primary.ssh_port, primary.remote_port):
        return (primary,)
    return primary, alternate


def relay_registration(settings: dict) -> tuple[str, str, str]:
    studio_id, secret = settings.get("creator_id"), settings.get("creator_key")
    if isinstance(studio_id, str) and re.fullmatch(r"[a-f0-9-]{8,64}", studio_id) and secret:
        return studio_id, secret, f"/v1/creators/{studio_id}/relay"
    if studio_id or secret or (settings.get("directory_url") or DEFAULT_DIRECTORY_URL) != DEFAULT_DIRECTORY_URL:
        raise ValueError("This Studio needs valid directory credentials to enroll in the ChannelForge relay.")
    secret = registration_key(DEFAULT_DIRECTORY_URL)
    studio_id = hashlib.sha256(f"creator-id:{secret}".encode()).hexdigest()[:32]
    return studio_id, secret, "/v1/creators/relay"


def enrolled_relay_origin(settings: dict) -> str:
    studio_id, secret, route = relay_registration(settings)
    file = DATA_DIR / "managed-community-relay.json"
    cached = load_json(file, {})
    if (isinstance(cached, dict) and cached.get("creator_id") == studio_id
            and isinstance(cached.get("profile"), dict)):
        return parse_community_relay_profile(cached["profile"]).origin

    identity = DATA_DIR / "community-relay-identity"
    keygen = openssh_tool("ssh-keygen")
    if not keygen:
        raise ValueError("OpenSSH is needed to enroll in the ChannelForge relay.")
    options = {"stdin": subprocess.DEVNULL, "capture_output": True, "check": True, "timeout": 8,
               "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0}
    if not identity.is_file():
        subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-C", "ChannelForge Relay", "-f", str(identity)], **options)
    public = subprocess.run([keygen, "-y", "-f", str(identity)], **options).stdout.decode("ascii").split()
    result = directory_request("POST", route, {"public_key": " ".join(public[:2])}, secret)
    profile = result.get("profile")
    if not isinstance(profile, dict):
        raise ValueError("The directory did not return a relay profile.")
    verified = parse_community_relay_profile(profile)
    # A managed slot can serve viewers through the VPS's own Funnel. It
    # must still connect to Studio through its assigned, pinned SSH transport.
    origin_host = urllib.parse.urlsplit(verified.origin).hostname
    if is_legacy_relay(verified.origin) or (origin_host.endswith(".ts.net") and origin_host != verified.funnel_host):
        raise ValueError("The directory did not provide a managed VPS origin.")
    profile["origin"] = verified.origin
    atomic_json(file, {"creator_id": studio_id, "profile": profile})
    return verified.origin


def stop_relay_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def retire_orphaned_relay(identity: Path, remote_port: int) -> None:
    """Release a verified older Studio's dead forward before replacing it."""
    if remote_port in CHECKED_RELAY_PORTS:
        return
    CHECKED_RELAY_PORTS.add(remote_port)
    forward = f"127.0.0.1:{remote_port}:127.0.0.1:"
    if sys.platform == "win32":
        # Match this Studio identity, its assigned VPS port and a dead parent.
        command = r'''
$identity = $env:CHANNELFORGE_STUDIO_SSH_KEY
$forward = $env:CHANNELFORGE_STUDIO_SSH_FORWARD
$re = '(?:^|\s)-R\s+"?' + [regex]::Escape($forward) + '(\d+)(?:"|\s|$)'
foreach ($p in (Get-CimInstance Win32_Process -Filter "Name = 'ssh.exe'")) {
    if (!$p.CommandLine -or !$p.CommandLine.Contains($identity)) { continue }
    if ($p.CommandLine -notmatch $re) { continue }
    $port = [int]$Matches[1]
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($p.ParentProcessId)"
    if ($parent -and $parent.CreationDate -le $p.CreationDate) { continue }
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { continue }
    $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($p.ProcessId)"
    if ($current -and $current.CreationDate -eq $p.CreationDate -and $current.CommandLine -eq $p.CommandLine) {
        Stop-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
    }
}
'''
        try:
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4,
                           creationflags=subprocess.CREATE_NO_WINDOW,
                           env={**os.environ, "CHANNELFORGE_STUDIO_SSH_KEY": str(identity),
                                "CHANNELFORGE_STUDIO_SSH_FORWARD": forward})
        except (OSError, subprocess.TimeoutExpired):
            pass
    elif sys.platform.startswith("linux"):
        import signal
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
                if stat[1] != "1" or proc.stat().st_uid != os.getuid():
                    continue
                args = (proc / "cmdline").read_bytes().decode("utf-8", "replace").rstrip("\0").split("\0")
                if Path(args[0]).name != "ssh" or "-i" not in args or args[args.index("-i") + 1] != str(identity) or "-R" not in args:
                    continue
                target = args[args.index("-R") + 1]
                if not target.startswith(forward):
                    continue
                port = int(target[len(forward):])
                with socket.socket() as connection:
                    connection.settimeout(0.1)
                    if connection.connect_ex((HOST, port)) == 0:
                        continue
                if (proc / "stat").read_text().rsplit(")", 1)[1].split()[19] == stat[19]:
                    os.kill(int(proc.name), signal.SIGTERM)
            except (OSError, IndexError, ValueError):
                continue


def set_community_relay(connection: CommunityRelayConnection | None) -> None:
    global COMMUNITY_RELAY
    with TUNNEL_LOCK:
        previous = COMMUNITY_RELAY
        if previous is connection:
            return
        COMMUNITY_RELAY = connection
        if previous is not None:
            stop_relay_process(previous.process)
        if connection is not None:
            def reconnect_after_exit():
                connection.process.wait()
                if COMMUNITY_RELAY is connection:
                    PUBLISH_CHANGED.set()

            threading.Thread(target=reconnect_after_exit, daemon=True, name="channel-forge-community-relay").start()


def start_relay_tls_bridge(host: str, port: int) -> socket.socket:
    """Give bundled OpenSSH one loopback connection to the relay's TLS ingress."""
    listener = socket.socket()
    try:
        listener.bind((HOST, 0))
        listener.listen(1)
        listener.settimeout(10)
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    except BaseException:
        listener.close()
        raise

    def forward() -> None:
        try:
            with listener:
                local, _ = listener.accept()
                with local, socket.create_connection((host, port), timeout=8) as remote:
                    with context.wrap_socket(remote, server_hostname=host) as encrypted:
                        relay_tls_bytes(local, encrypted)
        except (OSError, ValueError):
            # OpenSSH sees EOF and Studio tries its next verified route.
            return

    threading.Thread(target=forward, name="channel-forge-relay-tls", daemon=True).start()
    return listener


def relay_tls_bytes(local: socket.socket, encrypted: ssl.SSLSocket) -> None:
    """Pump both directions on one thread and bound the queued bytes."""
    sockets = (local, encrypted)
    for connection in sockets:
        connection.setblocking(False)
    pending = {connection: bytearray() for connection in sockets}
    other = {local: encrypted, encrypted: local}
    limit = 128 * 1024
    eof = False
    while not SHUTTING_DOWN.is_set():
        readers = [] if eof else [connection for connection in sockets if len(pending[other[connection]]) < limit]
        writers = [connection for connection in sockets if pending[connection]]
        if eof and not writers:
            return
        buffered_tls = encrypted in readers and encrypted.pending()
        readable, writable, _ = select.select(readers, writers, [], 0 if buffered_tls else 1)
        if buffered_tls and encrypted not in readable:
            readable.append(encrypted)
        for destination in writable:
            try:
                size = destination.send(pending[destination])
            except (BlockingIOError, ssl.SSLWantReadError, ssl.SSLWantWriteError):
                continue
            if not size:
                return
            del pending[destination][:size]
        for source in readable:
            buffer = pending[other[source]]
            try:
                data = source.recv(min(32 * 1024, limit - len(buffer)))
            except (BlockingIOError, ssl.SSLWantReadError, ssl.SSLWantWriteError):
                continue
            if not data:
                eof = True
                break
            buffer.extend(data)


def connect_community_relay(profile: CommunityRelayProfile) -> CommunityRelayConnection:
    """Start a candidate forward and prove it reaches this Studio over HTTPS."""
    global RELAY_JOB
    with TUNNEL_LOCK:
        if SHUTTING_DOWN.is_set() or PUBLIC_MEDIA_SERVER is None:
            raise InterruptedError("The read-only media server is unavailable.")
        local_port = PUBLIC_MEDIA_SERVER.server_port
        existing = COMMUNITY_RELAY
        if (existing and existing.profile == profile and existing.local_port == local_port
                and existing.process.poll() is None):
            return existing
        ssh = openssh_tool("ssh")
        identity = DATA_DIR / "community-relay-identity"
        if not ssh or not identity.is_file():
            raise ValueError("Install OpenSSH and provision this Studio's community relay key.")
        retire_orphaned_relay(identity, profile.remote_port)
        host = profile.ssh_host if profile.ssh_port == 22 else f"[{profile.ssh_host}]:{profile.ssh_port}"
        trust = f"{host} {profile.host_key}\n"
        known_hosts = DATA_DIR / f"community-host-{hashlib.sha256(trust.encode()).hexdigest()[:24]}"
        known_hosts.write_text(trust, encoding="ascii")
        null = "NUL" if sys.platform == "win32" else "/dev/null"
        arguments = [
            ssh, "-F", null, "-N", "-T", "-a", "-x", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
            "-i", str(identity), "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
            "-o", "StrictHostKeyChecking=yes", "-o", "HostKeyAlgorithms=ssh-ed25519",
            "-o", f'UserKnownHostsFile="{known_hosts.as_posix()}"', "-o", f"GlobalKnownHostsFile={null}",
            "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
            "-l", profile.ssh_user,
            "-R", f"127.0.0.1:{profile.remote_port}:127.0.0.1:{local_port}",
        ]
        for through_funnel in (False, True) if profile.funnel_host else (False,):
            if SHUTTING_DOWN.is_set():
                break
            bridge = start_relay_tls_bridge(profile.funnel_host, profile.funnel_port) if through_funnel else None
            target = ["-p", str(bridge.getsockname()[1]), "-o", f"HostKeyAlias={host}", HOST] if bridge else [
                "-p", str(profile.ssh_port), profile.ssh_host]
            process = None
            accepted = False
            try:
                command = [*arguments, *target]
                if sys.platform.startswith("linux"):
                    child = Path(__file__).with_name("relay_child.py")
                    command = [sys.executable, str(child), str(os.getpid()), *command]
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                if sys.platform == "win32":
                    if RELAY_JOB is None:
                        RELAY_JOB = relay_process_job()
                    RELAY_JOB(process)
                deadline = time.monotonic() + 16
                while not SHUTTING_DOWN.is_set() and process.poll() is None and time.monotonic() < deadline:
                    if relay_reaches_this_creator(profile.origin):
                        if profile.funnel_host and not relay_transfers_media(profile.origin, hd=True):
                            break
                        if process.poll() is None and not SHUTTING_DOWN.is_set():
                            accepted = True
                            return CommunityRelayConnection(profile, local_port, process)
                    SHUTTING_DOWN.wait(.5)
            finally:
                if not accepted:
                    if process is not None:
                        stop_relay_process(process)
                    if bridge:
                        bridge.close()
        raise ValueError("The community relay did not reach this Studio. Check its SSH key, assigned port, and HTTPS route.")


def accept_community_relay(candidates: tuple[CommunityRelayProfile, ...], preferred: str,
                           studio: dict, *, unavailable: str = "") -> CommunityRelayConnection:
    """Keep serving through the current route until a verified candidate is registered."""
    global REGISTERED_ORIGIN
    failure = None
    for candidate in sorted(candidates, key=lambda relay: relay.origin != preferred):
        if candidate.origin == unavailable:
            continue
        try:
            connection = connect_community_relay(candidate)
        except (OSError, ValueError) as exc:
            if SHUTTING_DOWN.is_set():
                raise
            failure = exc
            continue
        if not candidate.funnel_host and connection is not COMMUNITY_RELAY and not relay_transfers_media(candidate.origin, hd=True):
            stop_relay_process(connection.process)
            failure = ValueError("The community relay could not deliver a 720p test segment in time.")
            continue
        if candidate.origin != REGISTERED_ORIGIN:
            try:
                publish_creator(studio, origin=candidate.origin, community_relay_origin=candidates[0].origin)
            except Exception:
                if connection is not COMMUNITY_RELAY:
                    stop_relay_process(connection.process)
                raise
            REGISTERED_ORIGIN = candidate.origin
            set_community_relay(connection)
            stop_legacy_relay()
            generate_exports()
        else:
            set_community_relay(connection)
        return connection
    raise ValueError(f"No configured community relay passed validation. {failure or ''}".strip()) from failure


def relay_key_path() -> Path:
    identity = DATA_DIR / "relay-identity"
    if not identity.is_file():
        keygen = openssh_tool("ssh-keygen")
        if not keygen:
            raise ValueError("ChannelForge could not find the OpenSSH key generator. Install the current ChannelForge package.")
        subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-C", "ChannelForge Media Relay", "-f", str(identity)],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=8, check=True,
                       creationflags=0x08000000 if sys.platform == "win32" else 0)
    return identity


def enrolled_relay_probe(origin: str, kind: str, nonce: str) -> dict | None:
    enrolled = load_json(DATA_DIR / "managed-community-relay.json", {})
    profile = enrolled.get("profile") if isinstance(enrolled, dict) else None
    if not isinstance(profile, dict) or profile.get("origin") != origin:
        return None
    settings = config()
    try:
        studio_id, secret, route = relay_registration(settings)
        if studio_id != enrolled.get("creator_id"):
            return None
        return directory_request("POST", route, {"kind": kind, "nonce": nonce}, secret, timeout=5)
    except ValueError:
        return {}


def relay_reaches_this_creator(origin: str) -> bool:
    nonce = secrets.token_hex(6)
    probe = enrolled_relay_probe(origin, "identity", nonce)
    if probe is not None:
        return probe.get("instance") == RELAY_INSTANCE
    request = urllib.request.Request(f"{origin}/api/public/origin?check={nonce}",
                                     headers={"Accept": "application/json", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            return response.geturl() == request.full_url and json.loads(response.read(512)).get("instance") == RELAY_INSTANCE
    except (OSError, ValueError, AttributeError):
        return False


def relay_transfers_media(origin: str, *, hd: bool = False) -> bool:
    """Test actual byte delivery without starting an encoder or exposing library content."""
    challenge = secrets.token_urlsafe(24)
    request = urllib.request.Request(f"{origin}/api/public/origin?sample={challenge}",
                                     headers={"Accept": "application/octet-stream", "Cache-Control": "no-cache"})
    # A full sample represents at least one second at the live HD advertised peak (4 Mbps).
    size, timeout = (len(RELAY_MEDIA_SAMPLE), 1.0) if hd else (128 * 1024, 2.0)
    deadline = time.monotonic() + timeout
    with LOCK:
        RELAY_MEDIA_CHALLENGES[challenge] = size
    try:
        probe = enrolled_relay_probe(origin, "sample", challenge)
        if probe is not None:
            return probe.get("sha256") == hashlib.sha256(RELAY_MEDIA_SAMPLE[:size]).hexdigest()
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if (response.geturl() != request.full_url or response.headers.get_content_type() != "application/octet-stream"
                    or response.headers.get("Content-Length") != str(size)):
                return False
            body = bytearray()
            while len(body) < size and time.monotonic() < deadline:
                chunk = response.read1(min(16384, size - len(body)))
                if not chunk:
                    return False
                body.extend(chunk)
            return time.monotonic() <= deadline and body == RELAY_MEDIA_SAMPLE[:size]
    except (OSError, ValueError, AttributeError):
        return False
    finally:
        with LOCK:
            RELAY_MEDIA_CHALLENGES.pop(challenge, None)


def is_funnel_origin(origin: str) -> bool:
    return bool(re.fullmatch(r"https://[a-z0-9.-]+\.ts\.net(?::(?:443|8443|10000))?", origin, re.I))


def is_legacy_relay(origin: str) -> bool:
    host = urllib.parse.urlsplit(origin).hostname or ""
    return host == "localhost.run" or host == "lhr.life" or host.endswith((".localhost.run", ".lhr.life"))


def studio_publication_origin(requested: str, saved: str) -> str:
    if requested and not is_legacy_relay(requested):
        return requested
    relay = load_json(DATA_DIR / "community-relay.json", {})
    if isinstance(relay, dict) and relay:
        profile = parse_community_relay_profile(relay)
        if is_legacy_relay(profile.origin):
            raise ValueError("The community relay profile needs a current HTTPS relay address.")
        return profile.origin
    if saved and not is_legacy_relay(saved):
        return saved
    raise ValueError("Studio does not have a verified ChannelForge relay yet.")


def constrained_public_relay(settings=None):
    if settings is None:
        settings = config()
    host = urllib.parse.urlsplit(settings.get("public_url", "")).hostname or ""
    return host.endswith(".app.lhr.life") or (settings["public_tunnel"] and not host.endswith(".ts.net"))


def start_public_tunnel() -> str:
    global TUNNEL_URL
    with TUNNEL_LOCK:
        if SHUTTING_DOWN.is_set():
            raise InterruptedError("Creator is shutting down.")
        if TUNNEL_URL and PUBLIC_MEDIA_SERVER is not None and PUBLIC_MEDIA_SERVER.server_port == PUBLIC_MEDIA_PORT:
            return TUNNEL_URL
        origin = config()["public_url"]
        if not is_funnel_origin(origin):
            raise ValueError("Studio does not have a saved Tailscale media origin.")
        with stage_media_funnel(origin):
            pass
        TUNNEL_URL = origin
        return origin


def stop_public_tunnel() -> None:
    global TUNNEL_URL
    with TUNNEL_LOCK:
        TUNNEL_URL = ""


def stop_legacy_relay() -> None:
    global LEGACY_RELAY_PROCESS, LEGACY_RELAY_ORIGIN
    with TUNNEL_LOCK:
        if LEGACY_RELAY_PROCESS is not None:
            stop_relay_process(LEGACY_RELAY_PROCESS)
        LEGACY_RELAY_PROCESS = None
        LEGACY_RELAY_ORIGIN = ""


def fallback_studio_relay(profile: dict) -> None:
    """Publish the legacy route only after the same Studio answers through it."""
    global LEGACY_RELAY_PROCESS, LEGACY_RELAY_ORIGIN, REGISTERED_ORIGIN
    if not profile or SHUTTING_DOWN.is_set():
        return
    with TUNNEL_LOCK:
        if (LEGACY_RELAY_PROCESS is None or LEGACY_RELAY_PROCESS.poll() is not None
                or not relay_reaches_this_creator(LEGACY_RELAY_ORIGIN)):
            stop_legacy_relay()
            start_public_media_server(automatic=True)
            ssh = openssh_tool("ssh")
            if not ssh or PUBLIC_MEDIA_SERVER is None:
                raise ValueError("OpenSSH is needed for the legacy fallback.")
            known_hosts = DATA_DIR / "relay-known-hosts"
            known_hosts.write_text(LEGACY_RELAY_HOST_KEY + "\n", encoding="ascii")
            identity = relay_key_path()
            null = "NUL" if sys.platform == "win32" else "/dev/null"
            process = subprocess.Popen([
                ssh, "-F", null, "-T", "-a", "-x", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                "-i", str(identity), "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
                "-o", "StrictHostKeyChecking=yes", "-o", "HostKeyAlgorithms=ssh-ed25519",
                "-o", f'UserKnownHostsFile="{known_hosts.as_posix()}"', "-o", f"GlobalKnownHostsFile={null}",
                "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                "-R", f"80:{HOST}:{PUBLIC_MEDIA_SERVER.server_port}", "channelforge@localhost.run", "--", "--output", "json",
            ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            candidates = queue.Queue(maxsize=4)

            def read_output():
                with process.stdout as output:
                    for line in output:
                        origin = relay_address(line)
                        if origin:
                            try:
                                candidates.put_nowait(origin)
                            except queue.Full:
                                pass

            threading.Thread(target=read_output, name="channel-forge-legacy-relay", daemon=True).start()
            accepted = False
            try:
                deadline = time.monotonic() + 15
                while process.poll() is None and not SHUTTING_DOWN.is_set() and time.monotonic() < deadline:
                    try:
                        origin = candidates.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if (relay_reaches_this_creator(origin) and relay_transfers_media(origin)
                            and process.poll() is None and not SHUTTING_DOWN.is_set()):
                        LEGACY_RELAY_PROCESS, LEGACY_RELAY_ORIGIN = process, origin
                        accepted = True
                        break
            finally:
                if not accepted:
                    stop_relay_process(process)
            if not accepted:
                raise ValueError("The localhost.run fallback could not deliver this Studio’s test data.")
        if REGISTERED_ORIGIN != LEGACY_RELAY_ORIGIN:
            try:
                publish_creator(profile, origin=LEGACY_RELAY_ORIGIN, preserve_pending=True)
            except (OSError, ValueError):
                stop_legacy_relay()
                raise
            REGISTERED_ORIGIN = LEGACY_RELAY_ORIGIN
            generate_exports()


def ffmpeg_path() -> str:
    executable = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    roots = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)),
        Path(sys.executable).resolve().parent,
        Path(__file__).resolve().parent,
    ]
    found = next((str(root / executable) for root in roots if (root / executable).is_file()), None) or shutil.which("ffmpeg")
    if not found or not Path(found).is_file():
        raise ValueError("FFmpeg is required for standalone playback but was not found. Reinstall ChannelForge using the latest package.")
    return found


def ffprobe_path() -> str:
    ffmpeg = Path(ffmpeg_path())
    probe_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    bundled = ffmpeg.with_name(probe_name)
    found = str(bundled) if bundled.is_file() else shutil.which(probe_name)
    if not found or not Path(found).is_file():
        raise ValueError("FFprobe is required to inspect audio tracks but was not found.")
    return found


@lru_cache(maxsize=4)
def ffmpeg_supports_read_burst(executable: str) -> bool:
    try:
        help_text = subprocess.check_output([executable, "-hide_banner", "-h", "full"], timeout=4,
                                            stderr=subprocess.STDOUT,
                                            creationflags=0x08000000 if sys.platform == "win32" else 0)
    except (OSError, subprocess.SubprocessError):
        return False
    return b"-readrate_initial_burst" in help_text


@lru_cache(maxsize=4)
def probed_hardware_encoder(executable: str) -> str:
    """An encoder listed by an FFmpeg build may still have no usable device."""
    for name in ("h264_nvenc", "h264_qsv", "h264_amf"):
        try:
            result = subprocess.run([executable, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                                     "color=size=320x180:rate=25", "-frames:v", "2", "-c:v", name,
                                     "-b:v", "256k", "-bf", "0", "-f", "null", "-"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
                                    creationflags=0x08000000 if sys.platform == "win32" else 0)
            if result.returncode == 0:
                return name
        except (OSError, subprocess.SubprocessError):
            continue
    return ""


@lru_cache(maxsize=128)
def copyable_local_video(path, modified_ns, size):
    """Check pixel format, H.264 limits and initial sync-sample cadence before remuxing VOD."""
    try:
        probe = subprocess.run([ffprobe_path(), "-v", "error", "-select_streams", "v:0", "-read_intervals",
                                "%+#180", "-show_entries", "stream=codec_name,field_order,profile,level,width,height,pix_fmt:packet=flags,pts_time",
                                "-of", "json", path], capture_output=True, text=True, timeout=4, check=True,
                               creationflags=0x08000000 if sys.platform == "win32" else 0)
        data = json.loads(probe.stdout)
        stream = data["streams"][0]
        sync = [float(pkt["pts_time"]) for pkt in data["packets"] if "K" in pkt.get("flags", "")]
        return (stream.get("codec_name") == "h264" and stream.get("pix_fmt") == "yuv420p" and
                stream.get("profile") in ("Main", "High", "Baseline", "Constrained Baseline") and
                0 < stream["level"] <= 41 and stream.get("field_order") == "progressive" and
                stream["width"] <= 1920 and stream["height"] <= 1080 and
                len(sync) >= 2 and abs(sync[0]) <= .15 and
                all(0 < b - a <= 2.1 for a, b in zip(sync, sync[1:])))
    except (OSError, ValueError, IndexError, KeyError, TypeError, subprocess.SubprocessError):
        return False


def playback_audio_track(item: dict, media_input: str, input_options=(), *, preflight=False) -> tuple[int, bool | None]:
    """Resolve the preferred audio track and whether the source has audio."""
    recorded_language = str(item.get("audio_language", "")).casefold()
    recorded_index = max(0, int(item.get("audio_stream_index", 0) or 0))
    if not preflight and (recorded_language in {"en", "eng"} or recorded_language.startswith("english")):
        return recorded_index, True
    identity = hashlib.sha256(f'{item.get("source")}:{item.get("id")}:{media_input}'.encode()).hexdigest()
    with LOCK:
        cached = AUDIO_SELECTION_CACHE.get(identity)
    if cached is not None:
        return cached
    try:
        completed = subprocess.run(
            [ffprobe_path(), "-v", "error", "-select_streams", "a", "-show_entries", "stream=index:stream_tags=language:stream_disposition=default", "-of", "json", *input_options, media_input],
            capture_output=True, text=True, timeout=6, creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        if completed.returncode != 0:
            return recorded_index, True if item.get("audio_stream_id") else None
        streams = json.loads(completed.stdout or "{}").get("streams", [])
        choice = next((index for index, stream in enumerate(streams) if str(stream.get("tags", {}).get("language", "")).casefold() in {"en", "eng"}), None)
        if choice is None:
            choice = next((index for index, stream in enumerate(streams) if int(stream.get("disposition", {}).get("default", 0) or 0) == 1), None)
        recorded_index = int(choice if choice is not None else recorded_index)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return recorded_index, True if item.get("audio_stream_id") else None
    selection = (recorded_index, bool(streams))
    with LOCK:
        if len(AUDIO_SELECTION_CACHE) >= 1024:
            AUDIO_SELECTION_CACHE.pop(next(iter(AUDIO_SELECTION_CACHE)))
        AUDIO_SELECTION_CACHE[identity] = selection
    return selection


def ffmpeg_status() -> dict:
    try:
        path = Path(ffmpeg_path()).resolve()
        bundled = path.parent in {
            Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve(),
            Path(sys.executable).resolve().parent,
        }
        return {"available": True, "source": "Bundled encoder" if bundled else "System FFmpeg"}
    except ValueError as exc:
        return {"available": False, "source": str(exc)}


def sanitized_playback_error(error: object) -> str:
    message = str(error).strip() or "Playback could not start."
    message = re.sub(r"(?i)(X-Plex-Token(?:=|%3D))[^&\s\"']+", r"\1REDACTED", message)
    token = str(config().get("plex_token", ""))
    if token:
        message = message.replace(token, "REDACTED")
    for server in dict(PLEX_RESOURCE_CACHE.get("servers", {})).values():
        if server.get("token"):
            message = message.replace(str(server["token"]), "REDACTED")
    return message[:800]


def record_broadcast_event(channel_id, event, detail):
    with LOCK:
        BROADCAST_EVENTS.append({"time": datetime.now(timezone.utc).isoformat(),
                                 "channel": channel_id, "event": event, "detail": detail})


def broadcast_failure_reason(error):
    message = str(error).casefold()
    if isinstance(error, EncoderCapacityError):
        return "resource_limit"
    if "missing or unsupported" in message or "outside the configured" in message:
        return "local_source_missing"
    if any(marker in message for marker in ("invalid data found", "does not contain any stream",
                                             "matches no streams", "before its scheduled boundary")):
        return "invalid_program"
    if "404" in message or "did not provide a playable media part" in message:
        return "media_part_missing"
    if "plex" in message or "http" in message:
        return "plex_source_error"
    if "segments" in message or "playlist" in message:
        return "playlist_stalled"
    return "encoder_error"


def public_playback_message(reason):
    return {"resource_limit": "This studio is busy. Try again shortly.",
            "local_source_missing": "The scheduled file is unavailable at the studio.",
            "media_part_missing": "The studio's media server could not find this program.",
            "invalid_program": "The scheduled program could not be decoded.",
            "plex_source_error": "The studio lost its media server connection.",
            "playlist_stalled": "The studio stopped receiving new video segments."}.get(
                reason, "The studio could not start the video encoder.")


def broadcast_health(channel_id=None):
    with LOCK:
        feeds = [feed for key, feed in LIVE_PLAYOUT.items() if channel_id is None or key[0] == channel_id]
        if any(feed.capacity_error for feed in feeds):
            return "overloaded"
        if any(feed.error or feed.recovering for feed in feeds):
            return "reconnecting"
        if any(feed.active() and feed.segments for feed in feeds) or any(
                session["process"].poll() is None and (channel_id is None or session.get("channel_id") == channel_id)
                for session in FFMPEG_SESSIONS.values()):
            return "broadcasting"
        if any(feed.active() and not feed.finished for feed in feeds) or (channel_id is None and ENCODER_STARTING):
            return "starting"
    return "reconnecting" if channel_id and playback_error(channel_id) else "standby"


def encoder_usage(process=None):
    """Sample Studio or a running child without another monitoring daemon."""
    pid = os.getpid() if process is None else process.pid
    if process is not None and process.poll() is not None:
        return None, None
    try:
        if sys.platform.startswith("linux"):
            # The executable name in parentheses can contain spaces or ')'.
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return ((int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
                    int(fields[21]) * os.sysconf("SC_PAGE_SIZE"))
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
                            ("peak_rss", ctypes.c_size_t), ("rss", ctypes.c_size_t),
                            ("additional", ctypes.c_size_t * 6)]
            handle = wintypes.HANDLE(-1 if process is None else int(process._handle))
            times = [ctypes.c_ulonglong() for _ in range(4)]
            memory = Counters()
            memory.cb = ctypes.sizeof(memory)
            timed = ctypes.windll.kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times))
            sized = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb)
            return ((times[2].value + times[3].value) / 10**7 if timed else None, memory.rss if sized else None)
    except (AttributeError, OSError, ValueError, IndexError):
        pass
    return None, None


@lru_cache(maxsize=1)
def gpu_load(sample_bucket):
    utility = shutil.which("nvidia-smi")
    if utility:
        try:
            result = subprocess.run([utility, "--query-gpu=utilization.gpu,utilization.encoder",
                                     "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                    check=True, timeout=1.5, creationflags=0x08000000 if sys.platform == "win32" else 0)
            return [dict(gpu_percent=int(a), encoder_percent=int(b)) for a, b in
                    re.findall(r"(?m)^(\d+),\s*(\d+)\s*$", result.stdout)] or None
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return None


def creator_broadcast_diagnostics():
    with LOCK:
        sessions = list(FFMPEG_SESSIONS.values())
        report = {"state": broadcast_health(), "starting": len(ENCODER_STARTING), "encoder_limit": MAX_ENCODERS,
                  "cache_limit_bytes": STREAM_CACHE_LIMIT, "events": list(BROADCAST_EVENTS), "encoders": []}
    for s in sessions:
        cpu, rss = encoder_usage(s["process"])
        now = time.monotonic()
        with LOCK:
            old = s.get("usage_sample")
            if cpu is not None and (not old or now - old[0] >= .5):
                if old:
                    s["cpu_percent"] = round(max(0, cpu - old[1]) / (now - old[0]) * 100, 1)
                s["usage_sample"] = (now, cpu)
        report["encoders"].append({"channel": s["channel_id"], "program": s.get("program", ""),
                                   "active": s["process"].poll() is None, "pid": s["process"].pid,
                                   "video_encoder": s.get("video_encoder"), "audio_codec": s.get("audio_codec"),
                                   "profile": s.get("profile"), "cache_bytes": s.get("cache_bytes"),
                                   "budget_bytes": s.get("budget"), "cpu_seconds": cpu, "cpu_percent": s.get("cpu_percent"),
                                   "rss_bytes": rss, **s.get("progress", {})})
    report["gpu"] = gpu_load(int(time.monotonic() // 5)) if sessions else None
    return report


def channel_activity(include_program=False):
    with LOCK:
        feeds = list(LIVE_PLAYOUT.values())
        encoder_counts = {}
    activity = {}
    for feed in feeds:
        detail = feed.activity()
        if include_program:
            with feed.condition:
                detail["program"] = (feed.source or {}).get("title", "")
        activity[feed.channel_id] = detail
    return activity


def studio_process_usage():
    """Return Studio and managed child usage as percentages of machine capacity."""
    if sys.platform == "win32":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("size", ctypes.c_uint32), ("load", ctypes.c_uint32), ("values", ctypes.c_uint64 * 7)]

        memory = MemoryStatus()
        memory.size = ctypes.sizeof(memory)
        ram_total = memory.values[0] if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)) else 0
    elif sys.platform.startswith("linux"):
        total = re.search(r"(?m)^MemTotal:\s+(\d+) kB$", Path("/proc/meminfo").read_text(encoding="ascii"))
        ram_total = int(total[1]) * 1024 if total else 0
    else:
        return None, None

    with LOCK:
        managed = {session["process"].pid: session["process"] for session in FFMPEG_SESSIONS.values()}
    community = COMMUNITY_RELAY
    for process in (community.process if community else None, LEGACY_RELAY_PROCESS):
        if process is not None and process.poll() is None:
            managed[process.pid] = process
    processes = [None, *managed.values()]
    start = time.monotonic()
    before = [encoder_usage(process)[0] for process in processes]
    time.sleep(0.12)
    after = [encoder_usage(process) for process in processes]
    seconds = sum(max(0, current - prior) for prior, (current, _) in zip(before, after)
                  if prior is not None and current is not None)
    capacity = (time.monotonic() - start) * (os.cpu_count() or 1)
    cpu = 100 * seconds / capacity if capacity > 0 and before[0] is not None and after[0][0] is not None else None
    ram = 100 * sum(resident or 0 for _, resident in after) / ram_total if ram_total and after[0][1] is not None else None
    return tuple(round(min(100, number), 2) if number is not None else None for number in (cpu, ram))


def record_movie_viewer(session, viewer, now):
    # Call under LOCK. A playback ID is temporary and stays inside Studio.
    viewers = session.setdefault("movie_viewers", {})
    viewers.pop(viewer, None)
    if len(viewers) >= 2048:
        viewers.pop(next(iter(viewers)))
    viewers[viewer] = now


def movie_activity():
    now = time.monotonic()
    movies = {}
    with LOCK:
        for session in FFMPEG_SESSIONS.values():
            if not session.get("on_demand"):
                continue
            status = session["process"].poll()
            if status not in (None, 0):
                continue
            viewers = {viewer for viewer, seen in session.get("movie_viewers", {}).items() if now - seen < MOVIE_VIEWER_TTL}
            if not viewers and now - session.get("movie_media_at", float("-inf")) >= MOVIE_VIEWER_TTL:
                continue
            row = movies.setdefault(session["channel_id"], {"title": session.get("program") or "Untitled movie",
                                                           "viewers": set(), "encoder_count": 0})
            row["viewers"].update(viewers)
            row["encoder_count"] += status is None
    return sorted(({**row, "viewers": len(row["viewers"])} for row in movies.values()),
                  key=lambda row: (-row["viewers"], row["title"].casefold()))


def studio_system_activity(include_playback=False):
    try:
        cpu, ram = studio_process_usage()
    except (OSError, ValueError, IndexError):
        cpu = ram = None
    broadcasts = channel_activity(include_program=include_playback)
    now = time.monotonic()
    with LOCK:
        active_encoders = sum(session["process"].poll() is None for session in FFMPEG_SESSIONS.values())
        starting_encoders = len(ENCODER_STARTING)
    with STUDIO_TRAFFIC_LOCK:
        bandwidth = sum(size for second, size in STUDIO_TRAFFIC.items() if int(now) - second < 10)
    result = {"sharing": SHARING_STATUS, "registered": bool(REGISTERED_ORIGIN),
            "uptime_seconds": int(now - STUDIO_START), "cpu_percent": cpu, "ram_percent": ram,
            "live_channels": sum(row["state"] == "live" for row in broadcasts.values()),
            "viewer_sessions": sum(row["viewers"] for row in broadcasts.values()),
            "encoder_count": active_encoders, "encoder_starting": starting_encoders,
            "video_bps": bandwidth * 8 // 10, "heartbeat": HEARTBEAT_STATUS.copy()}
    if include_playback:
        result.update(broadcasts=broadcasts, movies=movie_activity())
    return result


def plex_part_key(item: dict) -> str:
    key = str(item.get("part_key", ""))
    if key.startswith("/library/parts/"):
        return key
    root = plex_server_request(str(item.get("key") or f'/library/metadata/{item["id"]}'), str(item.get("plex_server_id", "")))
    part = root.find(".//Part")
    key = part.get("key", "") if part is not None else ""
    if not key.startswith("/library/parts/"):
        raise ValueError("Plex did not provide a playable media part for this title.")
    return key


def local_media_path(item: dict) -> Path:
    candidate = Path(str(item.get("file_path", ""))).resolve()
    if item.get("source") != "local" or not candidate.is_file() or candidate.suffix.lower() not in MEDIA_EXTENSIONS:
        raise ValueError("The local media file is missing or unsupported.")
    for library in local_libraries():
        try:
            candidate.relative_to(Path(library["path"]).resolve())
            return candidate
        except ValueError:
            continue
    raise ValueError("That media file is outside the configured local libraries.")


def start_ffmpeg_session(channel: dict, *, public_delivery: bool = False, constrained_live: bool = False, position=None) -> str:
    """Share the startup and encoder among viewers tuning the same program."""
    channel_id = str(channel["id"])
    lock_index = zlib.crc32(channel_id.encode()) % len(ENCODER_START_LOCKS)
    with ENCODER_START_LOCKS[lock_index]:
        if SHUTTING_DOWN.is_set():
            raise InterruptedError("The studio is shutting down.")
        return start_or_reuse_encoder(channel, public_delivery=public_delivery, constrained_live=constrained_live, position=position)


def square_pixel_scale(width: int, height: int) -> str:
    # Size to the displayed picture first, then clear the source pixel aspect ratio.
    display_width = "(iw*if(gt(sar,0),sar,1))"
    factor = f"min(1,min({width}/{display_width},{height}/ih))"
    return f"scale=w='max(2,trunc({display_width}*{factor}/2)*2)':h='max(2,trunc(ih*{factor}/2)*2)',setsar=1"


def relay_renditions(constrained=False, *, on_demand=False, limits=BROADCAST_DEFAULT):
    # name, max dimensions, video target/peak (kbit/s), threads, advertised peak including audio and TS.
    economy = ("economy", 480, 270, 104, 124, 1, 224000) if constrained else ("economy", 480, 270, 128, 165, 1, 270000)
    balanced = ("balanced", 854, 480, 430, 540, 1, 690000)
    hd = ("hd", 1280, 720, 1150, 1450, 2, 1750000)
    if on_demand:
        return economy, balanced, hd
    max_height, max_kbps = limits
    rows = [row for row in (economy, balanced, hd) if row[2] <= max_height]
    result = []
    for index, (name, width, height, target, peak, threads, bandwidth) in enumerate(rows):
        ceiling = min(max_kbps, bandwidth // 1000) if constrained or index < len(rows) - 1 else max_kbps
        # Reserve space for AAC, MPEG-TS overhead, and variation within a segment.
        if ceiling * 1000 != bandwidth:
            peak = max(95, int(ceiling * 0.90) - 64)
            target = int(peak * 0.91)
        if result and ceiling * 1000 <= result[-1][-1]:
            result.pop()
            name = "economy" if not result else name
        result.append((name, width, height, target, peak, threads, ceiling * 1000))
    return tuple(result)


def limit_hls_master(manifest: bytes, max_bandwidth: int) -> bytes:
    if not max_bandwidth:
        return manifest
    pattern = rb"(?m)^#EXT-X-STREAM-INF:(?:[^\r\n]*?,)?BANDWIDTH=(\d+)[^\r\n]*\r?\n[^#\r\n][^\r\n]*\r?\n?"
    choices = list(re.finditer(pattern, manifest))
    if not choices:
        return manifest
    max_size = {400_000: (480, 270), 1_000_000: (854, 480)}.get(max_bandwidth)
    rendition_sizes = {b"economy": (480, 270), b"balanced": (854, 480), b"hd": (1280, 720)}
    eligible = []
    for choice in choices:
        if int(choice[1]) > max_bandwidth:
            continue
        attributes, uri = choice[0].splitlines()
        resolution = re.search(rb"\bRESOLUTION=(\d+)x(\d+)", attributes)
        if resolution:
            size = int(resolution[1]), int(resolution[2])
        else:
            name = re.search(rb"(?:^|[/_])(economy|balanced|hd)\.m3u8(?:\?|$)", uri)
            size = rendition_sizes.get(name[1]) if name else None
        if not max_size or not size or size[0] <= max_size[0] and size[1] <= max_size[1]:
            eligible.append(choice)
    keep = {choice.start() for choice in (eligible or [min(choices, key=lambda choice: int(choice[1]))])}
    return re.sub(pattern, lambda choice: choice[0] if choice.start() in keep else b"", manifest)


def requested_playback_bandwidth(query: str) -> int:
    cap = urllib.parse.parse_qs(query).get("max_kbps", [""])[0]
    return int(cap) * 1000 if cap in {"400", "1000"} else 0


def adaptive_relay_options(directory: Path, audio_index: int, has_audio: bool, video_limit_kbps: int | None = None,
                           *, constrained=False, on_demand=False, limits=BROADCAST_DEFAULT, audio_input=0) -> tuple[list[str], list[str], dict[str, int]]:
    renditions = relay_renditions(constrained, on_demand=on_demand, limits=limits)
    inputs = "".join(f"[{name}_in]" for name, *_ in renditions)
    filters = [f"[0:v:0]{DEINTERLACE_VIDEO},split={len(renditions)}{inputs}"]
    maps, variants, peaks = [], [], {}
    for index, (name, width, height, bitrate, maximum, threads, peak) in enumerate(renditions):
        unscaled_max = maximum
        if video_limit_kbps is not None:
            scale = min(1, video_limit_kbps / sum(row[4] for row in renditions))
            bitrate, maximum = max(1, int(bitrate * scale)), max(1, int(maximum * scale))
        # FFmpeg advertises its average video target; ABR needs room for the
        # peak video rate, audio and MPEG-TS overhead on difficult scenes.
        peaks[name] = peak - (unscaled_max - maximum) * 1000
        filters.append(f"[{name}_in]{square_pixel_scale(width, height)}[{name}]")
        maps += ["-map", f"[{name}]", f"-b:v:{index}", f"{bitrate}k",
                 f"-maxrate:v:{index}", f"{maximum}k", f"-bufsize:v:{index}", f"{maximum}k",
                 f"-threads:v:{index}", str(threads)]
        variant = f"v:{index}"
        if has_audio:
            maps += ["-map", f"{audio_input}:a:{audio_index}"]
            variant += f",a:{index}"
        variants.append(f"{variant},name:{name}")
    maps = ["-filter_complex_threads", "2", "-filter_complex", ";".join(filters), *maps]
    output = ["-master_pl_name", "index.m3u8", "-var_stream_map", " ".join(variants),
              "-hls_segment_filename", str(directory / "segment%v_%06d.ts"), str(directory / "index_%v.m3u8")]
    return maps, output, peaks


def stream_audio_options(has_audio: bool | None, on_demand: bool, remaining: float) -> list[str]:
    if has_audio is False:
        return []
    filters = [
        "aresample=48000:async=100:first_pts=0",
        "alimiter=limit=0.94:attack=4:release=35:level=0",
        "afade=t=in:st=0:d=0.025",
    ]
    if not on_demand:
        filters.append(f"afade=t=out:st={max(0, remaining - 0.035):.3f}:d=0.035")
    return [*AAC_FORMAT, "-af", ",".join(filters)]


def start_or_reuse_encoder(channel: dict, *, public_delivery: bool = False, constrained_live: bool = False, position=None) -> str:
    position = position or schedule_position(channel)
    item = position["item"]
    on_demand = bool(channel.get("on_demand"))
    start_key = "on-demand" if on_demand else position["start"].isoformat()
    if position.get("recovery_key"):
        start_key += ":" + position["recovery_key"]
    profile = "constrained" if constrained_live else "public" if public_delivery else "standard"
    reuse_key = (channel["id"], media_source_identity(item), start_key, profile,
                 None if on_demand else broadcast_settings(channel))
    with LOCK:
        generation = ENCODER_GENERATION
        for identity, existing in FFMPEG_SESSIONS.items():
            if (existing.get("reuse_key") == reuse_key and not existing.get("capacity_error")
                    and existing["process"].poll() in (None, 0) and (existing["directory"] / "index.m3u8").exists()):
                existing["last_access"] = time.time()
                return identity
    identity = secrets.token_hex(10)
    relay = public_delivery or config()["public_tunnel"]
    # Include audio and transport-stream overhead. VOD retains its full seek history.
    seconds = max(1, (position["stop"] - position["start"]).total_seconds())
    budget = int(seconds * (400_000 if relay else 1_000_000)) + 8 * 1024**2 if on_demand else STREAM_ROLLING_BUDGET
    STREAM_DIR.mkdir(parents=True, exist_ok=True)
    # Sustained HLS rates include audio and TS overhead. Use a smaller movie
    # profile when needed instead of rejecting a normal feature-length film.
    minimum = int(seconds * 40_000) + 8 * 1024**2 if on_demand else budget
    budget = reserve_encoder(identity, channel, budget, minimum)
    try:
        return launch_encoder(identity, channel, position, profile, relay, budget,
                              generation=generation, reuse_key=reuse_key)
    finally:
        with LOCK:
            ENCODER_STARTING.pop(identity, None)


def reserve_encoder(identity, channel, budget, minimum=None):
    free = shutil.disk_usage(STREAM_DIR).free
    with LOCK:
        active = [session for session in FFMPEG_SESSIONS.values() if session["process"].poll() is None]
        handoff = not channel.get("on_demand") and any(
            session.get("channel_id") == channel["id"] for session in FFMPEG_SESSIONS.values())
        if len(active) + len(ENCODER_STARTING) >= (MAX_ENCODERS if handoff else max(1, MAX_ENCODERS - 1)):
            raise EncoderCapacityError("This studio is serving other streams. Please try again shortly.")
        committed = sum(ENCODER_STARTING.values())
        future = committed
        for session in FFMPEG_SESSIONS.values():
            size = session.get("cache_bytes", session.get("budget", 0) if session["process"].poll() is not None else 0)
            expected = session.get("budget", 0) if session["process"].poll() is None else 0
            committed += max(size, expected)
            future += max(0, expected - size)
        available = min(STREAM_CACHE_LIMIT - committed, free - future - STREAM_FREE_RESERVE)
        if channel.get("on_demand"):
            # Keep room for the live channel's next program on a busy movie shelf.
            available = min(available - STREAM_ROLLING_BUDGET, STREAM_CACHE_LIMIT * 3 // 4)
        granted = min(available, budget)
        if granted < (budget if minimum is None else minimum):
            raise EncoderCapacityError("The studio has insufficient temporary storage for this stream.")
        ENCODER_STARTING[identity] = granted
        return granted


def encoder_source(item):
    if item.get("source") == "local":
        return str(local_media_path(item)), [], ""
    if item.get("source") == "jellyfin":
        connection, _ = jellyfin_section(item.get("section", ""))
        identity_id = jellyfin_id(item.get("id"))
        if not identity_id:
            raise ValueError("The selected Jellyfin title is invalid.")
        media_input = f'{connection["url"]}/Videos/{identity_id}/stream?static=true'
        return media_input, ["-headers", f"Authorization: {jellyfin_authorization(connection)}\r\n"], connection["token"]
    credentials = plex_credentials(str(item.get("plex_server_id", "")))
    if not credentials["token"]:
        raise ValueError("This channel uses Plex media, but Plex is not connected.")
    media_input = credentials["url"] + plex_part_key(item)
    separator = "&" if "?" in media_input else "?"
    return media_input + separator + urllib.parse.urlencode({"X-Plex-Token": credentials["token"]}), [], ""


def preflight_upcoming_source(feed, item):
    if not feed.active() or not NEXT_SOURCE_SLOTS.acquire(blocking=False):
        return

    def prepare():
        try:
            if not feed.active() or SHUTTING_DOWN.is_set():
                return
            media_input, source_options, _ = encoder_source(item)
            _, has_audio = playback_audio_track(item, media_input, source_options, preflight=True)
            outcome = "ready" if has_audio is not None else "deferred"
            record_broadcast_event(feed.channel_id, "up_next_preflight", outcome)
        except (OSError, ValueError) as exc:
            record_broadcast_event(feed.channel_id, "up_next_preflight", broadcast_failure_reason(exc))
        finally:
            NEXT_SOURCE_SLOTS.release()

    threading.Thread(target=prepare, daemon=True, name="channel-next-source").start()


def launch_encoder(identity, channel, position, profile, relay, budget, *, software_retry=False, generation=None,
                   reuse_key=None):
    item = position["item"]
    on_demand = bool(channel.get("on_demand"))
    public_delivery = profile != "standard"
    constrained_live = profile == "constrained"
    interstitial = item.get("source") == "interstitial"
    audio_input = 1 if interstitial else 0
    directory = STREAM_DIR / f"{identity}-{secrets.token_hex(6)}"
    if interstitial:
        media_input, source_options, jellyfin_token = str(directory / "studio-up-next.png"), [], ""
    else:
        media_input, source_options, jellyfin_token = encoder_source(item)
    # Keep the previous program's buffered segments available during the handoff.
    remaining = max(0.1, (position["stop"] - position["start"]).total_seconds() - position["offset_ms"] / 1000)
    input_timing = ["-re"] if on_demand else ["-re", "-ss", f'{position["offset_ms"] / 1000:.3f}']
    if on_demand and public_delivery:
        # Rebuild headroom after a congested interval. Keep disk reservations
        # and idle eviction identical to the realtime producer.
        input_timing = ["-readrate", "1.25"]
    if interstitial:
        input_timing = ["-re", "-loop", "1", "-framerate", "24"]
    elif item.get("source") != "local":
        # Plex media parts support byte ranges. Resume an interrupted read at
        # the missing byte while leaving normal end-of-file handling intact.
        input_timing += ["-rw_timeout", "7000000", "-reconnect", "1", "-reconnect_delay_max", "2"]
    input_timing += source_options
    executable = ffmpeg_path()
    if public_delivery and ffmpeg_supports_read_burst(executable):
        # Fill the viewer's startup buffer, then pace the encoder at normal speed.
        input_timing += ["-readrate_initial_burst", "6"]
    output_timing = [] if on_demand else ["-t", f"{remaining:.3f}"]
    # Remote players need several extra segments to absorb relay jitter.
    hls_options = (["-hls_playlist_type", "event", "-hls_flags", "independent_segments+temp_file"] if on_demand else
                   ["-hls_list_size", "12" if relay else "5", "-hls_delete_threshold", "45" if constrained_live else "90",
                    "-hls_flags", "independent_segments+temp_file+delete_segments"])
    audio_index, has_audio = (playback_audio_track(item, media_input, source_options) if source_options
                              else (0, True) if interstitial else playback_audio_track(item, media_input))
    duration = (position["stop"] - position["start"]).total_seconds()
    media_rate = int((budget - 8 * 1024**2) * 8 / max(1, duration) / 1000 * 0.82) if on_demand else None
    audio_rate = 64 if has_audio is not False else 0
    peaks = {}
    limits = BROADCAST_DEFAULT if on_demand else broadcast_settings(channel)
    renditions = relay_renditions(constrained_live, on_demand=on_demand, limits=limits)
    if constrained_live and has_audio is None and len(renditions) > 1:
        encoding = ["-map", "0:v:0", "-map", f"0:a:{audio_index}?", "-threads:v", "2",
                    "-vf", f"{DEINTERLACE_VIDEO},{square_pixel_scale(480, 270)}",
                    "-b:v", "104k", "-maxrate", "124k", "-bufsize", "124k"]
        output = ["-hls_segment_filename", str(directory / "segment%06d.ts"), str(directory / "index.m3u8")]
    elif relay and len(renditions) > 1 and has_audio is not None and (media_rate is None or media_rate >= 750):
        video_limit = media_rate - 3 * audio_rate if media_rate is not None else None
        encoding, output, peaks = adaptive_relay_options(directory, audio_index, has_audio, video_limit,
                                                         constrained=constrained_live, on_demand=on_demand, limits=limits,
                                                         audio_input=audio_input)
    else:
        encoding = ["-map", "0:v:0", "-map", f"{audio_input}:a:{audio_index}?"]
        if not on_demand:
            _, width, height, target, maximum, _, _ = renditions[-1]
            encoding += ["-vf", f"{DEINTERLACE_VIDEO},{square_pixel_scale(width, height)}",
                         "-b:v", f"{target}k", "-maxrate", f"{maximum}k", "-bufsize", f"{maximum}k"]
        elif relay:
            maximum = min(1000, media_rate - audio_rate) if media_rate is not None else 1000
            encoding += ["-vf", f"{DEINTERLACE_VIDEO},{square_pixel_scale(1280, 720)}",
                         "-b:v", f"{min(800, maximum)}k", "-maxrate", f"{maximum}k", "-bufsize", f"{maximum}k"]
        else:
            maximum = min(6000, media_rate - audio_rate) if media_rate is not None else 6000
            filters = DEINTERLACE_VIDEO if on_demand else f"{DEINTERLACE_VIDEO},{square_pixel_scale(1280, 720)}"
            encoding += ["-vf", filters, "-crf", "19", "-maxrate", f"{maximum}k", "-bufsize", f"{maximum * 2}k"]
        output = ["-hls_segment_filename", str(directory / "segment%06d.ts"), str(directory / "index.m3u8")]
    policy = os.environ.get("CHANNELFORGE_VIDEO_ENCODER", "auto").lower()
    codec = "libx264"
    if policy != "software" and not software_retry:
        if on_demand and not relay and item.get("source") == "local" and Path(media_input).suffix.lower() in (".mp4", ".m4v", ".mov"):
            info = Path(media_input).stat()
            if info.st_size * 1.25 + 8 * 1024**2 <= budget and copyable_local_video(media_input, info.st_mtime_ns, info.st_size):
                codec = "copy"
        if codec != "copy" and relay and policy not in ("remux", "software"):
            with LOCK:
                busy = len(ENCODER_STARTING) > 1 or any(s["process"].poll() is None for s in FFMPEG_SESSIONS.values())
            if policy == "hardware" or busy or int(item.get("height") or 0) >= 1080 or item.get("video_codec") in ("hevc", "av1"):
                codec = probed_hardware_encoder(executable) or "libx264"
    latency = [] if on_demand else ["-tune", "zerolatency"]
    segment_duration = "2" if constrained_live or (on_demand and public_delivery) else "1"
    audio = stream_audio_options(has_audio, on_demand, remaining)
    if constrained_live and audio:
        audio += ["-b:a:0", "48k"]
    video = ["-c:v", codec]
    if codec == "copy":
        encoding = ["-map", "0:v:0", "-map", f"0:a:{audio_index}?"]
    else:
        if codec == "libx264":
            video += ["-preset", "superfast" if constrained_live else "veryfast", *latency]
        elif codec == "h264_nvenc":
            video += ["-preset", "p4", "-tune", "ll", "-bf", "0", "-forced-idr", "1",
                      "-surfaces", "4", "-rc-lookahead", "0", "-delay", "0"]
        else:
            video += ["-bf", "0"]
        video += ["-pix_fmt", "yuv420p", "-force_key_frames", f"expr:gte(t,n_forced*{segment_duration})"]
        if relay:
            video += ["-level:v", "4.1", "-fpsmax:v", "60"]
    additional_inputs = []
    if interstitial:
        ambience = ambience_file(studio_branding.settings(channel.get("branding")))
        if ambience:
            additional_inputs = ["-ss", f'{position["offset_ms"] / 1000:.3f}', "-i", str(ambience)]
        else:
            additional_inputs = ["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono"]
    command = [
        executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-nostats", "-progress", "pipe:1",
        *input_timing, "-i", media_input, *additional_inputs, *output_timing,
        *encoding, *video,
        *audio,
        "-f", "hls", "-hls_time", segment_duration, *hls_options,
        *output,
    ]
    encoder_flags = 0
    if sys.platform == "win32":
        encoder_flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    elif nice := shutil.which("nice"):
        command = [nice, "-n", "5", *command]
    directory.mkdir(parents=True, exist_ok=True)
    live_feed = position.get("_broadcast_feed")
    session = {"directory": directory, "channel_id": channel["id"], "created": time.time(),
               "reuse_key": reuse_key, "feed": live_feed,
               "last_access": time.time(), "on_demand": on_demand, "budget": budget,
               "program": item.get("title", ""), "video_encoder": codec, "profile": profile,
               "audio_codec": "aac" if audio else "none", "segment_seconds": int(segment_duration), "variant_peaks": peaks,
               "media_start": position["start"] + timedelta(milliseconds=position["offset_ms"])}
    try:
        if interstitial:
            branding = studio_branding.settings(channel.get("branding"))
            upcoming = item["next_program"]
            render_channel_branding_card(branding, channel, upcoming).save(media_input)
        with LOCK:
            if SHUTTING_DOWN.is_set() or (generation is not None and generation != ENCODER_GENERATION):
                raise InterruptedError("The studio is shutting down.")
            if live_feed and not live_feed.active():
                raise InterruptedError("The live viewer lease expired.")
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=encoder_flags)
            session["process"] = process
            ENCODER_LAUNCHES[identity] = process
        def read_progress():
            counters = {}
            for line in process.stdout:
                key, _, value = line.decode("ascii", "replace").strip().partition("=")
                if key in ("frame", "dup_frames", "drop_frames", "out_time_us") and value.isdigit():
                    counters[key] = int(value)
                if key == "progress":
                    session["progress"] = counters.copy()
        progress = threading.Thread(target=read_progress, daemon=True, name="channel-encoder-stats")
        progress.start()
        session["progress_reader"] = progress
        # A damaged source may keep logging after startup. A full stderr pipe
        # would otherwise block FFmpeg and leave all viewers buffering.
        errors = deque(maxlen=4)
        def drain_errors():
            for chunk in iter(lambda: process.stderr.read(1024), b""):
                errors.append(chunk)
        reader = threading.Thread(target=drain_errors, daemon=True, name="channel-encoder-errors")
        reader.start()
        session["error_reader"] = reader
        deadline = time.monotonic() + 25
        playlist = directory / "index.m3u8"
        while time.monotonic() < deadline:
            if live_feed and not live_feed.active():
                raise InterruptedError("The live viewer lease expired.")
            result = process.poll()
            if (playlist.exists() and playlist.stat().st_size and
                    (not on_demand or movie_start_ready(playlist, result))):
                with LOCK:
                    if SHUTTING_DOWN.is_set() or (generation is not None and generation != ENCODER_GENERATION):
                        raise InterruptedError("Studio playback was stopped during startup.")
                    previous = FFMPEG_SESSIONS.get(identity)
                    FFMPEG_SESSIONS[identity] = session
                    ENCODER_LAUNCHES.pop(identity, None)
                    ENCODER_STARTING.pop(identity, None)
                if previous:
                    dispose_encoder(previous)
                return identity
            if result is not None:
                reader.join()
                error = b"".join(errors).decode("utf-8", "replace").strip()
                error = re.sub(r"(?i)(X-Plex-Token=)[^&\s']+", r"\1[redacted]", error)
                if jellyfin_token:
                    error = error.replace(jellyfin_token, "[redacted]")
                raise ValueError(f"Local FFmpeg playback failed: {error or 'the media could not be decoded.'}")
            time.sleep(0.05)
        raise ValueError("Local FFmpeg playback did not start within 25 seconds.")
    except Exception as exc:
        with LOCK:
            ENCODER_LAUNCHES.pop(identity, None)
        dispose_encoder(session)
        if (codec != "libx264" and isinstance(exc, ValueError) and not SHUTTING_DOWN.is_set() and
                (generation is None or generation == ENCODER_GENERATION)):
            record_broadcast_event(channel["id"], "encoder_fallback", codec)
            return launch_encoder(identity, channel, position, profile, relay, budget, software_retry=True,
                                  generation=generation, reuse_key=reuse_key)
        raise


def stop_encoder(session):
    """Reap the child before releasing its files, including failed startups."""
    process = session.get("process")
    if process is None:
        return
    with LOCK:
        stop_lock = session.setdefault("stop_lock", threading.Lock())
    with stop_lock:
        if session.get("reaped"):
            return
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait()
        if session.get("error_reader"):
            session["error_reader"].join()
        if process.stderr:
            process.stderr.close()
        if session.get("progress_reader"):
            session["progress_reader"].join()
            process.stdout.close()
        session["reaped"] = True


def dispose_encoder(session):
    stop_encoder(session)
    if session.get("directory"):
        remove_encoder_files(session["directory"])


def encoder_file(name):
    return re.fullmatch(ENCODER_FILE + r"(?:\.tmp)?", name) is not None


def remove_encoder_files(directory):
    if directory.is_symlink() or not directory.is_dir():
        return
    for file in directory.iterdir():
        if (encoder_file(file.name) or file.name == "studio-up-next.png") and file.is_file() and not file.is_symlink():
            try:
                file.unlink()
            except OSError:
                pass
    try:
        directory.rmdir()
    except OSError:
        pass  # Files unrelated to FFmpeg must stay in place.


def open_shared_playlist(path, flags):
    # Windows readers must permit FFmpeg to rename its completed playlist
    # over the file while an HTTP request is still reading the old snapshot.
    import _winapi
    import msvcrt
    share_read_write_delete = 0x1 | 0x2 | 0x4
    handle = _winapi.CreateFile(path, _winapi.GENERIC_READ, share_read_write_delete,
                                0, _winapi.OPEN_EXISTING, 0, 0)
    try:
        return msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
    except OSError:
        _winapi.CloseHandle(handle)
        raise


def read_encoder_playlist(path):
    opener = open_shared_playlist if sys.platform == "win32" else None
    for attempt in range(7):
        try:
            with open(path, "rb", opener=opener) as source:
                return source.read()
        except (FileNotFoundError, PermissionError):
            if attempt == 6:
                raise
            time.sleep(.025 * (attempt + 1))


def movie_start_ready(playlist: Path, result: int | None) -> bool:
    if result not in (None, 0):
        return False
    try:
        manifest = read_encoder_playlist(playlist)
        variants = re.findall(rb"(?m)^index_(?:economy|balanced|hd)\.m3u8\r?$", manifest)
        snapshots = [read_encoder_playlist(playlist.with_name(name.strip().decode("ascii"))) for name in variants]
    except (FileNotFoundError, PermissionError):
        return False
    for content in snapshots or [manifest]:
        if result == 0:
            if b"#EXTINF:" not in content or b"#EXT-X-ENDLIST" not in content:
                return False
        elif content.count(b"#EXTINF:") < 2:
            return False
    return True


def viewer_encoder_playlist(session, name):
    content = read_encoder_playlist(session["directory"] / name)
    if session.get("on_demand"):
        result = session["process"].poll()
        if result not in (None, 0):
            raise MovieEncodingError("Studio stopped encoding before the movie finished.")
        if result is None and b"#EXT-X-ENDLIST" in content:
            # FFmpeg writes the trailer before its process exit status is available.
            content = re.sub(rb"(?m)^#EXT-X-ENDLIST\r?\n?", b"", content)
    peaks = session.get("variant_peaks", {})
    if name != "index.m3u8" or not peaks:
        return content
    lines = content.splitlines(keepends=True)
    for index, line in enumerate(lines[:-1]):
        if not line.startswith(b"#EXT-X-STREAM-INF:"):
            continue
        variant = re.fullmatch(rb"index_(economy|balanced|hd)\.m3u8", lines[index+1].strip())
        if variant and (peak := peaks.get(variant[1].decode("ascii"))):
            lines[index] = re.sub(rb"(?<=[,:])BANDWIDTH=\d+", f"BANDWIDTH={peak}".encode(), line)
    return b"".join(lines)


class LivePlayout:
    def __init__(self, open_program, run_anchor=None, channel_id=None, prepare_program=None):
        self.run_anchor = run_anchor
        self.token = secrets.token_hex(12)
        self.open_program = open_program
        # Keep viewers inside the playlist through temporary relay stalls. Only
        # segment references live here; the encoder already retains the files.
        self.segments = deque(maxlen=30)
        self.condition = threading.Condition()
        self.started_at = self.last_request = time.monotonic()
        self.viewers = {}
        self.departed_viewers = {}
        self.leave_deadline = None
        self.peak_viewers = 0
        self.media_bytes = {}
        self.last_anonymous_request = float("-inf")
        self.error = None
        self.capacity_error = None
        self.finished = False
        self.stopped = False
        self.sequence = 0
        self.playlist_cache = {}
        self.channel_id = channel_id
        self.recovery_key = ""
        self.source = None
        self.recovering = False
        self.prepare_program = prepare_program
        self.next_boundary = None
        self.prepared_boundary = None
        self.resume_at = None
        self.media_start = None
        self.segment_seconds = 1
        self.broadcast_limits = BROADCAST_DEFAULT
        self.constrained = False

    def prepare_upcoming(self):
        boundary = self.next_boundary
        if (not self.prepare_program or boundary is None or self.prepared_boundary == boundary or
                not 0 < boundary.timestamp() - time.time() <= 4 or not self.active() or
                time.monotonic() - self.last_request > 10 or not NEXT_PROGRAM_SLOT.acquire(blocking=False)):
            return
        self.prepared_boundary = boundary
        def prepare():
            try:
                if self.active():
                    self.prepare_program(boundary)
            except (OSError, ValueError) as exc:
                record_broadcast_event(self.channel_id, "preparation_deferred", broadcast_failure_reason(exc))
            finally:
                NEXT_PROGRAM_SLOT.release()
        threading.Thread(target=prepare, daemon=True, name="channel-next-program").start()

    def heartbeat(self, viewer=None):
        with self.condition:
            if self.stopped:
                return
            now = time.monotonic()
            if viewer:
                if now - self.departed_viewers.get(viewer, float("-inf")) < LIVE_VIEWER_TTL:
                    return  # A late manifest request must not renew a closed player.
                self.departed_viewers.pop(viewer, None)
                self.leave_deadline = None
                self.expire_viewers(now)
                self.viewers.pop(viewer, None)
                if len(self.viewers) >= 2048:
                    self.viewers.pop(next(iter(self.viewers)))
                self.viewers[viewer] = now
                self.peak_viewers = max(self.peak_viewers, len(self.viewers))
            else:
                self.last_anonymous_request = now
            self.last_request = now

    def release_viewer(self, viewer):
        with self.condition:
            now = time.monotonic()
            self.expire_viewers(now)
            if self.viewers.pop(viewer, None) is None:
                return
            if len(self.departed_viewers) >= 2048:
                self.departed_viewers.pop(next(iter(self.departed_viewers)))
            self.departed_viewers[viewer] = now
            if not self.viewers:
                self.leave_deadline = now + LIVE_VIEWER_GRACE

    def activity(self):
        with self.condition:
            now = time.monotonic()
            self.expire_viewers(now)
            anonymous = now - self.last_anonymous_request < LIVE_VIEWER_TTL
            if self.finished or not self.active():
                state = "idle"
            elif not self.viewers and not anonymous:
                state = "cooldown"
            elif self.segments:
                state = "live"
            else:
                state = "starting"
            payload = sum(size for second, size in self.media_bytes.items() if int(now) - second < 10)
            return {"state": state, "viewers": len(self.viewers), "peak_viewers": self.peak_viewers,
                    "legacy_activity": anonymous, "upload_bps": payload * 8 // 10}

    def expire_viewers(self, now):
        while self.viewers:
            viewer = next(iter(self.viewers))
            if now - self.viewers[viewer] < LIVE_VIEWER_TTL:
                break
            self.viewers.pop(viewer)

    def record_upload(self, size):
        with self.condition:
            second = int(time.monotonic())
            self.media_bytes[second] = self.media_bytes.get(second, 0) + size
            while self.media_bytes and second - next(iter(self.media_bytes)) >= 10:
                self.media_bytes.pop(next(iter(self.media_bytes)))

    def active(self):
        if self.stopped or self.error is not None:
            return False
        now = time.monotonic()
        if now - self.last_anonymous_request < LIVE_LEGACY_IDLE:
            return True
        deadline = self.leave_deadline
        if deadline is None:
            deadline = self.last_request + LIVE_VIEWER_TTL + LIVE_VIEWER_GRACE
        return now < deadline

    def session_keys(self):
        with self.condition:
            return {renditions["economy"][1].split("/")[2] for _, _, renditions in self.segments}

    def advertised_segments(self, session_id):
        prefix = f"/ffmpeg-stream/{session_id}/"
        with self.condition:
            return {path.removeprefix(prefix) for _, _, renditions in self.segments for _, path in renditions.values()
                    if path.startswith(prefix)}

    def playlist(self, variant, viewer=None):
        with self.condition:
            self.heartbeat(viewer)
            self.condition.wait_for(lambda: self.segments or self.error or self.finished or self.stopped, timeout=15)
            if self.capacity_error:
                raise EncoderCapacityError(self.capacity_error)
            if self.error or not self.segments:
                raise ValueError("The channel could not prepare its next program.")
            version = (self.segments[0][0], self.segments[-1][0], self.finished)
            cached = self.playlist_cache.get(variant)
            if cached and cached[0] == version:
                return cached[1]
            first, discontinuity, _ = self.segments[0]
            lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS",
                     "#EXT-X-TARGETDURATION:2", f"#EXT-X-MEDIA-SEQUENCE:{first}",
                     f"#EXT-X-DISCONTINUITY-SEQUENCE:{discontinuity}"]
            for _, epoch, renditions in self.segments:
                if epoch != discontinuity:
                    lines.append("#EXT-X-DISCONTINUITY")
                    discontinuity = epoch
                lines.extend(renditions[variant])
            if self.finished:
                lines.append("#EXT-X-ENDLIST")
            content = "\n".join(lines) + "\n"
            self.playlist_cache[variant] = (version, content)
            return content

    def run(self):
        boundary = None
        epoch = 0
        retries = 0
        try:
            while self.active():
                program = None
                try:
                    program = self.open_program(boundary)
                    if program is None:
                        with self.condition:
                            self.finished = True
                            self.condition.notify_all()
                        return
                    session, directory, process, boundary = program
                    self.next_boundary = boundary
                    self.read_program(session, directory, process, epoch)
                    self.recovering = False
                    if self.source and self.active() and boundary.timestamp() - time.time() > 12:
                        quarantine_unplayable_source(self.source)
                        raise ValueError("Media ended substantially before its scheduled boundary.")
                except EncoderCapacityError:
                    raise
                except (OSError, ValueError) as exc:
                    if not self.channel_id or not self.active():
                        raise
                    self.recovering = True
                    cause = broadcast_failure_reason(exc)
                    record_broadcast_event(self.channel_id, "program_failure", cause)
                    if program and process and process.poll() is None:
                        with LOCK:
                            broken = FFMPEG_SESSIONS.get(session)
                        if broken:
                            stop_encoder(broken)
                    if retries >= 3:
                        raise
                    self.recovery_key = secrets.token_hex(6)
                    # Advance from the channel's current UTC position. Already
                    # published segments keep their old ID and remain fetchable.
                    now = datetime.now(timezone.utc)
                    boundary = max(now, self.resume_at) if self.resume_at else now
                    time.sleep(.5 * 2**retries)
                    retries += 1
                    epoch += 1
                    continue
                retries = 0
                self.recovery_key = ""
                # Bound the lookahead even when programs are very short.
                while self.active() and time.time() < boundary.timestamp() - 4:
                    time.sleep(.15)
                epoch += 1
        except Exception as exc:
            if self.channel_id:
                record_broadcast_event(self.channel_id, "broadcast_stopped", broadcast_failure_reason(exc))
                with LOCK:
                    PLAYBACK_ERRORS[self.channel_id] = (time.monotonic(), sanitized_playback_error(exc))
            with self.condition:
                self.error = str(exc)
                if isinstance(exc, EncoderCapacityError):
                    self.capacity_error = str(exc)
                self.condition.notify_all()
        finally:
            with self.condition:
                self.stopped = not self.finished
                self.condition.notify_all()

    def read_program(self, session, directory, process, epoch):
        master = read_encoder_playlist(directory / "index.m3u8").decode()
        adaptive = "#EXT-X-STREAM-INF" in master
        last = -1
        media_cursor = self.media_start
        progressed_at = time.monotonic()
        names = tuple(re.findall(r"(?m)^index_(economy|balanced|hd)\.m3u8\r?$", master)) if adaptive else ("economy",)
        paths = {name: directory / (f"index_{name}.m3u8" if adaptive else "index.m3u8") for name in names}
        fingerprints, snapshots, final_playlist = {}, {}, {}
        common = []
        while self.active():
            self.prepare_upcoming()
            returncode = process.poll() if process is not None else 0
            changed = False
            for name, playlist in paths.items():
                try:
                    info = playlist.stat()
                    fingerprint = (info.st_mtime_ns, info.st_size, info.st_ino)
                except FileNotFoundError:
                    fingerprint = None
                if name in fingerprints and fingerprints[name] == fingerprint and returncode is None:
                    continue
                content = read_encoder_playlist(playlist).decode()
                fingerprints[name] = fingerprint
                final_playlist[name] = "#EXT-X-ENDLIST" in content
                snapshots[name] = {
                    int(number): (duration, f"/ffmpeg-stream/{session}/{filename}")
                    for duration, filename, number in re.findall(
                        r"(?m)^(#EXTINF:[^\r\n]+)\r?\n(segment(?:[a-z]+_)?(\d+)\.ts)\r?$", content)
                }
                changed = True
            if changed:
                if not adaptive:
                    snapshots.update(balanced=snapshots["economy"], hd=snapshots["economy"])
                common = sorted(set.intersection(*(set(items) for items in snapshots.values())))
            if common and common[-1] > last:
                with self.condition:
                    for number in (value for value in common if value > last):
                        progressed_at = time.monotonic()
                        self.recovering = False
                        variants = {name: rows[number] for name, rows in snapshots.items()}
                        self.segments.append((self.sequence, epoch, variants))
                        if media_cursor is not None:
                            if last < 0 and number:
                                media_cursor += timedelta(seconds=number * self.segment_seconds)
                            duration = float(variants["economy"][0].split(":", 1)[1].split(",", 1)[0])
                            media_cursor += timedelta(seconds=duration)
                            self.resume_at = min(media_cursor, self.next_boundary)
                        self.sequence += 1
                        last = number
                    self.condition.notify_all()
            if all(final_playlist.values()) and common:
                return
            if returncode is not None:
                raise ValueError("The channel encoder stopped without a final playlist.")
            if time.monotonic() - progressed_at > 12:
                raise ValueError("The encoder stopped producing HLS segments.")
            time.sleep(.05 if last < 3 else .12)


def start_playback_route(channel: dict, *, public_delivery: bool = False, constrained_live: bool = False) -> str:
    """Start a demand-driven, browser-safe H.264/AAC encoder for every source."""
    identity = start_ffmpeg_session(channel, public_delivery=public_delivery, constrained_live=constrained_live)
    return f"/ffmpeg-stream/{identity}/index.m3u8"


def stop_ffmpeg_sessions() -> None:
    global ENCODER_GENERATION
    with LOCK:
        ENCODER_GENERATION += 1
        for feed in LIVE_PLAYOUT.values():
            with feed.condition:
                feed.stopped = True
                feed.last_request = float("-inf")
                feed.condition.notify_all()
        LIVE_PLAYOUT.clear()
        sessions = list(FFMPEG_SESSIONS.values())
        launches = list(ENCODER_LAUNCHES.values())
        FFMPEG_SESSIONS.clear()
    for process in launches:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    for session in sessions:
        dispose_encoder(session)


def reap_idle_sessions_once(now: float | None = None) -> int:
    """Stop encoders whose viewers stopped requesting HLS data."""
    moment = now if now is not None else time.time()
    with LOCK:
        expired = []
        for channel_id, feed in list(LIVE_PLAYOUT.items()):
            if not feed.active():
                del LIVE_PLAYOUT[channel_id]
                with feed.condition:
                    feed.stopped = True
                    feed.condition.notify_all()
        inactive_live = []
        active_live_directories = []
        for key, value in FFMPEG_SESSIONS.items():
            # Cached, finished movies can resume without starting Plex again.
            # An active movie encoder times out like live TV when viewers leave.
            last_access = value.get("last_access", value.get("created", moment))
            feed = value.get("feed")
            if feed and feed.active() and key in feed.session_keys():
                # The player may be reading its pre-buffered segments. Playlist
                # requests still lease the files referenced by that playlist.
                value["last_access"] = last_access = max(last_access, moment - (time.monotonic() - feed.last_request))
            elif feed and not feed.active() and value["process"].poll() is None:
                inactive_live.append(value)
            retention = 600 if value.get("on_demand") and value["process"].poll() == 0 else 60
            if moment - last_access > retention:
                expired.append((key, value))
            elif not value.get("on_demand") and value.get("directory"):
                active_live_directories.append((value["directory"], key, feed))
        for key, _value in expired:
            FFMPEG_SESSIONS.pop(key, None)
    for _key, session in expired:
        dispose_encoder(session)
    for session in inactive_live:
        if not any(session is value for _, value in expired):
            stop_encoder(session)
    for directory, key, feed in active_live_directories:
        advertised = feed.advertised_segments(key) if feed and feed.active() else ()
        prune_live_segments(directory, moment - 90, advertised)
    if STREAM_DIR.is_dir():
        maintain_stream_storage(moment)
    return len(expired)


def maintain_stream_storage(moment):
    global STREAM_PRESSURE_SINCE
    with LOCK:
        sessions = list(FFMPEG_SESSIONS.items())
        tracked = {session.get("directory") for _, session in sessions}
        starting = set(ENCODER_STARTING)
    for directory in STREAM_DIR.iterdir():
        if (directory in tracked or not STREAM_DIRECTORY_NAME.fullmatch(directory.name)
                or directory.name.split("-")[0] in starting or directory.is_symlink()):
            continue
        try:
            if (directory.is_dir() and directory.stat().st_mtime < moment - 120
                    and all(encoder_file(child.name) and child.is_file() and not child.is_symlink() for child in directory.iterdir())):
                remove_encoder_files(directory)
        except FileNotFoundError:
            pass
    used = 0
    for _, session in sessions:
        directory = session.get("directory")
        if not directory or not directory.is_dir():
            continue
        if not session.get("size_final"):
            final = session["process"].poll() is not None
            try:
                with os.scandir(directory) as entries:
                    session["cache_bytes"] = sum(entry.stat(follow_symlinks=False).st_size for entry in entries
                                                 if entry.is_file(follow_symlinks=False) and encoder_file(entry.name))
                session["size_final"] = final
            except FileNotFoundError:
                continue
        used += session.get("cache_bytes", 0)
    # Another application can consume the reserved space after a stream starts.
    # Evict inactive caches before interrupting a viewer; retain an explicit error
    # for a stopped stream rather than exposing a truncated playable movie.
    free = shutil.disk_usage(STREAM_DIR).free
    if free >= STREAM_FREE_RESERVE and used <= STREAM_CACHE_LIMIT:
        STREAM_PRESSURE_SINCE = None
        return
    if STREAM_PRESSURE_SINCE is None:
        STREAM_PRESSURE_SINCE = moment
    sessions.sort(key=lambda row: (moment - row[1].get("last_access", moment) < 60,
                                   -row[1].get("cache_bytes", 0)))
    for key, session in sessions:
        if free >= STREAM_FREE_RESERVE and used <= STREAM_CACHE_LIMIT:
            break
        with LOCK:
            if FFMPEG_SESSIONS.get(key) is not session or session.get("capacity_error"):
                continue
            idle = moment - session.get("last_access", moment) >= 60
            if not idle:
                if session["process"].poll() is not None:
                    continue  # An active completed movie costs no further space and remains seekable.
                within_margin = free > STREAM_FREE_RESERVE * 3 // 4 and used < STREAM_CACHE_LIMIT + STREAM_ROLLING_BUDGET
                if within_margin and (moment - STREAM_PRESSURE_SINCE < 30 or
                                      free >= STREAM_FREE_RESERVE and session.get("cache_bytes", 0) <= session.get("budget", 0)):
                    continue
            session["capacity_error"] = "The studio ran out of temporary storage. Try again when space is available."
            session["last_access"] = moment
        if idle:
            dispose_encoder(session)
            used -= session.get("cache_bytes", 0)
            session["cache_bytes"] = 0
            free = shutil.disk_usage(STREAM_DIR).free
        else:
            # Stop growth, retaining prior segments for requests already in flight.
            stop_encoder(session)
            break  # Reassess growth before interrupting another active stream.


def prune_live_segments(directory: Path, cutoff: float, advertised=()) -> None:
    for segment in directory.glob("segment*.ts"):
        try:
            if (segment.name not in advertised and encoder_file(segment.name)
                    and not segment.is_symlink() and segment.stat().st_mtime < cutoff):
                segment.unlink()
        except OSError:
            continue


def reap_idle_sessions() -> None:
    while not SHUTTING_DOWN.wait(5):
        try:
            reap_idle_sessions_once()
        except OSError as exc:
            print(f"Could not finish temporary stream cleanup: {exc}")


atexit.register(stop_ffmpeg_sessions)
atexit.register(stop_public_tunnel)
atexit.register(stop_legacy_relay)
atexit.register(set_community_relay, None)


def register_hls_url(url: str, channel_id: str = "") -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Plex returned an invalid stream URL.")
    key = secrets.token_urlsafe(18)
    with LOCK:
        now = time.time()
        for stale in [item for item, (_url, expires) in HLS_PROXY.items() if expires < now]:
            HLS_PROXY.pop(stale, None)
            HLS_PROXY_CHANNELS.pop(stale, None)
        HLS_PROXY[key] = (url, now + 7200)
        if channel_id:
            HLS_PROXY_CHANNELS[key] = channel_id
    return f"/hls-proxy/{key}"


def rewrite_hls_manifest(data: bytes, base_url: str, channel_id: str = "") -> bytes:
    """Keep every HLS request same-origin so Linux browsers avoid Plex CORS failures."""
    text = data.decode("utf-8-sig")
    output = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            line = register_hls_url(urllib.parse.urljoin(base_url, stripped), channel_id)
        elif "URI=\"" in line:
            line = re.sub(r'URI="([^"]+)"', lambda match: f'URI="{register_hls_url(urllib.parse.urljoin(base_url, match.group(1)), channel_id)}"', line)
        output.append(line)
    return ("\n".join(output) + "\n").encode("utf-8")


def fetch_plex_stream(url: str, range_header: str = "", channel_id: str = "") -> tuple[bytes, str, str, int, str]:
    headers = {"Accept": "*/*", "User-Agent": f"{APP_NAME}/{VERSION}"}
    if range_header:
        headers["Range"] = range_header
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise ValueError("A Plex stream segment exceeded the safety limit.")
            content_type = response.headers.get_content_type()
            final_url = response.geturl()
            status = getattr(response, "status", 200)
            content_range = response.headers.get("Content-Range", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", "replace")
        status = re.search(r'(?:status|message)=["\']([^"\']+)', detail, re.IGNORECASE)
        reason = status.group(1) if status else str(exc.reason)
        raise ValueError(f"Plex rejected the stream request (HTTP {exc.code}: {reason}).") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f"ChannelForge could not reach the Plex stream: {getattr(exc, 'reason', exc)}") from exc
    if data.lstrip().startswith(b"#EXTM3U") or "mpegurl" in content_type:
        data = rewrite_hls_manifest(data, final_url, channel_id)
        content_type = "application/vnd.apple.mpegurl"
    return data, content_type or "application/octet-stream", final_url, status, content_range


def generate_exports(values: list[dict] | None = None) -> dict:
    global EXPORT_CHANNEL_IDS, EXPORT_REFRESHED_AT
    values = values if values is not None else channels()
    values = [channel for channel in values if not run_finished(channel)]
    settings = config()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    tv = ET.Element("tv", {"generator-info-name": APP_NAME})
    for channel in values:
        node = ET.SubElement(tv, "channel", {"id": f"public-channelforge.{channel['id']}"})
        ET.SubElement(node, "display-name").text = channel["name"]
        ET.SubElement(node, "display-name").text = str(channel["number"])
        logo = next((item for item in asset_manifest() if item.get("id") == channel.get("logo_asset_id")), None)
        if logo:
            ET.SubElement(node, "icon", {"src": f"{server_base(settings)}/user-assets/{logo['filename']}"})
    start = datetime.now(timezone.utc)
    end = start + timedelta(hours=GUIDE_HOURS)
    for channel in values:
        for slot in schedule_window(channel, start, end):
            item, cursor, stop = slot["item"], slot["start"], slot["stop"]
            program = ET.SubElement(tv, "programme", {
                "start": xmltv_time(cursor), "stop": xmltv_time(stop),
                "channel": f"public-channelforge.{channel['id']}",
            })
            ET.SubElement(program, "title", {"lang": "en"}).text = item.get("title", "Untitled")
            if item.get("year"):
                ET.SubElement(program, "date").text = str(item["year"])
            ET.SubElement(program, "category", {"lang": "en"}).text = item.get("type", "video").title()
    ET.indent(tv)
    guide_path = EXPORT_DIR / "guide.xml"
    ET.ElementTree(tv).write(guide_path, encoding="utf-8", xml_declaration=True)
    base = server_base(settings)
    lines = ["#EXTM3U url-tvg=\"" + base + "/exports/guide.xml\""]
    for channel in values:
        stream = base + "/live/" + urllib.parse.quote(channel["id"]) + ".m3u8"
        logo = next((item for item in asset_manifest() if item.get("id") == channel.get("logo_asset_id")), None)
        logo_attribute = f' tvg-logo="{base}/user-assets/{logo["filename"]}"' if logo else ""
        rating = channel.get("channel_rating", "")
        level = CHANNEL_RATING_LEVELS.get(rating)
        rating_attribute = f' tvg-rating="{rating}" tvg-rating-rank="{level}"' if level else ""
        lines.extend([
            f'#EXTINF:-1 tvg-id="public-channelforge.{channel["id"]}" tvg-chno="{html.escape(str(channel["number"]))}"{logo_attribute}{rating_attribute},{channel["name"]}',
            stream,
        ])
    playlist_path = EXPORT_DIR / "channels.m3u"
    playlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    EXPORT_CHANNEL_IDS = tuple(channel["id"] for channel in values)
    EXPORT_REFRESHED_AT = time.monotonic()
    return {"guide": str(guide_path), "playlist": str(playlist_path), "channels": len(values)}


def vault_item_id(item: dict, *, qualified: bool = True) -> str:
    identity = f'{item.get("source", "plex")}:{item.get("plex_server_id", "")}:{item.get("id", "")}:{item.get("file_path", "")}'
    if qualified and item.get("source") == "jellyfin":
        identity += ":" + str(item.get("section", ""))
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


def vault_movies():
    for item in cached_plex_movies():
        if item.get("type") == "movie":
            yield str(item.get("section", "")), item
    for channel in channels():
        for item in channel.get("items", []):
            if item.get("type") == "movie":
                yield str(channel.get("section", "")), item


def unique_vault_movies():
    seen_ids, seen_titles = set(), set()
    for section, item in vault_movies():
        item_id = vault_item_id(item)
        title = unicodedata.normalize("NFKC", str(item.get("title", ""))).casefold()
        key = (" ".join(re.findall(r"\w+", title)), str(item.get("year") or "").strip())
        if item_id in seen_ids or (key[0] and key in seen_titles):
            continue
        seen_ids.add(item_id)
        if key[0]:
            seen_titles.add(key)
        yield item_id, section, item


def movie_added_at(item: dict) -> int:
    value = str(item.get("added_at") or "")
    if not value:
        return 0
    try:
        return max(0, int(value) if value.isdecimal() else int(parse_timestamp(value).timestamp()))
    except (ValueError, OverflowError, OSError):
        return 0


def public_movie(base: str, item_id: str, section: str, item: dict) -> dict:
    thumb = str(item.get("thumb", ""))
    backdrop = str(item.get("art", ""))
    backdrop_url = ""
    if item.get("source") == "jellyfin":
        poster_url = f"{base}/api/public/movie-art?id={item_id}" if jellyfin_id(thumb) else ""
        if jellyfin_id(backdrop):
            backdrop_url = f"{base}/api/public/movie-art?id={item_id}&type=Backdrop"
    elif item.get("source") == "local" and thumb:
        params = urllib.parse.urlencode({"section": item.get("section") or section, "id": item.get("id", "")})
        poster_url = f"{base}/api/local/image?{params}"
    elif thumb:
        params = urllib.parse.urlencode({"path": thumb, "server": item.get("plex_server_id", ""), "size": "poster"})
        poster_url = f"{base}/api/plex/image?{params}"
    else:
        poster_url = ""
    if backdrop.startswith("/library/") and item.get("source") not in {"jellyfin", "local"}:
        params = urllib.parse.urlencode({"path": backdrop, "server": item.get("plex_server_id", ""), "size": "backdrop"})
        backdrop_url = f"{base}/api/plex/image?{params}"
    return {
        "id": item_id, "title": item.get("title", "Untitled"), "year": item.get("year", ""),
        "type": "movie", "summary": item.get("summary", ""),
        "genres": list(item.get("genres", [])), "content_rating": item.get("content_rating", "") or "Unrated",
        "library": item.get("library", ""),
        "poster_url": poster_url, "backdrop_url": backdrop_url, "stream_url": f"{base}/vault/{item_id}.m3u8",
        "thumbnail_url": poster_url.replace("&size=poster", "&size=mini"),
        "duration_ms": item.get("duration_ms", 1800000),
        "added_at": movie_added_at(item),
    }


def vault_items(base: str | None = None, *, offset: int = 0, limit: int | None = None) -> list[dict]:
    base = base or server_base()
    records = itertools.islice(unique_vault_movies(), offset, None if limit is None else offset + limit)
    return [public_movie(base, *record) for record in records]


def search_public_vault(base: str, query: dict, offset: int, limit: int) -> dict:
    def value(name: str, fallback: str = "") -> str:
        return query.get(name, [fallback])[0]

    bounds = {"60s": (1960, 1970), "70s": (1970, 1980), "80s": (1980, 1990), "90s": (1990, 2000),
              "2000s": (2000, 2010), "recent": (2010, 10000)}
    first, last = bounds.get(value("decade"), (0, 10000))
    terms = value("q")[:240].casefold().split()
    genre, rating = value("genre").casefold(), value("rating")
    order = value("sort", "title")
    genres, ratings, matches = set(), set(), []
    for record in unique_vault_movies():
        item = record[2]
        item_genres = [str(name) for name in item.get("genres", []) if name]
        item_rating = str(item.get("content_rating") or "Unrated")
        if offset == 0:
            genres.update(item_genres)
            ratings.add(item_rating)
        item_year = str(item.get("year") or "")
        year = int(item_year) if re.fullmatch(r"[0-9]{4}", item_year) else 0
        if not first <= year < last or (genre and genre not in {name.casefold() for name in item_genres}) or (rating and rating != item_rating):
            continue
        title = str(item.get("title", "Untitled")).casefold()
        if terms:
            text = " ".join([title, item_year, *item_genres, str(item.get("summary") or ""), str(item.get("library") or "")]).casefold()
            if not all(term in text for term in terms):
                continue
        priority = -movie_added_at(item) if order == "added" else -year if order == "newest" else (year or 10000) if order == "oldest" else 0
        matches.append(((priority, title, record[0]), record))
    matches.sort(key=lambda match: match[0])
    end = offset + limit
    result = {"items": [public_movie(base, *record) for _, record in matches[offset:end]],
              "total": len(matches), "next_offset": end if end < len(matches) else None}
    if offset == 0:
        result.update(genres=sorted(genres, key=str.casefold), ratings=sorted(ratings, key=str.casefold))
    return result


def published_jellyfin_art(channel_id, image_id, image_type, section):
    channel = next((entry for entry in channels() if entry.get("id") == channel_id), None)
    if not channel or run_finished(channel):
        raise FileNotFoundError("Channel artwork was not found.")
    fields = ("art", "series_art") if image_type == "Backdrop" else ("thumb", "series_thumb")
    for item in channel.get("items", ()):
        if item.get("source") == "jellyfin" and item.get("section", channel.get("section")) == section:
            if any(item.get(field) == image_id for field in fields):
                connection, _ = jellyfin_section(section)
                width = 960 if image_type == "Backdrop" else 480
                return jellyfin_request(connection, f"/Items/{image_id}/Images/{image_type}?maxWidth={width}", image=True)
    raise FileNotFoundError("Channel artwork was not found.")


def public_channel_catalog(base: str | None = None, *, extended: bool = False, brief: bool = False, continuous: bool = False) -> dict:
    """Return lightweight guide/status data without starting a media session."""
    base = base or server_base()
    moment = datetime.now(timezone.utc)
    start = moment - timedelta(hours=1) if extended else moment
    end = moment + timedelta(hours=GUIDE_HOURS if extended else 3)
    assets = {item.get("id"): item for item in asset_manifest()}
    result = []
    settings = config()
    jellyfin_sources = {row["id"] for row in jellyfin_connections()}
    def image_url(item: dict, channel: dict, *fields: str) -> str:
        item = item.get("next_program", item)
        field = next((field for field in fields if item.get(field)), "")
        if not field:
            return ""
        image_path = str(item[field])
        media_section = str(item.get("section") or channel.get("section", ""))
        if item.get("source") == "local":
            return f"{base}/api/local/image?section={urllib.parse.quote(media_section, safe='')}&id={urllib.parse.quote(str(item.get('id', '')), safe='')}"
        if item.get("source") == "jellyfin":
            query = urllib.parse.urlencode({"channel": channel["id"], "section": media_section, "id": image_path,
                                            "type": "Backdrop" if field in {"art", "series_art"} else "Primary"})
            return f"{base}/api/public/program-art?{query}"
        size = "backdrop" if field in {"art", "series_art"} else "poster"
        return f"{base}/api/plex/image?path={urllib.parse.quote(image_path, safe='')}&server={urllib.parse.quote(str(item.get('plex_server_id', '')), safe='')}&size={size}"
    for channel in channels():
        if run_finished(channel, moment):
            continue
        current = {}
        try:
            items = timeline_items(channel)
            position = schedule_position(channel, moment, items=items)
            current = position["item"]
            if brief:
                slots = [position]
                if not run_finished(channel, position["stop"]):
                    slots.append(schedule_position(channel, position["stop"], items=items))
            else:
                slots = schedule_window(channel, start, end, items=items)
        except (OSError, ValueError, TypeError):
            slots = []
        status = broadcast_health(channel.get("id"))
        try:
            if current.get("source") == "interstitial":
                online = True
            elif current.get("source") == "local":
                online = Path(str(current.get("file_path", ""))).is_file()
            elif current.get("source") == "jellyfin":
                parts = str(current.get("section", "")).split(":")
                online = len(parts) == 3 and parts[0] == "jellyfin" and parts[1] in jellyfin_sources
            else:
                server_id = str(current.get("plex_server_id", ""))
                credentials = plex_credentials(server_id) if server_id else {"token": settings["plex_token"], "url": settings["plex_url"]}
                online = bool(current and credentials.get("token") and credentials.get("url"))
        except (OSError, ValueError, TypeError):
            online = False
        if not online and status == "standby":
            status = "reconnecting" if current else "standby"
        logo = assets.get(channel.get("logo_asset_id"))
        rating = channel.get("channel_rating", "")
        result.append({
            "id": str(channel.get("id", "")), "name": str(channel.get("name", "Untitled")),
            "number": str(channel.get("number", "")), "online": online, "state": status,
            "channel_rating": rating, "channel_rating_rank": CHANNEL_RATING_LEVELS.get(rating, 0),
            "stream_url": f"{base}/live/{urllib.parse.quote(str(channel.get('id', '')))}.m3u8",
            "air_until": channel.get("air_until", ""),
            "air_until_epoch_ms": int(parse_timestamp(channel["air_until"]).timestamp()*1000) if channel.get("air_until") else 0,
            **({"continuous_url": f"{base}/broadcast/{channel['id']}/master.m3u8",
                "firetv_url": f"{base}/broadcast/{channel['id']}/firetv.m3u8"}
               if continuous else {}),
            "logo_url": f"{base}/user-assets/{urllib.parse.quote(str(logo.get('filename', '')))}" if logo else "",
            "programs": [{
                "title": str(slot["item"].get("title", "Untitled")),
                "start_epoch_ms": int(slot["start"].timestamp() * 1000),
                "stop_epoch_ms": int(slot["stop"].timestamp() * 1000),
            } for slot in slots[:2]] if brief else [{
                "title": str(slot["item"].get("title", "Untitled")),
                "year": str(slot["item"].get("year", "")), "type": str(slot["item"].get("type", "video")),
                "genres": (slot["item"].get("genres") or [])[:2],
                "content_rating": str(slot["item"].get("content_rating", "")),
                "summary": str(slot["item"].get("summary", ""))[:600],
                "poster_url": image_url(slot["item"], channel, "thumb", "series_thumb", "art"),
                "series_poster_url": image_url(slot["item"], channel, "series_thumb"),
                "backdrop_url": image_url(slot["item"], channel, "series_art", "art", "series_thumb", "thumb"),
                "start": slot["start"].isoformat(), "stop": slot["stop"].isoformat(),
                "start_epoch_ms": int(slot["start"].timestamp() * 1000),
                "stop_epoch_ms": int(slot["stop"].timestamp() * 1000),
            } for slot in slots],
        })
    return {"version": 1, "creator_version": DISCOVERY_VERSION, "cinema_supported": True,
            "generated_at": moment.isoformat(), "channels": result}


def vault_source(item_id: str) -> dict:
    legacy = {}
    for _section, item in vault_movies():
        current_id = vault_item_id(item)
        if current_id == item_id:
            return item
        if item.get("source") == "jellyfin" and vault_item_id(item, qualified=False) == item_id:
            legacy[current_id] = item
    if len(legacy) == 1:
        return next(iter(legacy.values()))
    raise FileNotFoundError("Vault movie was not found.")


def playback_error(channel_id: str) -> str:
    with LOCK:
        failure = PLAYBACK_ERRORS.get(channel_id)
        if failure:
            failed_at, message = failure
            if time.monotonic() - failed_at < PLAYBACK_ERROR_COOLDOWN:
                return message
            PLAYBACK_ERRORS.pop(channel_id, None)
    return ""


def start_vault_session(item_id: str, *, public_delivery: bool = False) -> str:
    item = vault_source(item_id)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    channel = {"id": f"vault-{item_id}", "items": [item], "schedule_anchor": now.isoformat(),
               "playback_mode": "ordered", "on_demand": True}
    return start_playback_route(channel, public_delivery=public_delivery)


def plugin_tools() -> list[dict]:
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    tools = []
    for path in sorted(PLUGINS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"public_channelforge_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(module)
            definition = module.describe()
            tools.append({"id": path.stem, "name": str(definition["name"]), "description": str(definition.get("description", ""))})
        except Exception as exc:
            tools.append({"id": path.stem, "name": path.stem, "description": f"Could not load: {exc}", "disabled": True})
    return tools


def run_plugin(tool_id: str, payload: dict) -> object:
    if not tool_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Invalid tool name.")
    path = PLUGINS_DIR / f"{tool_id}.py"
    if not path.is_file():
        raise ValueError("Tool not found.")
    spec = importlib.util.spec_from_file_location(f"public_channelforge_plugin_{tool_id}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.run(payload, {"data_dir": str(DATA_DIR), "channels": channels()})


class Handler(BaseHTTPRequestHandler):
    disable_nagle_algorithm = True
    server_version = f"{APP_NAME}/{VERSION}"
    protocol_version = "HTTP/1.1"

    def send_response(self, code, message=None):
        super().send_response(code, message)
        started = getattr(self, "media_request_started", None)
        if started is not None:
            response_ms = round((time.monotonic() - started) * 1000)
            self.send_header("X-ChannelForge-Studio-Response-Ms", str(response_ms))
            self.media_request_started = None
        trace = getattr(self, "stream_trace", None)
        if trace:
            trace_id, phase, started = trace
            report = {"trace_id": trace_id, "phase": phase, "httpError": code,
                      "elapsed_ms": round((time.monotonic() - started) * 1000),
                      "at_ms": int(time.time() * 1000)}
            with LOCK:
                STREAM_TRACES.append(report)
            self.stream_trace = None

    def delivery_profile(self):
        settings = config()
        # A studio's local and public listeners serve the same broadcast clock
        # and encoded segments. Each viewer still uses its own transport URL.
        shared = isinstance(self, PublicMediaHandler) or bool(settings["creator_profile"])
        return shared, shared and constrained_public_relay(settings)

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(25)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def catalog_base(self) -> str:
        port = self.server.server_port
        if self.headers.get("Host", "") in {f"{HOST}:{port}", f"localhost:{port}", f"[::1]:{port}"}:
            return f"http://{HOST}:{port}"
        return server_base()

    def send_bytes(self, status: int, body: bytes, content_type: str, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        headers = extra_headers or {}
        self.send_header("Cache-Control", headers.get("Cache-Control", "no-store"))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self' http: https:; media-src 'self' blob: http: https:")
        for name, value in headers.items():
            if value and name.lower() != "cache-control":
                self.send_header(name, value)
        try:
            self.end_headers()
            self.wfile.write(body)
            if status == 200 and content_type == "video/mp2t":
                second = int(time.monotonic())
                with STUDIO_TRAFFIC_LOCK:
                    STUDIO_TRAFFIC[second] = STUDIO_TRAFFIC.get(second, 0) + len(body)
                    for previous in tuple(STUDIO_TRAFFIC):
                        if second - previous >= 10:
                            STUDIO_TRAFFIC.pop(previous, None)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            self.close_connection = True

    def send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {}
        if len(body) >= 1024 and urllib.parse.urlsplit(self.path).path in {
            "/api/public/channels", "/api/public/vault", "/api/library/items",
        }:
            headers["Vary"] = "Accept-Encoding"
            if any(part.strip().lower() == "gzip" for part in self.headers.get("Accept-Encoding", "").split(",")):
                body = gzip.compress(body, compresslevel=3)
                headers["Content-Encoding"] = "gzip"
        self.send_bytes(status, body, "application/json; charset=utf-8", headers)

    def redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def broadcast(self, channel_id, playlist, token, viewer=None, max_bandwidth=0):
        public, constrained = self.delivery_profile()
        if playlist in ("master", "firetv"):
            channel = next((row for row in channels() if row["id"] == channel_id), None)
            if not channel:
                return self.send_json(404, {"error": "Channel offline."})
            if run_finished(channel):
                return self.send_json(410, {"error": "This channel has finished its lineup."})
            limits = broadcast_settings(channel)

            def open_program(boundary, warm=False):
                current = next((row for row in channels() if row["id"] == channel_id), None)
                if not current:
                    raise ValueError("Channel offline.")
                if run_finished(current, boundary):
                    return None
                current = {**current, "broadcast_height": limits[0], "broadcast_kbps": limits[1]}
                position = schedule_position(current, boundary)
                position["_broadcast_feed"] = feed
                position["recovery_key"] = feed.token
                if not warm:
                    feed.source = position["item"]
                    if position.get("substitute_for"):
                        record_broadcast_event(channel_id, "schedule_substitution", "unavailable_source")
                    if feed.recovery_key:
                        position["recovery_key"] += ":" + feed.recovery_key
                try:
                    identity = start_ffmpeg_session(current, public_delivery=public, constrained_live=constrained, position=position)
                except ValueError as exc:
                    if broadcast_failure_reason(exc) in ("invalid_program", "media_part_missing"):
                        quarantine_unplayable_source(position["item"])
                    raise
                if not warm and position["item"].get("source") == "interstitial":
                    preflight_upcoming_source(feed, position["item"]["next_program"])
                with LOCK:
                    if not warm:
                        PLAYBACK_ERRORS.pop(channel_id, None)
                    session = FFMPEG_SESSIONS[identity]
                    if not warm:
                        feed.media_start = session["media_start"]
                        feed.segment_seconds = session["segment_seconds"]
                        session["feed"] = feed
                    return identity, session["directory"], session["process"], position["stop"]

            retired = []
            with LOCK:
                feed_key = next((key for key in LIVE_PLAYOUT if key[0] == channel_id), (channel_id, public, constrained))
                feed = LIVE_PLAYOUT.get(feed_key)
                if not feed or not feed.active() or feed.run_anchor != channel.get("schedule_anchor") or feed.broadcast_limits != limits:
                    if feed:
                        with feed.condition:
                            feed.stopped = True
                            feed.condition.notify_all()
                        retired = [session for session in FFMPEG_SESSIONS.values() if session.get("feed") is feed]
                        del LIVE_PLAYOUT[feed_key]
                    feed_key = (channel_id, public, constrained)
                    feed = LIVE_PLAYOUT[feed_key] = LivePlayout(open_program, channel.get("schedule_anchor"),
                                                               channel_id, lambda boundary: open_program(boundary, warm=True))
                    feed.broadcast_limits = limits
                    feed.constrained = constrained
                    start_feed = True
                else:
                    start_feed = False
                feed.heartbeat(viewer)
            for session in retired:
                stop_encoder(session)
            if start_feed:
                threading.Thread(target=feed.run, daemon=True, name="channel-playout").start()
            constrained = feed_key[2]
            lines = ["#EXTM3U"]
            variants = [(name, peak) for name, *_, peak in relay_renditions(constrained, limits=limits)]
            if playlist == "firetv" and not constrained:
                # A TV begins with the 480p track and can move down to 270p
                # or up to 720p without starting another compatibility encoder.
                variants.sort(key=lambda variant: variant[0] != "balanced")
            for name, bandwidth in variants:
                lines.extend([f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth}",
                              f"/broadcast/{channel_id}/{feed.token}/{name}.m3u8" + (f"?viewer={viewer}" if viewer else "")])
            manifest = "\n".join(lines) + "\n"
        else:
            with LOCK:
                feed = next((feed for key, feed in LIVE_PLAYOUT.items() if key[0] == channel_id), None)
                if feed and feed.token == token and feed.capacity_error:
                    return self.send_json(503, {"error": feed.capacity_error})
                if feed and feed.token == token and feed.error:
                    reason = broadcast_failure_reason(feed.error)
                    return self.send_json(503, {"state": "reconnecting", "reason": reason,
                                                "error": public_playback_message(reason)})
                if not feed or feed.token != token or not feed.active():
                    return self.send_json(410, {"error": "Tune this channel again."})
                if playlist not in {row[0] for row in relay_renditions(feed.constrained, limits=feed.broadcast_limits)}:
                    return self.send_json(404, {"error": "This quality is unavailable."})
            try:
                manifest = feed.playlist(playlist, viewer)
            except EncoderCapacityError as exc:
                return self.send_json(503, {"error": str(exc)})
            except ValueError:
                reason = broadcast_failure_reason(feed.error) if feed.error else "starting"
                return self.send_json(503, {"state": "reconnecting" if feed.error else "starting", "reason": reason,
                                           "error": public_playback_message(reason) if feed.error else "The studio is preparing video."})
        self.send_bytes(200, limit_hls_master(manifest.encode(), max_bandwidth), "application/vnd.apple.mpegurl")

    def send_playback_manifest(self, route: str, *, constrained_live: bool = False, max_bandwidth=0, viewer="") -> None:
        match = re.fullmatch(r"(/ffmpeg-stream/([a-zA-Z0-9_-]+)/)index\.m3u8", route)
        if match:
            with LOCK:
                session = FFMPEG_SESSIONS.get(match[2])
                if session and session.get("on_demand"):
                    session["movie_media_at"] = now = time.monotonic()
                    if LIVE_VIEWER_ID.fullmatch(viewer):
                        record_movie_viewer(session, viewer, now)
            if session:
                try:
                    manifest = viewer_encoder_playlist(session, "index.m3u8")
                except OSError:
                    manifest = b""
                if b"#EXT-X-STREAM-INF:" in manifest:
                    # Keep the media playlists at their session URLs. Only the
                    # master is inlined so a tune saves one network round trip.
                    manifest = re.sub(rb"(?m)^index_(economy|balanced|hd)\.m3u8$",
                                      lambda line: match[1].encode() + line[0], manifest)
                    return self.send_bytes(200, limit_hls_master(manifest, max_bandwidth), "application/vnd.apple.mpegurl")
        if constrained_live:
            master = f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=224000\n{route}\n".encode()
            return self.send_bytes(200, master, "application/vnd.apple.mpegurl")
        self.redirect(route)

    def body(self, max_bytes: int = 5_000_000) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > max_bytes:
            raise ValueError("Request is too large.")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        return value

    def do_GET(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            broadcast = BROADCAST_ROUTE.fullmatch(parsed.path)
            self.stream_trace = None
            self.media_request_started = None
            if broadcast or parsed.path.startswith(("/live/", "/vault/", "/ffmpeg-stream/")):
                self.media_request_started = time.monotonic()
            if broadcast or parsed.path.startswith(("/live/", "/vault/")):
                viewer = urllib.parse.parse_qs(parsed.query).get("viewer", [""])[0]
                if LIVE_VIEWER_ID.fullmatch(viewer):
                    phase = "rendition" if broadcast and broadcast[2] else "movie" if parsed.path.startswith("/vault/") else "tune"
                    self.stream_trace = (viewer, phase, time.monotonic())
            if broadcast:
                viewer = urllib.parse.parse_qs(parsed.query).get("viewer", [""])[0]
                return self.broadcast(broadcast[1], broadcast[3] or broadcast[4], broadcast[2],
                                      viewer if LIVE_VIEWER_ID.fullmatch(viewer) else None,
                                      requested_playback_bandwidth(parsed.query))
            if parsed.path == "/api/local-creator":
                settings = config()
                name = settings["creator_profile"].get("name") or settings["plex_profile"].get("server_name")
                self.send_json(200, {"app": APP_NAME, "version": DISCOVERY_VERSION, "name": name, "channel_count": sum(not run_finished(row) for row in channels()),
                                     "creator_id": settings["creator_id"], "public_url": settings["public_url"]})
            elif parsed.path == "/api/studio/activity":
                return self.send_json(200, studio_system_activity(include_playback=True))
            elif parsed.path == "/api/branding/preview":
                query = urllib.parse.parse_qs(parsed.query)
                channel_id = query.get("channel", [""])[0]
                channel = next((row for row in channels() if row["id"] == channel_id), None)
                program = {"title": "Your next programme"}
                if channel is None:
                    channel = {"name": "ChannelForge", "number": "1"}
                elif not run_finished(channel):
                    position = schedule_position(channel)
                    upcoming = position["item"].get("next_program")
                    if upcoming is not None:
                        program = upcoming
                    elif not run_finished(channel, position["stop"]):
                        next_slot = schedule_position(channel, position["stop"])
                        program = next_slot["item"].get("next_program", next_slot["item"])
                style = studio_branding.settings({"style": query.get("style", ["studio"])[0]})
                image = render_channel_branding_card(style, channel, program)
                output = io.BytesIO()
                image.save(output, "JPEG", quality=87)
                return self.send_bytes(200, output.getvalue(), "image/jpeg")
            elif parsed.path == "/api/diagnostics":
                with LOCK:
                    traces = list(STREAM_TRACES)
                return self.send_json(200, {"version": VERSION, "platform": sys.platform, "sharing": SHARING_STATUS,
                                            "registered": bool(REGISTERED_ORIGIN), "heartbeat": HEARTBEAT_STATUS.copy(),
                                            "broadcast": creator_broadcast_diagnostics(), "stream_traces": traces})
            elif parsed.path == "/playback-diagnostics.js":
                return self.send_bytes(200, (WEB_DIR / "playback-diagnostics.js").read_bytes(), "text/javascript")
            elif parsed.path == "/api/status":
                if urllib.parse.parse_qs(parsed.query).get("brief") == ["1"]:
                    return self.send_json(200, {"app": APP_NAME, "version": VERSION, "config": config(True),
                                                "broadcasts": channel_activity()})
                values = channels()
                now_playing = {}
                for channel in values:
                    if run_finished(channel):
                        continue
                    try:
                        position = schedule_position(channel)
                        now_playing[channel["id"]] = {
                            "title": position["item"].get("title", "Untitled"),
                            "start": position["start"].isoformat(), "stop": position["stop"].isoformat(),
                            "offset_ms": position["offset_ms"],
                        }
                    except ValueError:
                        pass
                self.send_json(200, {"app": APP_NAME, "version": VERSION, "config": config(True), "channels": values, "now_playing": now_playing, "assets": asset_manifest(), "branding_styles": studio_branding.style_options(), "tools": plugin_tools(), "backup_count": len(list(BACKUP_DIR.glob("*.json"))), "data_dir": str(DATA_DIR), "server_base": server_base(), "playback_engine": ffmpeg_status(), "broadcasts": channel_activity()})
            elif parsed.path == "/api/plex/libraries":
                self.send_json(200, {"libraries": plex_libraries()})
            elif parsed.path == "/api/plex/items":
                section = urllib.parse.parse_qs(parsed.query).get("section", [""])[0]
                self.send_json(200, {"items": plex_items(section)})
            elif parsed.path == "/api/libraries":
                query = urllib.parse.parse_qs(parsed.query)
                self.send_json(200, {"libraries": all_libraries(include_shared=query.get("scope") != ["connected"])})
            elif parsed.path == "/api/library/items":
                query = urllib.parse.parse_qs(parsed.query)
                section = query.get("section", [""])[0]
                self.send_json(200, {"items": library_items(section, prefer_index=query.get("indexed") == ["1"])})
            elif parsed.path == "/api/local/image":
                query = urllib.parse.parse_qs(parsed.query)
                data, content_type = local_image(query.get("section", [""])[0], query.get("id", [""])[0])
                self.send_bytes(200, data, content_type)
            elif parsed.path == "/api/jellyfin/image":
                query = urllib.parse.parse_qs(parsed.query)
                connection, _ = jellyfin_section(query.get("section", [""])[0])
                image_id = jellyfin_id(query.get("id", [""])[0])
                if not image_id:
                    raise FileNotFoundError
                data, content_type = jellyfin_request(connection, f"/Items/{image_id}/Images/Primary?maxWidth=384", image=True)
                self.send_bytes(200, data, content_type, {"Cache-Control": "private, max-age=300"})
            elif parsed.path == "/api/public/program-art":
                query = urllib.parse.parse_qs(parsed.query)
                image_id = jellyfin_id(query.get("id", [""])[0])
                image_type = query.get("type", [""])[0]
                if not image_id or image_type not in {"Backdrop", "Primary"}:
                    raise FileNotFoundError("Channel artwork was not found.")
                data, content_type = published_jellyfin_art(query.get("channel", [""])[0], image_id, image_type, query.get("section", [""])[0])
                self.send_bytes(200, data, content_type, {"Cache-Control": "public, max-age=300"})
            elif parsed.path == "/api/plex/image":
                query = urllib.parse.parse_qs(parsed.query)
                image_path = query.get("path", [""])[0]
                data, content_type = plex_binary(image_path, query.get("server", [""])[0], query.get("size", [""])[0])
                self.send_bytes(200, data, content_type, {"Cache-Control": "public, max-age=3600"})
            elif parsed.path == "/api/playback/status":
                channel_id = urllib.parse.parse_qs(parsed.query).get("channel", [""])[0]
                error = playback_error(channel_id)
                self.send_json(200, {"ok": not bool(error), "error": error})
            elif parsed.path == "/api/guide":
                offset = max(-24, min(GUIDE_HOURS - 6, int(urllib.parse.parse_qs(parsed.query).get("offset", ["0"])[0])))
                moment = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=offset)
                guide = []
                for channel in channels():
                    if run_finished(channel):
                        continue
                    slots = schedule_window(channel, moment, moment + timedelta(hours=6))
                    guide.append({"channel_id": channel["id"], "name": channel["name"], "number": channel["number"], "logo_asset_id": channel.get("logo_asset_id", ""), "programs": [{"title": slot["item"].get("title", "Untitled"), "year": slot["item"].get("year", ""), "type": slot["item"].get("type", "video"), "start": slot["start"].isoformat(), "stop": slot["stop"].isoformat()} for slot in slots]})
                self.send_json(200, {"generated_at": moment.isoformat(), "channels": guide})
            elif parsed.path == "/api/public/vault":
                query = urllib.parse.parse_qs(parsed.query)
                payload = {"version": 1, "generated_at": datetime.now(timezone.utc).isoformat()}
                if "id" in query:
                    item_id = query["id"][0]
                    record = None
                    if re.fullmatch(r"[a-f0-9]{24}", item_id):
                        record = next((row for row in unique_vault_movies() if row[0] == item_id), None)
                    payload.update(items=[public_movie(self.catalog_base(), *record)] if record else [], next_offset=None)
                elif "limit" in query:
                    limit = max(1, min(100, int(query["limit"][0])))
                    offset = max(0, int(query.get("offset", ["0"])[0]))
                    if "sort" in query:
                        payload.update(search_public_vault(self.catalog_base(), query, offset, limit))
                    else:
                        items = vault_items(self.catalog_base(), offset=offset, limit=limit + 1)
                        payload.update(items=items[:limit], next_offset=offset + limit if len(items) > limit else None)
                else:
                    payload["items"] = vault_items(self.catalog_base())
                self.send_json(200, payload)
            elif parsed.path == "/api/public/movie-art":
                query = urllib.parse.parse_qs(parsed.query)
                item_id = query.get("id", [""])[0]
                image_type = query.get("type", ["Primary"])[0]
                if not re.fullmatch(r"[a-f0-9]{24}", item_id):
                    raise FileNotFoundError
                item = vault_source(item_id)
                artwork = {"Primary": ("thumb", 480), "Backdrop": ("art", 720)}.get(image_type)
                if artwork is None:
                    raise FileNotFoundError
                field, max_width = artwork
                image_id = jellyfin_id(item.get(field))
                if item.get("source") != "jellyfin" or not image_id:
                    raise FileNotFoundError
                source, _ = jellyfin_section(item.get("section", ""))
                image, content_type = jellyfin_request(source, f"/Items/{image_id}/Images/{image_type}?maxWidth={max_width}", image=True)
                self.send_bytes(200, image, content_type, {"Cache-Control": "public, max-age=300"})
            elif parsed.path == "/api/public/channels":
                guide = urllib.parse.parse_qs(parsed.query).get("guide")
                self.send_json(200, public_channel_catalog(self.catalog_base(), extended=guide == ["extended"],
                                                           brief=guide == ["brief"], continuous=True))
            elif parsed.path == "/api/public/origin":
                query = urllib.parse.parse_qs(parsed.query)
                if "leaving" in query:
                    channel_id, viewer = query["leaving"][0], query.get("viewer", [""])[0]
                    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", channel_id) or not LIVE_VIEWER_ID.fullmatch(viewer):
                        return self.send_json(400, {"error": "Invalid live playback ID."})
                    with LOCK:
                        feed = next((feed for feed in LIVE_PLAYOUT.values() if feed.channel_id == channel_id), None)
                    if feed:
                        feed.release_viewer(viewer)
                    return self.send_bytes(204, b"", "text/plain")
                if "viewing" in query:
                    item_id, viewer = query["viewing"][0], query.get("viewer", [""])[0]
                    if not re.fullmatch(r"[a-f0-9]{24}", item_id) or not LIVE_VIEWER_ID.fullmatch(viewer):
                        return self.send_json(400, {"error": "Invalid movie playback ID."})
                    with LOCK:
                        for session in FFMPEG_SESSIONS.values():
                            if (session.get("on_demand") and session["channel_id"] == f"vault-{item_id}"
                                    and viewer in session.get("movie_viewers", {}) and session["process"].poll() in (None, 0)):
                                record_movie_viewer(session, viewer, time.monotonic())
                    return self.send_bytes(204, b"", "text/plain")
                challenge = query.get("sample", [""])[0]
                with LOCK:
                    sample_size = RELAY_MEDIA_CHALLENGES.pop(challenge, 0)
                if sample_size:
                    self.send_bytes(200, RELAY_MEDIA_SAMPLE[:sample_size], "application/octet-stream")
                else:
                    self.send_json(200, {"instance": RELAY_INSTANCE})
            elif parsed.path == "/api/update":
                release = (latest_release())
                release.pop("assets", None)
                self.send_json(200, release)
            elif parsed.path == "/api/update/progress":
                job_id = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
                with LOCK:
                    job = dict(UPDATE_JOBS.get(job_id, {}))
                if not job:
                    raise FileNotFoundError
                self.send_json(200, job)
            elif parsed.path == "/api/plex/signin/status":
                pin_id = int(urllib.parse.parse_qs(parsed.query).get("pin_id", ["0"])[0])
                self.send_json(200, poll_plex_signin(pin_id))
            elif parsed.path.startswith("/exports/"):
                requested = Path(parsed.path).name
                if requested not in {"guide.xml", "channels.m3u"}:
                    raise FileNotFoundError
                path = EXPORT_DIR / requested
                with LOCK:
                    values = channels()
                    if (not path.exists() or time.monotonic() - EXPORT_REFRESHED_AT >= 60
                            or EXPORT_CHANNEL_IDS != tuple(c["id"] for c in values if not run_finished(c))):
                        generate_exports(values)
                    body = path.read_bytes()
                self.send_bytes(200, body, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            elif parsed.path.startswith("/live/") and parsed.path.endswith(".m3u8"):
                channel_id = Path(parsed.path).stem
                viewer = urllib.parse.parse_qs(parsed.query).get("viewer", [""])[0]
                return self.broadcast(channel_id, "master", None, viewer if LIVE_VIEWER_ID.fullmatch(viewer) else None,
                                      requested_playback_bandwidth(parsed.query))
            elif parsed.path.startswith("/vault/") and parsed.path.endswith(".m3u8"):
                item_id = Path(parsed.path).stem
                if not re.fullmatch(r"[a-f0-9]{24}", item_id):
                    raise FileNotFoundError
                public_delivery, _ = self.delivery_profile()
                self.send_playback_manifest(start_vault_session(item_id, public_delivery=public_delivery),
                                            max_bandwidth=requested_playback_bandwidth(parsed.query), viewer=viewer)
            elif parsed.path.startswith("/ffmpeg-stream/"):
                parts = parsed.path.strip("/").split("/")
                if len(parts) != 3 or not re.fullmatch(ENCODER_FILE, parts[2]):
                    raise FileNotFoundError
                with LOCK:
                    session = FFMPEG_SESSIONS.get(parts[1])
                    if session and session.get("capacity_error") and parts[2].endswith(".m3u8"):
                        raise EncoderCapacityError(session["capacity_error"])
                    if session:
                        session["last_access"] = time.time()
                        if (feed := session.get("feed")) and not feed.stopped:
                            feed.last_request = time.monotonic()
                if not session:
                    raise FileNotFoundError
                path = session["directory"] / parts[2]
                if path.suffix == ".m3u8":
                    body = viewer_encoder_playlist(session, parts[2])
                    if session.get("on_demand"):
                        with LOCK:
                            session["movie_media_at"] = time.monotonic()
                    self.send_bytes(200, body, "application/vnd.apple.mpegurl")
                else:
                    body = path.read_bytes()
                    if session.get("on_demand"):
                        with LOCK:
                            session["movie_media_at"] = time.monotonic()
                    headers = {"Cache-Control": "public, max-age=60, immutable"} if isinstance(self, PublicMediaHandler) else None
                    self.send_bytes(200, body, "video/mp2t", headers)
                    if feed := session.get("feed"):
                        feed.record_upload(len(body))
            elif parsed.path.startswith("/hls-proxy/"):
                proxy_id = parsed.path.rsplit("/", 1)[-1]
                with LOCK:
                    target = HLS_PROXY.get(proxy_id)
                    channel_id = HLS_PROXY_CHANNELS.get(proxy_id, "")
                if not target or target[1] < time.time():
                    raise FileNotFoundError
                try:
                    data, content_type, _url, status, content_range = fetch_plex_stream(target[0], self.headers.get("Range", ""), channel_id)
                    if channel_id:
                        with LOCK:
                            PLAYBACK_ERRORS.pop(channel_id, None)
                except Exception as exc:
                    if channel_id:
                        with LOCK:
                            PLAYBACK_ERRORS[channel_id] = (time.monotonic(), sanitized_playback_error(exc))
                    raise
                self.send_bytes(status, data, content_type, {"Content-Range": content_range, "Accept-Ranges": "bytes"})
            elif parsed.path.startswith("/user-assets/"):
                filename = Path(parsed.path).name
                match = next((item for item in asset_manifest() if item.get("filename") == filename), None)
                if not match or (isinstance(self, PublicMediaHandler) and match.get("kind") == "ambience"):
                    raise FileNotFoundError
                self.send_bytes(200, (ASSETS_DIR / filename).read_bytes(), match.get("content_type", "application/octet-stream"))
            elif parsed.path == "/creator-sidebar.png":
                self.send_bytes(200, CREATOR_ICON.read_bytes(), "image/png")
            elif parsed.path in ("/", "/index.html", "/app.js", "/styles.css", "/player.css", "/plex.css", "/overlays.css", "/onboarding.css", "/ux.css", "/update.css", "/tasks.css", "/studio.css", "/desk.css", "/guide.css", "/wizard.css", "/tv-theme.css", "/hls.min.js", "/HLSJS_LICENSE.txt"):
                name = "index.html" if parsed.path in ("/", "/index.html") else parsed.path[1:]
                path = WEB_DIR / name
                body = path.read_bytes()
                self.send_bytes(200, body, mimetypes.guess_type(name)[0] or "text/plain")
            else:
                self.send_json(404, {"error": "Not found"})
        except FileNotFoundError:
            self.send_json(404, {"error": "Not found"})
        except (EncoderCapacityError, MovieEncodingError) as exc:
            self.send_json(503, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def do_POST(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/local/upload":
                query = urllib.parse.parse_qs(parsed.query)
                result = upload_local_media(self, query.get("section", [""])[0], self.headers.get("X-File-Name", ""))
                return self.send_json(201, {"upload": result})
            payload = self.body(CHANNEL_SAVE_BODY_BYTES) if parsed.path in {"/api/channels", "/api/channels/lineup-preview"} else self.body()
            if parsed.path == "/api/channels/lineup-preview":
                draft = clean_channel({**payload, "name": "Lineup preview", "number": "1", "playback_mode": "franchise"})
                self.send_json(200, {"items": [{"title": item["title"], "type": item["type"],
                    "date": item.get("release_date") or item.get("release_year") or (item.get("year", "") if not item.get("series") else "")}
                    for item in scheduled_items(draft)]})
            elif parsed.path == "/api/setup":
                old = config()
                plex_url, pasted_token = normalize_plex_connection(str(payload.get("plex_url", old["plex_url"])), str(payload.get("plex_token", "")))
                token = pasted_token or old["plex_token"]
                plex_settings = {}
                if (plex_url, token) != (old["plex_url"], old["plex_token"]):
                    plex_settings = {"plex_url": plex_url, "plex_token": token, "plex_profile": {}}
                    if token:
                        plex_settings["plex_profile"] = plex_profile(plex_request("/", {**old, **plex_settings}))
                with LOCK:
                    current = config()
                    updated = {
                        **current, **plex_settings,
                        "guide_hours": GUIDE_HOURS,
                        "bind_host": str(payload.get("bind_host", current["bind_host"])).strip() or HOST,
                        "public_url": current["public_url"] if current["managed_relay"] else str(payload.get("public_url", current["public_url"])).strip().rstrip("/"),
                        "directory_url": current["directory_url"] if current["managed_relay"] else str(payload.get("directory_url", current["directory_url"])).strip().rstrip("/"),
                    }
                    atomic_json(CONFIG_FILE, updated)
                generate_exports()
                if plex_settings:
                    sync_plex_movies_async()
                self.send_json(200, {"ok": True, "config": config(True)})
            elif parsed.path == "/api/directory/resume":
                with PUBLISH_LOCK, LOCK:
                    raw = load_json(CONFIG_FILE, {})
                    raw["auto_publish_channels"] = True
                    atomic_json(CONFIG_FILE, raw)
                queue_existing_publication()
                PUBLISH_CHANGED.set()
                self.send_json(200, {"ok": True})
            elif parsed.path == "/api/plex/test":
                old = config()
                plex_url, pasted_token = normalize_plex_connection(str(payload.get("plex_url", old["plex_url"])), str(payload.get("plex_token", "")))
                candidate = {**old, "plex_url": plex_url, "plex_token": pasted_token or old["plex_token"]}
                root = plex_request("/", candidate)
                libraries_root = plex_request("/library/sections", candidate)
                self.send_json(200, {"ok": True, "profile": plex_profile(root), "libraries": len(libraries_root.findall("Directory"))})
            elif parsed.path == "/api/plex/signin/start":
                self.send_json(200, start_plex_signin_external())
            elif parsed.path == "/api/update/prepare":
                self.send_json(200, {"ok": True})
                threading.Timer(0.4, self.server.shutdown).start()
                if APP_WINDOW is not None:
                    threading.Timer(0.7, APP_WINDOW.destroy).start()
            elif parsed.path == "/api/rewind/refresh":
                result = refresh_plex_movie_catalog()
                queue_existing_publication(has_movies=bool(result["movies"]))
                self.send_json(200, result)
            elif parsed.path == "/api/local/libraries":
                self.send_json(200, {"library": save_local_library(str(payload.get("name", "")), str(payload.get("path", "")))})
            elif parsed.path == "/api/jellyfin/connections":
                self.send_json(200, {"connection": connect_jellyfin(payload)})
            elif parsed.path == "/api/directory/publish":
                self.send_json(200, {"creator": publish_studio(payload)})
            elif parsed.path == "/api/channels":
                channel = save_channel(payload)
                preference = payload.get("publish_worldwide")
                pending = queue_channel_publication(channel, preference if isinstance(preference, bool) else None)
                self.send_json(200, {"channel": channel, "publishing_pending": pending})
            elif parsed.path == "/api/channels/order":
                ids = payload.get("ids", [])
                if not isinstance(ids, list):
                    raise ValueError("Channel order must be a list.")
                self.send_json(200, {"channels": reorder_channels([str(item) for item in ids])})
            elif re.fullmatch(r"/api/channels/[a-zA-Z0-9_-]+/(?:reair|refresh|shuffle)", parsed.path):
                channel_id, action = parsed.path.split("/")[-2:]
                channel = update_channel_run(channel_id, action)
                if action == "reair":
                    queue_channel_publication(channel, None)
                self.send_json(200, {"channel": channel})
            elif parsed.path == "/api/exports":
                self.send_json(200, generate_exports())
            elif parsed.path == "/api/assets/generate":
                self.send_json(200, {"asset": generate_asset(payload)})
            elif parsed.path == "/api/assets/upload":
                self.send_json(200, {"asset": upload_asset(payload)})
            elif parsed.path == "/api/assets/ambience":
                self.send_json(200, {"asset": upload_ambience(payload)})
            elif parsed.path == "/api/update/download":
                result = download_verified_update()
                if payload.get("launch") and result["can_launch"]:
                    apply_creator_update(result, self.server.shutdown)
                self.send_json(200, result)
            elif parsed.path == "/api/update/start":
                self.send_json(202, {"job_id": start_update_job(bool(payload.get("launch")), self.server.shutdown)})
            elif parsed.path.startswith("/api/tools/"):
                self.send_json(200, {"result": run_plugin(parsed.path.rsplit("/", 1)[-1], payload)})
            else:
                self.send_json(404, {"error": "Not found"})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def do_PUT(self) -> None:
        try:
            if re.fullmatch(r"/api/channels/[a-f0-9]{16}/branding", self.path):
                channel_id = self.path.split("/")[3]
                branding = self.body(8192)
                with PUBLISH_LOCK:
                    channel = next((row for row in channels() if row["id"] == channel_id), None)
                    if channel is None:
                        raise FileNotFoundError("Channel not found.")
                    saved = save_channel({**channel, "branding": branding}, channel_id)
                return self.send_json(200, {"channel": saved})
            if not self.path.startswith("/api/channels/"):
                return self.send_json(404, {"error": "Not found"})
            channel_id = self.path.rsplit("/", 1)[-1]
            self.send_json(200, {"channel": save_channel(self.body(CHANNEL_SAVE_BODY_BYTES), channel_id)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def do_DELETE(self) -> None:
        try:
            if self.path == "/api/directory/publish":
                with PUBLISH_LOCK:
                    unpublish_creator()
                    stop_public_tunnel()
                    stop_public_media_server()
                return self.send_json(200, {"ok": True})
            if self.path.startswith("/api/local/libraries/"):
                delete_local_library(self.path.rsplit("/", 1)[-1])
                LOCAL_LIBRARY_CACHE.clear()
                return self.send_json(200, {"ok": True})
            if self.path.startswith("/api/jellyfin/connections/"):
                connection_id = self.path.rsplit("/", 1)[-1]
                with LOCK:
                    atomic_json(JELLYFIN_FILE, [row for row in jellyfin_connections() if row["id"] != connection_id])
                return self.send_json(200, {"ok": True})
            if self.path.startswith("/api/assets/"):
                delete_asset(self.path.rsplit("/", 1)[-1])
                return self.send_json(200, {"ok": True})
            if not self.path.startswith("/api/channels/"):
                return self.send_json(404, {"error": "Not found"})
            delete_channel(self.path.rsplit("/", 1)[-1])
            self.send_json(200, {"ok": True})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})


class PublicMediaHandler(Handler):
    """Limit the public HTTPS origin to the paths TV viewers consume."""

    relay_prefix = ""

    def end_headers(self) -> None:
        self.send_header("X-ChannelForge-Version", DISCOVERY_VERSION)
        super().end_headers()

    def catalog_base(self) -> str:
        return server_base()

    def send_json(self, status: int, payload: object) -> None:
        if status == 400:
            payload = {"error": "Published media is temporarily unavailable."}
        super().send_json(status, payload)

    def redirect(self, location: str) -> None:
        if self.relay_prefix and location.startswith("/") and not location.startswith(("//", "/studio/")):
            location = self.relay_prefix + location
        super().redirect(location)

    def send_bytes(self, status: int, body: bytes, content_type: str, extra_headers=None) -> None:
        if self.relay_prefix and status == 200 and "mpegurl" in content_type:
            prefix = self.relay_prefix.encode("ascii")
            body = re.sub(rb'(?m)(^|URI=")/(?!(?:/|studio/))', rb"\1" + prefix + b"/", body)
        super().send_bytes(status, body, content_type, extra_headers)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        self.media_request_started = None
        self.stream_trace = None
        path = parsed.path
        slot = re.match(RELAY_SLOT_PATH + r"(?=/)", path)
        self.relay_prefix = slot[0] if slot else ""
        if slot:
            # Relay maps each exact slot to a distinct loopback tunnel. Keep
            # the original scope for outgoing HLS references and redirects.
            path = path[slot.end():]
            self.path = path + ("?" + parsed.query if parsed.query else "")
        if BROADCAST_ROUTE.fullmatch(path):
            return super().do_GET()
        if path == "/api/plex/image":
            image = urllib.parse.parse_qs(parsed.query).get("path", [""])[0]
            if not re.fullmatch(r"/library/metadata/\d+/(?:thumb|art)(?:/\d+)?", image):
                return self.send_json(404, {"error": "Not found"})
        elif path not in {
            "/api/public/channels", "/api/public/vault", "/api/public/origin", "/api/public/program-art", "/api/public/movie-art", "/api/local/image",
            "/exports/guide.xml", "/exports/channels.m3u",
        } and not re.fullmatch(
            r"/(?:live|vault)/[a-zA-Z0-9_-]+\.m3u8|"
            rf"/ffmpeg-stream/[a-zA-Z0-9_-]+/{ENCODER_FILE}|"
            r"/hls-proxy/[a-zA-Z0-9_-]+|/user-assets/[^/]+",
            path,
        ):
            return self.send_json(404, {"error": "Not found"})
        super().do_GET()

    def reject_write(self) -> None:
        self.close_connection = True
        self.send_json(405, {"error": "Published media is read-only."})

    do_POST = do_PUT = do_DELETE = reject_write


class PublicMediaServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            super().handle_error(request, client_address)

    def server_bind(self):
        if sys.platform == "win32":
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def open_public_media_server(port):
    server = PublicMediaServer((HOST, port), PublicMediaHandler)
    threading.Thread(target=server.serve_forever, daemon=True, name="channel-forge-public-media").start()
    return server


@contextmanager
def stage_media_funnel(origin):
    """Keep the old listener available until the verified Funnel is accepted."""
    global PUBLIC_MEDIA_SERVER
    with PUBLIC_MEDIA_LOCK:
        if SHUTTING_DOWN.is_set():
            raise InterruptedError("Creator is shutting down.")
        previous = PUBLIC_MEDIA_SERVER
        candidate = previous
        if previous is None or previous.server_port != PUBLIC_MEDIA_PORT:
            try:
                candidate = open_public_media_server(PUBLIC_MEDIA_PORT)
            except OSError as exc:
                raise ValueError(f"Studio media port {PUBLIC_MEDIA_PORT} is busy. Close the other listener and try again.") from exc
        try:
            if not relay_reaches_this_creator(origin):
                raise ValueError(f"This Funnel does not reach Studio. Forward port {PUBLIC_MEDIA_PORT} and check its HTTPS URL.")
            if not relay_transfers_media(origin, hd=True):
                raise ValueError("Funnel could not deliver the 720p test payload in time. Check the upload connection and try again.")
            yield
            PUBLIC_MEDIA_SERVER = candidate
        finally:
            if candidate is not previous:
                retired = previous if PUBLIC_MEDIA_SERVER is candidate else candidate
                if retired is not None:
                    retired.shutdown()
                    retired.server_close()


def attach_media_funnel(origin):
    try:
        with stage_media_funnel(origin):
            return True
    except ValueError:
        return False


def start_public_media_server(*, automatic: bool = False) -> None:
    global PUBLIC_MEDIA_SERVER
    with PUBLIC_MEDIA_LOCK:
        if SHUTTING_DOWN.is_set():
            raise InterruptedError("Creator is shutting down.")
        previous = PUBLIC_MEDIA_SERVER
        if previous is None or (not automatic and previous.server_port != PUBLIC_MEDIA_PORT):
            PUBLIC_MEDIA_SERVER = open_public_media_server(0 if automatic else PUBLIC_MEDIA_PORT)
            if previous is not None:
                previous.shutdown()
                previous.server_close()


def stop_public_media_server() -> None:
    global PUBLIC_MEDIA_SERVER
    with PUBLIC_MEDIA_LOCK:
        if PUBLIC_MEDIA_SERVER is not None:
            PUBLIC_MEDIA_SERVER.shutdown()
            PUBLIC_MEDIA_SERVER.server_close()
            PUBLIC_MEDIA_SERVER = None


atexit.register(stop_public_media_server)


def ensure_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(exist_ok=True)
    PLUGINS_DIR.mkdir(exist_ok=True)
    ASSETS_DIR.mkdir(exist_ok=True)
    install_default_assets()
    example = PLUGINS_DIR / "example_summary.py"
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "plugins" / "example_summary.py"
    if not example.exists() and bundled.exists():
        shutil.copy2(bundled, example)
    if not CONFIG_FILE.exists():
        atomic_json(CONFIG_FILE, config())
    else:
        persistent_client_identifier()
    if not CHANNELS_FILE.exists():
        atomic_json(CHANNELS_FILE, [])
    else:
        migrated = channels()
        changed = False
        for channel in migrated:
            if not channel.get("schedule_anchor"):
                channel["schedule_anchor"] = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()
                changed = True
            if "repeat_on_air" not in channel and channel.get("items") and not run_finished(channel):
                channel["repeat_on_air"] = True
                channel["air_until"] = ""
                changed = True
            if channel.get("items") and not isinstance(channel.get("branding"), dict):
                position = None if run_finished(channel) else schedule_position(channel)
                channel["branding"] = studio_branding.settings({"ambience": studio_branding.DEFAULT_AMBIENCE}, enabled=True)
                for field, asset_id in DEFAULT_CHANNEL_ARTWORK.items():
                    if not channel.get(field):
                        channel[field] = asset_id
                if position is not None:
                    # Keep the current slot and offset as the new cards extend the cycle.
                    prefix = timeline_items(channel)[:2 * position["index"]]
                    start = position.get("slot_start", position["start"])
                    channel["schedule_anchor"] = (start - lineup_duration(channel, items=prefix)).isoformat()
                changed = True
            for item in channel.get("items", []):
                if not item.get("key") and item.get("id"):
                    item["key"] = f"/library/metadata/{item['id']}"
                    changed = True
        if changed:
            atomic_json(CHANNELS_FILE, migrated)
    generate_exports()


def open_data_folder() -> None:
    if sys.platform == "win32":
        os.startfile(DATA_DIR)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(DATA_DIR)])
    else:
        subprocess.Popen(["xdg-open", str(DATA_DIR)])


def activate_desktop_window() -> None:
    if APP_WINDOW is not None:
        APP_WINDOW.show()
        APP_WINDOW.restore()


def claim_creator_desktop() -> bool:
    """Reuse the same Creator window, including when the Linux service owns the backend."""
    lock = (DATA_DIR / "desktop.lock").open("a+b")
    lock.seek(0)
    activation_file = DATA_DIR / "desktop-activation.json"
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            info = load_json(activation_file, {})
            try:
                with socket.create_connection((HOST, int(info["port"])), timeout=0.4) as client:
                    client.sendall(info["token"].encode("ascii"))
                    if client.recv(1) == b"1":
                        return False
            except (OSError, KeyError, TypeError, ValueError):
                pass
            time.sleep(0.1)
        return False

    token = secrets.token_hex(32).encode("ascii")
    pending = threading.Event()

    class ActivationRequest(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(0.5)
            try:
                if secrets.compare_digest(token, self.rfile.read(len(token))):
                    pending.set()
                    self.wfile.write(b"1")
            except OSError:
                pass

    server = socketserver.TCPServer((HOST, 0), ActivationRequest)
    atomic_json(activation_file, {"port": server.server_address[1], "token": token.decode("ascii")})
    threading.Thread(target=server.serve_forever, daemon=True, name="creator-desktop-activation").start()

    def run_activation():
        while True:
            pending.wait()
            if APP_WINDOW is None:
                time.sleep(0.1)
                continue
            pending.clear()
            activate_desktop_window()

    threading.Thread(target=run_activation, daemon=True, name="creator-window-activation").start()

    def cleanup_activation():
        server.shutdown()
        server.server_close()
        activation_file.unlink(missing_ok=True)
        lock.close()

    atexit.register(cleanup_activation)
    return True


def restore_public_sharing() -> None:
    global REGISTERED_ORIGIN, SHARING_STATUS
    failures = 0
    relay_misses = 0
    next_relay_check = 0.0
    next_heartbeat = 0.0

    def heartbeat(settings, state, now):
        nonlocal next_heartbeat
        if not settings["creator_profile"].get("heartbeat_supported") or now < next_heartbeat:
            return
        HEARTBEAT_STATUS["last_attempt"] = int(time.time())
        try:
            directory_request("POST", f'/v1/creators/{settings["creator_id"]}/heartbeat',
                              {"state": state}, settings["creator_key"])
        except ValueError:
            HEARTBEAT_STATUS["failures"] += 1
            next_heartbeat = now + min(120, 10 * 2 ** min(4, HEARTBEAT_STATUS["failures"] - 1))
            raise
        HEARTBEAT_STATUS.update(last_success=int(time.time()), failures=0)
        next_heartbeat = now + 60
        try:
            sync_public_channel_numbers(settings)
        except ValueError:
            pass  # Reconcile an interrupted number update on the next heartbeat.

    while not SHUTTING_DOWN.is_set():
        wait_seconds = 20
        PUBLISH_CHANGED.clear()
        if SHUTTING_DOWN.is_set():
            return
        with PUBLISH_LOCK:
            settings = config()
            profile = settings["creator_profile"]
            pending = settings["pending_creator_profile"]
            migrate = (not pending and profile and settings.get("auto_publish_channels")
                       and can_use_managed_relay(settings))
            if migrate:
                pending = {**profile, "managed_relay": True}
            failed_origin = ""
            try:
                if pending and settings["auto_publish_channels"]:
                    if migrate and is_funnel_origin(settings["public_url"]) and PUBLIC_MEDIA_SERVER is None:
                        # Restore the existing endpoint on restart before attempting migration.
                        start_public_media_server()
                    if not REGISTERED_ORIGIN:
                        SHARING_STATUS = "connecting"
                    publish_studio(pending)
                    next_heartbeat = 0.0
                    wait_seconds = 1
                elif profile:
                    saved_origin = settings.get("public_url", "")
                    if not saved_origin or is_legacy_relay(saved_origin):
                        if not REGISTERED_ORIGIN:
                            SHARING_STATUS = "connecting"
                        publish_studio({**profile, "public_url": saved_origin})
                        settings = config()
                        profile = settings["creator_profile"]
                        next_heartbeat = 0.0
                    automatic = not settings.get("community_relay_origin") and (
                        settings["public_tunnel"] or is_funnel_origin(settings.get("public_url", "")))
                    relays = () if automatic else community_relay_candidates(
                        settings.get("community_relay_origin") or settings["public_url"])
                    with LOCK:
                        check_transfer = len(relays) > 1 and any(
                            session["process"].poll() is None for session in FFMPEG_SESSIONS.values())
                    if not automatic:
                        start_public_media_server(automatic=bool(relays))
                    origin = start_public_tunnel() if automatic else settings["public_url"]
                    if relays:
                        previous = COMMUNITY_RELAY
                        connection = accept_community_relay(relays, origin, profile)
                        origin = connection.profile.origin
                        if connection is not previous:
                            relay_misses = 0
                            next_relay_check = time.monotonic() + 15
                            next_heartbeat = 0.0
                            settings = config()
                            profile = settings["creator_profile"]
                    elif COMMUNITY_RELAY is not None:
                        set_community_relay(None)
                    now = time.monotonic()
                    if origin != REGISTERED_ORIGIN:
                        SHARING_STATUS = "connecting"
                        publish_creator(profile, origin=origin)
                        REGISTERED_ORIGIN = origin
                        generate_exports()
                        settings = config()
                        profile = settings["creator_profile"]
                        next_heartbeat = 0.0
                        relay_misses = 0
                        next_relay_check = now + 60
                    elif (automatic or relays) and now >= next_relay_check:
                        healthy = relay_transfers_media(origin) if check_transfer else relay_reaches_this_creator(origin)
                        if healthy:
                            relay_misses = 0
                            next_relay_check = now + (15 if relays else 60)
                        else:
                            relay_misses += 1
                            next_relay_check = now + (5 if relays else 15)
                            if relay_misses >= 2:
                                if len(relays) > 1:
                                    accept_community_relay(relays, origin, profile, unavailable=origin)
                                    settings = config()
                                    profile = settings["creator_profile"]
                                    next_heartbeat = 0.0
                                    relay_misses = 0
                                    next_relay_check = now + 15
                                elif relays:
                                    failed_origin = origin
                                    set_community_relay(None)
                                    accept_community_relay(relays, origin, profile)
                                    relay_misses = 0
                                    next_relay_check = now + 15
                                    next_heartbeat = 0.0
                                else:
                                    stop_public_tunnel()
                                    REGISTERED_ORIGIN = ""
                                    raise ValueError("The public relay stopped responding. Reconnecting.")
                    if relays:
                        wait_seconds = max(1, min(wait_seconds, next_relay_check - now))
                    if profile.get("heartbeat_supported") and now >= next_heartbeat:
                        heartbeat(settings, "reconnecting" if relay_misses else broadcast_health(None), now)
                    SHARING_STATUS = "retrying" if relay_misses or HEARTBEAT_STATUS["failures"] else "live"
                else:
                    REGISTERED_ORIGIN = ""
                    next_heartbeat = 0.0
                    HEARTBEAT_STATUS.update(last_success=0, last_attempt=0, failures=0)
                    relay_misses = 0
                    SHARING_STATUS = "idle" if settings["auto_publish_channels"] else "private"
                failures = 0
            except Exception as exc:
                failures += 1
                if failures == 1:
                    print(f"ChannelForge public sharing could not connect: {sanitized_playback_error(exc)}")
                SHARING_STATUS = "retrying"
                saved = settings.get("public_url", "")
                if (profile and settings.get("auto_publish_channels") and settings.get("creator_id")
                        and settings.get("creator_key") and settings.get("directory_url") == DEFAULT_DIRECTORY_URL
                        and (settings.get("managed_relay") or pending.get("managed_relay") or is_legacy_relay(saved))
                        and not SHUTTING_DOWN.is_set()):
                    previous_origin = REGISTERED_ORIGIN
                    served = previous_origin or saved
                    try:
                        if served and served != failed_origin and relay_reaches_this_creator(served):
                            REGISTERED_ORIGIN = served
                        else:
                            fallback_studio_relay(profile)
                            set_community_relay(None)
                            stop_public_tunnel()
                        if REGISTERED_ORIGIN != previous_origin:
                            next_heartbeat = 0.0
                        if REGISTERED_ORIGIN and not SHUTTING_DOWN.is_set():
                            current = config()
                            if current["creator_profile"].get("heartbeat_supported"):
                                heartbeat(current, broadcast_health(None), time.monotonic())
                            SHARING_STATUS = "live"
                    except (OSError, ValueError, subprocess.SubprocessError):
                        pass
        PUBLISH_CHANGED.wait(min(120, 5 * 2 ** min(max(0, failures - 1), 5)) if failures else wait_seconds)


def shut_down_creator_host(server, *, serving_in_background=True):
    """Release this host's listeners and child processes, preserving published configuration."""
    if ALPHA_REPORTS is not None:
        metrics = studio_system_activity()
        metrics["movie_sessions"] = sum(row["viewers"] for row in movie_activity())
        ALPHA_REPORTS.submit(metrics)
    SHUTTING_DOWN.set()
    PUBLISH_CHANGED.set()
    if REGISTERED_ORIGIN and PUBLISH_LOCK.acquire(timeout=0.25):
        try:
            published = config()
            if published["creator_profile"].get("heartbeat_supported") and published["creator_id"] and published["creator_key"]:
                directory_request("POST", f'/v1/creators/{published["creator_id"]}/heartbeat',
                                  {"state": "offline"}, published["creator_key"], timeout=2)
        except (OSError, ValueError):
            # Existing directories can omit this state. Their origin probes still expire it.
            pass
        finally:
            PUBLISH_LOCK.release()
    try:
        if serving_in_background:
            server.shutdown()
        server.server_close()
    finally:
        try:
            stop_public_media_server()
        finally:
            try:
                stop_ffmpeg_sessions()
            finally:
                try:
                    stop_public_tunnel()
                finally:
                    try:
                        stop_legacy_relay()
                    finally:
                        set_community_relay(None)


def stop_creator_preview(window):
    """Stop only the dashboard decoder. The studio hosts viewers independently."""
    if window.events.loaded.is_set():
        window.run_js("window.stopCreatorPreview?.()")


def run_windows_studio(url, server=None) -> None:
    """Provide a single tray window whether Studio owns or joins the local server."""
    global APP_WINDOW
    import pystray
    import webview
    from PIL import Image

    if server is not None:
        threading.Thread(target=server.serve_forever, daemon=True, name="channel-forge-server").start()
    state = {"quitting": False, "notified": False}
    tray = None

    class TrayControls:
        def hide_studio(self):
            minimize_to_tray()

    window = webview.create_window(APP_TITLE, url=url, js_api=TrayControls(), width=1500, height=920,
                                   min_size=(1100, 720), background_color="#08111f")
    APP_WINDOW = window

    def open_window(_icon=None, _item=None):
        activate_desktop_window()

    def exit_application(_icon=None, _item=None):
        state["quitting"] = True
        if server is not None:
            SHUTTING_DOWN.set()
            PUBLISH_CHANGED.set()
        window.destroy()


    def minimize_to_tray(*_event):
        if state["quitting"] or UPDATE_EXITING:
            return True
        if tray is None or not tray.visible:
            return True
        # The close event runs on the native UI thread. Stop the preview without
        # waiting for WebView2 to return a JavaScript result.
        if window.events.loaded.is_set():
            native = getattr(getattr(window.native, "browser", None), "webview", None)
            if native is not None:
                native.ExecuteScriptAsync("window.stopCreatorPreview?.()")
        window.hide()
        if not state["notified"]:
            state["notified"] = True
            if tray and tray.HAS_NOTIFICATION:
                tray.notify("Studio is in the system tray. Open it from the tray or the Desktop shortcut.")
        return False

    window.events.closing += minimize_to_tray
    icon = CREATOR_ICON
    ico = icon.with_name("channel-forge-creator.ico")
    quit_label = "Exit and stop channels" if server is not None else "Close Studio window"
    with Image.open(icon) as image:
        tray = pystray.Icon(APP_TITLE, image.copy(), APP_TITLE + " · hosting channels", pystray.Menu(
            pystray.MenuItem("Open " + APP_TITLE, open_window, default=True),
            pystray.MenuItem("Hide Studio", minimize_to_tray),
            pystray.MenuItem(quit_label, exit_application),
        ))
    tray.run_detached()
    try:
        webview.start(debug=False, icon=str(ico))
    finally:
        try:
            if server is not None:
                shut_down_creator_host(server)
        finally:
            tray.stop()


def main() -> None:
    global APP_WINDOW, PUBLIC_MEDIA_PORT, ALPHA_REPORTS
    parser = argparse.ArgumentParser(description="Public_ChannelForge standalone TV server")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--public-media-port", type=int, choices=range(1, 65536), metavar="PORT",
                        help="Listen on a separate loopback port for a public HTTPS reverse proxy")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", action="store_true", help="Open the application data directory and exit")
    args = parser.parse_args()
    ensure_files()
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        threading.Thread(target=clean_windows_update_cache, args=(VERSION,), daemon=True).start()
    elif sys.platform.startswith("linux"):
        clean_linux_update_cache(DATA_DIR / "updates", VERSION)
    if args.data_dir:
        open_data_folder()
        return
    if not args.no_browser and not claim_creator_desktop():
        return
    port = args.port or config()["port"]
    bind_host = config()["bind_host"]
    url = f"http://{HOST if bind_host in {'0.0.0.0', '::'} else bind_host}:{port}"
    try:
        server = ThreadingHTTPServer((bind_host, port), Handler)
    except OSError as error:
        # The Linux package can keep Creator hosting through its user service.
        # Opening the desktop icon should attach to that existing service instead
        # of failing with "address already in use" or spawning another copy.
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=2) as response:
                is_creator = response.headers.get("Server", "").startswith(APP_NAME + "/")
        except OSError:
            is_creator = False
        if not is_creator:
            raise SystemExit(f"{APP_TITLE} could not listen on {url}: {error}") from error
        if args.no_browser:
            print(f"{APP_TITLE} is already running at {url}")
            return
        if sys.platform == "win32":
            run_windows_studio(url)
            return
        try:
            import webview
            APP_WINDOW = webview.create_window(APP_TITLE, url=url, width=1500, height=920, min_size=(760, 480), background_color="#08111f")
            start_webview(webview, application_id="public-channelforge",
                          icon=CREATOR_ICON)
        except ImportError:
            webbrowser.open(url)
        return
    print(f"{APP_NAME} {VERSION} is running at {url}")
    print(f"Data directory: {DATA_DIR}")
    from public_diagnostics import PublicDiagnostics
    ALPHA_REPORTS = PublicDiagnostics(DATA_DIR / "diagnostics", "studio", VERSION)
    ALPHA_REPORTS.install_crash_handler()
    if args.public_media_port:
        PUBLIC_MEDIA_PORT = args.public_media_port
        start_public_media_server()
        print(f"Read-only media origin is listening on http://{HOST}:{PUBLIC_MEDIA_PORT}")
    threading.Thread(target=reap_idle_sessions, daemon=True, name="channel-forge-idle-reaper").start()
    queue_existing_publication()
    threading.Thread(target=restore_public_sharing, daemon=True, name="channel-forge-public-sharing").start()
    sync_plex_movies_async()
    if not args.no_browser and sys.platform == "win32":
        run_windows_studio(url, server)
        return
    if not args.no_browser:
        try:
            import webview
            thread = threading.Thread(target=server.serve_forever, daemon=True, name="channel-forge-server")
            thread.start()
            APP_WINDOW = webview.create_window(APP_TITLE, url=url, width=1500, height=920, min_size=(760, 480), background_color="#08111f")
            try:
                start_webview(webview, application_id="public-channelforge",
                              icon=CREATOR_ICON)
            finally:
                shut_down_creator_host(server)
            return
        except ImportError:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shut_down_creator_host(server, serving_in_background=False)


if __name__ == "__main__":
    main()
