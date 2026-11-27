"""Shared download cache for the Windows Creator and TV suite."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path

from release_metadata import version_tuple


SUITE_SETUP = "ChannelForge_Setup.exe"
LEGACY_SETUPS = (
    "ChannelForge_Creator_Setup.exe", "ChannelForge_TV_Setup.exe", "Public_ChannelForge_Setup.exe",
)
RELEASE_VERSION = r"\d+(?:\.\d+){1,3}(?:-(?:alpha|beta|rc)\.\d+)?"
CACHED_SETUP = re.compile(rf"ChannelForge_Setup-v({RELEASE_VERSION})\.exe", re.IGNORECASE)
OLD_TV_DOWNLOAD = re.compile(rf"ChannelForge_TV-(?:Setup|Portable)-v({RELEASE_VERSION})\.exe", re.IGNORECASE)


def relay_process_job():
    """A non-inherited handle makes Windows close relay children with Studio."""
    import ctypes
    from ctypes import wintypes

    class Limits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                    ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", Limits), ("io", ctypes.c_uint64 * 6), ("memory", ctypes.c_size_t * 4)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(job)
        raise error

    def attach(process):
        if not kernel.AssignProcessToJobObject(job, int(process._handle)) and process.poll() is None:
            raise ctypes.WinError(ctypes.get_last_error())

    return attach


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
    current = version_tuple(version)
    root = windows_data()
    caches = (windows_update_cache(), root / "Public_ChannelForge" / "updates", root / "ChannelForge_TV" / "updates")
    expired = []
    for directory in caches:
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            match = CACHED_SETUP.fullmatch(path.name) or OLD_TV_DOWNLOAD.fullmatch(path.name)
            if match and version_tuple(match[1]) <= current:
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
