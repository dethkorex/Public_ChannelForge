import json
import os
import tempfile
import threading
import unittest
import http.client
import gzip
import io
from urllib.request import urlopen
from unittest import mock
from pathlib import Path

from tests.creator_fixture import app


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

    def test_guide_settings_save_while_the_existing_plex_connection_is_down(self):
        origin = "https://relay.example.net/studio/123456789012abcd"
        app.atomic_json(app.CONFIG_FILE, {**app.load_json(app.CONFIG_FILE, {}), "managed_relay": True, "public_url": origin})
        with (app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler) as local,
              mock.patch.object(app, "plex_request", side_effect=ValueError("Plex is offline")) as plex):
            threading.Thread(target=local.serve_forever, daemon=True).start()
            client = http.client.HTTPConnection("127.0.0.1", local.server_port, timeout=5)
            try:
                def save(payload):
                    client.request("POST", "/api/setup", json.dumps(payload), {"Content-Type": "application/json"})
                    response = client.getresponse()
                    return response.status, json.loads(response.read())

                status, result = save({"guide_hours": 36, "plex_token": "", "public_url": "http://stale.local",
                                       "directory_url": "https://old-directory.example.net"})
                self.assertEqual(status, 200)
                self.assertEqual(result["config"]["guide_hours"], 12)
                self.assertEqual(app.config()["public_url"], origin)
                self.assertEqual(app.config()["directory_url"], app.DEFAULT_DIRECTORY_URL)
                plex.assert_not_called()
                status, result = save({"guide_hours": 48, "plex_token": "new-token"})
                self.assertEqual((status, result["error"]), (400, "Plex is offline"))
                plex.assert_called_once()
                self.assertEqual(app.config()["guide_hours"], 12)
                self.assertEqual(app.config()["plex_token"], "test")
            finally:
                client.close()
                local.shutdown()

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
                ("GET", "/api/studio/activity", 404),
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

    def test_scoped_media_origin_keeps_playlists_redirects_and_viewer_leases_inside_the_slot(self):
        scope = "/studio/" + "a" * 16
        master = "/broadcast/demo/master.m3u8"
        viewer_id = "d" * 32
        variant = "/broadcast/demo/" + "b" * 24 + "/hd.m3u8?viewer=" + viewer_id
        manifest = ('#EXTM3U\n#EXT-X-MEDIA:URI="/hls-proxy/audio"\n' + variant + '\n').encode()

        def respond(handler, channel_id, playlist, token, viewer=None, max_bandwidth=0):
            self.assertEqual(channel_id, "demo")
            if playlist == "master":
                handler.send_bytes(200, manifest, "application/vnd.apple.mpegurl")
            else:
                self.assertEqual(viewer, viewer_id)
                handler.redirect("/ffmpeg-stream/" + "c" * 20 + "/index_hd.m3u8?viewer=" + viewer_id)

        with app.open_public_media_server(0) as server, mock.patch.object(app.Handler, "broadcast", respond):
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                client.request("GET", master)
                response = client.getresponse()
                self.assertEqual((response.status, response.read()), (200, manifest))
                client.request("GET", scope + master)
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read().decode().splitlines(),
                                 ["#EXTM3U", f'#EXT-X-MEDIA:URI="{scope}/hls-proxy/audio"', scope + variant])
                client.request("GET", scope + variant)
                response = client.getresponse()
                self.assertEqual(response.status, 302)
                self.assertTrue(response.getheader("Location").startswith(scope + "/ffmpeg-stream/"))
                self.assertTrue(response.getheader("Location").endswith("?viewer=" + viewer_id))
                response.read()
                client.request("GET", scope + "/api/studio/activity")
                self.assertEqual(client.getresponse().status, 404)
            finally:
                client.close()
                server.shutdown()

    def test_movie_activity_counts_playback_sessions_until_their_lease_expires(self):
        item_id, viewer, other_viewer = "a" * 24, "b" * 32, "c" * 32
        scope = "/studio/" + "d" * 16
        route = "/ffmpeg-stream/session/index.m3u8"
        manifest = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=270000\nindex_economy.m3u8\n"
        shared = {"on_demand": True, "channel_id": "vault-" + item_id, "program": "Active movie",
                  "process": mock.Mock(poll=mock.Mock(return_value=None))}
        other = {**shared, "process": mock.Mock(poll=mock.Mock(return_value=0)), "movie_viewers": {viewer: 100}}
        idle = {**shared, "channel_id": "vault-unused", "program": "Idle library title"}
        sessions = {"session": shared, "alternate": other, "idle": idle}
        with (app.open_public_media_server(0) as server,
              mock.patch.object(app, "FFMPEG_SESSIONS", sessions),
              mock.patch.object(app, "start_vault_session", return_value=route) as start,
              mock.patch.object(app, "viewer_encoder_playlist", return_value=manifest),
              mock.patch.object(app.time, "monotonic", return_value=100) as clock):
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                for playing in (viewer, other_viewer, other_viewer):
                    client.request("GET", f"{scope}/vault/{item_id}.m3u8?viewer={playing}")
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertIn((scope + "/ffmpeg-stream/session/index_economy.m3u8").encode(), response.read())
                self.assertEqual(app.movie_activity(), [{"title": "Active movie", "viewers": 2, "encoder_count": 1}])
                clock.return_value = 130
                client.request("GET", f"{scope}/api/public/origin?viewing={item_id}&viewer={other_viewer}")
                response = client.getresponse()
                self.assertEqual((response.status, response.read()), (204, b""))
                self.assertEqual(start.call_count, 3)
                clock.return_value = 150
                self.assertEqual(app.movie_activity(), [{"title": "Active movie", "viewers": 1, "encoder_count": 1}])
                clock.return_value = 175
                self.assertEqual(app.movie_activity(), [])
            finally:
                client.close()
                server.shutdown()

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

    def test_slow_funnel_keeps_the_verified_relay_listener(self):
        with mock.patch.object(app, "PUBLIC_MEDIA_PORT", 0), mock.patch.object(app, "PUBLIC_MEDIA_SERVER", None):
            try:
                app.start_public_media_server(automatic=True)
                relay = app.PUBLIC_MEDIA_SERVER
                with (mock.patch.object(app, "relay_reaches_this_creator", return_value=True),
                      mock.patch.object(app, "relay_transfers_media", return_value=False) as probe):
                    self.assertFalse(app.attach_media_funnel("https://studio.example"))
                probe.assert_called_once_with("https://studio.example", hd=True)
                self.assertIs(app.PUBLIC_MEDIA_SERVER, relay)
                self.assertTrue(app.relay_reaches_this_creator(f"http://127.0.0.1:{relay.server_port}"))
            finally:
                app.stop_public_media_server()

    def test_saved_funnel_requires_a_verified_funnel_address(self):
        app.atomic_json(app.CONFIG_FILE, {"public_url": "https://old.app.lhr.life", "public_tunnel": True})
        with (mock.patch.object(app, "TUNNEL_URL", ""),
              mock.patch.object(app.subprocess, "run") as run):
            with self.assertRaisesRegex(ValueError, "saved Tailscale media origin"):
                app.start_public_tunnel()
            run.assert_not_called()

    def test_studio_can_verify_saved_funnel_without_cli_status(self):
        origin = "https://studio.example.ts.net:8443"
        app.atomic_json(app.CONFIG_FILE, {"public_url": origin})
        with (mock.patch.object(app, "TUNNEL_URL", ""),
              mock.patch.object(app.subprocess, "run") as run,
              mock.patch.object(app, "stage_media_funnel") as verify):
            self.assertEqual(app.start_public_tunnel(), origin)
            verify.assert_called_once_with(origin)
            run.assert_not_called()

    def test_funnel_directory_failure_keeps_the_accepted_listener_and_origin(self):
        accepted = "https://verified.relay.example"
        candidate_origin = "https://studio.example.ts.net"
        app.atomic_json(app.CONFIG_FILE, {"public_url": accepted})
        previous, candidate = mock.Mock(server_port=48600), mock.Mock(server_port=18790)
        with (mock.patch.object(app, "PUBLIC_MEDIA_PORT", 18790),
              mock.patch.object(app, "PUBLIC_MEDIA_SERVER", previous),
              mock.patch.object(app, "open_public_media_server", return_value=candidate),
              mock.patch.object(app, "relay_reaches_this_creator", return_value=True) as check,
              mock.patch.object(app, "relay_transfers_media", return_value=True),
              mock.patch.object(app, "generate_exports"),
              mock.patch.object(app, "publish_creator", side_effect=ValueError("Directory unavailable"))):
            with self.assertRaisesRegex(ValueError, "Directory unavailable"):
                app.publish_studio({"name": "Test", "public_url": candidate_origin})
            check.assert_called_once_with(candidate_origin)
            self.assertIs(app.PUBLIC_MEDIA_SERVER, previous)
            self.assertEqual(app.config()["public_url"], accepted)
            previous.shutdown.assert_not_called()
            candidate.shutdown.assert_called_once()

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
                last_movie = following["items"][0]
                linked = read(f"?limit=1&id={last_movie['id']}")
                self.assertEqual(linked["items"], [last_movie])
                self.assertIsNone(linked["next_offset"])
                self.assertEqual(read("?id=" + "0" * 24)["items"], [])
                self.assertEqual(read("?id=invalid")["items"], [])
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

    def test_channel_rating_limit_applies_to_every_episode_and_is_exported(self):
        title = {"id": "9", "title": "Example", "type": "movie", "duration_ms": 3600000}
        payload = {"name": "Afternoon TV", "number": "12", "channel_rating": "PG-13",
                   "items": [{**title, "content_rating": "PG-13"},
                             {**title, "id": "8", "content_rating": "US:TV-PG"}]}
        for rating in ("R", "TV-MA", "TV-14", ""):
            with self.subTest(rating=rating), self.assertRaisesRegex(ValueError, "PG-13 or lower"):
                app.save_channel({**payload, "items": [*payload["items"], {**title, "id": "7", "content_rating": rating}]})
        channel = app.save_channel(payload)
        row = app.public_channel_catalog(brief=True)["channels"][0]
        self.assertEqual((row["channel_rating"], row["channel_rating_rank"]), ("PG-13", 5))
        self.assertIn('tvg-rating="PG-13" tvg-rating-rank="5"', (app.EXPORT_DIR / "channels.m3u").read_text(encoding="utf-8"))
        legacy_edit = {key: value for key, value in payload.items() if key != "channel_rating"}
        self.assertEqual(app.save_channel(legacy_edit, channel["id"])["channel_rating"], "PG-13")

    def test_public_number_conflict_preserves_lineup_and_deletion_syncs_remaining_numbers(self):
        payload = {"name": "Existing", "number": "07", "items": [{"id": "9", "title": "Title", "duration_ms": 3600000}]}
        channel = app.save_channel(payload)
        with self.assertRaisesRegex(ValueError, "already used in this Studio"):
            app.save_channel({**payload, "name": "Duplicate", "number": "7"})
        settings = {**app.config(), "creator_id": "a" * 32, "creator_key": "secret", "auto_publish_channels": True}
        with (mock.patch.object(app, "config", return_value=settings),
              mock.patch.object(app, "SYNCED_CHANNEL_NUMBERS", None),
              mock.patch.object(app, "directory_request", side_effect=[ValueError("already reserved"), {}]) as request):
            with self.assertRaisesRegex(ValueError, "already reserved"):
                app.save_channel({**payload, "number": "40"}, channel["id"])
            self.assertEqual(app.channels()[0]["number"], "07")
            app.delete_channel(channel["id"])
            self.assertEqual(request.call_args.args[2], {"mode": "sync", "channels": []})
        self.assertEqual(app.channels(), [])

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
        self.assertEqual(len(brief["programs"]), 2)
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

    def test_reshuffle_then_restore_mixed_marathon(self):
        items = [{"id": show + str(ep), "title": show + str(ep), "series": show,
                  "episode": ep, "season": 1} for show in ("B", "A") for ep in (2, 1)]
        items.insert(2, {"id": "film", "title": "Movie"})
        channel = app.save_channel({"name": "Mixed marathon", "number": "8",
                                    "playback_mode": "marathon", "items": items})
        def ids(value):
            return [item["id"] for item in app.scheduled_items(value)]
        expected = ["B1", "B2", "film", "A1", "A2"]
        self.assertEqual(ids(channel), expected)
        shuffled = app.update_channel_run(channel["id"], "shuffle")
        self.assertEqual(shuffled["playback_mode"], "marathon")
        changed = ids(shuffled)
        self.assertNotEqual(changed, expected)
        for show in ("A", "B"):
            index = changed.index(show + "1")
            self.assertEqual(changed[index:index + 2], [show + "1", show + "2"])
        edited = app.save_channel(shuffled, channel["id"])
        self.assertEqual(ids(edited), changed)
        app.save_channel({**edited, "restore_preset": True}, channel["id"])
        self.assertEqual(ids(app.channels()[0]), expected)
        self.assertNotIn("shuffle_seed", app.channels()[0])

    def test_manual_lineup_order_overrides_an_old_reshuffle_without_restarting(self):
        initial = {"name": "Evening", "number": "9", "playback_mode": "ordered",
                   "items": [{"id": "one"}, {"id": "two"}, {"id": "three"}]}
        channel = app.save_channel(initial)
        shuffled = app.update_channel_run(channel["id"], "shuffle")
        self.assertIn("shuffle_seed", shuffled)
        saved = app.save_channel({**initial, "items": list(reversed(initial["items"])),
                                  "lineup_reordered": True}, channel["id"])
        self.assertEqual([item["id"] for item in app.scheduled_items(saved)], ["three", "two", "one"])
        self.assertEqual(saved["schedule_anchor"], shuffled["schedule_anchor"])

    def test_presets_keep_same_named_series_from_different_libraries_separate(self):
        items = [dict(id=str(episode), title=f"Episode {episode}", series="Remake", type="episode",
                      section=section, episode=episode, season=1)
                 for section in ("library-a", "library-b") for episode in (2, 1)]
        ordered = [("library-a", 1), ("library-a", 2), ("library-b", 1), ("library-b", 2)]
        for mode in ("marathon", "franchise", "shuffle_in_order"):
            with self.subTest(mode=mode):
                channel = app.clean_channel({"name": "Test", "number": "1", "items": items, "playback_mode": mode})
                positions = [(item["section"], item["episode"]) for item in app.scheduled_items(channel)]
                self.assertCountEqual(positions, ordered)
                if mode == "shuffle_in_order":
                    self.assertTrue(all(a[0] != b[0] for a, b in zip(positions, positions[1:])))
                else:
                    self.assertEqual(positions, ordered)

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
        shows = app.ET.fromstring('<MediaContainer><Directory ratingKey="10" type="show" title="Example Show" year="1999" contentRating="TV-14" thumb="/library/metadata/10/thumb/1"><Genre tag="Anime"/><Genre tag="Adventure"/><Role tag="Lead Performer"/><Director tag="Series Director"/></Directory></MediaContainer>')
        episodes = app.ET.fromstring('<MediaContainer><Video ratingKey="99" type="episode" title="Pilot" grandparentRatingKey="10" grandparentTitle="Example Show" grandparentThumb="/library/metadata/10/thumb/1" parentIndex="1" index="1" duration="1800000" key="/library/metadata/99"><Media container="mkv" videoCodec="hevc" audioCodec="aac"><Part key="/library/parts/99/file.mkv"><Stream id="11" streamType="2" language="Japanese" languageCode="jpn"/><Stream id="12" streamType="2" language="English" languageCode="eng"/></Part></Media></Video></MediaContainer>')
        with mock.patch.object(app, "plex_libraries", return_value=libraries), mock.patch.object(app, "plex_server_request", side_effect=[shows, episodes]) as request:
            items = app.plex_items("2")
        self.assertEqual(request.call_args_list, [mock.call("/library/sections/2/all?type=2", ""), mock.call("/library/sections/2/all?type=4", "")])
        self.assertEqual(items[0]["key"], "/library/metadata/99")
        self.assertEqual(items[0]["series"], "Example Show")
        self.assertEqual(items[0]["series_thumb"], "/library/metadata/10/thumb/1")
        self.assertEqual(items[0]["genres"], ["Adventure", "Anime"])
        self.assertEqual(items[0]["actors"], ["Lead Performer"])
        self.assertEqual(items[0]["directors"], ["Series Director"])
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
        with (mock.patch.object(app, "plex_tv_json", return_value={"authToken": "new-token"}),
              mock.patch.object(app, "discover_plex_server", return_value="https://plex.example:32400"),
              mock.patch.object(app, "plex_request", return_value=root), mock.patch.object(app, "generate_exports"),
              mock.patch.object(app, "sync_plex_movies_async") as sync_movies):
            result = app.poll_plex_signin(42)
        sync_movies.assert_called_once_with()
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

    def test_edit_burst_preserves_daily_and_user_named_backups(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(app, "BACKUP_DIR", Path(folder)):
            earlier_day = Path(folder) / "20260101T120000000000Z.json"
            manual_copy = Path(folder) / "studio-recovery.json"
            for path in (earlier_day, manual_copy):
                path.write_text('{"channels":["original"]}', encoding="utf-8")
            for _ in range(25):
                app.backup_state("edit channel")
            self.assertTrue(earlier_day.exists())
            self.assertTrue(manual_copy.exists())
            self.assertLessEqual(len(list(Path(folder).glob("*.json"))), 23)

    def test_generates_original_channel_asset(self):
        asset = app.generate_asset({"name": "My Station", "kind": "logo", "primary": "#112233", "secondary": "#abcdef"})
        self.assertEqual(asset["kind"], "logo")
        content = (app.ASSETS_DIR / asset["filename"]).read_text(encoding="utf-8")
        self.assertIn("My Station", content)
        self.assertNotIn("Fish", content)

    def test_installs_complete_neutral_starter_asset_pack(self):
        app.install_default_assets()
        built_ins = [asset for asset in app.asset_manifest() if asset.get("built_in")]
        self.assertEqual(len(built_ins), len(app.studio_branding.STYLES) * 3 + len(app.studio_branding.AMBIENCE))
        self.assertEqual({asset["kind"] for asset in built_ins}, {"logo", "guide-background", "corner-bug", "ambience"})
        for asset in built_ins:
            path = app.ASSETS_DIR / asset["filename"]
            if asset["kind"] == "ambience":
                with app.wave.open(str(path), "rb") as track:
                    self.assertEqual(track.getnframes() / track.getframerate(), 25)
            else:
                self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
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
            with mock.patch.object(app.LivePlayout, "run"), mock.patch.object(app, "LIVE_PLAYOUT", {}):
                connection.request("GET", f"/live/{channel['id']}.m3u8")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                legacy = response.read()
                connection.request("GET", f"/broadcast/{channel['id']}/master.m3u8")
                response = connection.getresponse()
                self.assertEqual(response.read(), legacy)
                self.assertEqual(len(app.LIVE_PLAYOUT), 1)
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

    def test_directory_heartbeat_retries_and_resumes_without_republishing(self):
        settings = {"creator_profile": {"name":"Test", "heartbeat_supported":True},
                    "pending_creator_profile":{}, "creator_id":"studio", "creator_key":"secret",
                    "public_tunnel":False, "public_url":"https://studio.example"}
        with (
            mock.patch.object(app, "REGISTERED_ORIGIN", settings["public_url"]),
            mock.patch.object(app, "HEARTBEAT_STATUS", {"last_success":0, "last_attempt":0, "failures":0}),
            mock.patch.object(app, "config", return_value=settings),
            mock.patch.object(app, "start_public_media_server"),
            mock.patch.object(app, "publish_creator") as publish,
            mock.patch.object(app, "broadcast_health", return_value="standby"),
            mock.patch.object(app, "directory_request", side_effect=[ValueError("temporary outage"), {}, {}]) as request,
            mock.patch.object(app.time, "monotonic", side_effect=[100,105,125,145,165,185]),
            mock.patch.object(app.PUBLISH_CHANGED, "wait", side_effect=[None]*5+[InterruptedError]),
        ):
            with self.assertRaises(InterruptedError):
                app.restore_public_sharing()
            self.assertEqual(request.call_args_list, [mock.call("POST","/v1/creators/studio/heartbeat",{"state":"standby"},"secret")]*3)
            self.assertGreater(app.HEARTBEAT_STATUS["last_success"], 0)
            self.assertEqual(app.HEARTBEAT_STATUS["failures"], 0)
            publish.assert_not_called()

    def test_publication_retry_reuses_identity_and_restores_only_sharing_fields(self):
        origin = "https://studio.example"
        def outage(*args):
            with app.LOCK:
                current = app.load_json(app.CONFIG_FILE, {})
                current["guide_hours"] = 42
                app.atomic_json(app.CONFIG_FILE, current)
            raise ValueError("temporary")
        profile = {"name":"Studio", "public_url":origin}
        with (
            mock.patch.object(app, "start_public_media_server"),
            mock.patch.object(app, "stop_public_media_server"),
            mock.patch.object(app, "generate_exports"),
            mock.patch.object(app, "directory_request", side_effect=outage) as request,
        ):
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, "temporary"):
                    app.publish_studio(profile)
            self.assertEqual(app.config()["guide_hours"], 12)
            self.assertEqual(app.config()["public_url"], "")
            self.assertEqual(request.call_args_list[0], request.call_args_list[1])
            self.assertEqual(len(request.call_args.args[3]), 64)
            self.assertNotIn(request.call_args.args[3], str(app.config(True)))

    def test_failed_community_relay_publication_retires_only_the_candidate(self):
        old = app.CommunityRelayProfile("https://old.example", "relay.example", "studio", 22, 18101, "")
        new = old._replace(origin="https://new.example", remote_port=18102)
        current = app.CommunityRelayConnection(old, 8790, mock.Mock(poll=mock.Mock(return_value=None)))
        candidate = app.CommunityRelayConnection(new, 8790, mock.Mock(poll=mock.Mock(return_value=None)))
        app.atomic_json(app.CONFIG_FILE, {**app.config(), "public_url": old.origin})
        with (
            mock.patch.object(app, "COMMUNITY_RELAY", current),
            mock.patch.object(app, "PUBLIC_MEDIA_SERVER", mock.Mock(server_port=8790)),
            mock.patch.object(app, "community_relay_profile", return_value=new),
            mock.patch.object(app, "connect_community_relay", return_value=candidate),
            mock.patch.object(app, "relay_transfers_media", return_value=True),
            mock.patch.object(app, "start_public_media_server") as start,
            mock.patch.object(app, "generate_exports"),
            mock.patch.object(app, "publish_creator", side_effect=ValueError("directory unavailable")),
        ):
            with self.assertRaisesRegex(ValueError, "directory unavailable"):
                app.publish_studio({"name": "Studio", "public_url": new.origin})
            self.assertIs(app.COMMUNITY_RELAY, current)
            self.assertEqual(app.config()["public_url"], old.origin)
            start.assert_called_once_with(automatic=True)
            candidate.process.terminate.assert_called_once()
            current.process.terminate.assert_not_called()

    def test_slow_community_relay_cannot_replace_existing_publication(self):
        old_origin = "https://old.app.lhr.life"
        new = app.CommunityRelayProfile("https://new.example", "relay.example", "studio", 22, 18102, "")
        candidate = app.CommunityRelayConnection(new, 8790, mock.Mock())
        app.atomic_json(app.CONFIG_FILE, {"public_url": old_origin, "public_tunnel": True})
        with (
            mock.patch.object(app, "PUBLIC_MEDIA_SERVER", mock.Mock(server_port=8790)),
            mock.patch.object(app, "community_relay_profile", return_value=new),
            mock.patch.object(app, "connect_community_relay", return_value=candidate),
            mock.patch.object(app, "relay_transfers_media", return_value=False) as probe,
            mock.patch.object(app, "start_public_media_server"),
            mock.patch.object(app, "generate_exports"),
            mock.patch.object(app, "publish_creator") as publish,
            mock.patch.object(app, "stop_relay_process") as stop,
            mock.patch.object(app, "stop_public_tunnel") as stop_old,
        ):
            with self.assertRaisesRegex(ValueError, "720p test segment"):
                app.publish_studio({"name": "Studio", "public_url": new.origin})
            probe.assert_called_once_with(new.origin, hd=True)
            publish.assert_not_called()
            stop.assert_called_once_with(candidate.process)
            stop_old.assert_not_called()
            self.assertEqual(app.config()["public_url"], old_origin)

    def test_community_backup_keeps_legacy_relay_configuration_and_studio_identity(self):
        primary = app.CommunityRelayProfile("https://primary.example", "relay-a.example", "studio", 22, 18101, "")
        backup = primary._replace(origin="https://backup.example", ssh_host="relay-b.example")
        connection = app.CommunityRelayConnection(backup, 8790, mock.Mock())
        app.atomic_json(app.CONFIG_FILE, {"public_url": primary.origin, "creator_id": "studio-id",
                                         "creator_key": "existing-key", "creator_profile": {"name": "Test"}})
        with (
            mock.patch.object(app, "REGISTERED_ORIGIN", primary.origin),
            mock.patch.object(app, "connect_community_relay", side_effect=[ValueError("primary down"), connection]) as connect,
            mock.patch.object(app, "relay_transfers_media", return_value=True),
            mock.patch.object(app, "directory_request", return_value={"creator": {"id": "studio-id"}}) as directory,
            mock.patch.object(app, "generate_exports"),
            mock.patch.object(app, "set_community_relay") as activate,
        ):
            self.assertIs(app.accept_community_relay((primary, backup), primary.origin, {"name": "Test"}), connection)
            self.assertEqual(connect.call_args_list, [mock.call(primary), mock.call(backup)])
            method, path, payload, key = directory.call_args.args
            self.assertEqual((method, path, key), ("PUT", "/v1/creators/studio-id", "existing-key"))
            self.assertEqual(payload["status_url"], backup.origin + "/api/public/channels")
            self.assertEqual(app.config()["public_url"], backup.origin)
            self.assertEqual(app.config()["community_relay_origin"], primary.origin)
            self.assertEqual(app.REGISTERED_ORIGIN, backup.origin)
            activate.assert_called_once_with(connection)

    def test_community_backup_waits_for_directory_acceptance_before_switching(self):
        primary = app.CommunityRelayProfile("https://primary.example", "relay-a.example", "studio", 22, 18101, "")
        backup = primary._replace(origin="https://backup.example", ssh_host="relay-b.example")
        current = app.CommunityRelayConnection(primary, 8790, mock.Mock())
        candidate = app.CommunityRelayConnection(backup, 8790, mock.Mock())
        with (
            mock.patch.object(app, "REGISTERED_ORIGIN", primary.origin),
            mock.patch.object(app, "COMMUNITY_RELAY", current),
            mock.patch.object(app, "connect_community_relay", return_value=candidate) as connect,
            mock.patch.object(app, "relay_transfers_media", return_value=True),
            mock.patch.object(app, "publish_creator", side_effect=ValueError("directory unavailable")),
            mock.patch.object(app, "stop_relay_process") as stop,
            mock.patch.object(app, "set_community_relay") as activate,
        ):
            with self.assertRaisesRegex(ValueError, "directory unavailable"):
                app.accept_community_relay((primary, backup), primary.origin, {"name": "Test"}, unavailable=primary.origin)
            connect.assert_called_once_with(backup)
            activate.assert_not_called()
            stop.assert_called_once_with(candidate.process)
            self.assertEqual(app.REGISTERED_ORIGIN, primary.origin)

    def test_relay_transfer_probe_consumes_challenge_without_starting_encoding(self):
        server = app.open_public_media_server(0)
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with mock.patch.object(app.secrets, "token_urlsafe", return_value="one-use"), \
                    mock.patch.object(app, "start_ffmpeg_session") as start:
                self.assertTrue(app.relay_transfers_media(origin))
                self.assertTrue(app.relay_transfers_media(origin, hd=True))
                with urlopen(origin + "/api/public/origin?sample=one-use", timeout=2) as used:
                    self.assertEqual(json.load(used), {"instance": app.RELAY_INSTANCE})
                start.assert_not_called()
                self.assertNotIn("one-use", app.RELAY_MEDIA_CHALLENGES)
        finally:
            server.shutdown()
            server.server_close()

    def test_community_backup_that_cannot_transfer_video_is_not_published(self):
        primary = app.CommunityRelayProfile("https://primary.example", "relay-a.example", "studio", 22, 18101, "")
        backup = primary._replace(origin="https://backup.example", ssh_host="relay-b.example")
        current = app.CommunityRelayConnection(primary, 8790, mock.Mock())
        candidate = app.CommunityRelayConnection(backup, 8790, mock.Mock())
        with (
            mock.patch.object(app, "REGISTERED_ORIGIN", primary.origin),
            mock.patch.object(app, "COMMUNITY_RELAY", current),
            mock.patch.object(app, "connect_community_relay", return_value=candidate),
            mock.patch.object(app, "relay_transfers_media", return_value=False) as probe,
            mock.patch.object(app, "publish_creator") as publish,
            mock.patch.object(app, "stop_relay_process") as stop,
        ):
            with self.assertRaisesRegex(ValueError, "720p test segment"):
                app.accept_community_relay((primary, backup), primary.origin, {"name": "Test"}, unavailable=primary.origin)
            probe.assert_called_once_with(backup.origin, hd=True)
            publish.assert_not_called()
            stop.assert_called_once_with(candidate.process)
            self.assertIs(app.COMMUNITY_RELAY, current)

    def test_old_channel_without_id_receives_one_persistent_id(self):
        app.atomic_json(app.CHANNELS_FILE, [{"name":"Archive", "items":[]}])
        first = app.channels()[0]["id"]
        self.assertTrue(first)
        self.assertEqual(app.load_json(app.CHANNELS_FILE, [])[0]["id"], first)
        self.assertEqual(app.channels()[0]["id"], first)

    def test_worldwide_sharing_reconnects_a_broken_funnel(self):
        old = "https://previous.example.com"
        new = "https://reconnected.example.com"
        profile = {"name": "Test studio"}
        settings = {"creator_profile": profile, "pending_creator_profile": {}, "public_tunnel": True, "public_url": old}
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
            publish.assert_called_once_with(profile, origin=new)
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

    def test_legacy_relay_migrates_to_configured_vps_before_funnel(self):
        old = "https://current.app.lhr.life"
        new = "https://studio.relay.example.com"
        profile = app.CommunityRelayProfile(new, "relay.example.com", "studio", 22, 18102, "")
        with (
            mock.patch.object(app, "load_json", return_value={"origin": new}),
            mock.patch.object(app, "parse_community_relay_profile", return_value=profile),
        ):
            self.assertEqual(app.studio_publication_origin(old, old), new)
            self.assertEqual(app.studio_publication_origin("", old), new)

    def test_relay_changes_from_an_existing_studio_keep_its_directory_id(self):
        profile = {"name": "Upgraded Studio", "slug": "upgraded-studio"}
        studio_id = "aabbccdd"
        app.atomic_json(app.CONFIG_FILE, {"creator_id": studio_id, "creator_key": "test-key",
                                          "creator_profile": profile,
                                          "public_url": "https://previous.app.lhr.life"})
        with mock.patch.object(app, "directory_request", return_value={"creator": {"id": studio_id}}) as publish:
            for origin in ("https://new.example.org", "https://after-restart.example.org"):
                self.assertEqual(app.publish_creator(profile, origin=origin)["id"], studio_id)
                self.assertEqual(app.config()["public_url"], origin)
        self.assertEqual([call.args[:2] for call in publish.call_args_list],
                         [("PUT", f"/v1/creators/{studio_id}")] * 2)

    def test_studio_restart_reuses_saved_vps_identity_when_funnel_is_needed(self):
        studio_id = "ab0123456789abcd"
        origin = f"https://relay.example.ts.net/studio/{studio_id}"
        secret = "fixture-directory-key"
        host_key = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(32)
        relay = {"origin": origin, "ssh_host": "vps.example.net", "ssh_user": "studio", "ssh_port": 18088,
                 "remote_port": 18101, "host_key": "ssh-ed25519 " + app.base64.b64encode(host_key).decode("ascii"),
                 "funnel_host": "relay.example.ts.net", "funnel_port": 8443}
        enrollment = app.DATA_DIR / "managed-community-relay.json"
        identity = app.DATA_DIR / "community-relay-identity"
        for path in (enrollment, identity):
            self.addCleanup(path.unlink, missing_ok=True)
        app.atomic_json(enrollment, {"creator_id": studio_id, "profile": relay})
        identity.write_bytes(b"fixture-existing-ssh-key")
        app.atomic_json(app.CONFIG_FILE, {"public_url": origin, "community_relay_origin": origin,
                                         "creator_id": studio_id, "creator_key": secret,
                                         "creator_profile": {"name": "Existing Studio"}, "managed_relay": True})
        saved_enrollment = enrollment.read_bytes()
        fallback = mock.Mock()
        fallback.getsockname.return_value = (app.HOST, 19000)
        process = mock.Mock(poll=mock.Mock(return_value=None))
        media_proof = app.hashlib.sha256(app.RELAY_MEDIA_SAMPLE).hexdigest()
        with (
            mock.patch.object(app, "REGISTERED_ORIGIN", ""),
            mock.patch.object(app, "SHARING_STATUS", "idle"),
            mock.patch.object(app, "COMMUNITY_RELAY", None),
            mock.patch.object(app, "PUBLIC_MEDIA_SERVER", mock.Mock(server_port=8790)),
            mock.patch.object(app, "RELAY_JOB", mock.Mock()),
            mock.patch.object(app, "HEARTBEAT_STATUS", {"failures": 0}),
            mock.patch.object(app, "retire_orphaned_relay"),
            mock.patch.object(app, "install_default_assets"),
            mock.patch.object(app, "generate_exports"),
            mock.patch.object(app, "start_public_media_server"),
            mock.patch.object(app, "stop_legacy_relay"),
            mock.patch.object(app, "openssh_tool", return_value="ssh") as openssh,
            mock.patch.object(app, "start_relay_tls_bridge", return_value=fallback) as bridge,
            mock.patch.object(app, "set_community_relay") as activate,
            mock.patch.object(app.subprocess, "Popen", side_effect=[mock.Mock(poll=mock.Mock(return_value=1)), process]) as ssh,
            mock.patch.object(app, "directory_request", side_effect=[{"instance": app.RELAY_INSTANCE},
                               {"sha256": media_proof}, {"creator": {"id": studio_id}}]) as directory,
            mock.patch.object(app.PUBLISH_CHANGED, "wait", side_effect=InterruptedError),
        ):
            app.ensure_files()
            with self.assertRaises(InterruptedError):
                app.restore_public_sharing()
            self.assertEqual(app.SHARING_STATUS, "live")
            self.assertEqual(app.REGISTERED_ORIGIN, origin)
        openssh.assert_called_once_with("ssh")
        bridge.assert_called_once_with(relay["funnel_host"], relay["funnel_port"])
        self.assertEqual(ssh.call_args_list[0].args[0][-3:], ["-p", "18088", "vps.example.net"])
        self.assertEqual(ssh.call_args_list[1].args[0][-3:], ["-o", "HostKeyAlias=[vps.example.net]:18088", app.HOST])
        for launch in ssh.call_args_list:
            command = launch.args[0]
            self.assertEqual(command[command.index("-i") + 1], str(identity))
            self.assertEqual(command[command.index("-R") + 1], "127.0.0.1:18101:127.0.0.1:8790")
        self.assertIs(activate.call_args.args[0].process, process)
        self.assertEqual([call.args[:2] for call in directory.call_args_list],
                         [("POST", f"/v1/creators/{studio_id}/relay")] * 2 + [("PUT", f"/v1/creators/{studio_id}")])
        self.assertTrue(all(call.args[3] == secret for call in directory.call_args_list))
        self.assertEqual(enrollment.read_bytes(), saved_enrollment)
        self.assertEqual(identity.read_bytes(), b"fixture-existing-ssh-key")
        self.assertEqual(app.config()["public_url"], origin)

    def test_published_studio_migrates_automatically_and_keeps_its_live_route_on_failure(self):
        origin = "https://old.app.lhr.life"
        profile = {"name": "Test Studio"}
        app.atomic_json(app.CONFIG_FILE, {"public_url": origin, "creator_id": "aabbccdd",
                                          "creator_key": "test-key", "creator_profile": profile,
                                          "auto_publish_channels": True})
        with (mock.patch.object(app, "REGISTERED_ORIGIN", origin),
              mock.patch.object(app, "SHARING_STATUS", "live"),
              mock.patch.object(app, "publish_studio", side_effect=ValueError("relay unavailable")) as migrate,
              mock.patch.object(app, "relay_reaches_this_creator", return_value=True),
              mock.patch.object(app.PUBLISH_CHANGED, "wait", side_effect=InterruptedError)):
            with self.assertRaises(InterruptedError):
                app.restore_public_sharing()
            migrate.assert_called_once_with({**profile, "managed_relay": True})
            self.assertEqual(app.REGISTERED_ORIGIN, origin)
            self.assertEqual(app.SHARING_STATUS, "live")
        settings = app.config()
        self.assertEqual(settings["public_url"], origin)
        self.assertEqual(settings["creator_profile"], profile)
        self.assertEqual(settings["pending_creator_profile"], {})

    def test_relay_recovery_keeps_healthy_origin_when_upgrade_fails(self):
        self.assert_relay_recovery(current_healthy=True)

    def test_relay_recovery_uses_legacy_when_preferred_routes_and_old_origin_fail(self):
        self.assert_relay_recovery(current_healthy=False)

    def assert_relay_recovery(self, *, current_healthy):
        origin = "https://existing.tailnet.ts.net"
        profile = {"name": "Test Studio"}
        settings = {"public_url": origin, "creator_profile": profile, "creator_id": "aabbccdd",
                    "creator_key": "test-key", "directory_url": app.DEFAULT_DIRECTORY_URL,
                    "auto_publish_channels": True, "pending_creator_profile": {**profile, "managed_relay": True}}

        def publish_fallback(_profile):
            app.REGISTERED_ORIGIN = "https://backup.app.lhr.life"

        with (mock.patch.object(app, "REGISTERED_ORIGIN", origin),
              mock.patch.object(app, "SHARING_STATUS", "live"),
              mock.patch.object(app, "config", return_value=settings),
              mock.patch.object(app, "publish_studio", side_effect=ValueError("preferred unavailable")) as upgrade,
              mock.patch.object(app, "relay_reaches_this_creator", return_value=current_healthy) as probe,
              mock.patch.object(app, "fallback_studio_relay", side_effect=publish_fallback) as fallback,
              mock.patch.object(app, "set_community_relay") as retire,
              mock.patch.object(app.PUBLISH_CHANGED, "wait", side_effect=InterruptedError)):
            with self.assertRaises(InterruptedError):
                app.restore_public_sharing()
            upgrade.assert_called_once_with(settings["pending_creator_profile"])
            probe.assert_called_once_with(origin)
            if current_healthy:
                fallback.assert_not_called()
                retire.assert_not_called()
                self.assertEqual(app.REGISTERED_ORIGIN, origin)
            else:
                fallback.assert_called_once_with(profile)
                retire.assert_called_once_with(None)
                self.assertEqual(app.REGISTERED_ORIGIN, "https://backup.app.lhr.life")
            self.assertEqual(app.SHARING_STATUS, "live")

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

    def test_public_and_local_viewers_join_one_published_channel(self):
        channel = {"id": "shared", "schedule_anchor": "2026-01-01T00:00:00Z"}
        settings = {"creator_profile": {"name": "Studio"}, "public_tunnel": True,
                    "public_url": "https://studio.example"}
        with mock.patch.object(app, "config", return_value=settings), \
             mock.patch.object(app, "channels", return_value=[channel]), \
             mock.patch.object(app, "LIVE_PLAYOUT", {}), \
             mock.patch.object(app.LivePlayout, "run"):
            manifests = []
            for kind in (app.Handler, app.PublicMediaHandler):
                handler = object.__new__(kind)
                handler.send_bytes = mock.Mock()
                handler.broadcast("shared", "master", None)
                manifests.append(handler.send_bytes.call_args.args[1])
            self.assertEqual(len(app.LIVE_PLAYOUT), 1)
            self.assertIn(b"/broadcast/shared/", manifests[0])
            self.assertEqual(manifests[0], manifests[1])

    def test_playlist_read_survives_a_file_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            playlist = Path(temp) / "index.m3u8"
            playlist.write_bytes(b"#EXTM3U\n")
            original = open
            attempts = [PermissionError(), FileNotFoundError()]
            def open_during_replace(*args, **kwargs):
                if attempts:
                    raise attempts.pop(0)
                return original(*args, **kwargs)
            with mock.patch("builtins.open", side_effect=open_during_replace):
                self.assertEqual(app.read_encoder_playlist(playlist), b"#EXTM3U\n")

    @unittest.skipUnless(os.name == "nt", "Windows file sharing")
    def test_open_playlist_does_not_lock_the_filename_on_windows(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            playlist = directory / "index.m3u8"
            playlist.write_bytes(b"first")
            replacement = directory / "index.m3u8.tmp"
            replacement.write_bytes(b"second")
            with open(playlist, "rb", opener=app.open_shared_playlist) as reader:
                playlist.rename(directory / "previous.m3u8")
                os.replace(replacement, playlist)
                self.assertEqual(reader.read(), b"first")
            self.assertEqual(app.read_encoder_playlist(playlist), b"second")

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
        reuse_key = ("live", app.media_source_identity(position["item"]), position["start"].isoformat(),
                     "standard", app.BROADCAST_DEFAULT)
        key = "a" * 20
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(app, "STREAM_DIR", Path(temporary)), \
                mock.patch.object(app, "FFMPEG_SESSIONS", {}), mock.patch.object(app, "ENCODER_STARTING", {}):
            directory = Path(temporary)
            (directory / "index.m3u8").touch()
            running = mock.Mock()
            running.poll.return_value = None
            for index in range(app.MAX_ENCODERS - 1):
                app.FFMPEG_SESSIONS[key if index == 0 else str(index)] = {
                    "process": running, "channel_id": "live" if index == 0 else str(index), "directory": directory,
                    "reuse_key": reuse_key if index == 0 else None}
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
            handler.send_bytes.assert_called_once_with(
                200, b"first-segment", "video/mp2t", {"Cache-Control": "public, max-age=60, immutable"})
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
        self.assertEqual(app.version_tuple("v2.3"), app.version_tuple("2.3.0"))

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

