"""Verify Windows installer aliases and embedded payloads; optionally decode an HLS fixture."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile

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
    for module in ("webview", "windows_distribution", "release_metadata"):
        assert module in modules, module
    for relative in ("hls.min.js", "cinema.css", "playback-diagnostics.js"):
        assert tv.extract("web\\" + relative) == (root / "web" / relative).read_bytes(), relative
    for relative in ("cinema-cassette-case.png",):
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
    command = [sys.executable, str(root / "scripts/test_desktop_native.py"), str(fixture)]
    smoke = subprocess.run(command, check=True, capture_output=True, text=True, timeout=100)
    return json.loads(smoke.stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path, help="Isolated source tree after running build_windows.ps1")
    parser.add_argument("--fixture", type=Path, help="Folder containing test.m3u8 and its referenced TS files")
    args = parser.parse_args()
    root = args.stage.resolve()
    sys.path.insert(0, str(root))
    results = {"sha256": verify_build(root)}
    if args.fixture:
        results["built_in_webview_decode"] = decode(root, args.fixture.resolve())
    print(json.dumps(results, indent=2))
