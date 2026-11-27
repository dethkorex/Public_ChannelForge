"""Load the version and release notes used by the private Tester packages."""

import json
from pathlib import Path
import re


RELEASES = json.loads((Path(__file__).resolve().parents[1] / "TESTER_RELEASES.json").read_bytes())
VERSION = RELEASES["version"]
REVISIONS = RELEASES["revisions"]


def version_label(version: object) -> str:
    return version if isinstance(version, str) and re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,5}){2}", version) else ""


def notes_for(version: str, platform: str) -> list[str]:
    release = RELEASES["releases"].get(version, {})
    return release.get("common", []) + release.get(platform, [])
