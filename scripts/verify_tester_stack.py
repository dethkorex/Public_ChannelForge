"""Verify that both running desktop Tester apps match a candidate before broader testing."""

import argparse
import json
import time
from urllib.request import Request, urlopen


def running_record(port: int, path: str) -> dict:
    request = Request(f"http://127.0.0.1:{port}{path}", headers={"X-ChannelForge-Tester-Update": "1"})
    with urlopen(request, timeout=2) as response:
        return json.load(response)


def verify(tv_port: int, studio_port: int, version: str, revision: int, require_live: bool) -> dict:
    result = {}
    for component, port in (("tv", tv_port), ("studio", studio_port)):
        record = running_record(port, "/api/tester-update/installed")
        actual = (record.get("component"), record.get("version"), record.get("revision"))
        if actual != (component, version, revision):
            raise ValueError(f"{component} does not match v{version} build {revision}. It reported {actual!r}.")
        result[component] = {"version": version, "revision": revision}
    if require_live:
        diagnostics = running_record(studio_port, "/api/diagnostics")
        if diagnostics.get("version") != version or not diagnostics.get("registered") or diagnostics.get("sharing") != "live":
            raise ValueError("The matching Studio has not registered a live sharing route yet.")
        result["studio"]["publishing"] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tv-port", type=int, required=True)
    parser.add_argument("--studio-port", type=int, default=18787)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--require-live-studio", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=0)
    args = parser.parse_args()
    deadline = time.monotonic() + args.wait_seconds
    while True:
        try:
            result = verify(args.tv_port, args.studio_port, args.version, args.revision, args.require_live_studio)
        except (OSError, ValueError) as error:
            if time.monotonic() >= deadline:
                parser.exit(1, f"Tester preflight failed. {error}\n")
            time.sleep(1)
        else:
            print(json.dumps(result))
            return


if __name__ == "__main__":
    main()
