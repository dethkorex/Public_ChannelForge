"""Time HLS startup and recovery from the same public HTTP endpoint in two revisions.

Local fixtures, loopback TCP, anonymous HTTP. The player model starts with 3 s of
segments, consumes wall-clock time at 1x, and counts transitions into starvation.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
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
    parser.add_argument("--hd", action="store_true", help="Tune the 720p rendition of a 720p live source")
    parser.add_argument("--movie", action="store_true", help="Open the catalog movie on two public viewers")
    parser.add_argument("--fanout-nginx", help="Place the private NGINX relay between the viewers and Creator")
    parser.add_argument("--measure-idle", action="store_true", help="Check encoder shutdown after all viewers leave")
    parser.add_argument("--mixed-live-endpoints", action="store_true", help="Tune both legacy URLs and the continuous broadcast")
    parser.add_argument("--sustained", action="store_true",
                        help="Five-minute public relay playback with bounded links, program changes and disk cleanup")
    args = parser.parse_args()
    if not 1 <= args.viewers <= 32:
        parser.error("--viewers must be between 1 and 32")
    if args.movie and (args.fault or args.sustained):
        parser.error("--movie is a separate movie playback check")
    if args.hd and (args.movie or args.sustained):
        parser.error("--hd is for the live test without simulated slower connections")
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    with tempfile.TemporaryDirectory(prefix="cf-http-bench-") as root, ExitStack() as relay_cleanup:
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
        duration = 52 if args.sustained else args.seconds + (120 if args.measure_idle else 16)
        subprocess.run(
            [
                app.ffmpeg_path(),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=s={'1280x720' if args.hd else '854x480'}:r=24",
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
        observed = {"origin_segment_requests": 0, "origin_segment_bytes": 0, "peak_encoders": 0,
                    "peak_encoder_rss_bytes": 0, "encoder_cpu_seconds": 0.0}
        sample_lock = threading.Lock()
        cpu_by_encoder = {}
        class MeasuredOrigin(app.PublicMediaHandler):
            def send_bytes(self, status, body, content_type, extra_headers=None):
                if status == 200 and content_type == "video/mp2t":
                    with sample_lock:
                        observed["origin_segment_requests"] += 1
                        observed["origin_segment_bytes"] += len(body)
                return super().send_bytes(status, body, content_type, extra_headers)

        origin = app.PublicMediaServer(("127.0.0.1", 0), MeasuredOrigin)
        threading.Thread(target=origin.serve_forever, daemon=True).start()
        origin_base = "http://127.0.0.1:" + str(origin.server_port)
        base = origin_base
        if args.fanout_nginx:
            from benchmark_relay_fanout import start_loopback_relay

            relay_dir = relay_cleanup.enter_context(tempfile.TemporaryDirectory(prefix="cf-relay-bench-"))
            relay, relay_port = start_loopback_relay(args.fanout_nginx, Path(relay_dir), origin.server_port)
            relay_cleanup.callback(relay.wait, 6)
            relay_cleanup.callback(relay.terminate)
            base = f"http://127.0.0.1:{relay_port}"
        rendezvous = threading.Barrier(args.viewers)
        start = time.monotonic()

        def get(path):
            server = origin_base if path.startswith("/api/public/") else base
            with urlopen(server + path, timeout=16) as r:
                return r.read()

        def viewer(number):
            rendezvous.wait()
            if args.movie:
                listing = json.loads(get("/api/public/vault"))
                initial = urlsplit(listing["items"][0]["stream_url"]).path
            else:
                initial = "/broadcast/fixture/" + ("firetv" if number % 2 else "master") + ".m3u8"
                if args.mixed_live_endpoints and number % 3 == 0:
                    initial = "/live/fixture.m3u8"
                if args.mixed_live_endpoints:
                    initial += f"?viewer={number + 1:032x}"
            master = get(initial).decode()
            limited = args.sustained or args.movie
            rendition = "economy" if limited and number % 2 == 0 else "balanced"
            if args.hd:
                rendition = "hd"
            link_kbps = (360 if number % 2 == 0 else 1150) if limited else None
            variant = next(l for l in master.splitlines() if urlsplit(l).path.endswith(rendition + ".m3u8"))
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
                        if link_kbps or (args.hd and number == 0):
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
            def measure():
                while not reaper_stop.wait(.25):
                    with app.LOCK:
                        processes = [session["process"] for session in app.FFMPEG_SESSIONS.values()]
                        processes.extend(app.ENCODER_LAUNCHES.values())
                    processes = {process.pid: process for process in processes if process.poll() is None}
                    memory = 0
                    for pid, process in processes.items():
                        cpu, rss = app.encoder_usage(process)
                        if cpu is not None:
                            cpu_by_encoder[pid] = cpu
                        memory += rss or 0
                    with sample_lock:
                        observed["peak_encoders"] = max(observed["peak_encoders"], len(processes))
                        observed["peak_encoder_rss_bytes"] = max(observed["peak_encoder_rss_bytes"], memory)
                        observed["encoder_cpu_seconds"] = round(sum(cpu_by_encoder.values()), 3)
            if args.sustained or args.measure_idle:
                threading.Thread(target=reap, daemon=True).start()
            threading.Thread(target=measure, daemon=True).start()
            get("/api/public/channels?guide=brief")
            with app.LOCK:
                observed["encoders_from_guide_only"] = len(app.ENCODER_LAUNCHES) + len(app.FFMPEG_SESSIONS)
            assert observed["encoders_from_guide_only"] == 0
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
                if args.hd:
                    dimensions = subprocess.run([
                        app.ffprobe_path(), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(Path(root) / "viewer-0.ts"),
                    ], capture_output=True, text=True, check=True, timeout=15).stdout.splitlines()
                    assert "1280,720" in dimensions
                    result[0]["verified_resolution"] = "1280x720"
                if args.measure_idle:
                    stopped_at = time.monotonic()
                    while time.monotonic() - stopped_at < 81:
                        with app.LOCK:
                            still_active = any(session["process"].poll() is None for session in app.FFMPEG_SESSIONS.values())
                            still_active = still_active or any(process.poll() is None for process in app.ENCODER_LAUNCHES.values())
                            still_active = still_active or any(feed.active() for feed in app.LIVE_PLAYOUT.values())
                        if not still_active:
                            break
                        time.sleep(.1)
                    observed["encoding_stopped_after_seconds"] = round(time.monotonic() - stopped_at, 2)
                    assert not still_active
                if args.sustained or args.movie:
                    for number, metrics in enumerate(result):
                        decoded = subprocess.run([app.ffmpeg_path(), "-v", "error", "-f", "mpegts", "-i",
                            str(Path(root) / f"viewer-{number}.ts"), "-map", "0:v:0", "-an", "-f", "framemd5", "-"],
                            capture_output=True, check=True, timeout=100)
                        metrics["decoded_frames"] = sum(line.startswith(b"0,") for line in decoded.stdout.splitlines())
                print(json.dumps(dict(fault=args.fault, creator=observed, viewers=result), indent=2), flush=True)
                if args.sustained:
                    assert all(v["sessions"] >= 5 and v["sequence_gaps"] == 0 and v["stalls"] == 0 and
                               v["last_http_error"] is None and v["decoded_frames"] >= args.seconds * 22 for v in result)
                if args.movie:
                    assert all(v["sessions"] == 1 and v["sequence_gaps"] == 0 and v["stalls"] == 0 and
                               v["last_http_error"] is None and v["decoded_frames"] >= args.seconds * 22 for v in result)
                    assert len(app.FFMPEG_SESSIONS) == 1
                if args.mixed_live_endpoints and not args.fault and not args.sustained:
                    assert observed["peak_encoders"] == 1
                    assert all(viewer["sessions"] == 1 and viewer["stalls"] == 0 and viewer["sequence_gaps"] == 0 for viewer in result)
        finally:
            reaper_stop.set()
            origin.shutdown()
            origin.server_close()
            app.stop_ffmpeg_sessions()


if __name__ == "__main__":
    main()
