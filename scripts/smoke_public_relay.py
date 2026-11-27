"""Read an online Studio's HD HLS feed over IPv4 from another network."""

import hashlib
import json
import re
import secrets
import subprocess
import sys
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


def get(url):
    return subprocess.check_output(["curl", "--ipv4", "--silent", "--show-error", "--fail",
                                    "--max-time", "15", "--max-filesize", "8000000", url], timeout=17)


def check(base):
    if not re.fullmatch(r"https://[a-z0-9-]+\.[a-z0-9-]+\.ts\.net/studio/[a-f0-9]{16}", base):
        raise ValueError("Use a scoped HTTPS Tailscale Studio URL.")

    def scoped_url(parent, child):
        url = urljoin(parent, child)
        if not url.startswith(base + "/") or ".." in urlsplit(url).path.split("/"):
            raise ValueError("The Studio returned media outside its relay scope.")
        return url

    def viewer(url):
        address = urlsplit(url)
        query = dict(parse_qsl(address.query))
        return urlunsplit(address._replace(query=urlencode({**query, "viewer": lease})))

    lease = secrets.token_hex(16)
    catalog = json.loads(get(base + "/api/public/channels?guide=brief"))
    channels = [row for row in catalog["channels"] if row.get("online") and row.get("continuous_url")]
    if not channels:
        raise RuntimeError("The Studio has no online HLS channels.")
    url = viewer(scoped_url(base + "/", channels[0]["continuous_url"]))
    master = get(url).decode()
    path = next(line for line in master.splitlines() if "/hd.m3u8" in line and not line.startswith("#"))
    playlist = viewer(scoped_url(url, path))
    seen, contents = set(), set()
    delivered = 0.0
    deadline = time.monotonic() + 34
    while time.monotonic() < deadline and delivered < 10:
        manifest = get(playlist).decode()
        segments = re.findall(r"(?m)^#EXTINF:([\d.]+),[^\n]*\n([^#\n]+)", manifest)
        for seconds, path in segments:
            address = scoped_url(playlist, path)
            if address in seen:
                continue
            seen.add(address)
            segment = get(address)
            if not segment.startswith(b"\x47") or len(segment) % 188:
                raise RuntimeError("The relay did not return an MPEG-TS segment.")
            contents.add(hashlib.sha256(segment).digest())
            delivered += float(seconds)
        time.sleep(0.4)
    if delivered < 10 or len(contents) < 3:
        raise RuntimeError("The remote playlist did not advance through ten seconds of video.")
    print(f"PASS remote IPv4 relay: received {delivered:.1f}s across {len(contents)} HD segments (Studio {catalog['creator_version']}).")


if __name__ == "__main__":
    check(sys.argv[1].rstrip("/"))
