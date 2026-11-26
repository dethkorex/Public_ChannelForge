"""Regression checks for live source selection, EPG availability and real FFmpeg handoffs."""

import concurrent.futures
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from tests.creator_fixture import app


class BroadcastTests(unittest.TestCase):
    def test_public_hd_live_has_separate_bitrate_from_the_relay_and_movie_profiles(self):
        direct = app.relay_renditions()
        constrained = app.relay_renditions(constrained=True)
        movie = app.relay_renditions(on_demand=True)
        self.assertEqual(direct[-1][1:5], (1280, 720, 3000, 3400))
        self.assertEqual(constrained[-1], movie[-1])
        self.assertEqual(direct[1], constrained[1])
        self.assertEqual([row[0] for row in direct], ["economy", "balanced", "hd"])

    def test_active_playlist_leases_segments_until_last_viewer_leaves(self):
        feed = app.LivePlayout(lambda _: None)
        feed.last_request = time.monotonic() - 35
        feed.segments.append((0, 0, {"economy": ("#EXTINF:2,", "/ffmpeg-stream/shared/segment000001.ts")}))
        now = time.time()
        session = {"feed": feed, "last_access": now - 100, "process": Mock()}
        with (tempfile.TemporaryDirectory() as temp,
              patch.object(app, "STREAM_DIR", Path(temp) / "empty"),
              patch.dict(app.LIVE_PLAYOUT, {("channel", True, True): feed}, clear=True),
              patch.dict(app.FFMPEG_SESSIONS, {"shared": session}, clear=True),
              patch.object(app, "dispose_encoder") as dispose):
            self.assertEqual(app.reap_idle_sessions_once(now), 0)
            self.assertLess(now - session["last_access"], 40)
            dispose.assert_not_called()
            feed.last_request = time.monotonic() - 80
            self.assertEqual(app.reap_idle_sessions_once(now + 70), 1)
            dispose.assert_called_once_with(session)
            self.assertFalse(app.LIVE_PLAYOUT)

    def test_movie_master_reports_peak_bandwidth_and_keeps_ffmpeg_codec_and_resolution(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            master = directory / "index.m3u8"
            master.write_bytes(b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=570000,AVERAGE-BANDWIDTH=506000,'
                               b'RESOLUTION=854x480,CODECS="avc1.640029,mp4a.40.2"\nindex_balanced.m3u8\n')
            result = app.viewer_encoder_playlist({"directory": directory, "variant_peaks": {"balanced": 690000}},
                                                 "index.m3u8")
            self.assertEqual(result, master.read_bytes().replace(b":BANDWIDTH=570000", b":BANDWIDTH=690000"))

    def test_shutdown_reaps_child_during_first_playlist_wait(self):
        popen = subprocess.Popen
        children = []

        def delayed_encoder(_cmd, **kwargs):
            child = popen([sys.executable, "-c", "import time; time.sleep(25)"], **kwargs)
            children.append(child)
            return child

        start = app.datetime.now(app.timezone.utc)
        source = {"id": "delayed", "source": "local", "title": "Delayed"}
        position = {"item": source, "start": start, "stop": start + app.timedelta(seconds=30), "offset_ms": 0}
        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(app, "STREAM_DIR", Path(root)),
            patch.object(app, "local_media_path", return_value="test.mkv"),
            patch.object(app, "ffmpeg_path", return_value="testffmpeg"),
            patch.object(app, "playback_audio_track", return_value=(0, False)),
            patch.object(app.subprocess, "Popen", side_effect=delayed_encoder),
        ):
            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                pending = pool.submit(
                    app.launch_encoder, "test", {"id": "channel"}, position, "standard", False, 2**26
                )
                deadline = time.monotonic() + 4
                while "test" not in app.ENCODER_LAUNCHES and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertIn("test", app.ENCODER_LAUNCHES)
                app.stop_ffmpeg_sessions()
                with self.assertRaises(ValueError):
                    pending.result(timeout=4)
            self.assertIsNotNone(children[0].poll())
            self.assertNotIn("test", app.ENCODER_LAUNCHES)
            self.assertFalse(list(Path(root).iterdir()))

    def test_substitution_with_shorter_episodes_matches_late_tune_and_guide(self):
        anchor = app.datetime.now(app.timezone.utc)
        channel = {
            "id": "c",
            "schedule_anchor": anchor.isoformat(),
            "items": [
                {"id": "x", "source": "local", "duration_ms": 9000},
                {"id": "a", "duration_ms": 3000},
                {"id": "b", "duration_ms": 2000},
            ],
        }
        moment = anchor + app.timedelta(seconds=6)
        pos = app.schedule_position(channel, moment)
        self.assertEqual((pos["item"]["id"], pos["offset_ms"]), ("a", 1000))
        guide = app.schedule_window(channel, moment, anchor + app.timedelta(seconds=11))
        self.assertEqual(
            [(s["item"]["id"], (s["start"] - anchor).total_seconds()) for s in guide],
            [("a", 5), ("b", 8), ("a", 9)],
        )

    def test_shared_plex_credential_outage_preserves_schedule(self):
        row = {"id": "persist", "items": [{"id": "ep", "title": "Episode", "plex_server_id": "other"}]}
        with (
            patch.object(app, "channels", return_value=[row]),
            patch.object(app, "config", return_value={}),
            patch.object(app, "asset_manifest", return_value=[]),
            patch.object(app, "plex_credentials", side_effect=ValueError("Plex unavailable")),
        ):
            info = app.public_channel_catalog("http://example.test", brief=True)["channels"][0]
        self.assertEqual(info["programs"][0]["title"], "Episode")
        self.assertEqual(info["state"], "reconnecting")

    def test_missing_program_alternate_preserves_utc_boundaries(self):
        anchor = app.datetime.now(app.timezone.utc)
        bad = {
            "id": "bad",
            "title": "Unavailable",
            "source": "local",
            "file_path": "/nonexistent/example/movie.mkv",
            "duration_ms": 45000,
        }
        ok = {"id": "ok", "title": "Replacement", "duration_ms": 60000}
        channel = {"id": "demo", "items": [bad, ok], "schedule_anchor": anchor.isoformat()}
        now = anchor + app.timedelta(seconds=12)
        position = app.schedule_position(channel, now)
        self.assertEqual((position["item"], position["start"], position["offset_ms"]), (ok, anchor, 12000))
        self.assertEqual(position["stop"], anchor + app.timedelta(seconds=45))
        self.assertEqual(app.schedule_position(channel, position["stop"])["offset_ms"], 0)
        window = app.schedule_window(channel, anchor, anchor + app.timedelta(seconds=80))
        self.assertEqual([x["item"] for x in window], [ok, ok])
        self.assertEqual(window[1]["start"], position["stop"])
        self.assertEqual(channel["items"], [bad, ok])

    def test_overload_keeps_registration_and_retries_are_bounded(self):
        creator = app.LivePlayout(lambda _: None, channel_id="fixture")
        creator.open_program = unittest.mock.Mock(side_effect=ValueError("Plex unavailable"))
        with patch.object(app.time, "sleep"), patch.object(app, "PLAYBACK_ERRORS", {}):
            creator.run()
        self.assertEqual(creator.open_program.call_count, 4)
        self.assertTrue(creator.stopped)
        busy = app.LivePlayout(
            unittest.mock.Mock(side_effect=app.EncoderCapacityError("busy")), channel_id="busy"
        )
        with (
            patch.object(app, "LIVE_PLAYOUT", {("busy", True, False): busy}),
            patch.object(app, "PLAYBACK_ERRORS", {}),
        ):
            busy.run()
            self.assertEqual(app.broadcast_health("busy"), "overloaded")
            self.assertEqual(busy.open_program.call_count, 1)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg / FFprobe required")
    def test_two_viewers_recover_an_encoder_loss_across_programs(self):
        ffmpeg = shutil.which("ffmpeg")
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            movie = directory / "movie.mp4"
            show = directory / "episode.mkv"

            def make(path, duration, codec, audio):
                cmd = [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=12"]
                if audio:
                    cmd += ["-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000"]
                subprocess.run(
                    [*cmd, "-t", str(duration), "-c:v", codec, "-threads:v", "1", "-g", "12", str(path)],
                    check=True,
                    timeout=20,
                )

            make(movie, 12, "libx264", True)
            make(show, 4, "mpeg4", False)
            items = [
                {
                    "id": str(i),
                    "title": p.name,
                    "source": "local",
                    "file_path": str(p),
                    "duration_ms": duration * 1000,
                }
                for i, (p, duration) in enumerate(((movie, 12), (show, 4), (show, 4)))
            ]
            now = app.datetime.now(app.timezone.utc)
            channel = {
                "id": "live",
                "items": items,
                "schedule_anchor": now.isoformat(),
                "air_until": (now + app.timedelta(seconds=20)).isoformat(),
            }
            settings = {
                "creator_profile": {"name": "QA"},
                "public_tunnel": False,
                "public_url": "https://qa.example",
                "plex_token": "",
                "plex_url": "",
            }
            with (
                patch.object(app, "STREAM_DIR", directory / "streams"),
                patch.object(app, "channels", return_value=[channel]),
                patch.object(app, "local_libraries", return_value=[{"path": root}]),
                patch.object(app, "config", return_value=settings),
                patch.object(app, "FFMPEG_SESSIONS", {}),
                patch.object(app, "LIVE_PLAYOUT", {}),
                patch.object(app, "PLAYBACK_ERRORS", {}),
                patch.object(app, "BROADCAST_EVENTS", app.deque(maxlen=100)),
            ):

                def viewer(kind):
                    handler = object.__new__(kind)
                    handler.send_bytes = unittest.mock.Mock()
                    handler.broadcast("live", "master", None)
                    return handler.send_bytes.call_args.args[1]

                try:
                    with concurrent.futures.ThreadPoolExecutor(2) as pool:
                        masters = list(pool.map(viewer, [app.Handler, app.PublicMediaHandler]))
                    self.assertEqual(masters[0], masters[1])
                    self.assertIn(b"#EXT-X-STREAM-INF:BANDWIDTH=4000000\n", masters[0])
                    self.assertEqual(len(app.LIVE_PLAYOUT), 1)
                    feed = next(iter(app.LIVE_PLAYOUT.values()))
                    self.assertIn("#EXTINF:", feed.playlist("balanced"))
                    session = next(iter(app.FFMPEG_SESSIONS.values()))
                    self.assertIsNone(session["process"].poll())
                    first_segment = next(session["directory"].glob("segmentbalanced_*.ts"))
                    probe = json.loads(
                        subprocess.check_output(
                            [
                                shutil.which("ffprobe"),
                                "-v",
                                "error",
                                "-show_streams",
                                "-show_packets",
                                "-of",
                                "json",
                                str(first_segment),
                            ],
                            timeout=6,
                        )
                    )
                    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
                    audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
                    self.assertEqual(
                        (video["codec_name"], video["pix_fmt"], audio["codec_name"], audio["channels"]),
                        ("h264", "yuv420p", "aac", 2),
                    )
                    packets = [p for p in probe["packets"] if p["stream_index"] == video["index"]]
                    self.assertIn("K", packets[0]["flags"])
                    dts = [int(p["dts"]) for p in packets]
                    self.assertTrue(all(a < b for a, b in zip(dts, dts[1:])))
                    diag = app.creator_broadcast_diagnostics()
                    self.assertEqual(diag["state"], "broadcasting")
                    self.assertGreater(diag["encoders"][0]["rss_bytes"], 0)
                    self.assertNotIn(str(directory), json.dumps(diag))
                    session["process"].kill()
                    deadline = time.monotonic() + 34
                    while time.monotonic() < deadline and not (feed.finished or feed.stopped):
                        feed.playlist("balanced")
                        time.sleep(0.15)
                    self.assertTrue(feed.finished, feed.error)
                    playlist = feed.playlist("balanced")
                    self.assertTrue(playlist.endswith("#EXT-X-ENDLIST\n"))
                    self.assertGreaterEqual(playlist.count("#EXT-X-DISCONTINUITY\n"), 3)
                    data = bytearray()
                    for key, name in re.findall(r"(?m)^/ffmpeg-stream/([^/]+)/([^\n]+\.ts)$", playlist):
                        data.extend((app.FFMPEG_SESSIONS[key]["directory"] / name).read_bytes())
                    output = subprocess.check_output(
                        [
                            ffmpeg,
                            "-loglevel",
                            "error",
                            "-f",
                            "mpegts",
                            "-i",
                            "pipe:0",
                            "-map",
                            "0:v:0",
                            "-f",
                            "framemd5",
                            "-",
                        ],
                        input=data,
                        timeout=10,
                        stderr=subprocess.PIPE,
                    )
                    self.assertGreater(sum(line.startswith(b"0,") for line in output.splitlines()), 100)
                    self.assertIn("program_failure", [e["event"] for e in app.BROADCAST_EVENTS])
                    with feed.condition:
                        feed.last_request = float("-inf")
                    app.reap_idle_sessions_once(now=time.time() + 601)
                    self.assertFalse(app.FFMPEG_SESSIONS)
                finally:
                    app.stop_ffmpeg_sessions()


if __name__ == "__main__":
    unittest.main()
