"""Shared download cache for the Windows Creator and TV suite."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path


SUITE_SETUP = "ChannelForge_Setup.exe"
LEGACY_SETUPS = (
    "ChannelForge_Creator_Setup.exe", "ChannelForge_TV_Setup.exe", "Public_ChannelForge_Setup.exe",
)
RELEASE_VERSION = r"\d+(?:\.\d+){1,3}"
CACHED_SETUP = re.compile(rf"ChannelForge_Setup-v({RELEASE_VERSION})\.exe", re.IGNORECASE)
OLD_TV_DOWNLOAD = re.compile(rf"ChannelForge_TV-(?:Setup|Portable)-v({RELEASE_VERSION})\.exe", re.IGNORECASE)


def windows_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home()))


def windows_update_cache() -> Path:
    return windows_data() / "ChannelForge" / "updates"


def windows_setup_asset(names, legacy: str) -> str:
    return next(
        (name for name in (SUITE_SETUP, legacy, *LEGACY_SETUPS) if name in names),
        SUITE_SETUP,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def published_hash(checksums: str, asset: str) -> str:
    for line in checksums.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].lstrip("*") == asset:
            if re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                return parts[0].lower()
    raise ValueError("The release checksum could not be verified.")


def verified_windows_setup(directory: Path, version: str, expected: str, download, progress) -> Path:
    if not re.fullmatch(RELEASE_VERSION, version):
        raise ValueError("GitHub reported an invalid updater version.")
    return verified_download(directory / f"ChannelForge_Setup-v{version}.exe", expected, download, progress)


def verified_download(destination: Path, expected: str, download, progress) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)

    def matches_cache() -> bool:
        try:
            return sha256_file(destination) == expected
        except OSError:
            return False

    if matches_cache():
        return destination
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=destination.name + ".", suffix=".part", delete=False) as staging:
        temporary = Path(staging.name)
    try:
        download(temporary)
        size = temporary.stat().st_size
        progress("verifying", size, size)
        if sha256_file(temporary) != expected:
            raise ValueError("The downloaded installer failed SHA-256 verification.")
        # Both apps can check for the same update. Reuse the file if one won the race.
        if not matches_cache():
            try:
                temporary.replace(destination)
            except PermissionError:
                if not matches_cache():
                    raise
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def clean_windows_update_cache(version: str) -> None:
    """Remove superseded setup downloads inside the apps' own caches."""
    if not re.fullmatch(RELEASE_VERSION, version):
        return
    current = tuple(int(part) for part in version.split("."))
    root = windows_data()
    caches = (windows_update_cache(), root / "Public_ChannelForge" / "updates", root / "ChannelForge_TV" / "updates")
    expired = []
    for directory in caches:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            match = CACHED_SETUP.fullmatch(path.name) or OLD_TV_DOWNLOAD.fullmatch(path.name)
            if match and tuple(int(part) for part in match[1].split(".")) <= current:
                expired.append(path)
            elif directory != caches[0] and path.name in (SUITE_SETUP, *LEGACY_SETUPS):
                expired.append(path)
    for attempt in range(6):
        locked = []
        for path in expired:
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                locked.append(path)
            except OSError:
                continue
        if not locked:
            break
        expired = locked
        if attempt < 5:
            time.sleep(1)
