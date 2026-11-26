"""Time HLS startup and recovery from the same public HTTP endpoint in two revisions.

Local fixtures, loopback TCP, anonymous HTTP. The player model starts with 3 s of
segments, consumes wall-clock time at 1x, and counts transitions into starvation.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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
from urllib.parse import urljoin, urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, help="An alternate Public_ChannelForge.py revision")
    parser.add_argument(
        "--fault", action="store_true", help="Kill the active encoder six seconds after tuning"
    )
    parser.add_argument("--seconds", type=int, default=18)
    parser.add_argument("--viewers", type=int, default=2, help="Concurrent viewers tuning the same broadcast")
    parser.add_argument("--movie", action="store_true", help="Open the catalog movie on two public viewers")
    parser.add_argument("--sustained", action="store_true",
                        help="Five-minute public relay playback with bounded links, program changes and disk cleanup")
    args = parser.parse_args()
    if not 1 <= args.viewers <= 32:
        parser.error("--viewers must be between 1 and 32")
    if args.movie and (args.fault or args.sustained):
        parser.error("--movie is a separate movie playback check")
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    with tempfile.TemporaryDirectory(prefix="cf-http-bench-") as root:
        for variable in ("HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "XDG_CONFIG_HOME",
                         "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
            private = Path(root) / variable.lower()
            private.mkdir()
            os.environ[variable] = str(private)
        os.environ["PUBLIC_CHANNELFORGE_HOME"] = root
        if args.sustained:
            args.seconds = max(305, args.seconds)
        spec = importlib.util.spec_from_file_location(
            "fixture_creator", args.module or project / "Public_ChannelForge.py"
        )
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
        app.ensure_files()
        folder = Path(root) / "media"
        folder.mkdir()
        source = folder / "program.mp4"
        duration = 52 if args.sustained else args.seconds + 16
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
            "type": "movie",
            "source": "local",
            "file_path": str(source),
            "duration_ms": duration * 1000,
        }
        row = {"id": "fixture", "items": [item], "schedule_anchor": datetime.now(timezone.utc).isoformat()}
        if args.sustained:
            row["items"] = [{**item, "id": str(n), "title": f"Episode {n+1}"} for n in range(args.seconds//duration+2)]
            row["playback_mode"] = "ordered"
            row["air_until"] = (datetime.now(timezone.utc) + timedelta(seconds=len(row["items"]) * duration)).isoformat()
        app.atomic_json(app.CHANNELS_FILE, [row])
        settings = app.config()
        settings.update(creator_profile={"name": "QA"}, public_url="https://fixture.example")
        if args.sustained:
            settings["public_url"] = "https://fixture.app.lhr.life"
        app.atomic_json(app.CONFIG_FILE, settings)
        app.Handler.log_message = lambda *args: None
        app.PublicMediaHandler.log_message = lambda *args: None
        origin = app.PublicMediaServer(("127.0.0.1", 0), app.PublicMediaHandler)
        threading.Thread(target=origin.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:" + str(origin.server_port)
        rendezvous = threading.Barrier(args.viewers)
        start = time.monotonic()

        def get(path):
            with urlopen(base + path, timeout=16) as r:
                return r.read()

        def viewer(number):
            rendezvous.wait()
            if args.movie:
                listing = json.loads(get("/api/public/vault"))
                initial = urlsplit(listing["items"][0]["stream_url"]).path
            else:
                initial = "/broadcast/fixture/" + ("firetv" if number % 2 else "master") + ".m3u8"
            master = get(initial).decode()
            limited = args.sustained or args.movie
            rendition = "economy" if limited and number % 2 == 0 else "balanced"
            link_kbps = (360 if number % 2 == 0 else 1150) if limited else None
            variant = next(l for l in master.splitlines() if l.endswith(rendition + ".m3u8"))
            first = ready = None
            available = 0
            seen = set()
            played = stalls = stall_time = 0
            starved = False
            wire = 0
            programs = set()
            gaps = 0
            last_sequence = None
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
                    sequence = int(re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", playlist)[1])
                    entries = re.findall(r"(?m)^#EXTINF:([0-9.]+),[^\n]*\n([^#\n]+\.ts)$", playlist)
                    for idx, (dur, path) in enumerate(entries):
                        path = urljoin(variant, path)
                        if path in seen:
                            continue
                        data = get(path)
                        assert data and data[0] == 0x47
                        if link_kbps:
                            # Enforce a maximum downstream transfer rate per viewer, including repeat requests.
                            time.sleep(len(data) * 8 / (link_kbps * 1000))
                            with (Path(root) / f"viewer-{number}.ts").open("ab") as decoded_input:
                                decoded_input.write(data)
                        current = sequence + idx
                        if last_sequence is not None and current != last_sequence + 1:
                            gaps += 1
                        last_sequence = current
                        programs.add(path.split("/")[2])
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
                rendition=rendition, link_kbps=link_kbps, sessions=len(programs), sequence_gaps=gaps,
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
            reaper_stop = threading.Event()
            def reap():
                while not reaper_stop.wait(5):
                    app.reap_idle_sessions_once()
            if args.sustained:
                threading.Thread(target=reap, daemon=True).start()
            with ThreadPoolExecutor(max_workers=args.viewers) as pool:
                viewers = [pool.submit(viewer, n) for n in range(args.viewers)]
                if args.fault:
                    time.sleep(6)
                    with app.LOCK:
                        running = next(
                            s["process"] for s in app.FFMPEG_SESSIONS.values() if s["process"].poll() is None
                        )
                        running.kill()
                result = [f.result() for f in viewers]
                if args.sustained or args.movie:
                    for number, metrics in enumerate(result):
                        decoded = subprocess.run([app.ffmpeg_path(), "-v", "error", "-f", "mpegts", "-i",
                            str(Path(root) / f"viewer-{number}.ts"), "-map", "0:v:0", "-an", "-f", "framemd5", "-"],
                            capture_output=True, check=True, timeout=100)
                        metrics["decoded_frames"] = sum(line.startswith(b"0,") for line in decoded.stdout.splitlines())
                print(json.dumps(dict(fault=args.fault, viewers=result), indent=2), flush=True)
                if args.sustained:
                    assert all(v["sessions"] >= 5 and v["sequence_gaps"] == 0 and v["stalls"] == 0 and
                               v["last_http_error"] is None and v["decoded_frames"] >= args.seconds * 22 for v in result)
                if args.movie:
                    assert all(v["sessions"] == 1 and v["sequence_gaps"] == 0 and v["stalls"] == 0 and
                               v["last_http_error"] is None and v["decoded_frames"] >= args.seconds * 22 for v in result)
                    assert len(app.FFMPEG_SESSIONS) == 1
        finally:
            reaper_stop.set()
            origin.shutdown()
            origin.server_close()
            app.stop_ffmpeg_sessions()


if __name__ == "__main__":
    main()
