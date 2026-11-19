import json
import os
import tempfile
import threading
import unittest
import http.client
from unittest import mock
from pathlib import Path

os.environ["PUBLIC_CHANNELFORGE_HOME"] = tempfile.mkdtemp(prefix="public-channelforge-test-")
import Public_ChannelForge as app


class CoreTests(unittest.TestCase):
    def setUp(self):
        app.DATA_DIR.mkdir(parents=True, exist_ok=True)
        app.atomic_json(app.CONFIG_FILE, {"plex_url": "http://127.0.0.1:32400", "plex_token": "test", "guide_hours": 2, "port": 8787, "bind_host": "127.0.0.1", "public_url": ""})
        app.atomic_json(app.CHANNELS_FILE, [])

    def test_public_config_hides_token(self):
        value = app.config(True)
        self.assertEqual(value["plex_token"], "")
        self.assertTrue(value["token_saved"])

    def test_channel_validation_and_exports(self):
        channel = app.save_channel({"name": "My Channel", "number": "101", "section": "1", "items": [{"id": "9", "title": "Example", "type": "movie", "duration_ms": 3600000, "key": "/library/metadata/9"}]})
        self.assertEqual(channel["name"], "My Channel")
        guide = (app.EXPORT_DIR / "guide.xml").read_text(encoding="utf-8")
        playlist = (app.EXPORT_DIR / "channels.m3u").read_text(encoding="utf-8")
        self.assertIn("My Channel", guide)
        self.assertIn("public-channelforge.", playlist)
        self.assertIn(f"http://127.0.0.1:8787/live/{channel['id']}.m3u8", playlist)
        self.assertNotIn("web/index.html", playlist)

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

    def test_tv_library_returns_playable_episodes(self):
        libraries = [{"key": "2", "title": "TV", "type": "show"}]
        root = app.ET.fromstring('<MediaContainer><Video ratingKey="99" type="episode" title="Pilot" grandparentTitle="Example Show" parentIndex="1" index="1" duration="1800000" key="/library/metadata/99"/></MediaContainer>')
        with mock.patch.object(app, "plex_libraries", return_value=libraries), mock.patch.object(app, "plex_request", return_value=root) as request:
            items = app.plex_items("2")
        request.assert_called_once_with("/library/sections/2/all?type=4")
        self.assertEqual(items[0]["key"], "/library/metadata/99")
        self.assertEqual(items[0]["series"], "Example Show")
        self.assertIn("S01E01", items[0]["title"])

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
        manifest = b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\nsegment000.ts?token=private\n'
        rewritten = app.rewrite_hls_manifest(manifest, "http://plex.local:32400/session/master.m3u8").decode()
        self.assertNotIn("plex.local", rewritten)
        self.assertNotIn("token=private", rewritten)
        self.assertEqual(rewritten.count("/hls-proxy/"), 2)
        registered = [url for url, _expires in app.HLS_PROXY.values()]
        self.assertIn("http://plex.local:32400/session/segment000.ts?token=private", registered)

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
        app.install_default_assets()
        built_ins = [asset for asset in app.asset_manifest() if asset.get("built_in")]
        self.assertEqual(len(built_ins), len(app.DEFAULT_ASSET_THEMES) * 3)
        self.assertEqual({asset["kind"] for asset in built_ins}, {"logo", "guide-background", "corner-bug"})
        for asset in built_ins:
            self.assertTrue((app.ASSETS_DIR / asset["filename"]).is_file())
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
            with mock.patch.object(app, "fetch_plex_stream", return_value=(b"#EXTM3U\n", "application/vnd.apple.mpegurl", "http://plex/master.m3u8", 200, "")):
                connection.request("GET", f"/live/{channel['id']}.m3u8")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Content-Type"), "application/vnd.apple.mpegurl")
                self.assertEqual(response.read(), b"#EXTM3U\n")
        finally:
            connection.close()
            server.shutdown()
            server.server_close()

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
        with mock.patch.object(app, "latest_release", return_value=release), mock.patch.object(app, "github_bytes", side_effect=download), mock.patch.object(app.sys, "platform", "win32"):
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
        with mock.patch.object(app, "latest_release", return_value=release), mock.patch.object(app, "github_bytes", return_value=f"{digest}  Public_ChannelForge_Setup.exe\n".encode()), mock.patch.object(app, "github_file", side_effect=download), mock.patch.object(app.sys, "platform", "win32"):
            result = app.download_verified_update(lambda *event: events.append(event))
        self.assertEqual(result["sha256"], digest)
        self.assertTrue(any(event[0] == "downloading" for event in events))
        self.assertEqual(events[-1][0], "complete")


if __name__ == "__main__":
    unittest.main()

