"""Generate the managed relay's matching NGINX and enrollment configurations."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

from community import HOSTNAME, configuration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--funnel-host", required=True)
    parser.add_argument("--publisher-host", required=True)
    parser.add_argument("--host-key", required=True, type=Path)
    parser.add_argument("--count", type=int, default=64)
    parser.add_argument("--first-slot", type=int, default=18200)
    parser.add_argument("--media-port", type=int, default=18097)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+\.ts\.net", args.funnel_host):
        parser.error("Use the VPS's public Tailscale Funnel hostname.")
    if not re.fullmatch(HOSTNAME, args.publisher_host):
        parser.error("Use a DNS hostname for the direct publisher listener.")
    if not 8 <= args.count <= 256:
        parser.error("Configure 8 to 256 Studio slots.")
    host_key = " ".join(args.host_key.read_text(encoding="ascii").split()[:2])
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]{68}", host_key):
        parser.error("Use the direct publisher's Ed25519 public host key.")
    slots = []
    for port in range(args.first_slot, args.first_slot + args.count):
        scope = sha256(f"{args.funnel_host}:{port}".encode()).hexdigest()[:16]
        slots.append(dict(origin=f"https://{args.funnel_host}/studio/{scope}", ssh_host=args.publisher_host,
                          ssh_user="channelforge-relay", ssh_port=18088, remote_port=port, host_key=host_key,
                          funnel_host=args.funnel_host, funnel_port=8443))
    routes = {slot["origin"].removeprefix("https://"): slot["remote_port"] for slot in slots}
    nginx = configuration(routes, args.media_port, enrollment_port=18089)
    (args.output / "slots.json").write_text(json.dumps(slots, indent=2) + "\n", encoding="utf-8")
    (args.output / "managed-nginx.conf").write_text(nginx, encoding="utf-8")


if __name__ == "__main__":
    main()
