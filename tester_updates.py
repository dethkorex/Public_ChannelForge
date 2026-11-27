"""Manual, verified updates for the private desktop Tester suite."""
from __future__ import annotations

import atexit
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request

from tester_diagnostics import report_address

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WINDOWS = sys.platform == "win32"
INSTALL_MARKER = "ChannelForge-Tester-install.json"
ARTIFACT = "ChannelForgeTV-Tester-Windows.exe" if WINDOWS else "ChannelForgeTV-Tester-Linux.tar.gz"
MAX_PACKAGE_BYTES = 400 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def install_root() -> Path:
    path = Path(sys.executable if WINDOWS and getattr(sys, "frozen", False) else __file__).resolve().parent
    for entry in reversed((path, *path.parents)):
        if (entry / INSTALL_MARKER).is_file():
            return entry
    return path


def extract_linux(source: Path, destination: Path) -> None:
    with tarfile.open(source, "r:gz") as archive:
        expanded = 0
        for entry in archive:
            path = PurePosixPath(entry.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "ChannelForge-TV-Tester":
                raise ValueError("Invalid Tester package path.")
            target = destination.joinpath(*path.parts)
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif entry.isfile():
                expanded += entry.size
                if expanded > MAX_PACKAGE_BYTES:
                    raise ValueError("Tester package exceeds the extraction size limit.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(entry) as reader, target.open("xb") as output:
                    shutil.copyfileobj(reader, output)
                target.chmod(0o755 if entry.mode & 0o111 else 0o644)
            else:
                raise ValueError("Unsupported entry in Tester package.")


def launch_component(component: str, runtime: Path | None, *, wait: bool = False):
    if runtime is None:
        runtime = Path(sys.executable if WINDOWS and getattr(sys, "frozen", False) else __file__).resolve()
    command = [str(runtime)] if WINDOWS and runtime.suffix == ".exe" else [sys.executable, str(runtime)]
    environment = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"} if WINDOWS else None
    child = subprocess.Popen([*command, "--component", component], cwd=runtime.parent,
                             creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0, env=environment)
    if wait:
        return child.wait()


def settings() -> dict:
    return json.loads((ROOT / "assets/tester-config.json").read_text(encoding="utf-8"))


def alpha_version_label(version: str) -> str:
    return "Alpha V" + (version[:-2] if version.endswith(".0") else version)


def release_details(record: dict) -> dict:
    version = record.get("version", "")
    details = {}
    if isinstance(version, str) and re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,5}){2}", version):
        details["version"] = version
        details["display_version"] = alpha_version_label(version)
    notes = record.get("release_notes")
    if isinstance(notes, list):
        details["release_notes"] = [line.strip()[:240] for line in notes[:12] if isinstance(line, str) and line.strip()]
    return details


def runtime_for(root: Path, revision: int) -> Path:
    return root / f"r{revision}" / (ARTIFACT if WINDOWS else "ChannelForge-TV-Tester/tester_suite_launcher.py")


def installed_runtime(config: dict) -> Path | None:
    root = install_root() / "updates"
    try:
        revision = int((root / "current").read_text(encoding="ascii"))
    except (OSError, ValueError):
        return None
    runtime = runtime_for(root, revision)
    return runtime if revision > config["revision"] and runtime.is_file() else None


@contextmanager
def exclusive_file(path: Path):
    with path.open("a+b") as handle:
        handle.seek(0)
        if WINDOWS:
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def register_process(revision: int) -> None:
    """Hold a lock until app shutdown has released its window, ports, and children."""
    root = install_root() / "updates"
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"running-{revision}-{os.getpid()}.lock"
        lock = exclusive_file(path)
        lock.__enter__()
    except OSError:
        return

    @atexit.register
    def release():
        lock.__exit__(None, None, None)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


restarting = threading.Event()


def watch_restart(close) -> None:
    """Only watch the local install pointer. Launch never contacts the update server."""
    config = settings()

    def watch():
        while not restarting.wait(0.75):
            if installed_runtime(config) is not None:
                restarting.set()
                close()

    threading.Thread(target=watch, daemon=True, name="tester-update-restart").start()


