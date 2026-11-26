"""Measure one on-demand FFmpeg session shared by four simultaneous tunes.

Run from the checkout with FFmpeg/FFprobe on PATH and psutil installed for QA.
All Creator data and generated footage live in a new temporary directory.
"""

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import psutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", action="store_true", help="Use the public adaptive ladder")
    parser.add_argument("--seconds", type=int, default=16)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cf-broadcast-bench-") as temp:
        os.environ["PUBLIC_CHANNELFORGE_HOME"] = temp
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import Public_ChannelForge as app

        app.ensure_files()
        source_dir = Path(temp) / "media"
        source_dir.mkdir()
        video = source_dir / "fixture.mp4"
        ffmpeg = shutil.which("ffmpeg")
        subprocess.run(
            [
                ffmpeg,
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
                str(args.seconds),
                "-c:v",
                "libx264",
                "-threads:v",
                "2",
                "-preset",
                "veryfast",
                "-g",
                "24",
                "-keyint_min",
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
                "-movflags",
                "+faststart",
                str(video),
            ],
            check=True,
        )
        app.save_local_library("Fixture", str(source_dir))
        channel = {
            "id": "fixture",
            "on_demand": True,
            "items": [
                {
                    "id": "movie",
                    "title": "Movie",
                    "source": "local",
                    "video_codec": "h264",
                    "file_path": str(video),
                    "duration_ms": args.seconds * 1000,
                }
            ],
        }
        process = psutil.Process()
        stop = threading.Event()
        samples = {}
        peak = [0]

        def measure():
            while not stop.is_set():
                rss = 0
                for child in process.children():
                    try:
                        cpu = child.cpu_times()
                        samples[child.pid] = cpu.user + cpu.system
                        rss += child.memory_info().rss
                    except psutil.Error:
                        pass
                peak[0] = max(peak[0], rss)
                stop.wait(0.05)

        sampler = threading.Thread(target=measure)
        sampler.start()
        try:
            start = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(4) as pool:
                ids = list(
                    pool.map(
                        lambda _: app.start_ffmpeg_session(channel, public_delivery=args.public), range(4)
                    )
                )
            ready = time.perf_counter() - start
            assert len(set(ids)) == 1 and len(app.FFMPEG_SESSIONS) == 1
            session = app.FFMPEG_SESSIONS[ids[0]]
            assert session["process"].wait(timeout=args.seconds + 30) == 0
            elapsed = time.perf_counter() - start
        finally:
            stop.set()
            sampler.join()
        try:
            directory = session["directory"]
            playlist = next(iter(directory.glob("index_hd.m3u8")), directory / "index.m3u8")
            output = subprocess.check_output(
                [ffmpeg, "-v", "error", "-i", str(playlist), "-map", "0:v", "-f", "framemd5", "-"]
            )
            frames = sum(line.startswith(b"0,") for line in output.splitlines())
            assert frames == args.seconds * 24, frames
            print(
                json.dumps(
                    dict(
                        public=args.public,
                        seconds=args.seconds,
                        first_playlist_s=round(ready, 3),
                        elapsed_s=round(elapsed, 3),
                        child_cpu_s=round(sum(samples.values()), 3),
                        peak_child_mib=round(peak[0] / 1024**2, 1),
                        transport_bytes=sum(p.stat().st_size for p in directory.glob("*.ts")),
                        video_encoder=session.get("video_encoder", "libx264"),
                        frames=frames,
                        viewer_count=len(ids),
                    )
                )
            )
        finally:
            app.stop_ffmpeg_sessions()


if __name__ == "__main__":
    main()
