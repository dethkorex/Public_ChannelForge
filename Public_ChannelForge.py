"""Public_ChannelForge: local-first personal channel guide builder."""

from __future__ import annotations

import argparse
import atexit
import base64
import concurrent.futures
import hashlib
import html
import importlib.util
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

from windows_distribution import clean_windows_update_cache, published_hash, verified_windows_setup, windows_setup_asset, windows_update_cache

APP_NAME = "Public_ChannelForge"
VERSION = "3.5.8"
HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_PUBLIC_MEDIA_PORT = 8790
RELAY_HOST_KEY = "localhost.run ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILVqOuSMnyeGDVO1lG6EaG5In/dXABCchhmHKkuRU2s9"
DEFAULT_DIRECTORY_URL = "https://channelforge-directory.fresh-anorak.workers.dev"
OLD_DIRECTORY_URL = "https://directory.channelforge.tv"
MEDIA_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".mpg", ".mpeg", ".wmv", ".flv"}
POSTER_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def app_dir() -> Path:
    override = os.environ.get("PUBLIC_CHANNELFORGE_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / APP_NAME
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "public-channelforge"


DATA_DIR = app_dir()
CONFIG_FILE = DATA_DIR / "config.json"
CHANNELS_FILE = DATA_DIR / "channels.json"
VAULT_CATALOG_FILE = DATA_DIR / "rewind-cinema.json"
EXPORT_DIR = DATA_DIR / "exports"
PLUGINS_DIR = DATA_DIR / "plugins"
ASSETS_DIR = DATA_DIR / "assets"
BACKUP_DIR = DATA_DIR / "backups"
POSTER_CACHE_DIR = DATA_DIR / "poster-cache"
WEB_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "web"
CREATOR_ICON = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / "channel-forge-creator.png"
LOCK = threading.RLock()
# Bound the lock pool while allowing different channels to start in parallel.
ENCODER_START_LOCKS = tuple(threading.Lock() for _ in range(32))
HLS_PROXY: dict[str, tuple[str, float]] = {}
HLS_PROXY_CHANNELS: dict[str, str] = {}
UPDATE_JOBS: dict[str, dict] = {}
PLAYBACK_ERRORS: dict[str, str] = {}
FFMPEG_SESSIONS: dict[str, dict] = {}
LOCAL_LIBRARY_CACHE: dict[str, tuple[float, list[dict]]] = {}
AUDIO_SELECTION_CACHE: dict[str, int] = {}
PLEX_RESOURCE_CACHE: dict[str, object] = {"expires": 0.0, "servers": {}}
STREAM_DIR = DATA_DIR / "streams"
TUNNEL_LOCK = threading.RLock()
TUNNEL_PROCESS: subprocess.Popen | None = None
TUNNEL_URL = ""
TUNNEL_URLS: queue.Queue[str] = queue.Queue()
PUBLISH_LOCK = threading.RLock()
PUBLIC_MEDIA_SERVER: ThreadingHTTPServer | None = None
PUBLIC_MEDIA_PORT = DEFAULT_PUBLIC_MEDIA_PORT
PUBLIC_MEDIA_LOCK = threading.RLock()
APP_WINDOW = None
UPDATE_EXITING = False
DEFAULT_ASSET_THEMES = {
    "kids": ("Kids Club", "#ffea3d", "#ff5ebc", "star"),
    "anime": ("Anime Pulse", "#ff4d7d", "#6de8ff", "anime"),
    "horror": ("Horror Midnight", "#ff334f", "#64118c", "horror"),
    "action": ("Action Blast", "#ffcf27", "#ff4b18", "action"),
    "scifi": ("Sci-Fi Signal", "#45f3ff", "#6d4cff", "planet"),
    "comedy": ("Laugh Track", "#ffe35b", "#3fe09b", "smile"),
    "drama": ("Drama House", "#eab8ff", "#7b3fc6", "drama"),
    "retro": ("Retro Rewind", "#ff8ad8", "#38e7df", "cassette"),
    "classic": ("Classic Television", "#f4c95d", "#d66b4d", "television"),
    "cinema": ("Cinema Premiere", "#f1d18a", "#8f2437", "clapper"),
    "sports": ("Sports Arena", "#7cff6b", "#1473e6", "ball"),
    "news": ("News Desk", "#42a5ff", "#e9f2ff", "globe"),
    "mystery": ("Mystery Files", "#a98bff", "#26365f", "search"),
    "fantasy": ("Fantasy Quest", "#ffd45a", "#9a4de2", "crown"),
    "adventure": ("Adventure Trails", "#ffb44d", "#1bb69d", "mountain"),
    "crime": ("Crime Watch", "#d8e0ed", "#cb3148", "badge"),
    "music": ("Music Video", "#ff54cf", "#674dff", "music"),
    "nature": ("Nature World", "#8cf05c", "#087f73", "leaf"),
    "kitchen": ("Kitchen Club", "#ff9c52", "#e94562", "chef"),
    "holiday": ("Holiday Cheer", "#f5fbff", "#3ac7e5", "snowflake"),
}
GITHUB_REPOSITORY = "dethkorex/Public_ChannelForge"
GITHUB_API_RELEASE = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"


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


def version_tuple(value: str) -> tuple[int, int, int]:
    parts = value.strip().lower().lstrip("v").split(".")
    numbers = []
    for part in parts[:3]:
        digits = "".join(char for char in part if char.isdigit())
        numbers.append(int(digits or 0))
    return tuple((numbers + [0, 0, 0])[:3])


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
    try:
        payload = json.loads(github_bytes(GITHUB_API_RELEASE, 2_000_000))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError("Could not check GitHub for updates.") from exc
    tag = str(payload.get("tag_name", ""))
    assets = [{"name": str(item.get("name", "")), "url": str(item.get("browser_download_url", "")), "size": int(item.get("size", 0) or 0)} for item in payload.get("assets", [])]
    return {"current_version": VERSION, "latest_version": tag.lstrip("v"), "update_available": version_tuple(tag) > version_tuple(VERSION), "release_url": str(payload.get("html_url", "")), "published_at": str(payload.get("published_at", "")), "notes": str(payload.get("body", ""))[:4000], "assets": assets}


def update_asset_name(release: dict) -> tuple[str, str]:
    names = {item["name"] for item in release["assets"]}
    version = release["latest_version"]
    if sys.platform == "win32":
        return windows_setup_asset(names, "ChannelForge_Creator_Setup.exe"), "SHA256SUMS.txt"
    if Path("/etc/arch-release").exists():
        candidate = f"public-channelforge-{version}-1-any.pkg.tar.zst"
    elif Path("/etc/debian_version").exists():
        candidate = f"Public_ChannelForge-{version}-linux-all.deb"
    else:
        candidate = f"ChannelForge-{version}-linux-x64.tar.gz" if f"ChannelForge-{version}-linux-x64.tar.gz" in names else f"Public_ChannelForge-{version}-linux-x64.tar.gz"
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
    if asset_name.endswith(".deb"):
        command = f'sudo apt install "{destination}"'
    elif asset_name.endswith(".pkg.tar.zst"):
        command = f'sudo pacman -U "{destination}"'
    else:
        command = f'Extract "{destination}" and run install.sh'
    progress("complete", destination.stat().st_size, destination.stat().st_size)
    return {"version": release["latest_version"], "asset": asset_name, "path": str(destination), "sha256": actual, "command": command, "can_launch": sys.platform == "win32"}


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
    shutdown()
    if APP_WINDOW is not None:
        APP_WINDOW.destroy()


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
            with LOCK:
                UPDATE_JOBS[job_id].update({"status": "complete", "result": result, "stage": "complete", "percent": 100})
            if launch and result["can_launch"]:
                threading.Timer(0.8, lambda: launch_verified_windows_update(result["path"])).start()
                threading.Timer(1.5, lambda: exit_for_update(shutdown)).start()
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
        "guide_hours": max(1, min(168, int(value.get("guide_hours", 24)))),
        "port": max(1024, min(65535, int(value.get("port", DEFAULT_PORT)))),
        "bind_host": str(value.get("bind_host", HOST)).strip() or HOST,
        "public_url": str(value.get("public_url", "")).strip().rstrip("/"),
        "public_tunnel": bool(value.get("public_tunnel", False)),
        "plex_profile": value.get("plex_profile", {}) if isinstance(value.get("plex_profile", {}), dict) else {},
        "client_identifier": str(value.get("client_identifier", "")) or secrets.token_hex(16),
        "local_libraries": value.get("local_libraries", []) if isinstance(value.get("local_libraries", []), list) else [],
        "directory_url": directory_url,
        "creator_id": "" if legacy_directory else str(value.get("creator_id", "")),
        "creator_key": "" if legacy_directory else str(value.get("creator_key", "")),
        "creator_profile": value.get("creator_profile", {}) if isinstance(value.get("creator_profile", {}), dict) else {},
    }
    if public:
        result["plex_token"] = ""
        result["token_saved"] = bool(value.get("plex_token"))
        result["creator_published"] = bool(result["creator_id"] and result["creator_key"])
        result["creator_key"] = ""
        result["tunnel_active"] = bool(TUNNEL_PROCESS and TUNNEL_PROCESS.poll() is None and TUNNEL_URL)
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


