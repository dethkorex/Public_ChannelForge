"""Verify Windows installer aliases and embedded payloads; optionally decode an HLS fixture."""

import argparse
import hashlib
import http.server
import importlib
import importlib.util
import json
import multiprocessing
from multiprocessing import spawn
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit

from PyInstaller.archive.readers import CArchiveReader


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def verify_build(root):
    output = root / "release"
    installer = output / "ChannelForge_Setup.exe"
    installer_hash = digest(installer)
    for alias in ("ChannelForge_Creator_Setup", "ChannelForge_TV_Setup", "Public_ChannelForge_Setup"):
        assert digest(output / f"{alias}.exe") == installer_hash, alias
    archive = CArchiveReader(str(installer))
    hashes = {"installer": installer_hash}
    for product in ("Creator", "TV"):
        name = f"ChannelForge_{product}.exe"
        expected = digest(root / "dist" / name)
        assert digest(root / "installer" / "payload" / name) == expected, name
        assert hashlib.sha256(archive.extract("payload\\" + name)).hexdigest() == expected, name
        hashes[product] = expected
    assert digest(output / "ChannelForge_TV.exe") == hashes["TV"]
    tv = CArchiveReader(str(output / "ChannelForge_TV.exe"))
    modules = tv.open_embedded_archive("PYZ.pyz").toc
    for module in ("native_vlc", "vlc", "webview", "tkinter", "windows_distribution", "release_metadata"):
        assert module in modules, module
    for relative in ("desktop-playback.js", "hls.min.js", "cinema.css", "playback-diagnostics.js"):
        assert tv.extract("web\\" + relative) == (root / "web" / relative).read_bytes(), relative
    for relative in ("cinema-cassette-case.png", "cinema-vhs-spines.png"):
        assert tv.extract("assets\\" + relative) == (root / "assets" / relative).read_bytes(), relative
    creator = CArchiveReader(str(root / "dist" / "ChannelForge_Creator.exe"))
    for tool in ("ffmpeg.exe", "ffprobe.exe", "openssh\\ssh.exe", "openssh\\ssh-keygen.exe"):
        assert creator.extract(tool).startswith(b"MZ"), tool
    spec = importlib.util.spec_from_file_location("qa_installer", root / "installer" / "windows_installer.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    with tempfile.TemporaryDirectory() as temp:
        destination = Path(temp) / "existing"
        destination.mkdir()
        for product in ("Creator", "TV"):
            (destination / f"ChannelForge_{product}.exe").write_bytes(b"older build")
        assert setup.install_binaries(destination, ("creator", "tv"))
        assert not setup.install_binaries(destination, ("creator", "tv"))
        for product in ("Creator", "TV"):
            assert digest(destination / f"ChannelForge_{product}.exe") == hashes[product]
        assert all(entry.is_file() for entry in destination.iterdir())
        assert not list(destination.glob("*.updating"))
    return hashes


def decode(root, fixture):
    # Exercise the installed executable's freeze_support branch. Keep the worker's
    # embedded Python path so an installed developer module cannot mask a bad build.
    native = importlib.import_module("native_vlc").NativePlayback()
    executable = str(root / "release" / "ChannelForge_TV.exe")
    requests = []

    class Fixture(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            name = "test.m3u8" if path == "/stream" else path.removeprefix("/")
            if "/" in name or not name.endswith((".m3u8", ".ts")) or not (fixture / name).is_file():
                self.send_error(404)
                return
            requests.append(name)
            body = (fixture / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    prepare = spawn.get_preparation_data

    def embedded_paths(name):
        data = prepare(name)
        for field in ("sys_path", "init_main_from_path", "init_main_from_name"):
            data.pop(field, None)
        return data

    def frozen_arguments(**fields):
        return [executable, "--multiprocessing-fork", *(f"{key}={value!r}" for key, value in fields.items())]

    old_executable = spawn.get_executable()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        multiprocessing.set_executable(executable)
        with patch.object(spawn, "get_command_line", frozen_arguments), patch.object(spawn, "get_preparation_data", embedded_paths):
            assert native.start(1, f"http://127.0.0.1:{server.server_port}/stream", "Packaged viewer smoke", False, 0, True, False)
        resumed = False
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = native.state(1)
            if state.get("action"):
                raise RuntimeError(f"Packaged renderer exited: {state}")
            if state.get("ready") and not resumed:
                native.command(1, "resume")
                resumed = True
            if state.get("frames", 0) >= 25 and state["time"] >= 1 and len(set(requests)) >= 3:
                return dict(state, requested_files=len(set(requests)))
            time.sleep(0.12)
        raise RuntimeError(f"Packaged renderer timed out: {state}, requests={requests}")
    finally:
        multiprocessing.set_executable(old_executable)
        native.close()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path, help="Isolated source tree after running build_windows.ps1")
    parser.add_argument("--fixture", type=Path, help="Folder containing test.m3u8 and its referenced TS files")
    args = parser.parse_args()
    root = args.stage.resolve()
    sys.path.insert(0, str(root))
    results = {"sha256": verify_build(root)}
    if args.fixture:
        results["native_decode"] = decode(root, args.fixture.resolve())
    print(json.dumps(results, indent=2))
