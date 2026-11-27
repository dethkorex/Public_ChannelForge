"""Build and verify an unsigned ChannelForge Roku developer sideload ZIP."""

import argparse
import hashlib
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree
import zipfile

SOURCE = Path(__file__).resolve().parents[1] / "tv" / "roku"


def build(output: Path) -> str:
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
    args = parser.parse_args()
    print(f"{build(args.output)}  {args.output}")
