"""Regression checks for live source selection, EPG availability and real FFmpeg handoffs."""

import concurrent.futures
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from tests.creator_fixture import app


class BroadcastTests(unittest.TestCase):
    def test_large_saved_lineup_reaches_the_twelve_hour_guide_without_encoding(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            moment = app.datetime.now(app.timezone.utc)
            items = [{"id": str(index), "title": f"Title {index}", "type": "movie" if index % 2 else "episode",
                      "duration_ms": 1_800_000, "summary": "x" * 600} for index in range(8_000)]
            payload = {"name": "Long lineup", "number": "31", "items": items,
                       "branding": {"enabled": False},
                       "schedule_anchor": (moment - app.timedelta(seconds=1818*7_000)).isoformat()}
            body = json.dumps(payload).encode()
            self.assertGreater(len(body), 5_000_000)
            handler = object.__new__(app.Handler)
            handler.headers = {"Content-Length": str(len(body))}
            handler.rfile = io.BytesIO(body)
            with (patch.object(app, "CONFIG_FILE", directory / "config.json"),
                  patch.object(app, "CHANNELS_FILE", directory / "channels.json"),
                  patch.object(app, "ASSETS_DIR", directory / "assets"),
                  patch.object(app, "EXPORT_DIR", directory / "exports"),
                  patch.object(app, "backup_state"), patch.object(app, "sync_public_channel_numbers"),
                  patch.object(app, "start_ffmpeg_session") as encoder):
                saved = app.save_channel(handler.body(app.CHANNEL_SAVE_BODY_BYTES))
                self.assertEqual(len(saved["items"]), len(items))
                self.assertEqual(saved["branding"], {"enabled": True, "seconds": 18, "style": "studio", "ambience": "soft-synth"})
                self.assertEqual(saved["logo_asset_id"], "builtin-studio-studio-logo")
                self.assertEqual(saved["background_asset_id"], "builtin-studio-studio-guide-background")
                self.assertEqual(saved["corner_bug_asset_id"], "builtin-studio-studio-corner-bug")
                catalog = app.public_channel_catalog("https://studio.example", extended=True)
                scheduled = catalog["channels"][0]["programs"]
                self.assertTrue(any(row["title"] == "Title 7023" for row in scheduled))
                end = app.parse_timestamp(scheduled[-1]["stop"])
                self.assertGreaterEqual(end, moment + app.timedelta(hours=12))
                self.assertTrue((directory / "exports" / "guide.xml").is_file())
                encoder.assert_not_called()

    def test_native_up_next_follows_the_full_episode_and_repeats_in_utc(self):
        anchor = app.datetime(2026, 11, 27, tzinfo=app.timezone.utc)
        episode = {"id":"ep-7", "title":"A visit on Christmas Eve", "series":"Sample Show", "duration_ms":1_800_000}
        movie = {"id":"movie-1", "title":"Holiday Home", "duration_ms":3_600_000}
        channel = {"id":"candidate", "schedule_anchor":anchor.isoformat(), "repeat_on_air":True,
                   "playback_mode":"custom", "items":[episode,movie], "branding":{"enabled":True,"seconds":25}}
        first = app.schedule_position(channel, anchor + app.timedelta(minutes=29,seconds=59))
        self.assertEqual((first["item"]["id"], first["stop"]), ("ep-7", anchor + app.timedelta(minutes=30)))
        card = app.schedule_position(channel, first["stop"])
        self.assertEqual(card["item"]["next_program"], movie)
        self.assertEqual(card["stop"] - card["start"], app.timedelta(seconds=25))
        following = app.schedule_position(channel, card["stop"])
        self.assertEqual((following["item"], following["offset_ms"]), (movie,0))
        final = app.schedule_position(channel, following["stop"])
        self.assertEqual(final["item"]["next_program"], episode)
        self.assertEqual(app.schedule_position(channel, final["stop"])["item"], episode)
        self.assertFalse(app.run_finished(channel, anchor + app.timedelta(days=10)))

    def test_upgrade_adds_default_card_without_moving_the_current_programme(self):
        moment = app.datetime.now(app.timezone.utc)
        anchor = moment - app.timedelta(seconds=3600 * 21 + 2025)
        lineup = [{"id": "one", "title": "First Programme", "duration_ms": 1_800_000},
                  {"id": "two", "title": "Second Programme", "duration_ms": 1_800_000}]
        old = {"id": "legacy", "name": "Legacy", "number": "41", "items": lineup, "repeat_on_air": True,
               "logo_asset_id": "my-existing-logo", "schedule_anchor": anchor.isoformat()}
        custom = {**old, "id": "configured", "number": "42", "branding": {"enabled": False, "style": "midnight"}}
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            with (patch.object(app, "CHANNELS_FILE", folder / "channels.json"),
                  patch.object(app, "CONFIG_FILE", folder / "config.json"),
                  patch.object(app, "ASSETS_DIR", folder / "assets"),
                  patch.object(app, "EXPORT_DIR", folder / "exports"),
                  patch.object(app, "PLUGINS_DIR", folder / "plugins"),
                  patch.object(app, "generate_exports"), patch.object(app, "persistent_client_identifier")):
                app.atomic_json(app.CHANNELS_FILE, [old, custom])
                expected = app.schedule_position(old, moment)
                app.ensure_files()
                current, configured = app.channels()
                self.assertEqual(current["branding"], {"enabled": True, "seconds": 18, "style": "studio", "ambience": "soft-synth"})
                self.assertEqual(current["logo_asset_id"], "my-existing-logo")
                self.assertEqual(current["background_asset_id"], "builtin-studio-studio-guide-background")
                actual = app.schedule_position(current, moment)
                self.assertEqual((actual["item"]["id"], actual["offset_ms"]), (expected["item"]["id"], expected["offset_ms"]))
                self.assertEqual(configured["branding"], custom["branding"])
                app.ensure_files()
                self.assertEqual(app.channels()[0], current)

    def test_prepared_up_next_encoder_is_not_reused_after_a_lineup_edit(self):
        moment = app.datetime.now(app.timezone.utc)
        channel = {"id": "fixture", "updated_at": "before"}
        slot = {"start": moment, "stop": moment + app.timedelta(seconds=18),
                "item": {"id": "up-next-0", "source": "interstitial", "next_program": {"id": "movie-a"}}}
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "index.m3u8").touch()
            process = unittest.mock.Mock()
            process.poll.return_value = None
            with (patch.object(app, "STREAM_DIR", folder), patch.object(app, "FFMPEG_SESSIONS", {}),
                  patch.object(app, "reserve_encoder", return_value=app.STREAM_ROLLING_BUDGET),
                  patch.object(app, "launch_encoder", return_value="prepared") as launch):
                self.assertEqual(app.start_or_reuse_encoder(channel, position=slot), "prepared")
                key = launch.call_args.kwargs["reuse_key"]
                app.FFMPEG_SESSIONS["cached"] = {"reuse_key": key, "process": process, "directory": folder}
                self.assertEqual(app.start_or_reuse_encoder(channel, position=slot), "cached")
                updated = {**channel, "updated_at": "after"}
                slot["item"]["next_program"] = {"id": "movie-b"}
                self.assertEqual(app.start_or_reuse_encoder(updated, position=slot), "prepared")
                self.assertNotEqual(key, launch.call_args.kwargs["reuse_key"])

    def test_up_next_matches_playback_when_the_scheduled_movie_is_missing(self):
        anchor = app.datetime(2026, 11, 27, tzinfo=app.timezone.utc)
        first = {"id": "ep-7", "title": "A visit on Christmas Eve", "duration_ms": 1_800_000}
        missing = {"id": "unavailable", "title": "Missing movie", "source": "local", "duration_ms": 3_600_000}
        available = {"id": "available", "title": "Holiday Home", "duration_ms": 3_600_000}
        channel = {"schedule_anchor": anchor.isoformat(), "repeat_on_air": True,
                   "items": [first, missing, available], "branding": {"enabled": True}}
        card = app.schedule_position(channel, anchor + app.timedelta(minutes=30))
        self.assertEqual(card["item"]["next_program"], available)
        self.assertEqual(card["item"]["title"], "Up Next · Holiday Home")
        self.assertEqual(app.schedule_position(channel, card["stop"])["item"], available)

    def test_up_next_uses_series_poster_then_falls_back_to_valid_episode_art(self):
        series = "/library/metadata/11/thumb/1"
        episode = "/library/metadata/42/thumb/1"
        upcoming = {"id": "42", "series": "Sample Show", "series_thumb": series,
                    "thumb": episode, "plex_server_id": "source-b"}
        output = io.BytesIO()
        Image.new("RGB", (160, 90), "#36bad2").save(output, "PNG")
        with patch.object(app, "plex_binary", side_effect=[(b"invalid", "image/png"), (output.getvalue(), "image/png")]) as fetch:
            poster = app.channel_branding_poster(upcoming)
        self.assertEqual([call.args for call in fetch.call_args_list], [(series, "source-b", "poster"), (episode, "source-b", "poster")])
        self.assertEqual(poster.size, (160, 90))
        self.assertEqual(poster.getpixel((80, 45)), (54, 186, 210))

    def test_known_viewers_move_from_starting_through_live_and_cooldown(self):
        with patch.object(app.time, "monotonic", return_value=100) as clock:
            feed = app.LivePlayout(lambda _: None, channel_id="sample")
            self.assertEqual(feed.activity()["state"], "cooldown")
            feed.heartbeat("1" * 32)
            self.assertEqual(feed.activity()["state"], "starting")
            feed.segments.append((0, 0, {"economy": ("#EXTINF:2,", "/ffmpeg-stream/prog/segment0.ts")}))
            feed.heartbeat("2" * 32)
            feed.heartbeat("2" * 32)
            self.assertEqual(feed.activity()["state"], "live")
            self.assertEqual(feed.activity()["viewers"], 2)
            feed.record_upload(2000)
            self.assertEqual(feed.activity()["upload_bps"], 1600)
            clock.return_value += app.LIVE_VIEWER_TTL
            self.assertEqual(feed.activity()["state"], "cooldown")
            self.assertEqual(feed.activity()["viewers"], 0)
            feed.heartbeat("3" * 32)
            self.assertEqual(feed.activity()["state"], "live")
            self.assertEqual(feed.activity()["peak_viewers"], 2)
            clock.return_value += app.LIVE_VIEWER_TTL + app.LIVE_VIEWER_GRACE
            self.assertEqual(feed.activity()["state"], "idle")
            self.assertEqual(feed.activity()["upload_bps"], 0)

    def test_last_explicit_departure_reaps_the_encoder_and_ignores_late_playlist_requests(self):
        feed = app.LivePlayout(lambda _: None, channel_id="sample")
        first, second = "1" * 32, "2" * 32
        with patch.object(app.time, "monotonic", return_value=100) as clock:
            feed.heartbeat(first)
            feed.heartbeat(second)
            feed.release_viewer(first)
            feed.heartbeat(first)
            self.assertEqual(feed.activity()["viewers"], 1)
            clock.return_value += 6
            self.assertTrue(feed.active())
            feed.release_viewer(second)
            self.assertEqual(feed.activity()["state"], "cooldown")
            clock.return_value += 2
            feed.heartbeat(second)
            self.assertTrue(feed.active())
            clock.return_value += 1
            session = {"process": Mock(poll=Mock(return_value=None)), "feed": feed, "last_access": 200}
            with (patch.object(app, "LIVE_PLAYOUT", {("sample", True, False): feed}),
                  patch.object(app, "FFMPEG_SESSIONS", {"sample": session}),
                  patch.object(app, "stop_encoder") as stop):
                app.reap_idle_sessions_once(now=200)
                self.assertFalse(app.LIVE_PLAYOUT)
                stop.assert_called_once_with(session)

    def test_new_and_legacy_viewers_keep_the_feed_after_another_viewer_leaves(self):
        with patch.object(app.time, "monotonic", return_value=100) as clock:
            feed = app.LivePlayout(lambda _: None)
            feed.heartbeat()
            feed.heartbeat("a" * 32)
            feed.release_viewer("a" * 32)
            clock.return_value += 8
            self.assertTrue(feed.active())
            clock.return_value += app.LIVE_LEGACY_IDLE
            self.assertFalse(feed.active())
            feed = app.LivePlayout(lambda _: None)
            feed.heartbeat("a" * 32)
            feed.release_viewer("a" * 32)
            clock.return_value += 1
            feed.heartbeat("b" * 32)
            clock.return_value += 8
            self.assertTrue(feed.active())

    def test_public_hd_live_has_separate_bitrate_from_the_relay_and_movie_profiles(self):
        direct = app.relay_renditions()
        constrained = app.relay_renditions(constrained=True)
        movie = app.relay_renditions(on_demand=True)
        self.assertEqual(direct[-1][1:3], (1280, 720))
        self.assertEqual(direct[-1][-1], 2500000)
        self.assertLess(direct[-1][4], 2500)
        self.assertEqual(constrained[-1], movie[-1])
        self.assertEqual(direct[1], constrained[1])
        self.assertEqual([row[0] for row in direct], ["economy", "balanced", "hd"])
        self.assertEqual([row[0] for row in app.relay_renditions(limits=(480, 1100))], ["economy", "balanced"])
        self.assertEqual(app.relay_renditions(limits=(480, 1100))[-1][-1], 1100000)
        self.assertEqual(app.relay_renditions(limits=(270, 350))[-1][2], 270)
        self.assertEqual(app.broadcast_settings({}), (720, 2500))
        with self.assertRaises(ValueError):
            app.broadcast_settings({"broadcast_height": 720, "broadcast_kbps": 2501})
        master = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=270000\neconomy.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2500000\nhd.m3u8\n"
        self.assertEqual(app.limit_hls_master(master, 400000), master.split(b"#EXT-X-STREAM-INF:BANDWIDTH=2500000")[0])
        self.assertEqual(app.limit_hls_master(master, 0), master)
        economy = b"#EXT-X-STREAM-INF:BANDWIDTH=270000\n/broadcast/test/feed/economy.m3u8?viewer=example\n"
        balanced = b"#EXT-X-STREAM-INF:BANDWIDTH=370000,RESOLUTION=854x356\nindex_balanced.m3u8\n"
        widescreen_hd = b"#EXT-X-STREAM-INF:BANDWIDTH=950000,RESOLUTION=1280x532\nindex_hd.m3u8\n"
        master = b"#EXTM3U\n" + economy + balanced + widescreen_hd
        self.assertEqual(app.limit_hls_master(master, 400000), b"#EXTM3U\n" + economy)
        self.assertEqual(app.limit_hls_master(master, 1000000), b"#EXTM3U\n" + economy + balanced)

    def test_active_playlist_leases_segments_until_last_viewer_leaves(self):
        feed = app.LivePlayout(lambda _: None)
        feed.last_request = time.monotonic() - (app.LIVE_VIEWER_TTL + app.LIVE_VIEWER_GRACE - 1)
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

    def test_movie_only_reports_end_after_successful_encoder_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            completed = b"#EXTM3U\n#EXTINF:2,\nsegment0.ts\n#EXT-X-ENDLIST\n"
            (directory / "index.m3u8").write_bytes(completed)
            process = Mock()
            session = {"directory": directory, "on_demand": True, "process": process}
            process.poll.return_value = None
            self.assertNotIn(b"#EXT-X-ENDLIST", app.viewer_encoder_playlist(session, "index.m3u8"))
            process.poll.return_value = 1
            with self.assertRaises(app.MovieEncodingError):
                app.viewer_encoder_playlist(session, "index.m3u8")
            process.poll.return_value = 0
            self.assertEqual(app.viewer_encoder_playlist(session, "index.m3u8"), completed)

    def test_movie_waits_for_a_playable_startup_buffer_in_each_rendition(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            master = directory / "index.m3u8"
            master.write_bytes(b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000\nindex_economy.m3u8\n"
                               b"#EXT-X-STREAM-INF:BANDWIDTH=1500000\nindex_hd.m3u8\n")
            one = b"#EXTM3U\n#EXTINF:2,\nsegment0.ts\n"
            two = one + b"#EXTINF:2,\nsegment1.ts\n"
            economy, hd = directory / "index_economy.m3u8", directory / "index_hd.m3u8"
            economy.write_bytes(two)
            hd.write_bytes(one)
            self.assertFalse(app.movie_start_ready(master, None))
            hd.write_bytes(two)
            self.assertTrue(app.movie_start_ready(master, None))
            self.assertFalse(app.movie_start_ready(master, 1))
            master.write_bytes(one + b"#EXT-X-ENDLIST\n")
            self.assertTrue(app.movie_start_ready(master, 0))

    def test_cleanup_retains_old_segments_still_advertised_by_live_playlist(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            feed = app.LivePlayout(lambda _: None)
            feed.heartbeat("1" * 32)
            keep = directory / "segment000001.ts"
            remove = directory / "segment000000.ts"
            now = time.time()
            for path in (keep, remove):
                path.write_bytes(b"ts")
                os.utime(path, (now - 100, now - 100))
            feed.segments.append((0, 0, {"economy": ("#EXTINF:2,", f"/ffmpeg-stream/shared/{keep.name}")}))
            session = {"feed": feed, "last_access": now, "directory": directory, "process": Mock()}
            with (patch.object(app, "STREAM_DIR", directory),
                  patch.dict(app.LIVE_PLAYOUT, {("channel", True, True): feed}, clear=True),
                  patch.dict(app.FFMPEG_SESSIONS, {"shared": session}, clear=True),
                  patch.object(app, "maintain_stream_storage")):
                app.reap_idle_sessions_once(now)
            self.assertTrue(keep.exists())
            self.assertFalse(remove.exists())

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
                    self.assertIn(b"#EXT-X-STREAM-INF:BANDWIDTH=2500000\n", masters[0])
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
