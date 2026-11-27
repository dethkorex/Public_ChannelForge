"""Privately package the viewer for Windows and Linux with separate tester data."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tester_diagnostics import report_address
from tester_release_notes import REVISIONS, VERSION, notes_for, version_label

MODULES = ("ChannelForge_TV.py", "Public_ChannelForge.py", "windows_distribution.py", "linux_runtime.py",
           "release_metadata.py", "tester_diagnostics.py", "tester_suite_launcher.py", "tester_updates.py")


def package(state: Path, target: str, revision: int, ffmpeg_dir: Path | None, openssh_dir: Path | None,
            version=VERSION, relay_pilot=""):
    if not version_label(version):
        raise ValueError("Expected a numeric Tester version.")
    if relay_pilot and not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+=[a-z0-9-]+(?:\.[a-z0-9-]+)+", relay_pilot):
        raise ValueError("Expected a Tester relay in FALLBACK_HOST=RELAY_HOST format.")
    state = state.resolve()
    downloads = state / "assets"
    work = state / "desktop-work"
    work.mkdir(parents=True, exist_ok=True)
    downloads.mkdir(exist_ok=True)
    report_url = (state / "share-url.txt").read_text(encoding="ascii").strip() + "/report"
    if not report_address(report_url):
        raise ValueError("A valid private report URL is required.")
    config = work / "tester-config.json"
    config.write_text(json.dumps({"report_url": report_url, "revision": revision, "version": version,
                                  "release_notes": notes_for(version, "desktop"), "relay_pilot": relay_pilot}), encoding="utf-8")
    data = [(ROOT / folder, folder) for folder in ("web", "assets", "plugins")] + [(config, "assets")]

    if target == "windows":
        name = "ChannelForgeTV-Tester-Windows"
        arguments = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                     "--onefile", "--name", name, "--icon", str(ROOT / "assets/channel-forge-tester.ico"),
                     "--distpath", str(work / "dist"), "--workpath", str(work / "work"), "--specpath", str(work)]
        for source, destination in data:
            arguments.extend(("--add-data", f"{source};{destination}"))
        if ffmpeg_dir:
            for tool in ("ffmpeg.exe", "ffprobe.exe"):
                path = (ffmpeg_dir / tool).resolve(strict=True)
                arguments.extend(("--add-binary", f"{path};."))
        if openssh_dir:
            for tool in ("ssh.exe", "ssh-keygen.exe", "libcrypto.dll"):
                path = (openssh_dir / tool).resolve(strict=True)
                arguments.extend(("--add-binary", f"{path};openssh"))
            for notice in ("LICENSE.txt", "NOTICE.txt"):
                path = (openssh_dir / notice).resolve(strict=True)
                arguments.extend(("--add-data", f"{path};openssh"))
        subprocess.run([*arguments, str(ROOT / "tester_suite_launcher.py")], cwd=ROOT, check=True)
        artifact = downloads / f"{name}.exe"
        (work / "dist" / f"{name}.exe").replace(artifact)
    else:
        folder = work / "ChannelForge-TV-Tester"
        folder.mkdir(exist_ok=True)
        for module in MODULES:
            shutil.copy2(ROOT / module, folder / module)
        for source, destination in data:
            (folder / destination).mkdir(exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, folder / destination, dirs_exist_ok=True)
            else:
                shutil.copy2(source, folder / destination / source.name)
        for label, mode in (("ChannelForge-TV-Tester", "tv"), ("ChannelForge-Studio-Tester", "studio")):
            (folder / label).write_bytes(
                f'#!/bin/sh\nset -eu\ncd -- "$(dirname -- "$0")"\nexec /usr/bin/python3 -EsB tester_suite_launcher.py --open {mode}\n'.encode())
        (folder / "ChannelForge-Tester-install.json").write_text('{"product":"ChannelForge Tester"}', encoding="utf-8")
        (folder / "README.txt").write_bytes(
            b"ChannelForge Tester suite\n\nRun ./ChannelForge-TV-Tester or ./ChannelForge-Studio-Tester.\n"
            b"Open System and select Check for Update to check and install a new Tester build.\n"
            b"These apps use Python 3, pywebview, GTK WebKit, GStreamer and FFmpeg.\n"
            b"On Debian-based systems the ChannelForge TV package installs these dependencies.\n"
            b"Open Developer to review and send diagnostics.\n")
        archive = work / "ChannelForgeTV-Tester-Linux.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for path in sorted(folder.rglob("*")):
                info = bundle.gettarinfo(str(path), path.relative_to(work).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mode = 0o755 if path.is_dir() or path.name in {"ChannelForge-TV-Tester", "ChannelForge-Studio-Tester"} else 0o644
                if path.is_file():
                    with path.open("rb") as content:
                        bundle.addfile(info, content)
                else:
                    bundle.addfile(info)
        artifact = downloads / archive.name
        archive.replace(artifact)
    with artifact.open("rb") as data:
        checksum = hashlib.file_digest(data, "sha256").hexdigest()
    record = {"revision": revision, "sha256": checksum, "bytes": artifact.stat().st_size, "version": version}
    metadata = work / f"{artifact.name}.json"
    metadata.write_text(json.dumps(record), encoding="ascii")
    metadata.replace(state / metadata.name)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("state", type=Path)
    parser.add_argument("target", choices=("windows", "linux"))
    parser.add_argument("--revision", type=int, default=REVISIONS["desktop"])
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--ffmpeg-dir", type=Path)
    parser.add_argument("--openssh-dir", type=Path)
    parser.add_argument("--relay-pilot", default="")
    args = parser.parse_args()
    package(args.state, args.target, args.revision, args.ffmpeg_dir, args.openssh_dir, args.version, args.relay_pilot)