def directory_request(method: str, path: str, payload: dict | None = None, key: str = "") -> dict:
    settings = config()
    base = settings["directory_url"]
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("The Creator Directory must use HTTPS.")
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": f"{APP_NAME}/{VERSION}"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            value = json.loads(response.read())
            return value if isinstance(value, dict) else {}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("error", "")
        except Exception:
            detail = ""
        raise ValueError(detail or f"Creator Directory returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError("Could not reach the hosted Creator Directory.") from exc


def publish_creator(payload: dict) -> dict:
    settings = config()
    public_url = settings["public_url"]
    origin = urllib.parse.urlsplit(public_url)
    if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.path or origin.query or origin.fragment:
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
        "logo_url": logo_url,
        "logo_asset_id": logo_asset_id if selected_logo else "",
    }
    if not profile["name"]:
        raise ValueError("Enter a creator or studio name.")
    if settings["creator_id"] and settings["creator_key"]:
        result = directory_request("PUT", f'/v1/creators/{settings["creator_id"]}', profile, settings["creator_key"])
        creator_id, creator_key = settings["creator_id"], settings["creator_key"]
    else:
        result = directory_request("POST", "/v1/creators", profile)
        creator_id, creator_key = str(result.get("creator", {}).get("id", "")), str(result.get("api_key", ""))
        if not creator_id or not creator_key:
            raise ValueError("The Creator Directory did not return publishing credentials.")
    raw = load_json(CONFIG_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    raw.update({"directory_url": settings["directory_url"], "creator_id": creator_id,
                "creator_key": creator_key, "creator_profile": profile})
    atomic_json(CONFIG_FILE, raw)
    return result.get("creator", {"id": creator_id, **profile})


def unpublish_creator() -> None:
    settings = config()
    if settings["creator_id"] and settings["creator_key"]:
        directory_request("DELETE", f'/v1/creators/{settings["creator_id"]}', None, settings["creator_key"])
    raw = load_json(CONFIG_FILE, {})
    if isinstance(raw, dict):
        raw.update({"creator_id": "", "creator_key": "", "creator_profile": {}})
        atomic_json(CONFIG_FILE, raw)


def publish_studio(payload: dict) -> dict:
    if not str(payload.get("name", "")).strip():
        raise ValueError("Enter a creator or studio name.")
    with PUBLISH_LOCK:
        previous = load_json(CONFIG_FILE, {})
        if not isinstance(previous, dict):
            previous = {}
        raw = previous.copy()
        if payload.get("directory_url"):
            new_directory = str(payload["directory_url"]).strip().rstrip("/")
            if new_directory != str(previous.get("directory_url", DEFAULT_DIRECTORY_URL)).rstrip("/"):
                raw.update({"creator_id": "", "creator_key": ""})
            raw["directory_url"] = new_directory
        manual_origin = str(payload.get("public_url", "")).strip().rstrip("/")
        already_serving = PUBLIC_MEDIA_SERVER is not None
        already_relaying = TUNNEL_PROCESS is not None
        start_public_media_server()
        try:
            origin = manual_origin or start_public_tunnel()
            raw.update({"public_url": origin, "public_tunnel": not manual_origin})
            atomic_json(CONFIG_FILE, raw)
            generate_exports()
            creator = publish_creator(payload)
        except Exception:
            atomic_json(CONFIG_FILE, previous)
            generate_exports()
            if not already_relaying:
                stop_public_tunnel()
            if not already_serving:
                stop_public_media_server()
            raise
        if manual_origin:
            stop_public_tunnel()
        return creator


def channels() -> list[dict]:
    value = load_json(CHANNELS_FILE, [])
    return value if isinstance(value, list) else []


def backup_state(reason: str) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {"created_at": datetime.now(timezone.utc).isoformat(), "reason": reason, "channels": channels(), "assets": asset_manifest()}
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    atomic_json(BACKUP_DIR / name, snapshot)
    for stale in sorted(BACKUP_DIR.glob("*.json"))[:-20]:
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


def theme_icon_svg(icon: str, x: float, y: float, size: float, primary: str, secondary: str) -> str:
    """Return an original, dependency-free SVG mark for a built-in theme."""
    shapes = {
        "star": '<path d="M50 5 61 37 95 38 68 58 77 91 50 72 23 91 32 58 5 38 39 37Z" fill="url(#g)"/><circle cx="32" cy="45" r="5" fill="#08111f"/><circle cx="68" cy="45" r="5" fill="#08111f"/>',
        "anime": '<circle cx="50" cy="50" r="27" fill="url(#g)"/><path d="M50 4v17M50 79v17M4 50h17M79 50h17M18 18l12 12M70 70l12 12M82 18 70 30M30 70 18 82" stroke="currentColor" stroke-width="7"/><path d="M28 53q22-30 44 0-22 27-44 0Z" fill="#08111f"/><circle cx="50" cy="49" r="9" fill="currentColor"/>',
        "horror": '<path d="M17 24q33-24 66 0l-6 48-18 18H41L23 72Z" fill="url(#g)"/><path d="m27 46 18-7-4 18-15-2m47-9-18-7 4 18 15-2M43 70h14l-3 13h-8Z" fill="#08111f"/>',
        "action": '<path d="m57 3-39 53h25L34 97l48-60H58Z" fill="url(#g)"/><path d="M8 20h27M4 32h22M74 69h22M66 81h29" stroke="currentColor" stroke-width="6"/>',
        "planet": '<circle cx="50" cy="49" r="29" fill="url(#g)"/><ellipse cx="50" cy="50" rx="47" ry="17" fill="none" stroke="currentColor" stroke-width="7" transform="rotate(-14 50 50)"/><circle cx="66" cy="34" r="6" fill="#08111f"/>',
        "smile": '<circle cx="50" cy="50" r="42" fill="url(#g)"/><circle cx="35" cy="40" r="6" fill="#08111f"/><circle cx="65" cy="40" r="6" fill="#08111f"/><path d="M25 57q25 30 50 0" fill="none" stroke="#08111f" stroke-width="8" stroke-linecap="round"/>',
        "drama": '<path d="M8 18h39v50Q28 96 8 68Z" fill="url(#g)"/><path d="M53 10h39v58Q73 94 53 68Z" fill="currentColor" opacity=".8"/><circle cx="21" cy="39" r="4" fill="#08111f"/><circle cx="35" cy="39" r="4" fill="#08111f"/><path d="M19 55q9 10 18 0M64 47q9-9 18 0" fill="none" stroke="#08111f" stroke-width="5"/>',
        "cassette": '<rect x="5" y="20" width="90" height="62" rx="8" fill="url(#g)"/><rect x="15" y="29" width="70" height="31" rx="6" fill="#08111f"/><circle cx="34" cy="45" r="10" fill="none" stroke="currentColor" stroke-width="5"/><circle cx="66" cy="45" r="10" fill="none" stroke="currentColor" stroke-width="5"/><path d="m27 70 8-8h30l8 8" fill="none" stroke="#08111f" stroke-width="6"/>',
        "television": '<rect x="8" y="20" width="84" height="65" rx="13" fill="url(#g)"/><rect x="18" y="30" width="54" height="44" rx="8" fill="#08111f"/><circle cx="82" cy="42" r="6" fill="#08111f"/><circle cx="82" cy="61" r="6" fill="#08111f"/><path d="m36 20-15-17m43 17L79 3" stroke="currentColor" stroke-width="6"/>',
        "clapper": '<path d="M8 28h84v58H8Z" fill="url(#g)"/><path d="M5 10h88v25H5Z" fill="currentColor"/><path d="m18 11-10 23m35-23L33 34m35-23L58 34m35-23L83 34" stroke="#08111f" stroke-width="8"/><path d="M25 55h50M25 70h31" stroke="#08111f" stroke-width="6"/>',
        "ball": '<circle cx="50" cy="50" r="44" fill="url(#g)"/><path d="m50 25 17 12-7 20H40l-7-20Zm0 0V7M33 37 15 30m52 7 18-7M40 57 28 78m32-21 12 21" fill="none" stroke="#08111f" stroke-width="6"/>',
        "globe": '<circle cx="50" cy="50" r="43" fill="url(#g)"/><path d="M8 50h84M50 7c26 23 26 63 0 86M50 7c-26 23-26 63 0 86M15 29h70M15 71h70" fill="none" stroke="#08111f" stroke-width="5"/>',
        "search": '<circle cx="42" cy="40" r="28" fill="none" stroke="url(#g)" stroke-width="12"/><path d="m62 61 30 30" stroke="currentColor" stroke-width="14" stroke-linecap="round"/><path d="M31 40h22" stroke="currentColor" stroke-width="5"/>',
        "crown": '<path d="m10 30 20 18 20-35 20 35 20-18-9 55H19Z" fill="url(#g)"/><circle cx="50" cy="66" r="7" fill="#08111f"/>',
        "mountain": '<circle cx="75" cy="25" r="15" fill="currentColor"/><path d="M3 87 35 28l17 29 12-17 33 47Z" fill="url(#g)"/><path d="m24 49 11-21 10 18-9-4Z" fill="#f5f7fb"/>',
        "badge": '<path d="M50 4 89 20v32q0 29-39 44Q11 81 11 52V20Z" fill="url(#g)"/><path d="m50 22 8 17 19 3-14 13 4 19-17-9-17 9 4-19-14-13 19-3Z" fill="#08111f"/>',
        "music": '<path d="M38 19v54q0 15-17 15Q5 88 5 76t19-14q4 0 7 1V12L90 3v55q0 15-17 15-16 0-16-12t19-14q4 0 7 1V25Z" fill="url(#g)"/>',
        "leaf": '<path d="M89 9Q22 10 13 58q-5 28 24 31 48 5 52-80Z" fill="url(#g)"/><path d="M20 84Q45 51 78 25M43 57l-5-24m18 10 21 2" fill="none" stroke="#08111f" stroke-width="6"/>',
        "chef": '<path d="M23 45Q4 42 8 25q4-15 23-10Q40-2 51 13q17-10 25 5 20-1 17 18-2 12-17 11v39H23Z" fill="url(#g)"/><path d="M23 66h53M35 48v15m13-15v15m13-15v15" stroke="#08111f" stroke-width="6"/>',
        "snowflake": '<path d="M50 4v92M10 27l80 46M10 73l80-46M50 4l-9 14m9-14 9 14M10 27l17 1m-17-1 8-15m-8 61 17-1m-17 1 8 15m72-61-17 1m17-1-8-15m8 61-17-1m17 1-8 15M50 96l-9-14m9 14 9-14" stroke="url(#g)" stroke-width="8" stroke-linecap="round"/>',
    }
    shape = shapes.get(icon, shapes["star"])
    return f'<g transform="translate({x-size/2} {y-size/2}) scale({size/100})" color="{primary}">{shape}</g>'


def default_asset_svg(label: str, kind: str, primary: str, secondary: str, icon: str = "star") -> bytes:
    dimensions = {"logo": (1200, 675), "guide-background": (1920, 1080), "corner-bug": (600, 600)}
    width, height = dimensions[kind]
    safe_label = html.escape(label.upper())
    if kind == "guide-background":
        icons = "".join(theme_icon_svg(icon, width*(.12+i*.18), height*(.2+(i%2)*.18), height*.19, primary, secondary) for i in range(5))
        body = f'<path d="M0 {height*.75} Q {width*.28} {height*.45} {width*.55} {height*.7} T {width} {height*.48} V {height} H0Z" fill="url(#g)" opacity=".34"/><g opacity=".16">{icons}</g>{theme_icon_svg(icon,width*.82,height*.57,height*.52,primary,secondary)}'
        title_y, title_size = height * .86, 54
    elif kind == "corner-bug":
        body = f'<circle cx="{width/2}" cy="{height/2}" r="{height*.43}" fill="url(#g)" opacity=".95"/><circle cx="{width/2}" cy="{height/2}" r="{height*.35}" fill="#08111f"/>{theme_icon_svg(icon,width/2,height*.42,height*.34,primary,secondary)}'
        title_y, title_size = height * .72, 34
    else:
        body = f'<rect x="{width*.06}" y="{height*.12}" width="{width*.88}" height="{height*.76}" rx="68" fill="url(#g)"/><rect x="{width*.085}" y="{height*.16}" width="{width*.83}" height="{height*.68}" rx="48" fill="#08111f"/>{theme_icon_svg(icon,width*.29,height*.5,height*.47,primary,secondary)}'
        title_y, title_size = height * .49, max(32, 70-len(label)*2)
    text_x = "62%" if kind == "logo" else "50%"
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{primary}"/><stop offset="1" stop-color="{secondary}"/></linearGradient><filter id="glow"><feGaussianBlur stdDeviation="8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs><rect width="100%" height="100%" fill="#08111f"/><circle cx="82%" cy="18%" r="24%" fill="{secondary}" opacity=".13"/>{body}<text x="{text_x}" y="{title_y}" text-anchor="middle" fill="#f7fbff" font-family="Arial,sans-serif" font-size="{title_size}" font-weight="900" letter-spacing="3" filter="url(#glow)">{safe_label}</text></svg>'''
    return svg.encode("utf-8")


def install_default_assets() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    values = asset_manifest()
    expected_ids = {f"builtin-{slug}-{kind}" for slug in DEFAULT_ASSET_THEMES for kind in ("logo", "guide-background", "corner-bug")}
    retired = [item for item in values if item.get("built_in") and item.get("id") not in expected_ids]
    for item in retired:
        (ASSETS_DIR / Path(str(item.get("filename", ""))).name).unlink(missing_ok=True)
    if retired:
        values = [item for item in values if item not in retired]
    known = {item.get("id"): item for item in values}
    changed = bool(retired)
    for slug, (label, primary, secondary, icon) in DEFAULT_ASSET_THEMES.items():
        for kind in ("logo", "guide-background", "corner-bug"):
            asset_id = f"builtin-{slug}-{kind}"
            filename = f"{asset_id}.svg"
            if asset_id not in known:
                values.append({"id": asset_id, "name": f"{label} {kind.replace('-', ' ').title()}", "kind": kind, "filename": filename, "content_type": "image/svg+xml", "built_in": True, "created_at": "2026-11-19T00:00:00+00:00"})
                changed = True
            elif known[asset_id].get("built_in"):
                expected_name = f"{label} {kind.replace('-', ' ').title()}"
                if known[asset_id].get("name") != expected_name:
                    known[asset_id]["name"] = expected_name
                    changed = True
            path = ASSETS_DIR / filename
            content = default_asset_svg(label, kind, primary, secondary, icon)
            if not path.exists() or path.read_bytes() != content:
                path.write_bytes(content)
    if changed:
        atomic_json(ASSETS_DIR / "manifest.json", values)


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


def plex_binary(path: str, server_id: str = "") -> tuple[bytes, str]:
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Invalid Plex image path.")
    cache_key = hashlib.sha256(f"{server_id}:{path}".encode()).hexdigest()
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
            data = response.read(12_000_000)
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
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


def plex_items(section: str) -> list[dict]:
    server_id, section_id = plex_section_parts(section)
    libraries = plex_libraries(server_id) if server_id else plex_libraries()
    library = next((item for item in libraries if item["key"] == section), None)
    if not library:
        raise ValueError("Plex library was not found.")
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
        result.append({
            "id": rating_key,
            "title": (f'{node.get("grandparentTitle", "")} · S{int(node.get("parentIndex", "0") or 0):02d}E{int(node.get("index", "0") or 0):02d} · {node.get("title", "Untitled")}' if node.get("type") == "episode" else node.get("title", "Untitled")),
            "year": parent_metadata.get("year") or node.get("year", ""),
            "type": node.get("type", "video"),
            "duration_ms": int(node.get("duration", "1800000") or 1800000),
            "key": node.get("key", f"/library/metadata/{rating_key}"),
            "thumb": node.get("thumb", ""),
            "series_thumb": node.get("grandparentThumb", "") or parent_metadata.get("thumb", ""),
            "art": node.get("art", "") or node.get("grandparentArt", "") or parent_metadata.get("art", ""),
            "summary": (node.get("summary", "") or parent_metadata.get("summary", ""))[:600],
            "content_rating": node.get("contentRating", "") or parent_metadata.get("content_rating", ""),
            "rating": node.get("rating", "") or parent_metadata.get("rating", ""),
            "genres": episode_genres or list(parent_metadata.get("genres", [])),
            "series": series_title,
            "season": int(node.get("parentIndex", "0") or 0),
            "episode": int(node.get("index", "0") or 0),
            "part_key": part_node.get("key", "") if part_node is not None else "",
            "container": media_node.get("container", "") if media_node is not None else "",
            "video_codec": media_node.get("videoCodec", "") if media_node is not None else "",
            "audio_codec": media_node.get("audioCodec", "") if media_node is not None else "",
            "audio_stream_index": audio_streams.index(preferred_audio) if preferred_audio is not None else 0,
            "audio_stream_id": preferred_audio.get("id", "") if preferred_audio is not None else "",
            "audio_language": (preferred_audio.get("languageCode", "") or preferred_audio.get("language", "")) if preferred_audio is not None else "",
            "plex_server_id": server_id,
        })
    return result


def refresh_plex_movie_catalog() -> dict:
    """Cache every movie library visible to the signed-in Plex account.

    Plex Home/shared-library grants are account scoped, so Rewind Cinema uses
    the same aggregate library discovery as Channel Builder. A temporarily
    offline shared server is skipped without hiding movies from healthy
    servers, and its credentials are never exposed in the public vault feed.
    """
    libraries = [library for library in all_libraries() if library.get("source") != "local" and library.get("type") == "movie"]
    movies, failed = [], []

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
                    } for item in items)
                except (OSError, ValueError) as exc:
                    failed.append({"library": library.get("title", "Movies"), "error": str(exc)})
    movies.sort(key=lambda item: (str(item.get("title", "")).casefold(), str(item.get("year", ""))))
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "items": movies}
    atomic_json(VAULT_CATALOG_FILE, payload)
    return {
        "movies": len(movies), "libraries": len(libraries),
        "shared_movies": sum(1 for item in movies if item.get("source") == "plex-shared"),
        "failed_libraries": failed, "updated_at": payload["updated_at"],
    }


def cached_plex_movies() -> list[dict]:
    payload = load_json(VAULT_CATALOG_FILE, {})
    items = payload.get("items", []) if isinstance(payload, dict) else []
    return items if isinstance(items, list) else []


def sync_plex_movies_async() -> None:
    if not config().get("plex_token"):
        return
    def worker() -> None:
        try:
            result = refresh_plex_movie_catalog()
            print(f'Rewind Cinema indexed {result["movies"]} Plex movies.')
        except Exception as exc:
            print(f"Rewind Cinema sync failed: {exc}")
    threading.Thread(target=worker, daemon=True, name="channel-forge-rewind-sync").start()


def all_libraries() -> list[dict]:
    local = [{"key": item["key"], "title": item["title"], "type": "local", "source": "local"} for item in local_libraries()]
    result = []
    if config()["plex_token"]:
        try:
            result.extend({**item, "source": "plex"} for item in plex_libraries())
        except ValueError:
            pass
        try:
            shared_servers = plex_resource_servers()
        except ValueError:
            shared_servers = {}
        if shared_servers:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(shared_servers)), thread_name_prefix="plex-shared-library") as pool:
                jobs = {pool.submit(plex_libraries, server_id): server_id for server_id in shared_servers}
                for job in concurrent.futures.as_completed(jobs):
                    try:
                        result.extend({**item, "source": "plex-shared"} for item in job.result())
                    except ValueError:
                        continue
        if not result and not local:
            raise ValueError("No accessible Plex or local libraries were found.")
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
        except ValueError:
            continue
        identity = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:24]
        poster = local_poster(resolved, root)
        title = resolved.stem.replace("_", " ").replace(".", " ").strip()
        result.append({
            "id": f"local-{identity}", "title": title, "year": "", "type": "video",
            "duration_ms": local_duration_ms(resolved), "key": f"local:{identity}",
            "thumb": f"local:{identity}" if poster else "", "summary": str(relative.parent) if relative.parent != Path(".") else "Local media",
            "art": f"local:{identity}" if poster else "",
            "content_rating": "", "rating": "", "genres": [], "series": relative.parts[0] if len(relative.parts) > 1 else "",
            "season": 0, "episode": 0, "part_key": "", "container": resolved.suffix.lstrip("."),
            "video_codec": "", "audio_codec": "", "source": "local", "file_path": str(resolved),
            "audio_stream_index": 0, "audio_stream_id": "", "audio_language": "",
        })
    LOCAL_LIBRARY_CACHE[section] = (time.time(), result)
    return result


def library_items(section: str) -> list[dict]:
    return local_items(section) if section.startswith("local:") else plex_items(section)


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


def clean_channel(payload: dict, existing_id: str = "") -> dict:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("Channel name is required.")
    number = str(payload.get("number", "")).strip()
    if not number:
        raise ValueError("Channel number is required.")
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
            "duration_ms": max(1000, min(86400000, int(item.get("duration_ms", 1800000)))),
            "key": str(item.get("key", f"/library/metadata/{item['id']}"))[:500],
            "thumb": str(item.get("thumb", ""))[:500], "art": str(item.get("art", ""))[:500], "summary": str(item.get("summary", ""))[:600],
            "content_rating": str(item.get("content_rating", ""))[:40], "rating": str(item.get("rating", ""))[:20],
            "genres": [str(value)[:80] for value in item.get("genres", []) if str(value).strip()][:20],
            "series": str(item.get("series", ""))[:200],
            "season": max(0, int(item.get("season", 0) or 0)), "episode": max(0, int(item.get("episode", 0) or 0)),
            "part_key": str(item.get("part_key", ""))[:500], "container": str(item.get("container", ""))[:30],
            "video_codec": str(item.get("video_codec", ""))[:30], "audio_codec": str(item.get("audio_codec", ""))[:30],
            "audio_stream_index": max(0, int(item.get("audio_stream_index", 0) or 0)),
            "audio_stream_id": str(item.get("audio_stream_id", ""))[:40], "audio_language": str(item.get("audio_language", ""))[:40],
            "source": "local" if item.get("source") == "local" else "plex",
            "file_path": str(item.get("file_path", ""))[:4000] if item.get("source") == "local" else "",
            "plex_server_id": str(item.get("plex_server_id", ""))[:300] if item.get("source") != "local" else "",
        })
    if not normalized:
        raise ValueError("The selected titles were invalid.")
    return {
        "id": existing_id or secrets.token_hex(8), "name": name[:100], "number": number[:12],
        "section": section, "items": normalized,
        "logo_asset_id": str(payload.get("logo_asset_id", ""))[:80],
        "background_asset_id": str(payload.get("background_asset_id", ""))[:80],
        "corner_bug_asset_id": str(payload.get("corner_bug_asset_id", ""))[:80],
        "playback_mode": str(payload.get("playback_mode", "ordered")) if str(payload.get("playback_mode", "ordered")) in {"ordered", "shuffle", "shuffle_in_order", "marathon", "franchise"} else "ordered",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "schedule_anchor": str(payload.get("schedule_anchor") or datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()),
    }


def save_channel(payload: dict, channel_id: str = "") -> dict:
    with LOCK:
        backup_state("edit channel" if channel_id else "create channel")
        values = channels()
        if channel_id:
            previous = next((value for value in values if value.get("id") == channel_id), None)
            if previous and not payload.get("schedule_anchor"):
                payload = {**payload, "schedule_anchor": previous.get("schedule_anchor")}
        item = clean_channel(payload, channel_id)
        if channel_id:
            index = next((i for i, value in enumerate(values) if value.get("id") == channel_id), None)
            if index is None:
                raise ValueError("Channel not found.")
            values[index] = item
        else:
            values.append(item)
        atomic_json(CHANNELS_FILE, values)
        generate_exports(values)
        plex_posters = [(str(media.get("thumb", "")), str(media.get("plex_server_id", ""))) for media in item["items"] if media.get("source") != "local" and media.get("thumb")]
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


def schedule_position(channel: dict, moment: datetime | None = None) -> dict:
    """Resolve a deterministic 24/7 channel position for any UTC moment."""
    moment = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    items = scheduled_items(channel)
    if not items:
        raise ValueError("This channel has no scheduled titles.")
    durations = [max(1000, int(item.get("duration_ms", 1_800_000))) for item in items]
    cycle_ms = sum(durations)
    anchor = parse_timestamp(str(channel.get("schedule_anchor", "")))
    elapsed_ms = int((moment - anchor).total_seconds() * 1000)
    cycles, within = divmod(elapsed_ms, cycle_ms)
    cursor = anchor + timedelta(milliseconds=cycles * cycle_ms)
    for index, duration in enumerate(durations):
        if within < duration:
            start = cursor
            stop = start + timedelta(milliseconds=duration)
            return {"item": items[index], "index": index, "start": start, "stop": stop, "offset_ms": within}
        within -= duration
        cursor += timedelta(milliseconds=duration)
    raise RuntimeError("Could not resolve channel schedule.")


def schedule_window(channel: dict, start: datetime, end: datetime) -> list[dict]:
    position = schedule_position(channel, start)
    cursor = position["start"]
    index = position["index"]
    items = scheduled_items(channel)
    result = []
    while cursor < end:
        item = items[index]
        stop = cursor + timedelta(milliseconds=max(1000, int(item.get("duration_ms", 1_800_000))))
        result.append({"item": item, "start": cursor, "stop": stop})
        cursor, index = stop, (index + 1) % len(items)
    return result


FRANCHISE_TIMELINES = {
    "alien": {
        "prometheus": 0, "alien covenant": 1, "alien": 2, "alien earth": 3,
        "aliens": 4, "alien 3": 5, "alien resurrection": 6,
    },
    "it": {"welcome to derry": 0, "it": 1, "it chapter two": 2, "it chapter 2": 2},
}


def normalized_title(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def franchise_family(value: object) -> str:
    title = normalized_title(value)
    if any(token in title for token in ("alien", "prometheus")):
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


def scheduled_items(channel: dict) -> list[dict]:
    """Return a stable lineup for ordered, shuffled, or series-marathon playback."""
    items = list(channel.get("items", []))
    mode = channel.get("playback_mode", "ordered")
    seed = str(channel.get("id", "channel"))
    def item_identity(item: dict) -> str:
        source = str(item.get("plex_server_id") or item.get("file_path") or "")
        return f'{source}:{item.get("id", "")}' if source else str(item.get("id", ""))

    stable_key = lambda item: hashlib.sha256(f"{seed}:{item_identity(item)}".encode()).hexdigest()
    if mode == "shuffle":
        return sorted(items, key=stable_key)
    if mode == "franchise":
        # Build mixed movie/show blocks. A show's episodes stay in strict
        # season/episode order, while movies and shows are placed on one smart
        # franchise timeline. This keeps examples such as Alien -> Alien:
        # Earth -> Aliens and Welcome to Derry -> It -> It Chapter Two intact.
        blocks: dict[str, dict] = {}
        seen_ids: set[str] = set()
        for original, item in enumerate(items):
            item_id = item_identity(item)
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            series = str(item.get("series", "")).strip()
            title = series or str(item.get("title") or item_id)
            block_id = f"show:{series.casefold()}" if series else f"movie:{item_id or original}"
            block = blocks.setdefault(block_id, {"title": title, "year": item.get("year", ""), "original": original, "items": []})
            block["items"].append(item)
            if str(item.get("year", "")).isdigit() and (not str(block["year"]).isdigit() or int(item["year"]) < int(block["year"])):
                block["year"] = item["year"]
        families: dict[str, list[dict]] = {}
        family_order: dict[str, int] = {}
        for block in blocks.values():
            family = franchise_family(block["title"])
            families.setdefault(family, []).append(block)
            family_order[family] = min(family_order.get(family, block["original"]), block["original"])
        result = []
        for family in sorted(families, key=lambda value: family_order[value]):
            ordered = sorted(families[family], key=lambda block: franchise_block_order(family, block["title"], block["year"], block["original"]))
            for block in ordered:
                result.extend(sorted(block["items"], key=lambda value: (int(value.get("season", 0)), int(value.get("episode", 0)), str(value.get("title", "")))))
        return result
    if mode in {"shuffle_in_order", "marathon"}:
        groups: dict[str, list[dict]] = {}
        seen_ids: set[str] = set()
        for item in items:
            item_id = item_identity(item)
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            title = str(item.get("title") or item.get("id"))
            group = str(item.get("series") or title)
            groups.setdefault(group, []).append(item)
        group_names = list(groups)
        if mode == "shuffle_in_order":
            group_names.sort(key=lambda name: hashlib.sha256(f"{seed}:{name}".encode()).hexdigest())
        ordered_groups = {
            name: sorted(groups[name], key=lambda value: (int(value.get("season", 0)), int(value.get("episode", 0)), str(value.get("title", ""))))
            for name in group_names
        }
        if mode == "shuffle_in_order":
            # Rotate between the shuffled shows, advancing each show's episodes
            # in sequence. This keeps variety without scrambling episode order or
            # repeating the same episode/show twice when another show is available.
            return [episode for episode_index in range(max(map(len, ordered_groups.values()), default=0)) for name in group_names for episode in ordered_groups[name][episode_index:episode_index + 1]]
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


def start_public_tunnel() -> str:
    global TUNNEL_PROCESS, TUNNEL_URL, TUNNEL_URLS
    with TUNNEL_LOCK:
        if TUNNEL_PROCESS is None or TUNNEL_PROCESS.poll() is not None:
            bundled = Path(getattr(sys, "_MEIPASS", "")) / "openssh" / "ssh.exe"
            ssh = str(bundled) if getattr(sys, "frozen", False) and bundled.is_file() else shutil.which("ssh")
            if not ssh:
                raise ValueError("Automatic public sharing requires OpenSSH, which was not found on this computer.")
            known_hosts = DATA_DIR / "relay-known-hosts"
            known_hosts.write_text(RELAY_HOST_KEY + "\n", encoding="ascii")
            null = "NUL" if sys.platform == "win32" else "/dev/null"
            process = subprocess.Popen(
                [ssh, "-F", null, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                 "-o", "IdentityFile=none", "-o", "IdentityAgent=none",
                 "-o", "StrictHostKeyChecking=yes", "-o", "HostKeyAlgorithms=ssh-ed25519",
                 "-o", f'UserKnownHostsFile="{known_hosts.as_posix()}"', "-o", f"GlobalKnownHostsFile={null}",
                 "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
                 "-o", "ServerAliveCountMax=2", "-R", f"80:{HOST}:{PUBLIC_MEDIA_PORT}",
                 "nokey@localhost.run", "--", "--output", "json"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
            TUNNEL_PROCESS, TUNNEL_URL = process, ""
            urls: queue.Queue[str] = queue.Queue()
            TUNNEL_URLS = urls

            def read_output() -> None:
                with process.stdout as output:
                    for line in output:
                        address = relay_address(line)
                        if address:
                            urls.put(address)

            threading.Thread(target=read_output, daemon=True, name="channel-forge-relay-output").start()
        deadline = time.monotonic() + 25
        while TUNNEL_PROCESS.poll() is None:
            try:
                TUNNEL_URL = TUNNEL_URLS.get_nowait()
                continue
            except queue.Empty:
                if TUNNEL_URL:
                    return TUNNEL_URL
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.15)
        stop_public_tunnel()
        raise ValueError("Could not open the free media relay. Check the internet connection and try again.")


def stop_public_tunnel() -> None:
    global TUNNEL_PROCESS, TUNNEL_URL
    with TUNNEL_LOCK:
        if TUNNEL_PROCESS and TUNNEL_PROCESS.poll() is None:
            TUNNEL_PROCESS.terminate()
        TUNNEL_PROCESS = None
        TUNNEL_URL = ""


def plex_live_url(channel: dict, moment: datetime | None = None) -> str:
    """Create a Plex universal-transcoder HLS session for the live schedule point."""
    position = schedule_position(channel, moment)
    item = position["item"]
    credentials = plex_credentials(str(item.get("plex_server_id", "")))
    metadata_path = str(item.get("key") or f"/library/metadata/{item['id']}")
    if not metadata_path.startswith("/library/metadata/"):
        metadata_path = f"/library/metadata/{item['id']}"
    parameters = {
        "path": metadata_path,
        "mediaIndex": "0", "partIndex": "0", "protocol": "hls",
        "offset": f"{position['offset_ms'] / 1000:.3f}", "fastSeek": "1",
        # Force a browser-safe H.264/AAC rendition instead of exposing Linux Chrome
        # to an original codec/container it may not support.
        "directPlay": "0", "directStream": "0", "videoCodec": "h264", "audioCodec": "aac",
        "videoQuality": "100", "videoResolution": "1920x1080", "maxVideoBitrate": "20000", "subtitleSize": "100",
        "audioBoost": "100", "location": "lan", "context": "streaming", "copyts": "1", "session": secrets.token_hex(12),
        "X-Plex-Token": credentials["token"], "X-Plex-Product": APP_NAME,
        "X-Plex-Version": VERSION, "X-Plex-Client-Identifier": persistent_client_identifier(),
        "X-Plex-Platform": "Chrome", "X-Plex-Platform-Version": "120.0", "X-Plex-Device": "Linux",
        "X-Plex-Device-Name": APP_NAME, "X-Plex-Model": "hosted",
        "X-Plex-Client-Profile-Extra": "add-transcode-target(type=videoProfile&context=streaming&protocol=hls&container=mpegts&videoCodec=h264&audioCodec=aac)",
    }
    if item.get("audio_stream_id"):
        parameters["audioStreamID"] = str(item["audio_stream_id"])
    return credentials["url"] + "/video/:/transcode/universal/start.m3u8?" + urllib.parse.urlencode(parameters)


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


def preferred_audio_index(item: dict, media_input: str) -> int:
    """Prefer an English audio track, then the file default, then track zero."""
    recorded_language = str(item.get("audio_language", "")).casefold()
    recorded_index = max(0, int(item.get("audio_stream_index", 0) or 0))
    if recorded_language in {"en", "eng"} or recorded_language.startswith("english"):
        return recorded_index
    identity = hashlib.sha256(f'{item.get("source")}:{item.get("id")}:{media_input}'.encode()).hexdigest()
    if identity in AUDIO_SELECTION_CACHE:
        return AUDIO_SELECTION_CACHE[identity]
    try:
        completed = subprocess.run(
            [ffprobe_path(), "-v", "error", "-select_streams", "a", "-show_entries", "stream=index:stream_tags=language:stream_disposition=default", "-of", "json", media_input],
            capture_output=True, text=True, timeout=15, creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        streams = json.loads(completed.stdout or "{}").get("streams", [])
        choice = next((index for index, stream in enumerate(streams) if str(stream.get("tags", {}).get("language", "")).casefold() in {"en", "eng"}), None)
        if choice is None:
            choice = next((index for index, stream in enumerate(streams) if int(stream.get("disposition", {}).get("default", 0) or 0) == 1), None)
        recorded_index = int(choice if choice is not None else recorded_index)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    AUDIO_SELECTION_CACHE[identity] = recorded_index
    return recorded_index


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


def start_ffmpeg_session(channel: dict) -> str:
    """Share the startup and encoder among viewers tuning the same program."""
    channel_id = str(channel["id"])
    lock_index = zlib.crc32(channel_id.encode()) % len(ENCODER_START_LOCKS)
    with ENCODER_START_LOCKS[lock_index]:
        return start_or_reuse_encoder(channel)


def start_or_reuse_encoder(channel: dict) -> str:
    position = schedule_position(channel)
    item = position["item"]
    on_demand = bool(channel.get("on_demand"))
    start_key = "on-demand" if on_demand else position["start"].isoformat()
    identity = hashlib.sha256(f'{channel["id"]}:{item["id"]}:{start_key}'.encode()).hexdigest()[:20]
    with LOCK:
        existing = FFMPEG_SESSIONS.get(identity)
        if existing and existing["process"].poll() in (None, 0) and (existing["directory"] / "index.m3u8").exists():
            existing["last_access"] = time.time()
            return identity
    if item.get("source") == "local":
        media_input = str(local_media_path(item))
    else:
        credentials = plex_credentials(str(item.get("plex_server_id", "")))
        if not credentials["token"]:
            raise ValueError("This channel uses Plex media, but Plex is not connected.")
        media_input = credentials["url"] + plex_part_key(item)
        separator = "&" if "?" in media_input else "?"
        media_input += separator + urllib.parse.urlencode({"X-Plex-Token": credentials["token"]})
    # Keep the previous program's buffered segments available during the handoff.
    directory = STREAM_DIR / f"{identity}-{secrets.token_hex(6)}"
    directory.mkdir(parents=True, exist_ok=True)
    remaining = max(0.1, (position["stop"] - position["start"]).total_seconds() - position["offset_ms"] / 1000)
    input_timing = ["-re"] if on_demand else ["-re", "-ss", f'{position["offset_ms"] / 1000:.3f}']
    output_timing = [] if on_demand else ["-t", f"{remaining:.3f}"]
    hls_options = (["-hls_playlist_type", "event", "-hls_flags", "independent_segments"] if on_demand else
                   ["-hls_list_size", "5", "-hls_flags", "delete_segments+independent_segments"])
    relay_encoding = []
    if config()["public_tunnel"]:
        relay_encoding = ["-vf", "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                          "-b:v", "420k", "-maxrate", "500k", "-bufsize", "500k", "-b:a", "64k"]
    command = [
        ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin",
        *input_timing, "-i", media_input, *output_timing,
        "-map", "0:v:0", "-map", f"0:a:{preferred_audio_index(item, media_input)}?", "-c:v", "libx264", "-preset", "ultrafast",
        "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-force_key_frames", "expr:gte(t,n_forced*1)",
        "-c:a", "aac", "-ac", "2", "-ar", "48000", *relay_encoding,
        "-f", "hls", "-hls_time", "1", *hls_options,
        "-hls_segment_filename", str(directory / "segment%06d.ts"), str(directory / "index.m3u8"),
    ]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=0x08000000 if sys.platform == "win32" else 0)
    with LOCK:
        FFMPEG_SESSIONS[identity] = {"process": process, "directory": directory, "channel_id": channel["id"], "created": time.time(), "last_access": time.time(), "on_demand": on_demand}
    deadline = time.time() + 25
    playlist = directory / "index.m3u8"
    while time.time() < deadline:
        if playlist.exists() and playlist.stat().st_size:
            return identity
        if process.poll() is not None:
            error = (process.stderr.read(4096) if process.stderr else b"").decode("utf-8", "replace").strip()
            raise ValueError(f"Local FFmpeg playback failed: {error or 'the media could not be decoded.'}")
        time.sleep(0.2)
    process.terminate()
    raise ValueError("Local FFmpeg playback did not start within 25 seconds.")


def start_playback_route(channel: dict) -> str:
    """Start a demand-driven, browser-safe H.264/AAC encoder for every source."""
    return f"/ffmpeg-stream/{start_ffmpeg_session(channel)}/index.m3u8"


def stop_ffmpeg_sessions() -> None:
    with LOCK:
        sessions = list(FFMPEG_SESSIONS.values())
        FFMPEG_SESSIONS.clear()
    for session in sessions:
        if session["process"].poll() is None:
            session["process"].terminate()


def reap_idle_sessions_once(now: float | None = None) -> int:
    """Stop encoders whose viewers stopped requesting HLS data."""
    moment = now if now is not None else time.time()
    with LOCK:
        expired = []
        for key, value in FFMPEG_SESSIONS.items():
            # Cached, finished movies can resume without starting Plex again.
            # An active movie encoder times out like live TV when viewers leave.
            last_access = value.get("last_access", value.get("created", moment))
            retention = 600 if value.get("on_demand") and value["process"].poll() == 0 else 60
            if moment - last_access > retention:
                expired.append((key, value))
        for key, _value in expired:
            FFMPEG_SESSIONS.pop(key, None)
    for _key, session in expired:
        process = session["process"]
        if process.poll() is None:
            process.terminate()
        if session.get("directory"):
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                continue
            shutil.rmtree(session["directory"], ignore_errors=True)
    return len(expired)


def reap_idle_sessions() -> None:
    while True:
        time.sleep(15)
        reap_idle_sessions_once()


atexit.register(stop_ffmpeg_sessions)
atexit.register(stop_public_tunnel)


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
    values = values if values is not None else channels()
    settings = config()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    tv = ET.Element("tv", {"generator-info-name": APP_NAME})
    for channel in values:
        node = ET.SubElement(tv, "channel", {"id": f"public-channelforge.{channel['id']}"})
        ET.SubElement(node, "display-name").text = channel["name"]
        ET.SubElement(node, "display-name").text = channel["number"]
        logo = next((item for item in asset_manifest() if item.get("id") == channel.get("logo_asset_id")), None)
        if logo:
            ET.SubElement(node, "icon", {"src": f"{server_base(settings)}/user-assets/{logo['filename']}"})
    start = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=2)
    end = start + timedelta(hours=settings["guide_hours"])
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
        lines.extend([
            f'#EXTINF:-1 tvg-id="public-channelforge.{channel["id"]}" tvg-chno="{html.escape(channel["number"])}"{logo_attribute},{channel["name"]}',
            stream,
        ])
    playlist_path = EXPORT_DIR / "channels.m3u"
    playlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"guide": str(guide_path), "playlist": str(playlist_path), "channels": len(values)}


def vault_item_id(item: dict) -> str:
    identity = f'{item.get("source", "plex")}:{item.get("plex_server_id", "")}:{item.get("id", "")}:{item.get("file_path", "")}'
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


def vault_items() -> list[dict]:
    result, seen = [], set()
    base = server_base()
    sources = [(str(item.get("section", "")), [item]) for item in cached_plex_movies()]
    sources.extend((str(channel.get("section", "")), channel.get("items", [])) for channel in channels())
    for section, items in sources:
        for item in items:
            item_id = vault_item_id(item)
            if item_id in seen:
                continue
            seen.add(item_id)
            thumb = str(item.get("thumb", ""))
            if item.get("source") == "local" and thumb:
                media_section = str(item.get("section") or section)
                poster_url = f"{base}/api/local/image?section={urllib.parse.quote(media_section)}&id={urllib.parse.quote(str(item.get('id', '')))}"
            elif thumb:
                server_id = urllib.parse.quote(str(item.get("plex_server_id", "")))
                poster_url = f"{base}/api/plex/image?path={urllib.parse.quote(thumb)}&server={server_id}"
            else:
                poster_url = ""
            result.append({
                "id": item_id, "title": item.get("title", "Untitled"), "year": item.get("year", ""),
                "type": item.get("type", "video"), "summary": item.get("summary", ""),
                "genres": list(item.get("genres", [])), "content_rating": item.get("content_rating", "") or "Unrated",
                "library": item.get("library", ""),
                "poster_url": poster_url, "stream_url": f"{base}/vault/{item_id}.m3u8",
                "duration_ms": item.get("duration_ms", 1800000),
            })
    return result


def public_channel_catalog() -> dict:
    """Return lightweight guide/status data without starting a media session."""
    base = server_base()
    moment = datetime.now(timezone.utc)
    end = moment + timedelta(hours=3)
    assets = {item.get("id"): item for item in asset_manifest()}
    result = []
    settings = config()
    def image_url(item: dict, section: str, field: str = "thumb") -> str:
        image_path = str(item.get(field, "") or item.get("thumb", ""))
        if not image_path:
            return ""
        if item.get("source") == "local":
            media_section = str(item.get("section") or section)
            return f"{base}/api/local/image?section={urllib.parse.quote(media_section, safe='')}&id={urllib.parse.quote(str(item.get('id', '')), safe='')}"
        return f"{base}/api/plex/image?path={urllib.parse.quote(image_path, safe='')}&server={urllib.parse.quote(str(item.get('plex_server_id', '')), safe='')}"
    for channel in channels():
        try:
            position = schedule_position(channel, moment)
            current = position["item"]
            if current.get("source") == "local":
                online = Path(str(current.get("file_path", ""))).is_file()
            else:
                server_id = str(current.get("plex_server_id", ""))
                online = bool(plex_credentials(server_id).get("token") and plex_credentials(server_id).get("url")) if server_id else bool(settings.get("plex_token") and settings.get("plex_url"))
            with LOCK:
                if PLAYBACK_ERRORS.get(str(channel.get("id", ""))):
                    online = False
            slots = schedule_window(channel, moment, end)
        except (OSError, ValueError, TypeError):
            online, slots = False, []
        logo = assets.get(channel.get("logo_asset_id"))
        result.append({
            "id": str(channel.get("id", "")), "name": str(channel.get("name", "Untitled")),
            "number": str(channel.get("number", "")), "online": online,
            "stream_url": f"{base}/live/{urllib.parse.quote(str(channel.get('id', '')))}.m3u8",
            "logo_url": f"{base}/user-assets/{urllib.parse.quote(str(logo.get('filename', '')))}" if logo else "",
            "programs": [{
                "title": str(slot["item"].get("title", "Untitled")),
                "year": str(slot["item"].get("year", "")), "type": str(slot["item"].get("type", "video")),
                "summary": str(slot["item"].get("summary", ""))[:600],
                "poster_url": image_url(slot["item"], str(channel.get("section", ""))),
                "backdrop_url": image_url(slot["item"], str(channel.get("section", "")), "art"),
                "start": slot["start"].isoformat(), "stop": slot["stop"].isoformat(),
            } for slot in slots],
        })
    return {"version": 1, "generated_at": moment.isoformat(), "channels": result}


def vault_source(item_id: str) -> dict:
    for item in cached_plex_movies():
        if vault_item_id(item) == item_id:
            return item
    for channel in channels():
        for item in channel.get("items", []):
            if vault_item_id(item) == item_id:
                return item
    raise ValueError("Vault title was not found.")


def start_vault_session(item_id: str) -> str:
    item = vault_source(item_id)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    channel = {"id": f"vault-{item_id}", "items": [item], "schedule_anchor": now.isoformat(),
               "playback_mode": "ordered", "on_demand": True}
    return start_playback_route(channel)


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
    server_version = f"{APP_NAME}/{VERSION}"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_bytes(self, status: int, body: bytes, content_type: str, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self' http: https:; media-src 'self' blob: http: https:")
        for name, value in (extra_headers or {}).items():
            if value:
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, payload: object) -> None:
        self.send_bytes(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 5_000_000:
            raise ValueError("Request is too large.")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object.")
        return value

    def do_GET(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/status":
                values = channels()
                now_playing = {}
                for channel in values:
                    try:
                        position = schedule_position(channel)
                        now_playing[channel["id"]] = {
                            "title": position["item"].get("title", "Untitled"),
                            "start": position["start"].isoformat(), "stop": position["stop"].isoformat(),
                            "offset_ms": position["offset_ms"],
                        }
                    except ValueError:
                        pass
                self.send_json(200, {"app": APP_NAME, "version": VERSION, "config": config(True), "channels": values, "now_playing": now_playing, "assets": asset_manifest(), "tools": plugin_tools(), "backup_count": len(list(BACKUP_DIR.glob("*.json"))), "data_dir": str(DATA_DIR), "server_base": server_base(), "playback_engine": ffmpeg_status()})
            elif parsed.path == "/api/plex/libraries":
                self.send_json(200, {"libraries": plex_libraries()})
            elif parsed.path == "/api/plex/items":
                section = urllib.parse.parse_qs(parsed.query).get("section", [""])[0]
                self.send_json(200, {"items": plex_items(section)})
            elif parsed.path == "/api/libraries":
                self.send_json(200, {"libraries": all_libraries()})
            elif parsed.path == "/api/library/items":
                section = urllib.parse.parse_qs(parsed.query).get("section", [""])[0]
                self.send_json(200, {"items": library_items(section)})
            elif parsed.path == "/api/local/image":
                query = urllib.parse.parse_qs(parsed.query)
                data, content_type = local_image(query.get("section", [""])[0], query.get("id", [""])[0])
                self.send_bytes(200, data, content_type)
            elif parsed.path == "/api/plex/image":
                query = urllib.parse.parse_qs(parsed.query)
                image_path = query.get("path", [""])[0]
                data, content_type = plex_binary(image_path, query.get("server", [""])[0])
                self.send_bytes(200, data, content_type)
            elif parsed.path == "/api/playback/status":
                channel_id = urllib.parse.parse_qs(parsed.query).get("channel", [""])[0]
                with LOCK:
                    error = PLAYBACK_ERRORS.get(channel_id, "")
                self.send_json(200, {"ok": not bool(error), "error": error})
            elif parsed.path == "/api/guide":
                offset = max(-24, min(168, int(urllib.parse.parse_qs(parsed.query).get("offset", ["0"])[0])))
                moment = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=offset)
                guide = []
                for channel in channels():
                    slots = schedule_window(channel, moment, moment + timedelta(hours=6))
                    guide.append({"channel_id": channel["id"], "name": channel["name"], "number": channel["number"], "logo_asset_id": channel.get("logo_asset_id", ""), "programs": [{"title": slot["item"].get("title", "Untitled"), "year": slot["item"].get("year", ""), "type": slot["item"].get("type", "video"), "start": slot["start"].isoformat(), "stop": slot["stop"].isoformat()} for slot in slots]})
                self.send_json(200, {"generated_at": moment.isoformat(), "channels": guide})
            elif parsed.path == "/api/public/vault":
                self.send_json(200, {"version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "items": vault_items()})
            elif parsed.path == "/api/public/channels":
                self.send_json(200, public_channel_catalog())
            elif parsed.path == "/api/update":
                release = latest_release()
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
                if not path.exists():
                    generate_exports()
                self.send_bytes(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            elif parsed.path.startswith("/live/") and parsed.path.endswith(".m3u8"):
                channel_id = Path(parsed.path).stem
                channel = next((item for item in channels() if item.get("id") == channel_id), None)
                if not channel:
                    raise FileNotFoundError
                try:
                    route = start_playback_route(channel)
                    with LOCK:
                        PLAYBACK_ERRORS.pop(channel_id, None)
                except Exception as exc:
                    with LOCK:
                        PLAYBACK_ERRORS[channel_id] = sanitized_playback_error(exc)
                    raise
                self.redirect(route)
            elif parsed.path.startswith("/vault/") and parsed.path.endswith(".m3u8"):
                item_id = Path(parsed.path).stem
                if not re.fullmatch(r"[a-f0-9]{24}", item_id):
                    raise FileNotFoundError
                self.redirect(start_vault_session(item_id))
            elif parsed.path.startswith("/ffmpeg-stream/"):
                parts = parsed.path.strip("/").split("/")
                if len(parts) != 3 or not re.fullmatch(r"(?:index\.m3u8|segment\d+\.ts)", parts[2]):
                    raise FileNotFoundError
                with LOCK:
                    session = FFMPEG_SESSIONS.get(parts[1])
                    if session:
                        session["last_access"] = time.time()
                if not session:
                    raise FileNotFoundError
                path = session["directory"] / parts[2]
                if not path.is_file():
                    raise FileNotFoundError
                self.send_bytes(200, path.read_bytes(), "application/vnd.apple.mpegurl" if path.suffix == ".m3u8" else "video/mp2t")
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
                            PLAYBACK_ERRORS[channel_id] = sanitized_playback_error(exc)
                    raise
                self.send_bytes(status, data, content_type, {"Content-Range": content_range, "Accept-Ranges": "bytes"})
            elif parsed.path.startswith("/user-assets/"):
                filename = Path(parsed.path).name
                match = next((item for item in asset_manifest() if item.get("filename") == filename), None)
                if not match:
                    raise FileNotFoundError
                self.send_bytes(200, (ASSETS_DIR / filename).read_bytes(), match.get("content_type", "application/octet-stream"))
            elif parsed.path in ("/", "/index.html", "/app.js", "/styles.css", "/player.css", "/plex.css", "/overlays.css", "/onboarding.css", "/ux.css", "/update.css", "/tasks.css", "/studio.css", "/desk.css", "/guide.css", "/wizard.css", "/tv-theme.css", "/hls.min.js", "/HLSJS_LICENSE.txt"):
                name = "index.html" if parsed.path in ("/", "/index.html") else parsed.path[1:]
                path = WEB_DIR / name
                self.send_bytes(200, path.read_bytes(), mimetypes.guess_type(name)[0] or "text/plain")
            else:
                self.send_json(404, {"error": "Not found"})
        except FileNotFoundError:
            self.send_json(404, {"error": "Not found"})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def do_POST(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/local/upload":
                query = urllib.parse.parse_qs(parsed.query)
                result = upload_local_media(self, query.get("section", [""])[0], self.headers.get("X-File-Name", ""))
                return self.send_json(201, {"upload": result})
            payload = self.body()
            if parsed.path == "/api/setup":
                old = config()
                plex_url, pasted_token = normalize_plex_connection(str(payload.get("plex_url", old["plex_url"])), str(payload.get("plex_token", "")))
                token = pasted_token or old["plex_token"]
                updated = {
                    "plex_url": plex_url,
                    "plex_token": token,
                    "guide_hours": max(1, min(168, int(payload.get("guide_hours", old["guide_hours"])))),
                    "port": old["port"],
                    "bind_host": str(payload.get("bind_host", old["bind_host"])).strip() or HOST,
                    "public_url": str(payload.get("public_url", old["public_url"])).strip().rstrip("/"),
                    "public_tunnel": old.get("public_tunnel", False),
                    "client_identifier": old["client_identifier"],
                    "local_libraries": old["local_libraries"],
                    "directory_url": str(payload.get("directory_url", old["directory_url"])).strip().rstrip("/"),
                    "creator_id": old["creator_id"], "creator_key": old["creator_key"], "creator_profile": old["creator_profile"],
                }
                if token:
                    root = plex_request("/", updated)
                    updated["plex_profile"] = plex_profile(root)
                else:
                    updated["plex_profile"] = {}
                atomic_json(CONFIG_FILE, updated)
                generate_exports()
                sync_plex_movies_async()
                self.send_json(200, {"ok": True, "config": config(True)})
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
                self.send_json(200, refresh_plex_movie_catalog())
            elif parsed.path == "/api/local/libraries":
                self.send_json(200, {"library": save_local_library(str(payload.get("name", "")), str(payload.get("path", "")))})
            elif parsed.path == "/api/directory/publish":
                self.send_json(200, {"creator": publish_studio(payload)})
            elif parsed.path == "/api/channels":
                self.send_json(200, {"channel": save_channel(payload)})
            elif parsed.path == "/api/channels/order":
                ids = payload.get("ids", [])
                if not isinstance(ids, list):
                    raise ValueError("Channel order must be a list.")
                self.send_json(200, {"channels": reorder_channels([str(item) for item in ids])})
            elif parsed.path == "/api/exports":
                self.send_json(200, generate_exports())
            elif parsed.path == "/api/assets/generate":
                self.send_json(200, {"asset": generate_asset(payload)})
            elif parsed.path == "/api/assets/upload":
                self.send_json(200, {"asset": upload_asset(payload)})
            elif parsed.path == "/api/update/download":
                result = download_verified_update()
                self.send_json(200, result)
                if payload.get("launch") and result["can_launch"]:
                    installer = result["path"]
                    threading.Timer(0.8, lambda: launch_verified_windows_update(installer)).start()
                    threading.Timer(1.5, lambda: exit_for_update(self.server.shutdown)).start()
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
            if not self.path.startswith("/api/channels/"):
                return self.send_json(404, {"error": "Not found"})
            channel_id = self.path.rsplit("/", 1)[-1]
            self.send_json(200, {"channel": save_channel(self.body(), channel_id)})
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

    def send_json(self, status: int, payload: object) -> None:
        if status == 400:
            payload = {"error": "Published media is temporarily unavailable."}
        super().send_json(status, payload)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/api/plex/image":
            image = urllib.parse.parse_qs(parsed.query).get("path", [""])[0]
            if not re.fullmatch(r"/library/metadata/\d+/(?:thumb|art)(?:/\d+)?", image):
                return self.send_json(404, {"error": "Not found"})
        elif path not in {
            "/api/public/channels", "/api/public/vault", "/api/local/image",
            "/exports/guide.xml", "/exports/channels.m3u",
        } and not re.fullmatch(
            r"/(?:live|vault)/[a-zA-Z0-9_-]+\.m3u8|"
            r"/ffmpeg-stream/[a-zA-Z0-9_-]+/(?:index\.m3u8|segment\d+\.ts)|"
            r"/hls-proxy/[a-zA-Z0-9_-]+|/user-assets/[^/]+",
            path,
        ):
            return self.send_json(404, {"error": "Not found"})
        super().do_GET()

    def reject_write(self) -> None:
        self.close_connection = True
        self.send_json(405, {"error": "Published media is read-only."})

    do_POST = do_PUT = do_DELETE = reject_write


def start_public_media_server() -> None:
    global PUBLIC_MEDIA_SERVER
    with PUBLIC_MEDIA_LOCK:
        if PUBLIC_MEDIA_SERVER is None:
            server = ThreadingHTTPServer((HOST, PUBLIC_MEDIA_PORT), PublicMediaHandler)
            threading.Thread(target=server.serve_forever, daemon=True, name="channel-forge-public-media").start()
            PUBLIC_MEDIA_SERVER = server


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


def restore_public_sharing() -> None:
    time.sleep(1)
    registered_origin = ""
    while True:
        with PUBLISH_LOCK:
            settings = config()
            profile = settings["creator_profile"]
            if not profile:
                registered_origin = ""
            else:
                try:
                    start_public_media_server()
                    origin = start_public_tunnel() if settings["public_tunnel"] else settings["public_url"]
                    if origin != registered_origin:
                        raw = load_json(CONFIG_FILE, {})
                        raw["public_url"] = origin
                        atomic_json(CONFIG_FILE, raw)
                        generate_exports()
                        publish_creator(profile)
                        registered_origin = origin
                except Exception as exc:
                    print(f"ChannelForge public sharing could not connect: {sanitized_playback_error(exc)}")
        time.sleep(20)


def main() -> None:
    global APP_WINDOW, PUBLIC_MEDIA_PORT
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
    if args.data_dir:
        open_data_folder()
        return
    port = args.port or config()["port"]
    bind_host = config()["bind_host"]
    url = f"http://{HOST if bind_host in {'0.0.0.0', '::'} else bind_host}:{port}"
    try:
        server = ThreadingHTTPServer((bind_host, port), Handler)
    except OSError:
        # The Linux package can keep Creator hosting through its user service.
        # Opening the desktop icon should attach to that existing service instead
        # of failing with "address already in use" or spawning another copy.
        if args.no_browser:
            print(f"ChannelForge Creator is already running at {url}")
            return
        try:
            import webview
            APP_WINDOW = webview.create_window("ChannelForge Creator", url=url, width=1500, height=920, min_size=(1100, 720), background_color="#08111f")
            webview.start(debug=False)
        except ImportError:
            webbrowser.open(url)
        return
    print(f"{APP_NAME} {VERSION} is running at {url}")
    print(f"Data directory: {DATA_DIR}")
    if args.public_media_port:
        PUBLIC_MEDIA_PORT = args.public_media_port
        start_public_media_server()
        print(f"Read-only media origin is listening on http://{HOST}:{PUBLIC_MEDIA_PORT}")
    threading.Thread(target=reap_idle_sessions, daemon=True, name="channel-forge-idle-reaper").start()
    threading.Thread(target=restore_public_sharing, daemon=True, name="channel-forge-public-sharing").start()
    if not args.no_browser and sys.platform == "win32":
        import webview
        import ctypes
        import pystray
        from PIL import Image
        thread = threading.Thread(target=server.serve_forever, daemon=True, name="channel-forge-server")
        thread.start()
        window = webview.create_window("ChannelForge Creator", url=url, width=1500, height=920, min_size=(1100, 720), background_color="#08111f")
        APP_WINDOW = window
        state = {"quitting": False, "notified": False}
        tray: pystray.Icon | None = None

        def open_window(_icon=None, _item=None) -> None:
            window.show()

        def exit_application(_icon=None, _item=None) -> None:
            state["quitting"] = True
            if tray:
                tray.stop()
            window.destroy()

        def minimize_to_tray(*_event) -> bool:
            if state["quitting"] or UPDATE_EXITING:
                return True
            window.hide()
            if not state["notified"]:
                state["notified"] = True
                ctypes.windll.user32.MessageBoxW(0, "ChannelForge Creator is still hosting your channels.\n\nUse its system-tray icon to reopen it or choose Exit to take the server offline.", "ChannelForge Creator is still running", 0x40)
            return False

        window.events.closing += minimize_to_tray
        tray = pystray.Icon("ChannelForge Creator", Image.open(CREATOR_ICON), "ChannelForge Creator — hosting channels", pystray.Menu(
            pystray.MenuItem("Open ChannelForge Creator", open_window, default=True),
            pystray.MenuItem("Exit and stop channels", exit_application),
        ))
        tray.run_detached()
        try:
            webview.start(debug=False)
        finally:
            if tray:
                tray.stop()
            server.shutdown()
            server.server_close()
        return
    if not args.no_browser:
        try:
            import webview
            thread = threading.Thread(target=server.serve_forever, daemon=True, name="channel-forge-server")
            thread.start()
            APP_WINDOW = webview.create_window("ChannelForge Creator", url=url, width=1500, height=920, min_size=(1100, 720), background_color="#08111f")
            try:
                webview.start(debug=False)
            finally:
                server.shutdown()
                server.server_close()
            return
        except ImportError:
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