def private_release(config: dict) -> dict | None:
    endpoint = config.get("report_url", "")
    if not report_address(endpoint):
        raise ValueError("This Tester installation has no private update address.")
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(endpoint.removesuffix("/report") + "/manifest.json?manual=1", timeout=6) as response:
        body = json.loads(response.read(8192))
    info = body.get(ARTIFACT)
    if not isinstance(info, dict):
        raise ValueError("No package for this platform is listed on the Tester channel.")
    revision, size, digest = info.get("revision"), info.get("bytes"), info.get("sha256")
    if (type(revision) is not int or not 0 < revision < 100_000_000 or
            type(size) is not int or not 0 < size <= MAX_PACKAGE_BYTES or
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise ValueError("The Tester channel returned invalid update information.")
    return info if revision > config["revision"] else None


def stage_release(root: Path, config: dict, release: dict, progress) -> Path:
    revision, size = release["revision"], release["bytes"]
    opener = urllib.request.build_opener(NoRedirect)
    url = config["report_url"].removesuffix("/report") + "/" + ARTIFACT
    with tempfile.TemporaryDirectory(dir=root, prefix=".updating-") as staging:
        stage = Path(staging)
        download = stage / ARTIFACT
        hashed = hashlib.sha256()
        amount = 0
        deadline = time.monotonic() + 900
        progress(stage="downloading", downloaded=0, total=size)
        with opener.open(url, timeout=20) as incoming, download.open("xb") as output:
            while chunk := incoming.read(min(256 * 1024, size - amount + 1)):
                amount += len(chunk)
                if amount > size or time.monotonic() > deadline:
                    raise ValueError("The Tester download exceeded its size or time limit.")
                hashed.update(chunk)
                output.write(chunk)
                progress(downloaded=amount)
        progress(stage="verifying")
        if amount != size or hashed.hexdigest() != release["sha256"]:
            raise ValueError("The download did not match the published checksum. Try again.")
        contents = stage / "payload"
        contents.mkdir()
        runtime = runtime_for(root, revision)
        relative = runtime.relative_to(root / f"r{revision}")
        if WINDOWS:
            download.replace(contents / relative)
        else:
            extract_linux(download, contents)
        if not (contents / relative).is_file():
            raise ValueError("The updated Tester launcher is missing.")
        if installed_runtime(config) == runtime:
            return runtime
        final = root / f"r{revision}"
        if final.exists():
            shutil.rmtree(final)
        contents.rename(final)
        return runtime


job_lock = threading.Lock()
job: dict = {}


def update_status() -> dict:
    with job_lock:
        return dict(job)


def start_update(revision: int) -> dict:
    def progress(**values):
        with job_lock:
            job.update(values)

    def download():
        root = install_root() / "updates"
        try:
            root.mkdir(parents=True, exist_ok=True)
            with exclusive_file(root / "update.lock"):
                config = settings()
                release = private_release(config)
                if release is None or release["revision"] != revision:
                    raise ValueError("The available build changed. Check for updates again.")
                runtime = stage_release(root, config, release, progress)
                command = [str(runtime)] if WINDOWS else [sys.executable, str(runtime)]
                coordinator = subprocess.Popen([*command, "--restart-suite", str(revision)], cwd=runtime.parent,
                                               creationflags=subprocess.CREATE_NO_WINDOW if WINDOWS else 0,
                                               env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"} if WINDOWS else None)
                progress(stage="restarting", status="restarting")
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if installed_runtime(config) == runtime:
                    return
                if coordinator.poll() is not None:
                    break
                time.sleep(0.25)
            raise ValueError("The update was verified, but restart could not start. Check for the update again.")
        except (OSError, ValueError, TypeError, tarfile.TarError) as error:
            message = str(error) if isinstance(error, ValueError) else "Could not install the Tester update. Check your connection or whether another Tester app is updating."
            progress(stage="error", status="error", error=message)

    with job_lock:
        if job.get("status") in {"busy", "restarting"}:
            return dict(job)
        job.clear()
        job.update(stage="checking", status="busy")
    threading.Thread(target=download, daemon=True, name="tester-update-download").start()
    return update_status()


def local_update(method: str, path: str, headers, port: int, *, component: str = "") -> tuple[int, dict]:
    origin = f"http://127.0.0.1:{port}"
    if (headers.get("Host") != origin.removeprefix("http://") or
            headers.get("Origin", origin) != origin or headers.get("X-ChannelForge-Tester-Update") != "1"):
        return 403, {"error": "Open System in the Tester app to manage updates."}
    try:
        config = settings()
        if method == "GET" and path == "/api/tester-update/installed":
            return 200, {"component": component, "revision": config["revision"], **release_details(config)}
        if method == "GET" and path == "/api/tester-update":
            release = private_release(config)
            return 200, {"current": config["revision"], "available": release["revision"] if release else None,
                         **release_details(release or config)}
        if method == "GET" and path == "/api/tester-update/progress":
            return 200, update_status()
        if method == "POST" and re.fullmatch(r"/api/tester-update/[0-9]{1,8}", path):
            return 202, start_update(int(path.rsplit("/", 1)[1]))
    except (OSError, ValueError, TypeError):
        return 503, {"error": "Could not check the private Tester channel. Try again."}
    return 404, {"error": "Tester update action unavailable."}


def restart_suite(revision: int) -> None:
    root = install_root() / "updates"
    if settings()["revision"] != revision:
        raise ValueError("Invalid Tester restart version.")
    runtime = runtime_for(root, revision)
    if not runtime.is_file():
        raise ValueError("The verified Tester build could not be found.")
    # Wait for the downloader to release the update lock before notifying the apps.
    for _ in range(40):
        try:
            with exclusive_file(root / "update.lock"):
                temp = root / "current.pending"
                temp.write_text(str(revision), encoding="ascii")
                temp.replace(root / "current")
            break
        except OSError:
            time.sleep(0.25)
    else:
        return
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        running = False
        for path in root.glob("running-*-*.lock"):
            match = re.fullmatch(r"running-(\d{1,8})-\d+\.lock", path.name)
            if not match or int(match[1]) >= revision:
                continue
            try:
                with exclusive_file(path):
                    pass
            except OSError:
                running = running or path.exists()
        if not running:
            break
        time.sleep(0.25)
    else:
        return
    for component in ("studio", "tv"):
        launch_component(component, runtime)
