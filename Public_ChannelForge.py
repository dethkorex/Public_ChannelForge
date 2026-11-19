"""Public_ChannelForge: local-first Plex channel guide builder."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import importlib.util
import json
import mimetypes
import os
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
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

APP_NAME = "Public_ChannelForge"
VERSION = "2.3.0"
HOST = "127.0.0.1"
DEFAULT_PORT = 8787


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
EXPORT_DIR = DATA_DIR / "exports"
PLUGINS_DIR = DATA_DIR / "plugins"
ASSETS_DIR = DATA_DIR / "assets"
WEB_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "web"
LOCK = threading.RLock()
DEFAULT_ASSET_THEMES = {
    "classic": ("Classic TV", "#f4c95d", "#d66b4d"),
    "neon": ("Neon Night", "#42f5e9", "#9b5cff"),
    "cinema": ("Cinema", "#f1d18a", "#6b1d2c"),
    "kids": ("Kids Pop", "#ff6fb5", "#4dd9ff"),
    "sports": ("Sports Arena", "#7cff6b", "#1473e6"),
    "news": ("News Desk", "#42a5ff", "#e9f2ff"),
    "spooky": ("Spooky", "#c875ff", "#ff7b4d"),
    "minimal": ("Minimal", "#ffffff", "#6f7f95"),
}
GITHUB_REPOSITORY = "dethkorex/Public_ChannelForge"
GITHUB_API_RELEASE = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


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
        return "Public_ChannelForge_Setup.exe", "SHA256SUMS.txt"
    if Path("/etc/arch-release").exists():
        candidate = f"public-channelforge-{version}-1-any.pkg.tar.zst"
    elif Path("/etc/debian_version").exists():
        candidate = f"Public_ChannelForge-{version}-linux-all.deb"
    else:
        candidate = f"Public_ChannelForge-{version}-linux-x64.tar.gz"
    if candidate not in names:
        raise ValueError("The latest release does not contain an installer for this platform.")
    return candidate, "SHA256SUMS-Linux.txt"


def download_verified_update() -> dict:
    release = latest_release()
    if not release["update_available"]:
        raise ValueError("Public ChannelForge is already up to date.")
    asset_name, checksum_name = update_asset_name(release)
    by_name = {item["name"]: item for item in release["assets"]}
    if asset_name not in by_name or checksum_name not in by_name:
        raise ValueError("The release is missing its installer or checksum file.")
    checksum_text = github_bytes(by_name[checksum_name]["url"], 1_000_000).decode("utf-8", "replace")
    expected = next((line.split()[0].lower() for line in checksum_text.splitlines() if line.strip().endswith(asset_name)), "")
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError("The release checksum could not be verified.")
    data = github_bytes(by_name[asset_name]["url"], 150_000_000)
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError("The downloaded installer failed SHA-256 verification.")
    update_dir = DATA_DIR / "updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    destination = update_dir / asset_name
    destination.write_bytes(data)
    if sys.platform == "win32":
        command = f'Run the verified installer: "{destination}"'
    elif asset_name.endswith(".deb"):
        command = f'sudo apt install "{destination}"'
    elif asset_name.endswith(".pkg.tar.zst"):
        command = f'sudo pacman -U "{destination}"'
    else:
        command = f'Extract "{destination}" and run install.sh'
    return {"version": release["latest_version"], "asset": asset_name, "path": str(destination), "sha256": actual, "command": command, "can_launch": sys.platform == "win32"}


def config(public: bool = False) -> dict:
    value = load_json(CONFIG_FILE, {})
    if not isinstance(value, dict):
        value = {}
    result = {
        "plex_url": str(value.get("plex_url", "http://127.0.0.1:32400")).rstrip("/"),
        "plex_token": str(value.get("plex_token", "")),
        "guide_hours": max(1, min(168, int(value.get("guide_hours", 24)))),
        "port": max(1024, min(65535, int(value.get("port", DEFAULT_PORT)))),
        "bind_host": str(value.get("bind_host", HOST)).strip() or HOST,
        "public_url": str(value.get("public_url", "")).strip().rstrip("/"),
        "plex_profile": value.get("plex_profile", {}) if isinstance(value.get("plex_profile", {}), dict) else {},
    }
    if public:
        result["plex_token"] = ""
        result["token_saved"] = bool(value.get("plex_token"))
    return result


def channels() -> list[dict]:
    value = load_json(CHANNELS_FILE, [])
    return value if isinstance(value, list) else []


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


def default_asset_svg(label: str, kind: str, primary: str, secondary: str) -> bytes:
    dimensions = {"logo": (1200, 675), "guide-background": (1920, 1080), "corner-bug": (600, 600)}
    width, height = dimensions[kind]
    safe_label = html.escape(label.upper())
    if kind == "guide-background":
        body = f'<path d="M0 {height*.72} Q {width*.28} {height*.48} {width*.52} {height*.7} T {width} {height*.52} V {height} H0Z" fill="url(#g)" opacity=".28"/><g opacity=".18">' + "".join(f'<circle cx="{width*(.12+i*.15)}" cy="{height*(.18+(i%2)*.14)}" r="{height*.12}" fill="none" stroke="{primary}" stroke-width="6"/>' for i in range(6)) + "</g>"
        title_y, title_size = height * .82, 46
    elif kind == "corner-bug":
        body = f'<circle cx="{width/2}" cy="{height/2}" r="{height*.39}" fill="url(#g)" opacity=".92"/><circle cx="{width/2}" cy="{height/2}" r="{height*.31}" fill="#08111f"/>'
        title_y, title_size = height * .53, 42
    else:
        body = f'<rect x="{width*.08}" y="{height*.14}" width="{width*.84}" height="{height*.72}" rx="64" fill="url(#g)" opacity=".9"/><rect x="{width*.105}" y="{height*.18}" width="{width*.79}" height="{height*.64}" rx="48" fill="#08111f"/>'
        title_y, title_size = height * .53, 64
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{primary}"/><stop offset="1" stop-color="{secondary}"/></linearGradient></defs><rect width="100%" height="100%" fill="#08111f"/>{body}<text x="50%" y="{title_y}" text-anchor="middle" fill="{primary}" font-family="Arial,sans-serif" font-size="{title_size}" font-weight="800" letter-spacing="5">{safe_label}</text></svg>'''
    return svg.encode("utf-8")


def install_default_assets() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    values = asset_manifest()
    known = {item.get("id") for item in values}
    changed = False
    for slug, (label, primary, secondary) in DEFAULT_ASSET_THEMES.items():
        for kind in ("logo", "guide-background", "corner-bug"):
            asset_id = f"builtin-{slug}-{kind}"
            filename = f"{asset_id}.svg"
            if asset_id not in known:
                values.append({"id": asset_id, "name": f"{label} {kind.replace('-', ' ').title()}", "kind": kind, "filename": filename, "content_type": "image/svg+xml", "built_in": True, "created_at": "2026-11-19T00:00:00+00:00"})
                changed = True
            path = ASSETS_DIR / filename
            if not path.exists():
                path.write_bytes(default_asset_svg(label, kind, primary, secondary))
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
        with urllib.request.urlopen(request, timeout=12) as response:
            return ET.fromstring(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError("Plex rejected the token. Check the server URL and token.") from exc
        raise ValueError(f"Plex returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f"Could not reach Plex at {settings['plex_url']}.") from exc
    except ET.ParseError as exc:
        raise ValueError("Plex returned an unexpected response.") from exc


def plex_binary(path: str) -> tuple[bytes, str]:
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Invalid Plex image path.")
    settings = config()
    if not settings["plex_token"]:
        raise ValueError("Enter a Plex token in Setup first.")
    separator = "&" if "?" in path else "?"
    url = settings["plex_url"] + path + separator + urllib.parse.urlencode({"X-Plex-Token": settings["plex_token"]})
    request = urllib.request.Request(url, headers={"Accept": "image/*", "X-Plex-Product": APP_NAME})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read(12_000_000)
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                raise ValueError("Plex did not return an image.")
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


def plex_libraries() -> list[dict]:
    root = plex_request("/library/sections")
    return [
        {"key": node.get("key", ""), "title": node.get("title", "Untitled"), "type": node.get("type", "unknown"), "uuid": node.get("uuid", ""), "thumb": node.get("thumb", "")}
        for node in root.findall("Directory") if node.get("key")
    ]


def plex_items(section: str) -> list[dict]:
    if not section.isdigit():
        raise ValueError("Invalid Plex library section.")
    root = plex_request(f"/library/sections/{section}/all")
    result = []
    for node in list(root):
        rating_key = node.get("ratingKey")
        if not rating_key:
            continue
        result.append({
            "id": rating_key,
            "title": node.get("title", "Untitled"),
            "year": node.get("year", ""),
            "type": node.get("type", "video"),
            "duration_ms": int(node.get("duration", "1800000") or 1800000),
            "key": node.get("key", f"/library/metadata/{rating_key}"),
            "thumb": node.get("thumb", ""),
            "summary": node.get("summary", "")[:600],
            "content_rating": node.get("contentRating", ""),
            "rating": node.get("rating", ""),
        })
    return result


def clean_channel(payload: dict, existing_id: str = "") -> dict:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("Channel name is required.")
    number = str(payload.get("number", "")).strip()
    if not number:
        raise ValueError("Channel number is required.")
    items = payload.get("items", [])
    if not isinstance(items, list) or not items:
        raise ValueError("Select at least one Plex title.")
    normalized = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            continue
        normalized.append({
            "id": str(item["id"]), "title": str(item.get("title", "Untitled"))[:300],
            "year": str(item.get("year", ""))[:10], "type": str(item.get("type", "video"))[:30],
            "duration_ms": max(60000, min(86400000, int(item.get("duration_ms", 1800000)))),
            "key": str(item.get("key", f"/library/metadata/{item['id']}"))[:500],
            "thumb": str(item.get("thumb", ""))[:500], "summary": str(item.get("summary", ""))[:600],
            "content_rating": str(item.get("content_rating", ""))[:40], "rating": str(item.get("rating", ""))[:20],
        })
    if not normalized:
        raise ValueError("The selected titles were invalid.")
    return {
        "id": existing_id or secrets.token_hex(8), "name": name[:100], "number": number[:12],
        "section": str(payload.get("section", ""))[:20], "items": normalized,
        "logo_asset_id": str(payload.get("logo_asset_id", ""))[:80],
        "background_asset_id": str(payload.get("background_asset_id", ""))[:80],
        "corner_bug_asset_id": str(payload.get("corner_bug_asset_id", ""))[:80],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "schedule_anchor": str(payload.get("schedule_anchor") or datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()),
    }


def save_channel(payload: dict, channel_id: str = "") -> dict:
    with LOCK:
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
        return item


def delete_channel(channel_id: str) -> None:
    with LOCK:
        values = channels()
        updated = [item for item in values if item.get("id") != channel_id]
        if len(values) == len(updated):
            raise ValueError("Channel not found.")
        atomic_json(CHANNELS_FILE, updated)
        generate_exports(updated)


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
    items = channel.get("items", [])
    if not items:
        raise ValueError("This channel has no scheduled titles.")
    durations = [max(60_000, int(item.get("duration_ms", 1_800_000))) for item in items]
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
    items = channel["items"]
    result = []
    while cursor < end:
        item = items[index]
        stop = cursor + timedelta(milliseconds=max(60_000, int(item.get("duration_ms", 1_800_000))))
        result.append({"item": item, "start": cursor, "stop": stop})
        cursor, index = stop, (index + 1) % len(items)
    return result


def server_base(settings: dict | None = None) -> str:
    settings = settings or config()
    if settings.get("public_url"):
        return settings["public_url"]
    host = settings.get("bind_host", HOST)
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings['port']}"


def plex_live_url(channel: dict, moment: datetime | None = None) -> str:
    """Create a Plex universal-transcoder HLS session for the live schedule point."""
    settings = config()
    position = schedule_position(channel, moment)
    item = position["item"]
    metadata_path = str(item.get("key") or f"/library/metadata/{item['id']}")
    if not metadata_path.startswith("/library/metadata/"):
        metadata_path = f"/library/metadata/{item['id']}"
    parameters = {
        "path": metadata_path,
        "mediaIndex": "0", "partIndex": "0", "protocol": "hls",
        "offset": f"{position['offset_ms'] / 1000:.3f}", "fastSeek": "1",
        "directPlay": "1", "directStream": "1", "subtitleSize": "100",
        "audioBoost": "100", "location": "lan", "session": secrets.token_hex(12),
        "X-Plex-Token": settings["plex_token"], "X-Plex-Product": APP_NAME,
        "X-Plex-Version": VERSION, "X-Plex-Client-Identifier": "public-channelforge",
    }
    return settings["plex_url"] + "/video/:/transcode/universal/start.m3u8?" + urllib.parse.urlencode(parameters)


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
        lines.extend([
            f'#EXTINF:-1 tvg-id="public-channelforge.{channel["id"]}" tvg-chno="{html.escape(channel["number"])}",{channel["name"]}',
            stream,
        ])
    playlist_path = EXPORT_DIR / "channels.m3u"
    playlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"guide": str(guide_path), "playlist": str(playlist_path), "channels": len(values)}


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

    def send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self' http: https:; media-src 'self' blob: http: https:")
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
                self.send_json(200, {"app": APP_NAME, "version": VERSION, "config": config(True), "channels": values, "now_playing": now_playing, "assets": asset_manifest(), "tools": plugin_tools(), "data_dir": str(DATA_DIR), "server_base": server_base()})
            elif parsed.path == "/api/plex/libraries":
                self.send_json(200, {"libraries": plex_libraries()})
            elif parsed.path == "/api/plex/items":
                section = urllib.parse.parse_qs(parsed.query).get("section", [""])[0]
                self.send_json(200, {"items": plex_items(section)})
            elif parsed.path == "/api/plex/image":
                image_path = urllib.parse.parse_qs(parsed.query).get("path", [""])[0]
                data, content_type = plex_binary(image_path)
                self.send_bytes(200, data, content_type)
            elif parsed.path == "/api/guide":
                moment = datetime.now(timezone.utc)
                guide = []
                for channel in channels():
                    slots = schedule_window(channel, moment, moment + timedelta(hours=6))
                    guide.append({"channel_id": channel["id"], "name": channel["name"], "number": channel["number"], "logo_asset_id": channel.get("logo_asset_id", ""), "programs": [{"title": slot["item"].get("title", "Untitled"), "year": slot["item"].get("year", ""), "type": slot["item"].get("type", "video"), "start": slot["start"].isoformat(), "stop": slot["stop"].isoformat()} for slot in slots]})
                self.send_json(200, {"generated_at": moment.isoformat(), "channels": guide})
            elif parsed.path == "/api/update":
                release = latest_release()
                release.pop("assets", None)
                self.send_json(200, release)
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
                if not config()["plex_token"]:
                    raise ValueError("Plex is not connected.")
                self.redirect(plex_live_url(channel))
            elif parsed.path.startswith("/user-assets/"):
                filename = Path(parsed.path).name
                match = next((item for item in asset_manifest() if item.get("filename") == filename), None)
                if not match:
                    raise FileNotFoundError
                self.send_bytes(200, (ASSETS_DIR / filename).read_bytes(), match.get("content_type", "application/octet-stream"))
            elif parsed.path in ("/", "/index.html", "/app.js", "/styles.css", "/player.css", "/plex.css", "/overlays.css", "/hls.min.js", "/HLSJS_LICENSE.txt"):
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
                }
                root = plex_request("/", updated)
                updated["plex_profile"] = plex_profile(root)
                atomic_json(CONFIG_FILE, updated)
                generate_exports()
                self.send_json(200, {"ok": True, "config": config(True)})
            elif parsed.path == "/api/channels":
                self.send_json(200, {"channel": save_channel(payload)})
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
                    threading.Timer(0.8, lambda: subprocess.Popen([installer], cwd=str(Path(installer).parent))).start()
                    threading.Timer(2.0, self.server.shutdown).start()
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
            if self.path.startswith("/api/assets/"):
                delete_asset(self.path.rsplit("/", 1)[-1])
                return self.send_json(200, {"ok": True})
            if not self.path.startswith("/api/channels/"):
                return self.send_json(404, {"error": "Not found"})
            delete_channel(self.path.rsplit("/", 1)[-1])
            self.send_json(200, {"ok": True})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Public_ChannelForge standalone TV server")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", action="store_true", help="Open the application data directory and exit")
    args = parser.parse_args()
    ensure_files()
    if args.data_dir:
        open_data_folder()
        return
    port = args.port or config()["port"]
    bind_host = config()["bind_host"]
    server = ThreadingHTTPServer((bind_host, port), Handler)
    url = f"http://{HOST if bind_host in {'0.0.0.0', '::'} else bind_host}:{port}"
    print(f"{APP_NAME} {VERSION} is running at {url}")
    print(f"Data directory: {DATA_DIR}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
