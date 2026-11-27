"""Build and verify an unsigned ChannelForge Roku developer sideload ZIP."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from xml.etree import ElementTree
import zipfile

from tester_release_notes import REVISIONS, VERSION, notes_for


SOURCE = Path(__file__).resolve().parents[1] / "tv" / "roku"


def build(output: Path, tester_version: str | None = None, tester_revision: int | None = None,
          tester_portal: str = "", relay_pilot: str = "") -> str:
    if tester_version and not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,5}", tester_version):
        raise ValueError("Expected a numeric Roku Tester version.")
    if tester_revision is not None and (not tester_version or not 0 <= tester_revision <= 99999):
        raise ValueError("Expected a Roku Tester build number between 0 and 99999.")
    if tester_portal and (not tester_version or not re.fullmatch(
            r"https://[a-zA-Z0-9.:-]+/[A-Za-z0-9_-]{32,64}", tester_portal)):
        raise ValueError("Expected a private Tester portal HTTPS URL.")
    if relay_pilot and (not tester_version or not re.fullmatch(
            r"[a-z0-9-]+(?:\.[a-z0-9-]+)+=[a-z0-9-]+(?:\.[a-z0-9-]+)+", relay_pilot)):
        raise ValueError("Expected a Tester relay in FALLBACK_HOST=RELAY_HOST format.")
    entries = [SOURCE / "manifest"]
    for directory in ("components", "source", "images", "sounds"):
        entries.extend(file for file in (SOURCE / directory).rglob("*") if file.is_file())
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        for path in sorted(entries):
            entry = zipfile.ZipInfo(path.relative_to(SOURCE).as_posix())
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            data = path.read_bytes()
            if path.name == "manifest" and tester_version:
                major, minor, revision = tester_version.split(".")
                if tester_revision is not None:
                    revision = str(tester_revision)
                    # Keep Roku's package version advancing through the Alpha V4.0 rename.
                    if major == "4" and minor == "0":
                        minor = "1"
                fields = {"title": "ChannelForge TV Tester", "channelforge_version": tester_version,
                          "major_version": major, "minor_version": minor, "build_version": revision}
                lines = data.decode("utf-8").splitlines()
                result = [f"{key}={fields.get(key, value)}"
                          for key, value in (line.split("=", 1) for line in lines if "=" in line)]
                if tester_portal:
                    result.extend((f"channelforge_tester_portal={tester_portal}",
                                   f"channelforge_tester_revision={revision}",
                                   "channelforge_tester_notes=" + json.dumps(notes_for(tester_version, "roku"))))
                if relay_pilot:
                    result.append(f"channelforge_relay_pilot={relay_pilot}")
                data = ("\n".join(result) + "\n").encode()
            archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise ValueError("Archive failed CRC validation")
        members = set(archive.namelist())
        required = {"manifest", "source/main.brs", "components/MainScene.xml"}
        for line in archive.read("manifest").decode().splitlines():
            if "=pkg:/" in line:
                required.add(line.split("=pkg:/", 1)[1])
        for name in members:
            if name.endswith(".brs"):
                archive.read(name).decode("utf-8")
            if name.endswith(".xml"):
                for script in ElementTree.fromstring(archive.read(name)).findall("script"):
                    uri = script.attrib["uri"]
                    required.add(uri[5:] if uri.startswith("pkg:/") else str(PurePosixPath(name).parent / uri))
        missing = required - members
        if missing:
            raise ValueError(f"Missing package files: {sorted(missing)}")
    return hashlib.sha256(output.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--tester-version")
    parser.add_argument("--tester-revision", type=int)
    parser.add_argument("--tester-portal-file", type=Path)
    parser.add_argument("--relay-pilot", default="")
    args = parser.parse_args()
    portal = args.tester_portal_file.read_text(encoding="ascii").strip().rstrip("/") if args.tester_portal_file else ""
    tester_version = args.tester_version or (VERSION if portal else None)
    revision = args.tester_revision
    if revision is None and tester_version == VERSION:
        revision = REVISIONS["roku"]
    print(f"{build(args.output, tester_version, revision, portal, args.relay_pilot)}  {args.output}")
