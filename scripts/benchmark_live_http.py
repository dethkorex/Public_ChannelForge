"""Time HLS startup and recovery from the same public HTTP endpoint in two revisions.

Local fixtures, loopback TCP, anonymous HTTP. The player model starts with 3 s of
segments, consumes wall-clock time at 1x, and counts transitions into starvation.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen
from urllib.error import HTTPError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, help="An alternate Public_ChannelForge.py revision")
    parser.add_argument(
        "--fault", action="store_true", help="Kill the active encoder six seconds after tuning"
    )
    parser.add_argument("--seconds", type=int, default=18)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    with tempfile.TemporaryDirectory(prefix="cf-http-bench-") as root:
        os.environ["PUBLIC_CHANNELFORGE_HOME"] = root
        spec = importlib.util.spec_from_file_location(
            "fixture_creator", args.module or project / "Public_ChannelForge.py"
        )
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        app.ensure_files()
        folder = Path(root) / "media"
        folder.mkdir()
        source = folder / "program.mp4"
        duration = args.seconds + 16
        subprocess.run(
            [
                app.ffmpeg_path(),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=854x480:r=24",
                "-f",
                "lavfi",
                "-i",
                "sine=f=320:r=48000",
                "-t",
                str(duration),
                "-c:v",
                "libx264",
                "-threads:v",
                "2",
                "-preset",
                "veryfast",
                "-g",
                "24",
                "-sc_threshold",
                "0",
                "-pix_fmt",
                "yuv420p",
                "-b:v",
                "500k",
                "-maxrate",
                "650k",
                "-bufsize",
                "650k",
                "-c:a",
                "aac",
                "-ac",
                "2",
                str(source),
            ],
            check=True,
        )
        app.save_local_library("Fixture", str(folder))
        item = {
            "id": "program",
            "title": "Fixture",
            "source": "local",
            "file_path": str(source),
            "duration_ms": duration * 1000,
        }
        row = {"id": "fixture", "items": [item], "schedule_anchor": datetime.now(timezone.utc).isoformat()}
        app.atomic_json(app.CHANNELS_FILE, [row])
        settings = app.config()
        settings.update(creator_profile={"name": "QA"}, public_url="https://fixture.example")
        app.atomic_json(app.CONFIG_FILE, settings)
        app.Handler.log_message = lambda *args: None
        app.PublicMediaHandler.log_message = lambda *args: None
        origin = app.PublicMediaServer(("127.0.0.1", 0), app.PublicMediaHandler)
        threading.Thread(target=origin.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:" + str(origin.server_port)
        rendezvous = threading.Barrier(2)
        start = time.monotonic()

        def get(path):
            with urlopen(base + path, timeout=16) as r:
                return r.read()

        def viewer():
            rendezvous.wait()
            master = get("/broadcast/fixture/master.m3u8").decode()
            variant = next(l for l in master.splitlines() if "/balanced.m3u8" in l)
            first = ready = None
            available = 0
            seen = set()
            played = stalls = stall_time = 0
            starved = False
            wire = 0
            error = None
            last = time.monotonic()

            def account():
                nonlocal available, played, stalls, starved, stall_time, last
                now = time.monotonic()
                dt = now - last
                last = now
                if ready is not None:
                    consumed = min(dt, available)
                    played += consumed
                    available -= consumed
                    if dt - consumed > 0.001:
                        if not starved:
                            stalls += 1
                        starved = True
                        stall_time += dt - consumed

            while time.monotonic() - start < args.seconds:
                try:
                    playlist = get(variant).decode()
                    for dur, path in re.findall(r"(?m)^#EXTINF:([0-9.]+),[^\n]*\n([^#\n]+\.ts)$", playlist):
                        if path in seen:
                            continue
                        data = get(path)
                        assert data and data[0] == 0x47
                        account()
                        wire += len(data)
                        seen.add(path)
                        available += float(dur)
                        starved = False
                        first = first if first is not None else time.monotonic() - start
                        if ready is None and available >= 3:
                            ready = time.monotonic() - start
                except (HTTPError, TimeoutError) as exc:
                    error = getattr(exc, "code", "timeout")
                account()
                time.sleep(0.1)
            return dict(
                first_segment_s=round(first, 3) if first is not None else None,
                three_seconds_ready_s=round(ready, 3) if ready is not None else None,
                played_s=round(played, 2),
                stalls=stalls,
                starved_s=round(stall_time, 2),
                payload_B=wire,
                last_http_error=error,
                segments=len(seen),
            )

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                viewers = [pool.submit(viewer) for _ in range(2)]
                if args.fault:
                    time.sleep(6)
                    with app.LOCK:
                        running = next(
                            s["process"] for s in app.FFMPEG_SESSIONS.values() if s["process"].poll() is None
                        )
                        running.kill()
                result = [f.result() for f in viewers]
                print(json.dumps(dict(fault=args.fault, viewers=result), indent=2))
        finally:
            origin.shutdown()
            origin.server_close()
            app.stop_ffmpeg_sessions()


if __name__ == "__main__":
    main()
