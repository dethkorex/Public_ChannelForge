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

    def test_live_url_uses_plex_hls_at_current_offset(self):
        channel = app.clean_channel({"name": "Live", "number": "3", "schedule_anchor": "2026-01-01T00:00:00+00:00", "items": [
            {"id": "44", "title": "Show", "duration_ms": 3600000, "key": "/library/metadata/44"},
        ]})
        url = app.plex_live_url(channel, app.datetime(2026, 1, 1, 0, 5, tzinfo=app.timezone.utc))
        self.assertIn("/video/:/transcode/universal/start.m3u8?", url)
        self.assertIn("offset=300.000", url)
        self.assertIn("path=%2Flibrary%2Fmetadata%2F44", url)

    def test_extracts_token_from_pasted_plex_url(self):
        url, token = app.normalize_plex_connection("http://plex.local:32400/web/index.html?foo=bar&X-Plex-Token=secret-token")
        self.assertEqual(url, "http://plex.local:32400?foo=bar")
        self.assertEqual(token, "secret-token")
        self.assertNotIn("secret-token", url)

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
            connection.request("GET", f"/live/{channel['id']}.m3u8")
            response = connection.getresponse()
            self.assertEqual(response.status, 302)
            self.assertIn("/video/:/transcode/universal/start.m3u8", response.getheader("Location"))
            response.read()
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


if __name__ == "__main__":
    unittest.main()

