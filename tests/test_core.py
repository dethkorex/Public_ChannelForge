import json
import os
import tempfile
import threading
import unittest
import http.client
import gzip
import io
from unittest import mock
from pathlib import Path

os.environ["PUBLIC_CHANNELFORGE_HOME"] = tempfile.mkdtemp(prefix="public-channelforge-test-")
import Public_ChannelForge as app


class CoreTests(unittest.TestCase):
    def setUp(self):
        app.DATA_DIR.mkdir(parents=True, exist_ok=True)
        app.atomic_json(app.CONFIG_FILE, {"plex_url": "http://127.0.0.1:32400", "plex_token": "test", "guide_hours": 2, "port": 8787, "bind_host": "127.0.0.1", "public_url": ""})
        app.atomic_json(app.CHANNELS_FILE, [])
        app.VAULT_CATALOG_FILE.unlink(missing_ok=True)
        app.LOCAL_LIBRARY_CACHE.clear()
        app.PLEX_RESOURCE_CACHE.update({"expires": 0.0, "servers": {}})

    def test_public_config_hides_token(self):
        value = app.config(True)
        self.assertEqual(value["plex_token"], "")
        self.assertTrue(value["token_saved"])

    def test_atomic_json_retries_a_windows_sharing_violation(self):
        target = app.DATA_DIR / "retry.json"
        replace = Path.replace
        error = PermissionError("sharing violation")
        error.winerror = 32
        attempts = []

        def replace_after_unlock(temporary, destination):
            attempts.append(temporary)
            if len(attempts) == 1:
                raise error
            return replace(temporary, destination)

        with mock.patch.object(Path, "replace", replace_after_unlock):
            app.atomic_json(target, {"complete": True})
        self.assertEqual(app.load_json(target, {}), {"complete": True})
        self.assertEqual(len(attempts), 2)
        self.assertFalse(attempts[0].exists())

    def test_public_media_origin_does_not_publish_creator_controls(self):
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.PublicMediaHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            for method, path, expected in [
                ("GET", "/api/public/channels", 200),
                ("GET", "/api/public/vault", 200),
                ("GET", "/api/local-creator", 404),
                ("GET", "/api/status", 404),
                ("GET", "/api/plex/image?path=/library/sections", 404),
                ("POST", "/api/setup", 405),
                ("POST", "/api/update/prepare", 405),
                ("PUT", "/api/channels/channel-id", 405),
                ("DELETE", "/api/directory/publish", 405),
            ]:
                with self.subTest(method=method, path=path):
                    client.request(method, path)
                    response = client.getresponse()
                    self.assertEqual(response.status, expected)
                    response.read()
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)

    def test_automatic_media_origin_does_not_take_over_a_busy_port(self):
        with app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler) as occupied:
            with mock.patch.object(app, "PUBLIC_MEDIA_PORT", occupied.server_port), mock.patch.object(app, "PUBLIC_MEDIA_SERVER", None):
                try:
                    app.start_public_media_server(automatic=True)
                    self.assertNotEqual(app.PUBLIC_MEDIA_SERVER.server_port, occupied.server_port)
                    relay = app.PUBLIC_MEDIA_SERVER
                    with mock.patch.object(app, "relay_reaches_this_creator") as proof:
                        self.assertFalse(app.attach_media_funnel("https://studio.example"))
                        proof.assert_not_called()
                    self.assertIs(app.PUBLIC_MEDIA_SERVER, relay)
                    self.assertTrue(app.relay_reaches_this_creator(f"http://127.0.0.1:{relay.server_port}"))
                finally:
                    app.stop_public_media_server()

    def test_failed_funnel_probe_keeps_the_verified_relay_listener(self):
        with mock.patch.object(app, "PUBLIC_MEDIA_PORT", 0), mock.patch.object(app, "PUBLIC_MEDIA_SERVER", None):
            try:
                app.start_public_media_server(automatic=True)
                relay = app.PUBLIC_MEDIA_SERVER
                with mock.patch.object(app, "relay_reaches_this_creator", return_value=False):
                    self.assertFalse(app.attach_media_funnel("https://studio.example"))
                self.assertIs(app.PUBLIC_MEDIA_SERVER, relay)
                self.assertTrue(app.relay_reaches_this_creator(f"http://127.0.0.1:{relay.server_port}"))
            finally:
                app.stop_public_media_server()

    def test_new_channel_publication_is_queued_and_can_be_disabled(self):
        with mock.patch.object(app, "PUBLISH_CHANGED", threading.Event()):
            self.assertTrue(app.queue_channel_publication({"name": "My new channel"}, True))
            self.assertTrue(app.PUBLISH_CHANGED.is_set())
            first = app.config()["pending_creator_profile"]
            self.assertEqual(first["name"], "My new channel")
            self.assertTrue(app.queue_channel_publication({"name": "Another channel"}, True))
            self.assertEqual(app.config()["pending_creator_profile"], first)
            self.assertFalse(app.queue_channel_publication({"name": "Local channel"}, False))
            self.assertFalse(app.config(True)["publishing_pending"])
            self.assertFalse(app.config()["auto_publish_channels"])
            app.queue_existing_publication(has_movies=True)
            self.assertFalse(app.config(True)["publishing_pending"])

    def test_cached_movies_queue_a_studio_once(self):
        app.atomic_json(app.CONFIG_FILE, {"plex_profile": {"server_name": "Plex Studio"}})
        app.atomic_json(app.VAULT_CATALOG_FILE, {"items": [{"id": "1", "type": "movie"}]})
        app.queue_existing_publication()
        profile = app.config()["pending_creator_profile"]
        self.assertEqual(profile["name"], "Plex Studio")
        app.queue_existing_publication(has_movies=True)
        self.assertEqual(app.config()["pending_creator_profile"], profile)

    def test_remote_movie_filters_apply_across_pages(self):
        app.atomic_json(app.VAULT_CATALOG_FILE, {"items": [
            {"id": "1", "type": "movie", "title": "Alpine", "year": 1996, "genres": ["Drama"]},
            {"id": "2", "type": "movie", "title": "Forest", "year": 2005, "genres": ["Adventure"], "summary": "A mountain trek"},
            {"id": "3", "type": "movie", "title": "Valley", "year": 2002, "genres": ["Adventure"], "summary": "A mountain race"},
        ]})
        filters = {"q": ["mountain"], "genre": ["Adventure"], "decade": ["2000s"], "sort": ["newest"]}
        first = app.search_public_vault("https://studio.example", filters, 0, 1)
        second = app.search_public_vault("https://studio.example", filters, first["next_offset"], 1)
        self.assertEqual(first["total"], 2)
        self.assertEqual(first["genres"], ["Adventure", "Drama"])
        self.assertEqual([item["title"] for item in first["items"] + second["items"]], ["Forest", "Valley"])
        self.assertIsNone(second["next_offset"])

    def test_vault_hides_duplicate_releases_from_shared_plex_libraries(self):
        app.atomic_json(app.VAULT_CATALOG_FILE, {"items": [
            {"id": "7", "plex_server_id": "a", "type": "movie", "title": "Rewind!", "year": 1985},
            {"id": "8", "plex_server_id": "b", "type": "movie", "title": "REWIND", "year": "1985"},
            {"id": "9", "plex_server_id": "c", "type": "movie", "title": "Rewind", "year": 2004},
        ]})
        self.assertEqual([item["year"] for item in app.vault_items()], [1985, 2004])

    def test_local_movie_catalog_uses_loopback_even_when_studio_is_public(self):
        app.atomic_json(app.CONFIG_FILE, {"public_url": "https://public-studio.example"})
        app.atomic_json(app.VAULT_CATALOG_FILE, {"items": [
            {"id": "42", "title": "Movie", "type": "movie", "thumb": "/library/metadata/42/thumb/1"},
        ]})
        with app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler) as local:
            threading.Thread(target=local.serve_forever, daemon=True).start()
            client = http.client.HTTPConnection("127.0.0.1", local.server_port, timeout=5)
            try:
                client.request("GET", "/api/public/vault")
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                movie = json.loads(response.read())["items"][0]
                direct = f"http://127.0.0.1:{local.server_port}/"
                self.assertTrue(movie["poster_url"].startswith(direct))
                self.assertTrue(movie["stream_url"].startswith(direct))
                self.assertTrue(movie["thumbnail_url"].endswith("&size=mini"))
                self.assertTrue(app.vault_items()[0]["poster_url"].startswith("https://public-studio.example/"))
            finally:
                client.close()
                local.shutdown()

    def test_public_movie_catalog_can_be_read_in_pages_without_changing_legacy_response(self):
        app.atomic_json(app.VAULT_CATALOG_FILE, {"items": [
            {"id": "1", "title": "First", "type": "movie"},
            {"id": "1", "title": "Duplicate", "type": "movie"},
            {"id": "2", "title": "An episode", "type": "episode"},
            {"id": "3", "title": "Second", "type": "movie"},
            {"id": "4", "title": "Third", "type": "movie"},
        ]})
        with app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler) as local:
            threading.Thread(target=local.serve_forever, daemon=True).start()
            client = http.client.HTTPConnection("127.0.0.1", local.server_port, timeout=5)
            try:
                def read(query):
                    client.request("GET", "/api/public/vault" + query)
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    return json.loads(response.read())

                first = read("?limit=2")
                following = read("?limit=2&offset=2")
                original = read("")
                self.assertEqual(first["next_offset"], 2)
                self.assertIsNone(following["next_offset"])
                self.assertNotIn("next_offset", original)
                self.assertEqual(first["items"] + following["items"], original["items"])
                self.assertEqual([movie["title"] for movie in original["items"]], ["First", "Second", "Third"])
            finally:
                client.close()
                local.shutdown()

    def test_creator_prefers_hls_js_when_chromium_claims_native_hls(self):
        script = (app.WEB_DIR / "app.js").read_text(encoding="utf-8")
        hls_branch = 'if(window.Hls&&Hls.isSupported())'
        native_fallback = 'else if(video.canPlayType("application/vnd.apple.mpegurl"))'
        self.assertIn(hls_branch, script)
        self.assertIn(native_fallback, script)
        self.assertLess(script.index(hls_branch), script.index(native_fallback))
        self.assertIn('video.removeAttribute("src")', script)

    def test_channel_validation_and_exports(self):
        channel = app.save_channel({"name": "My Channel", "number": "101", "section": "1", "items": [{"id": "9", "title": "Example", "type": "movie", "duration_ms": 3600000, "key": "/library/metadata/9"}]})
        self.assertEqual(channel["name"], "My Channel")
        guide = (app.EXPORT_DIR / "guide.xml").read_text(encoding="utf-8")
        playlist = (app.EXPORT_DIR / "channels.m3u").read_text(encoding="utf-8")
        self.assertIn("My Channel", guide)
        self.assertIn("public-channelforge.", playlist)
        self.assertIn(f"http://127.0.0.1:8787/live/{channel['id']}.m3u8", playlist)
        self.assertNotIn("web/index.html", playlist)

    def test_public_channel_catalog_supplies_online_guide_without_starting_playback(self):
        channel = app.save_channel({"name": "Public Guide", "number": "12", "section": "1", "items": [{"id": "9", "title": "Example", "type": "movie", "duration_ms": 3600000, "key": "/library/metadata/9", "series_thumb": "/library/metadata/8/thumb/3", "art": "/library/metadata/9/art/1"}]})
        with mock.patch.object(app, "start_playback_route") as playback:
            catalog = app.public_channel_catalog()
        playback.assert_not_called()
        public = next(item for item in catalog["channels"] if item["id"] == channel["id"])
        self.assertTrue(public["online"])
        self.assertTrue(public["stream_url"].endswith(f"/live/{channel['id']}.m3u8"))
        self.assertGreater(len(public["programs"]), 0)
        self.assertIn("/api/plex/image?path=", public["programs"][0]["poster_url"])
        self.assertEqual(public["programs"][0]["poster_url"], public["programs"][0]["series_poster_url"])
        self.assertIn("%2Fart%2F", public["programs"][0]["backdrop_url"])
        self.assertNotIn("X-Plex-Token", json.dumps(public))
        brief = app.public_channel_catalog(brief=True)["channels"][0]
        self.assertEqual(brief["stream_url"], public["stream_url"])
        self.assertEqual(len(brief["programs"]), 1)
        current = brief["programs"][0]
        self.assertEqual(current["title"], "Example")
        self.assertEqual(set(current), {"title", "start_epoch_ms", "stop_epoch_ms"})

    def test_public_channel_catalog_hides_missing_local_source(self):
        channel = app.save_channel({"name": "Missing Local", "number": "13", "items": [{"id": "local-9", "title": "Offline", "source": "local", "file_path": str(app.DATA_DIR / "missing-video.mkv"), "duration_ms": 3600000}]})
        public = next(item for item in app.public_channel_catalog()["channels"] if item["id"] == channel["id"])
        self.assertFalse(public["online"])

    def test_schedule_is_stable_across_restarts(self):
        channel = app.clean_channel({"name": "Clock", "number": "2", "schedule_anchor": "2026-01-01T00:00:00+00:00", "items": [
            {"id": "1", "title": "First", "duration_ms": 600000},
            {"id": "2", "title": "Second", "duration_ms": 600000},
        ]})
        moment = app.datetime(2026, 1, 1, 0, 12, 30, tzinfo=app.timezone.utc)
        position = app.schedule_position(channel, moment)
        self.assertEqual(position["item"]["title"], "Second")
        self.assertEqual(position["offset_ms"], 150000)

    def test_shuffle_lineup_is_stable(self):
        channel = app.clean_channel({"name": "Mix", "number": "4", "playback_mode": "shuffle", "items": [
            {"id": str(index), "title": f"Title {index}", "duration_ms": 60000} for index in range(8)
        ]})
        first = [item["id"] for item in app.scheduled_items(channel)]
        second = [item["id"] for item in app.scheduled_items(channel)]
        self.assertEqual(first, second)
        self.assertNotEqual(first, [str(index) for index in range(8)])

    def test_marathon_shuffles_series_but_keeps_episodes_in_order(self):
        items = [
            {"id": "b2", "title": "B 2", "series": "Show B", "season": 1, "episode": 2},
            {"id": "a2", "title": "A 2", "series": "Show A", "season": 1, "episode": 2},
            {"id": "b1", "title": "B 1", "series": "Show B", "season": 1, "episode": 1},
            {"id": "a1", "title": "A 1", "series": "Show A", "season": 1, "episode": 1},
        ]
        channel = app.clean_channel({"name": "Marathon", "number": "5", "playback_mode": "marathon", "items": items})
        lineup = app.scheduled_items(channel)
        for series in {item["series"] for item in lineup}:
            episodes = [item["episode"] for item in lineup if item["series"] == series]
            self.assertEqual(episodes, sorted(episodes))

    def test_shuffle_shows_rotates_series_and_never_repeats_an_episode(self):
        items = [
            {"id": "a1", "title": "A 1", "series": "Show A", "season": 1, "episode": 1},
            {"id": "a2", "title": "A 2", "series": "Show A", "season": 1, "episode": 2},
            {"id": "b1", "title": "B 1", "series": "Show B", "season": 1, "episode": 1},
            {"id": "b2", "title": "B 2", "series": "Show B", "season": 1, "episode": 2},
            {"id": "a1", "title": "A 1 duplicate", "series": "Show A", "season": 1, "episode": 1},
        ]
        channel = app.clean_channel({"name": "Series Mix", "number": "6", "playback_mode": "shuffle_in_order", "items": items})
        lineup = app.scheduled_items(channel)
        self.assertEqual(len(lineup), 4)
        self.assertEqual(len({item["id"] for item in lineup}), 4)
        self.assertTrue(all(left["series"] != right["series"] for left, right in zip(lineup, lineup[1:])))
        for series in {item["series"] for item in lineup}:
            self.assertEqual([item["episode"] for item in lineup if item["series"] == series], [1, 2])

    def test_smart_franchise_places_complete_show_between_movies(self):
        items = [
            {"id": "aliens", "title": "Aliens", "type": "movie", "year": "1986"},
            {"id": "earth-2", "title": "Alien: Earth · S01E02", "series": "Alien: Earth", "season": 1, "episode": 2, "year": "2025"},
            {"id": "alien", "title": "Alien", "type": "movie", "year": "1979"},
            {"id": "earth-1", "title": "Alien: Earth · S01E01", "series": "Alien: Earth", "season": 1, "episode": 1, "year": "2025"},
        ]
        channel = app.clean_channel({"name": "Alien Timeline", "number": "7", "playback_mode": "franchise", "items": items})
        lineup = app.scheduled_items(channel)
        self.assertEqual([item["id"] for item in lineup], ["alien", "earth-1", "earth-2", "aliens"])

    def test_smart_franchise_places_welcome_to_derry_before_it_movies(self):
        items = [
            {"id": "it-2", "title": "It Chapter Two", "type": "movie", "year": "2019"},
            {"id": "derry-2", "title": "Welcome to Derry · S01E02", "series": "Welcome to Derry", "season": 1, "episode": 2, "year": "2025"},
            {"id": "it-1", "title": "It", "type": "movie", "year": "2017"},
            {"id": "derry-1", "title": "Welcome to Derry · S01E01", "series": "Welcome to Derry", "season": 1, "episode": 1, "year": "2025"},
        ]
        channel = app.clean_channel({"name": "Derry Timeline", "number": "8", "playback_mode": "franchise", "items": items})
        lineup = app.scheduled_items(channel)
        self.assertEqual([item["id"] for item in lineup], ["derry-1", "derry-2", "it-1", "it-2"])

    def test_tv_library_returns_playable_episodes(self):
        libraries = [{"key": "2", "title": "TV", "type": "show"}]
        shows = app.ET.fromstring('<MediaContainer><Directory ratingKey="10" type="show" title="Example Show" year="1999" contentRating="TV-14" thumb="/library/metadata/10/thumb/1"><Genre tag="Anime"/><Genre tag="Adventure"/></Directory></MediaContainer>')
        episodes = app.ET.fromstring('<MediaContainer><Video ratingKey="99" type="episode" title="Pilot" grandparentRatingKey="10" grandparentTitle="Example Show" grandparentThumb="/library/metadata/10/thumb/1" parentIndex="1" index="1" duration="1800000" key="/library/metadata/99"><Media container="mkv" videoCodec="hevc" audioCodec="aac"><Part key="/library/parts/99/file.mkv"><Stream id="11" streamType="2" language="Japanese" languageCode="jpn"/><Stream id="12" streamType="2" language="English" languageCode="eng"/></Part></Media></Video></MediaContainer>')
        with mock.patch.object(app, "plex_libraries", return_value=libraries), mock.patch.object(app, "plex_server_request", side_effect=[shows, episodes]) as request:
            items = app.plex_items("2")
        self.assertEqual(request.call_args_list, [mock.call("/library/sections/2/all?type=2", ""), mock.call("/library/sections/2/all?type=4", "")])
        self.assertEqual(items[0]["key"], "/library/metadata/99")
        self.assertEqual(items[0]["series"], "Example Show")
        self.assertEqual(items[0]["series_thumb"], "/library/metadata/10/thumb/1")
        self.assertEqual(items[0]["genres"], ["Adventure", "Anime"])
        self.assertEqual(items[0]["year"], "1999")
        self.assertEqual(items[0]["content_rating"], "TV-14")
        self.assertEqual(items[0]["audio_language"], "eng")
        self.assertEqual(items[0]["audio_stream_index"], 1)
        self.assertIn("S01E01", items[0]["title"])
        self.assertEqual(items[0]["part_key"], "/library/parts/99/file.mkv")

    def test_local_library_scans_media_and_sidecar_poster(self):
        root = Path(tempfile.mkdtemp(prefix="channel-forge-local-"))
        video = root / "Series" / "Pilot Episode.mkv"
        video.parent.mkdir()
        video.write_bytes(b"media")
        video.with_suffix(".jpg").write_bytes(b"poster")
        library = app.save_local_library("Local TV", str(root))
        with mock.patch.object(app, "local_duration_ms", return_value=2_700_000):
            items = app.local_items(library["key"])
        self.assertEqual(items[0]["source"], "local")
        self.assertEqual(items[0]["file_path"], str(video.resolve()))
        self.assertEqual(items[0]["duration_ms"], 2_700_000)
        self.assertTrue(items[0]["thumb"])
        data, content_type = app.local_image(library["key"], items[0]["id"])
        self.assertEqual(data, b"poster")
        self.assertEqual(content_type, "image/jpeg")

    def test_local_channel_does_not_require_plex_token(self):
        root = Path(tempfile.mkdtemp(prefix="channel-forge-local-play-"))
        video = root / "Movie.mp4"
        video.write_bytes(b"media")
        library = app.save_local_library("Movies", str(root))
        item = {"id": "local-test", "title": "Movie", "duration_ms": 600000, "source": "local", "file_path": str(video), "key": "local:test"}
        channel = app.clean_channel({"name": "Local", "number": "10", "section": library["key"], "items": [item]})
        app.atomic_json(app.CONFIG_FILE, {"plex_url": "http://127.0.0.1:32400", "plex_token": "", "local_libraries": [{"id": library["id"], "name": "Movies", "path": str(root)}]})
        self.assertEqual(app.local_media_path(channel["items"][0]), video.resolve())

    def test_local_movie_upload_streams_into_configured_library(self):
        root = Path(tempfile.mkdtemp(prefix="channel-forge-upload-"))
        library = app.save_local_library("Uploads", str(root))
        handler = mock.Mock()
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {"Content-Length": "11"}
        handler.rfile = io.BytesIO(b"movie-bytes")
        result = app.upload_local_media(handler, library["key"], "My Movie.mp4")
        uploaded = root / "ChannelForge Uploads" / result["name"]
        self.assertEqual(uploaded.read_bytes(), b"movie-bytes")

    def test_remote_movie_upload_is_rejected(self):
        handler = mock.Mock()
        handler.client_address = ("192.0.2.10", 12345)
        with self.assertRaisesRegex(ValueError, "only from"):
            app.upload_local_media(handler, "local:missing", "movie.mp4")

    def test_live_url_uses_plex_hls_at_current_offset(self):
        channel = app.clean_channel({"name": "Live", "number": "3", "schedule_anchor": "2026-01-01T00:00:00+00:00", "items": [
            {"id": "44", "title": "Show", "duration_ms": 3600000, "key": "/library/metadata/44"},
        ]})
        url = app.plex_live_url(channel, app.datetime(2026, 1, 1, 0, 5, tzinfo=app.timezone.utc))
        self.assertIn("/video/:/transcode/universal/start.m3u8?", url)
        self.assertIn("offset=300.000", url)
        self.assertIn("path=%2Flibrary%2Fmetadata%2F44", url)
        self.assertIn("directPlay=0", url)
        self.assertIn("videoCodec=h264", url)
        self.assertIn("audioCodec=aac", url)

    def test_hls_manifest_urls_are_rewritten_to_same_origin_proxy(self):
        app.HLS_PROXY.clear()
        app.HLS_PROXY_CHANNELS.clear()
        manifest = b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\nsegment000.ts?token=private\n'
        rewritten = app.rewrite_hls_manifest(manifest, "http://plex.local:32400/session/master.m3u8").decode()
        self.assertNotIn("plex.local", rewritten)
        self.assertNotIn("token=private", rewritten)
        self.assertEqual(rewritten.count("/hls-proxy/"), 2)
        registered = [url for url, _expires in app.HLS_PROXY.values()]
        self.assertIn("http://plex.local:32400/session/segment000.ts?token=private", registered)

    def test_plex_channel_uses_demand_driven_browser_safe_ffmpeg(self):
        channel = app.clean_channel({"name": "Plex TV", "number": "11", "items": [
            {"id": "44", "title": "Show", "duration_ms": 3600000, "key": "/library/metadata/44"},
        ]})
        with mock.patch.object(app, "start_ffmpeg_session", return_value="encoded-session") as ffmpeg:
            route = app.start_playback_route(channel)
        self.assertEqual(route, "/ffmpeg-stream/encoded-session/index.m3u8")
        ffmpeg.assert_called_once_with(channel, public_delivery=False, constrained_live=False)

    def test_playback_errors_remove_plex_token(self):
        error = app.sanitized_playback_error("failed https://plex/video?X-Plex-Token=test&part=1")
        self.assertNotIn("X-Plex-Token=test", error)
        self.assertNotIn("test&", error)

    def test_extracts_token_from_pasted_plex_url(self):
        url, token = app.normalize_plex_connection("http://plex.local:32400/web/index.html?foo=bar&X-Plex-Token=secret-token")
        self.assertEqual(url, "http://plex.local:32400?foo=bar")
        self.assertEqual(token, "secret-token")
        self.assertNotIn("secret-token", url)

    def test_plex_signin_creates_official_pin_auth_url(self):
        with mock.patch.object(app, "plex_tv_json", return_value={"id": 42, "code": "ABCD1234", "expiresIn": 600}), mock.patch.object(app, "persistent_client_identifier", return_value="stable-client"):
            result = app.start_plex_signin()
        self.assertEqual(result["pin_id"], 42)
        self.assertEqual(result["expires_in"], 600)
        self.assertTrue(result["auth_url"].startswith("https://app.plex.tv/auth#?"))
        self.assertIn("clientID=stable-client", result["auth_url"])
        self.assertIn("code=ABCD1234", result["auth_url"])

    def test_completed_plex_signin_saves_discovered_server(self):
        root = app.ET.fromstring('<MediaContainer friendlyName="Signed In Plex" myPlexUsername="viewer" version="1.2.3"/>')
        with mock.patch.object(app, "plex_tv_json", return_value={"authToken": "new-token"}), mock.patch.object(app, "discover_plex_server", return_value="https://plex.example:32400"), mock.patch.object(app, "plex_request", return_value=root), mock.patch.object(app, "generate_exports"):
            result = app.poll_plex_signin(42)
        saved = app.config()
        self.assertTrue(result["complete"])
        self.assertEqual(saved["plex_token"], "new-token")
        self.assertEqual(saved["plex_url"], "https://plex.example:32400")
        self.assertEqual(saved["plex_profile"]["username"], "viewer")

    def test_reads_safe_plex_profile_information(self):
        root = app.ET.fromstring('<MediaContainer friendlyName="Living Room Plex" myPlexUsername="viewer" version="1.42.2" machineIdentifier="private-id" myPlexSigninState="ok"/>')
        profile = app.plex_profile(root)
        self.assertEqual(profile["server_name"], "Living Room Plex")
        self.assertEqual(profile["username"], "viewer")
        self.assertTrue(profile["signed_in"])
        self.assertNotIn("machine_id", profile)

    def test_discovers_only_plex_servers_shared_with_signed_in_user(self):
        payload = json.dumps([
            {"name": "My Server", "provides": "server", "owned": True, "clientIdentifier": "mine", "accessToken": "owner-token", "connections": [{"uri": "https://mine.example", "local": False, "relay": False}]},
            {"name": "Dad's Server", "provides": "server", "owned": False, "clientIdentifier": "dad-server", "accessToken": "shared-token", "connections": [{"uri": "https://relay.example", "local": False, "relay": True}, {"uri": "https://dad.example", "local": False, "relay": False}]},
        ]).encode()

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return payload

        with mock.patch.object(app.urllib.request, "urlopen", return_value=Response()):
            servers = app.plex_resource_servers(True)
        self.assertEqual(set(servers), {"dad-server"})
        self.assertEqual(servers["dad-server"]["url"], "https://dad.example")
        self.assertEqual(servers["dad-server"]["token"], "shared-token")

    def test_shared_plex_libraries_and_items_keep_token_server_side(self):
        server = {"id": "dad-server", "name": "Dad's Server", "url": "https://dad.example", "token": "shared-token", "owned": False}
        libraries = app.ET.fromstring('<MediaContainer><Directory key="7" title="Classic TV" type="show" uuid="shared-lib"/></MediaContainer>')
        shows = app.ET.fromstring('<MediaContainer><Directory ratingKey="22" type="show" title="Shared Show" year="2005"><Genre tag="Drama"/></Directory></MediaContainer>')
        episodes = app.ET.fromstring('<MediaContainer><Video ratingKey="99" type="episode" title="Pilot" grandparentRatingKey="22" grandparentTitle="Shared Show" parentIndex="1" index="1" duration="1200000" key="/library/metadata/99"><Media container="mkv" videoCodec="h264" audioCodec="aac"><Part key="/library/parts/99/file.mkv"/></Media></Video></MediaContainer>')
        with mock.patch.object(app, "plex_resource_servers", return_value={"dad-server": server}), mock.patch.object(app, "plex_request", side_effect=[libraries, libraries, shows, episodes]) as request:
            shared = app.plex_libraries("dad-server")
            items = app.plex_items("plex:dad-server:7")
        self.assertEqual(shared[0]["key"], "plex:dad-server:7")
        self.assertIn("shared by Dad's Server", shared[0]["title"])
        self.assertEqual(items[0]["plex_server_id"], "dad-server")
        self.assertEqual(items[0]["title"], "Shared Show · S01E01 · Pilot")
        self.assertEqual(items[0]["genres"], ["Drama"])
        self.assertTrue(all(call.args[1]["plex_token"] == "shared-token" for call in request.call_args_list))
        channel = app.clean_channel({"name": "Shared TV", "number": "77", "section": "plex:dad-server:7", "items": items})
        self.assertEqual(channel["items"][0]["plex_server_id"], "dad-server")
        self.assertNotIn("shared-token", json.dumps(channel))

    def test_plex_poster_proxy_keeps_token_server_side(self):
        class Headers:
            @staticmethod
            def get_content_type(): return "image/jpeg"
        class Response:
            headers = Headers()
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            @staticmethod
            def read(_limit): return b"poster-bytes"
        with mock.patch.object(app.urllib.request, "urlopen", return_value=Response()) as request:
            data, content_type = app.plex_binary("/library/metadata/9/thumb/123")
        self.assertEqual(data, b"poster-bytes")
        self.assertEqual(content_type, "image/jpeg")
        requested_url = request.call_args.args[0].full_url
        self.assertIn("X-Plex-Token=test", requested_url)
        self.assertNotIn("X-Plex-Token", "/api/plex/image?path=%2Flibrary%2Fmetadata%2F9%2Fthumb%2F123")

    def test_channel_requires_media(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            app.clean_channel({"name": "Empty", "number": "1", "items": []})

    def test_channel_can_be_deleted_and_exports_are_updated(self):
        channel = app.save_channel({"name": "Temporary", "number": "404", "items": [{"id": "4", "title": "Remove Me", "duration_ms": 60000}]})
        app.delete_channel(channel["id"])
        self.assertFalse(any(item["id"] == channel["id"] for item in app.channels()))
        self.assertNotIn("Temporary", (app.EXPORT_DIR / "guide.xml").read_text(encoding="utf-8"))

    def test_channels_can_be_reordered(self):
        first = app.save_channel({"name": "First", "number": "1", "items": [{"id": "1", "title": "One"}]})
        second = app.save_channel({"name": "Second", "number": "2", "items": [{"id": "2", "title": "Two"}]})
        ordered = app.reorder_channels([second["id"], first["id"]])
        self.assertEqual([item["name"] for item in ordered], ["Second", "First"])
        with self.assertRaisesRegex(ValueError, "did not match"):
            app.reorder_channels([first["id"]])

    def test_automatic_backup_excludes_plex_credentials(self):
        app.save_channel({"name": "Backed Up", "number": "7", "items": [{"id": "7", "title": "Safe"}]})
        snapshot = json.loads(sorted(app.BACKUP_DIR.glob("*.json"))[-1].read_text(encoding="utf-8"))
        self.assertIn("channels", snapshot)
        self.assertNotIn("plex_token", json.dumps(snapshot))

    def test_example_tool(self):
        app.ensure_files()
        tools = app.plugin_tools()
        self.assertTrue(any(tool["id"] == "example_summary" for tool in tools))

    def test_generates_original_channel_asset(self):
        asset = app.generate_asset({"name": "My Station", "kind": "logo", "primary": "#112233", "secondary": "#abcdef"})
        self.assertEqual(asset["kind"], "logo")
        content = (app.ASSETS_DIR / asset["filename"]).read_text(encoding="utf-8")
        self.assertIn("My Station", content)
        self.assertNotIn("Fish", content)

    def test_installs_complete_neutral_starter_asset_pack(self):
        self.assertEqual(len(app.DEFAULT_ASSET_THEMES), 20)
        app.install_default_assets()
        built_ins = [asset for asset in app.asset_manifest() if asset.get("built_in")]
        self.assertEqual(len(built_ins), len(app.DEFAULT_ASSET_THEMES) * 3)
        self.assertEqual({asset["kind"] for asset in built_ins}, {"logo", "guide-background", "corner-bug"})
        for asset in built_ins:
            self.assertTrue((app.ASSETS_DIR / asset["filename"]).is_file())
            root = app.ET.fromstring((app.ASSETS_DIR / asset["filename"]).read_bytes())
            self.assertEqual(root.tag.rsplit("}", 1)[-1], "svg")
            self.assertTrue(any(node.tag.rsplit("}", 1)[-1] in {"path", "circle", "rect", "ellipse"} for node in root.iter()))
        with self.assertRaisesRegex(ValueError, "cannot be deleted"):
            app.delete_asset(built_ins[0]["id"])

    def test_rejects_active_svg_upload(self):
        import base64
        unsafe = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>').decode()
        with self.assertRaisesRegex(ValueError, "scripts"):
            app.upload_asset({"name": "Unsafe", "kind": "logo", "data": "data:image/svg+xml;base64," + unsafe})

    def test_standalone_http_hosts_guide_playlist_and_live_entry(self):
        channel = app.save_channel({"name": "HTTP TV", "number": "9", "schedule_anchor": "2026-01-01T00:00:00+00:00", "items": [{"id": "99", "title": "Hosted Show", "duration_ms": 600000, "key": "/library/metadata/99"}]})
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", "/api/guide")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn(b"Hosted Show", response.read())
            connection.request("GET", "/exports/channels.m3u")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn(f"/live/{channel['id']}.m3u8".encode(), response.read())
            with mock.patch.object(app, "start_playback_route", return_value="/hls-proxy/plex-session"):
                connection.request("GET", f"/live/{channel['id']}.m3u8")
                response = connection.getresponse()
                self.assertEqual(response.status, 302)
                self.assertEqual(response.getheader("Location"), "/hls-proxy/plex-session")
                response.read()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()

    def test_public_rewind_cinema_uses_proxied_posters_and_playable_urls(self):
        movie = {"id": "99", "title": "Retro Movie", "type": "movie", "year": "1985",
                 "duration_ms": 600000, "key": "/library/metadata/99", "thumb": "/library/metadata/99/thumb/1"}
        episode = {"id": "100", "title": "Pilot", "type": "episode"}
        unknown = {"id": "101", "title": "Untyped video"}
        app.save_channel({"name": "Vault TV", "number": "80", "items": [movie, episode, unknown]})
        app.atomic_json(app.VAULT_CATALOG_FILE, {"items": [movie, episode]})
        items = app.vault_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Retro Movie")
        self.assertIn("/api/plex/image?path=", items[0]["poster_url"])
        self.assertNotIn("X-Plex-Token", items[0]["poster_url"])
        self.assertRegex(items[0]["stream_url"], r"/vault/[a-f0-9]{24}\.m3u8$")
        self.assertEqual(app.vault_source(items[0]["id"])["id"], "99")
        with self.assertRaises(FileNotFoundError):
            app.vault_source(app.vault_item_id(episode))
        self.assertEqual([item["id"] for item in app.channels()[0]["items"]], ["99", "100", "101"])

    def test_transient_live_playback_failure_expires(self):
        with mock.patch.object(app, "PLAYBACK_ERRORS", {"retry-channel": (100.0, "Source timed out")}):
            with mock.patch.object(app.time, "monotonic", return_value=105.0):
                self.assertEqual(app.playback_error("retry-channel"), "Source timed out")
            with mock.patch.object(app.time, "monotonic", return_value=140.0):
                self.assertEqual(app.playback_error("retry-channel"), "")
            self.assertNotIn("retry-channel", app.PLAYBACK_ERRORS)

    def test_live_cleanup_keeps_recent_evicted_segments(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            stale = directory / "segmenteconomy_000001.ts"
            recent = directory / "segmenteconomy_000002.ts"
            manifest = directory / "index_economy.m3u8"
            for path, modified_at in ((stale, 50), (recent, 120), (manifest, 50)):
                path.touch()
                os.utime(path, (modified_at, modified_at))
            app.prune_live_segments(directory, 100)
            self.assertFalse(stale.exists())
            self.assertTrue(recent.is_file())
            self.assertTrue(manifest.is_file())

    def test_public_movie_catalog_negotiates_gzip(self):
        handler = object.__new__(app.Handler)
        handler.path = "/api/public/vault"
        handler.headers = {"Accept-Encoding": "gzip"}
        handler.send_bytes = mock.Mock()
        payload = {"items": [{"type": "movie", "title": f"Film {number}", "summary": "Description " * 60} for number in range(100)]}
        handler.send_json(200, payload)
        _, body, _, headers = handler.send_bytes.call_args.args
        self.assertEqual(headers, {"Vary": "Accept-Encoding", "Content-Encoding": "gzip"})
        self.assertEqual(json.loads(gzip.decompress(body)), payload)
        self.assertLess(len(body) * 5, len(gzip.decompress(body)))

    def test_adaptive_tune_sends_master_without_redirecting(self):
        handler = object.__new__(app.Handler)
        handler.send_bytes = mock.Mock()
        handler.redirect = mock.Mock()
        route = "/ffmpeg-stream/test-session/index.m3u8"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = directory / "index.m3u8"
            manifest.write_bytes(b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=192000\nindex_economy.m3u8\n")
            with mock.patch.object(app, "FFMPEG_SESSIONS", {"test-session": {"directory": directory}}):
                handler.send_playback_manifest(route)
                handler.send_bytes.assert_called_once_with(
                    200, b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=192000\n/ffmpeg-stream/test-session/index_economy.m3u8\n",
                    "application/vnd.apple.mpegurl",
                )
                handler.redirect.assert_not_called()
                manifest.write_bytes(b"#EXTM3U\n#EXTINF:1,\nsegment000001.ts\n")
                handler.send_playback_manifest(route)
                handler.redirect.assert_called_once_with(route)

    def test_constrained_live_tune_keeps_media_on_the_program_session(self):
        handler = object.__new__(app.PublicMediaHandler)
        handler.send_bytes = mock.Mock()
        handler.redirect = mock.Mock()
        route = "/ffmpeg-stream/low-bandwidth-session/index.m3u8"

        handler.send_playback_manifest(route, constrained_live=True)

        handler.send_bytes.assert_called_once_with(
            200, b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=224000\n/ffmpeg-stream/low-bandwidth-session/index.m3u8\n",
            "application/vnd.apple.mpegurl",
        )
        handler.redirect.assert_not_called()

    def test_rewind_cinema_automatically_indexes_all_plex_movies(self):
        libraries = [{"key": "1", "title": "Movies", "type": "movie", "source": "plex"}, {"key": "2", "title": "TV", "type": "show", "source": "plex"}]
        movies = [{"id": "10", "title": "Movie One", "type": "movie", "thumb": "/library/metadata/10/thumb/1", "key": "/library/metadata/10"}]
        with mock.patch.object(app, "all_libraries", return_value=libraries), mock.patch.object(app, "plex_items", return_value=movies) as plex_items:
            result = app.refresh_plex_movie_catalog()
        self.assertEqual(result["movies"], 1)
        plex_items.assert_called_once_with("1")
        catalog = app.vault_items()
        self.assertEqual(catalog[0]["title"], "Movie One")
        self.assertEqual(app.vault_source(catalog[0]["id"])["id"], "10")

    def test_rewind_cinema_indexes_movies_shared_with_the_plex_account(self):
        libraries = [
            {"key": "1", "title": "My Movies", "type": "movie", "source": "plex"},
            {"key": "plex:dad-server:7", "title": "Dad Movies", "type": "movie", "source": "plex-shared", "shared": True},
        ]
        def items(section):
            return [{"id": section, "title": "Shared Movie" if section.startswith("plex:") else "My Movie", "type": "movie", "thumb": "/thumb", "plex_server_id": "dad-server" if section.startswith("plex:") else ""}]
        with mock.patch.object(app, "all_libraries", return_value=libraries), mock.patch.object(app, "plex_items", side_effect=items):
            result = app.refresh_plex_movie_catalog()
        self.assertEqual(result["movies"], 2)
        self.assertEqual(result["shared_movies"], 1)
        shared = next(item for item in app.cached_plex_movies() if item["source"] == "plex-shared")
        self.assertEqual(shared["section"], "plex:dad-server:7")
        self.assertEqual(shared["plex_server_id"], "dad-server")

    def test_connected_library_list_does_not_wait_for_shared_discovery(self):
        personal = {"key": "1", "title": "Movies", "type": "movie"}
        with mock.patch.object(app, "plex_libraries", return_value=[personal]), \
             mock.patch.object(app, "plex_resource_servers") as discover:
            self.assertEqual(app.all_libraries(include_shared=False), [{**personal, "source": "plex"}])
        discover.assert_not_called()

    def test_worldwide_sharing_reconnects_a_broken_tunnel_even_while_ssh_is_alive(self):
        old = "https://previous.example.com"
        new = "https://reconnected.example.com"
        profile = {"name": "Test studio"}
        settings = {"creator_profile": profile, "pending_creator_profile": {}, "public_tunnel": True}
        with (
            mock.patch.object(app, "REGISTERED_ORIGIN", old),
            mock.patch.object(app, "SHARING_STATUS", "live"),
            mock.patch.object(app, "config", return_value=settings),
            mock.patch.object(app, "start_public_media_server"),
            mock.patch.object(app, "start_public_tunnel", side_effect=[old, old, old, new, new]),
            mock.patch.object(app, "relay_reaches_this_creator", side_effect=[True, False, False]) as health,
            mock.patch.object(app, "stop_public_tunnel") as stop,
            mock.patch.object(app, "publish_creator") as publish,
            mock.patch.object(app, "generate_exports"),
            mock.patch.object(app.time, "monotonic", side_effect=[100, 160, 180, 185, 205]),
            mock.patch.object(app.PUBLISH_CHANGED, "wait", side_effect=[None] * 4 + [InterruptedError]),
        ):
            with self.assertRaises(InterruptedError):
                app.restore_public_sharing()
            self.assertEqual(health.call_args_list, [mock.call(old)] * 3)
            stop.assert_called_once()
            publish.assert_called_once_with(profile)
            self.assertEqual(app.REGISTERED_ORIGIN, new)
            self.assertEqual(app.SHARING_STATUS, "live")

    def test_library_workspace_reads_the_current_movie_index_without_plex(self):
        section = "plex:shared-server:3"
        first = {"id": "1", "title": "First movie", "type": "movie", "section": section}
        second = {**first, "id": "2", "title": "A newly indexed movie"}
        with mock.patch.object(app, "plex_items", return_value=[]) as fetch:
            app.atomic_json(app.VAULT_CATALOG_FILE, {"items": [first]})
            self.assertEqual(app.library_items(section, prefer_index=True), [first])
            app.atomic_json(app.VAULT_CATALOG_FILE, {"items": [second]})
            self.assertEqual(app.library_items(section, prefer_index=True), [second])
            fetch.assert_not_called()
            self.assertEqual(app.library_items("44", prefer_index=True), [])
            fetch.assert_called_once_with("44")

    def test_failed_encoder_launch_leaves_no_stream_directory_or_registry_entry(self):
        channel = {"id": "test-launch", "items": [{"id": "local", "duration_ms": 90000, "source": "local"}]}
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(app, "STREAM_DIR", Path(temporary)), \
                mock.patch.object(app, "FFMPEG_SESSIONS", {}), mock.patch.object(app, "ffmpeg_path", return_value="ffmpeg"), \
                mock.patch.object(app, "playback_audio_track", return_value=(0, False)), \
                mock.patch.object(app, "local_media_path", return_value="video.mp4"), \
                mock.patch.object(app.subprocess, "Popen", side_effect=OSError("cannot launch")):
            with self.assertRaisesRegex(OSError, "cannot launch"):
                app.start_ffmpeg_session(channel)
            self.assertFalse(app.FFMPEG_SESSIONS)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_continuous_media_manifest_marks_transition_and_final_segment(self):
        feed = app.LivePlayout(lambda boundary: None)
        boundary = app.datetime.now(app.timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "index.m3u8").write_text("#EXTM3U\n#EXTINF:1.0,\nsegment000001.ts\n#EXT-X-ENDLIST\n")
            feed.open_program = mock.Mock(side_effect=[("first", directory, None, boundary),
                                                        ("second", directory, None, boundary), None])
            feed.run()
        manifest = feed.playlist("hd")
        self.assertTrue(feed.finished)
        self.assertEqual(manifest.count("#EXT-X-DISCONTINUITY\n"), 1)
        self.assertIn("/ffmpeg-stream/first/segment000001.ts", manifest)
        self.assertIn("/ffmpeg-stream/second/segment000001.ts", manifest)
        self.assertTrue(manifest.endswith("#EXT-X-ENDLIST\n"))

    def test_manually_entered_free_relay_uses_low_bandwidth_video(self):
        for host, automatic, expected in (("studio.app.lhr.life", False, True),
                                          ("studio.tailnet.ts.net", True, False),
                                          ("media.example.org", False, False)):
            with self.subTest(host=host), mock.patch.object(app, "config", return_value={
                "public_url": "https://" + host, "public_tunnel": automatic
            }):
                self.assertEqual(app.constrained_public_relay(), expected)

    def test_shared_movie_uses_public_delivery_on_a_custom_origin(self):
        with mock.patch.object(app, "vault_source", return_value={"id": "movie"}), \
                mock.patch.object(app, "start_playback_route", return_value="/playlist") as start:
            self.assertEqual(app.start_vault_session("catalog-id", public_delivery=True), "/playlist")
            self.assertTrue(start.call_args.args[0]["on_demand"])
            self.assertTrue(start.call_args.kwargs["public_delivery"])

    def test_funnel_detection_requires_exclusive_verified_media_mapping(self):
        host = "studio.tailnet.ts.net:8443"
        handlers = {"/": {"Proxy": "http://127.0.0.1:8790"}}
        block = {"Web": {host: {"Handlers": handlers}}, "AllowFunnel": {host: True}}
        status = {"Foreground": {"connection": block}}
        self.assertEqual(app.media_funnel_origin(status, 8790), "https://" + host)
        self.assertEqual(app.media_funnel_origin(status, 8787), "")
        handlers["/other"] = {"Proxy": "http://127.0.0.1:8787"}
        self.assertEqual(app.media_funnel_origin(status, 8790), "")

    def test_encoder_cleanup_kills_stuck_child_before_removing_segments(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [app.subprocess.TimeoutExpired("ffmpeg", 2), 0]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "stream"
            directory.mkdir()
            (directory / "segment000001.ts").touch()
            app.dispose_encoder({"directory": directory, "process": process})
            self.assertFalse(directory.exists())
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        process.stderr.close.assert_called_once()

    def test_encoder_capacity_reserves_live_handoff_and_reuses_existing_stream(self):
        channel = {"id": "live", "items": [{"id": "one", "duration_ms": 60000}]}
        position = app.schedule_position(channel)
        key = app.hashlib.sha256(f'live:one:{position["start"].isoformat()}:standard'.encode()).hexdigest()[:20]
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(app, "STREAM_DIR", Path(temporary)), \
                mock.patch.object(app, "FFMPEG_SESSIONS", {}), mock.patch.object(app, "ENCODER_STARTING", {}):
            directory = Path(temporary)
            (directory / "index.m3u8").touch()
            running = mock.Mock()
            running.poll.return_value = None
            for index in range(app.MAX_ENCODERS - 1):
                app.FFMPEG_SESSIONS[key if index == 0 else str(index)] = {
                    "process": running, "channel_id": "live" if index == 0 else str(index), "directory": directory}
            self.assertEqual(app.start_ffmpeg_session(channel, position=position), key)
            with self.assertRaises(app.EncoderCapacityError):
                app.reserve_encoder("other", {"id": "other"}, 1)
            app.reserve_encoder("next", channel, 1)
            with self.assertRaises(app.EncoderCapacityError):
                app.reserve_encoder("another", channel, 1)

    def test_disk_admission_counts_pending_encoders(self):
        with mock.patch.object(app, "FFMPEG_SESSIONS", {}), mock.patch.object(app, "ENCODER_STARTING", {}), \
                mock.patch.object(app.shutil, "disk_usage", return_value=mock.Mock(free=app.STREAM_FREE_RESERVE + 150)):
            app.reserve_encoder("first", {"id": "one"}, 100)
            with self.assertRaisesRegex(app.EncoderCapacityError, "temporary storage"):
                app.reserve_encoder("second", {"id": "two"}, 100)
            self.assertEqual(app.ENCODER_STARTING, {"first": 100})

    def test_stream_cleanup_preserves_seek_history_personal_files_and_pending_start(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(app, "STREAM_DIR", Path(temporary)), \
                mock.patch.object(app, "FFMPEG_SESSIONS", {}), mock.patch.object(app, "ENCODER_STARTING", {}):
            dirs = []
            for digit in range(5):
                directory = Path(temporary) / (str(digit) * 20 + "-" + "a" * 12)
                directory.mkdir()
                (directory / "segment000001.ts").write_bytes(b"data")
                dirs.append(directory)
            movie, orphan, personal, pending, fresh = dirs
            (personal / "family.txt").touch()
            for directory in dirs[:-1]:
                os.utime(directory, (100, 100))
            app.ENCODER_STARTING["3" * 20] = 10
            app.FFMPEG_SESSIONS["movie"] = {"directory": movie, "process": mock.Mock(), "on_demand": True}
            app.maintain_stream_storage(500)
            self.assertFalse(orphan.exists())
            self.assertTrue((movie / "segment000001.ts").exists())
            self.assertTrue((personal / "family.txt").exists())
            self.assertTrue((personal / "segment000001.ts").exists())
            self.assertTrue(pending.exists())
            self.assertTrue(fresh.exists())

    def test_storage_pressure_evicts_idle_before_active_movie(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(app, "STREAM_DIR", Path(temporary)), \
                mock.patch.object(app, "FFMPEG_SESSIONS", {}), mock.patch.object(app, "STREAM_CACHE_LIMIT", 6):
            for key, access in (("active", 490), ("idle", 100)):
                directory = Path(temporary) / key
                directory.mkdir()
                (directory / "segment000001.ts").write_bytes(b"data")
                app.FFMPEG_SESSIONS[key] = {"directory": directory, "process": mock.Mock(), "last_access": access}
            app.maintain_stream_storage(500)
            self.assertFalse((Path(temporary) / "idle").exists())
            self.assertIn("capacity_error", app.FFMPEG_SESSIONS["idle"])
            self.assertTrue((Path(temporary) / "active" / "segment000001.ts").exists())

    def test_completed_movie_can_seek_to_first_segment_during_disk_pressure(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(app, "STREAM_DIR", Path(temporary)), \
                mock.patch.object(app, "FFMPEG_SESSIONS", {}), mock.patch.object(app, "STREAM_CACHE_LIMIT", 1), \
                mock.patch.object(app, "STREAM_PRESSURE_SINCE", 10):
            directory = Path(temporary) / "movie"
            directory.mkdir()
            process = mock.Mock()
            process.poll.return_value = 0
            app.FFMPEG_SESSIONS["movie"] = {"directory": directory, "process": process, "last_access": 490, "on_demand": True}
            (directory / "segment000001.ts").write_bytes(b"first-segment")
            app.maintain_stream_storage(500)
            handler = object.__new__(app.PublicMediaHandler)
            handler.path = "/ffmpeg-stream/movie/segment000001.ts"
            handler.send_bytes = mock.Mock()
            handler.do_GET()
            handler.send_bytes.assert_called_once_with(200, b"first-segment", "video/mp2t")
            process.terminate.assert_not_called()

    def test_transient_pressure_allows_active_movie_to_keep_encoding(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(app, "STREAM_DIR", Path(temporary)), \
                mock.patch.object(app, "FFMPEG_SESSIONS", {}), mock.patch.object(app, "STREAM_CACHE_LIMIT", 5), \
                mock.patch.object(app, "STREAM_PRESSURE_SINCE", None):
            directory = Path(temporary) / "movie"
            directory.mkdir()
            process = mock.Mock()
            process.poll.return_value = None
            app.FFMPEG_SESSIONS["movie"] = {"directory": directory, "process": process, "last_access": 490, "budget": 5}
            (directory / "segment000001.ts").write_bytes(b"123456")
            app.maintain_stream_storage(500)
            (directory / "segment000001.ts").write_bytes(b"1234")
            app.maintain_stream_storage(515)
            self.assertIsNone(app.STREAM_PRESSURE_SINCE)
            self.assertNotIn("capacity_error", app.FFMPEG_SESSIONS["movie"])
            process.terminate.assert_not_called()

    def test_feature_length_vod_receives_profile_budget_and_reserves_live_storage(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(app, "STREAM_DIR", Path(temporary)), \
                mock.patch.object(app, "FFMPEG_SESSIONS", {}), mock.patch.object(app, "ENCODER_STARTING", {}), \
                mock.patch.object(app.shutil, "disk_usage", return_value=mock.Mock(free=10 * 1024**3)):
            first = app.reserve_encoder("one", {"id": "one", "on_demand": True}, 5 * 1024**3, 300_000_000)
            second = app.reserve_encoder("two", {"id": "two", "on_demand": True}, 5 * 1024**3, 300_000_000)
            self.assertGreater(first, second)
            self.assertEqual(first + second + app.STREAM_ROLLING_BUDGET, app.STREAM_CACHE_LIMIT)
            self.assertLess(app.STREAM_CACHE_LIMIT + app.STREAM_ROLLING_BUDGET, 5_000_000_000)

    def test_idle_playback_session_is_stopped(self):
        process = mock.Mock()
        process.poll.return_value = None
        app.FFMPEG_SESSIONS["idle"] = {"process": process, "created": 1.0, "last_access": 10.0}
        app.FFMPEG_SESSIONS["active"] = {"process": mock.Mock(), "created": 90.0, "last_access": 90.0}
        self.assertEqual(app.reap_idle_sessions_once(100.0), 1)
        process.terminate.assert_called_once()
        self.assertNotIn("idle", app.FFMPEG_SESSIONS)
        self.assertIn("active", app.FFMPEG_SESSIONS)

    def test_updater_compares_semantic_versions(self):
        self.assertGreater(app.version_tuple("v2.10.0"), app.version_tuple("2.9.9"))
        self.assertEqual(app.version_tuple("v2.3"), (2, 3, 0))

    def test_updater_verifies_windows_installer_checksum(self):
        installer = b"verified-installer"
        digest = app.hashlib.sha256(installer).hexdigest()
        release = {"latest_version": "9.0.0", "update_available": True, "assets": [
            {"name": "Public_ChannelForge_Setup.exe", "url": "https://github.com/example/setup", "size": len(installer)},
            {"name": "SHA256SUMS.txt", "url": "https://github.com/example/sums", "size": 100},
        ]}
        def download(url, _limit):
            return f"{digest}  Public_ChannelForge_Setup.exe\n".encode() if url.endswith("sums") else installer
        with mock.patch.object(app, "latest_release", return_value=release), mock.patch.object(app, "github_bytes", side_effect=download), mock.patch.object(app, "windows_update_cache", return_value=app.DATA_DIR / "test-updates"), mock.patch.object(app.sys, "platform", "win32"):
            result = app.download_verified_update()
        self.assertEqual(result["sha256"], digest)
        self.assertTrue(result["can_launch"])
        self.assertEqual(Path(result["path"]).read_bytes(), installer)

    def test_updater_reports_streaming_download_progress(self):
        installer = b"progress-installer"
        digest = app.hashlib.sha256(installer).hexdigest()
        release = {"latest_version": "9.1.0", "update_available": True, "assets": [
            {"name": "Public_ChannelForge_Setup.exe", "url": "https://github.com/example/setup", "size": len(installer)},
            {"name": "SHA256SUMS.txt", "url": "https://github.com/example/sums", "size": 100},
        ]}
        events = []
        def download(_url, destination, _limit, progress):
            destination.write_bytes(installer)
            progress("downloading", len(installer), len(installer))
        with mock.patch.object(app, "latest_release", return_value=release), mock.patch.object(app, "github_bytes", return_value=f"{digest}  Public_ChannelForge_Setup.exe\n".encode()), mock.patch.object(app, "github_file", side_effect=download), mock.patch.object(app, "windows_update_cache", return_value=app.DATA_DIR / "test-updates"), mock.patch.object(app.sys, "platform", "win32"):
            result = app.download_verified_update(lambda *event: events.append(event))
        self.assertEqual(result["sha256"], digest)
        self.assertTrue(any(event[0] == "downloading" for event in events))
        self.assertEqual(events[-1][0], "complete")


if __name__ == "__main__":
    unittest.main()

