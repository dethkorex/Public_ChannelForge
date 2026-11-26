import hashlib
import http.client
import http.server
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
from contextlib import closing
from pathlib import Path
from unittest import mock

import ChannelForge_TV as tv
import linux_runtime
import release_metadata


class FakeResponse:
    def __init__(self, payload: bytes):
        self.stream = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}
        self.status = 200

    def read(self, size=-1):
        return self.stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class TvAppTests(unittest.TestCase):
    def test_linux_archive_update_replaces_bundles_in_same_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "new"
            installed = root / "my ChannelForge"
            source.mkdir()
            installed.mkdir()
            for name in linux_runtime.LINUX_APP_FILES:
                if name in ("web", "plugins", "assets"):
                    (source / name).mkdir()
                    (source / name / "new.txt").write_text("new")
                    (installed / name).mkdir()
                    (installed / name / "obsolete.txt").write_text("old")
                else:
                    (source / name).write_text("new")
                    (installed / name).write_text("old")
            (installed / "personal.txt").write_text("keep")
            linux_runtime.install_linux_files(source, installed)
            self.assertEqual((installed / "ChannelForge_TV.py").read_text(), "new")
            self.assertEqual((installed / "Public_ChannelForge.py").read_text(), "new")
            self.assertEqual((installed / "personal.txt").read_text(), "keep")
            self.assertFalse((installed / "web" / "obsolete.txt").exists())
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["my ChannelForge", "new"])
            command = linux_runtime.linux_install_command(root / "update.tar.gz", installed)
            self.assertIn("--replace", command)
            self.assertIn("trap", command)

    def test_linux_installer_rechecks_download_before_running_package_manager(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "verified.deb"
            package.write_bytes(b"altered")
            expected = hashlib.sha256(b"original").hexdigest()
            with mock.patch.object(linux_runtime, "can_apply_linux_update", return_value=True), \
                    mock.patch.object(linux_runtime.subprocess, "run") as install:
                with self.assertRaisesRegex(ValueError, "changed after verification"):
                    linux_runtime.apply_linux_update(package, expected, Path("/opt/public-channelforge"))
            install.assert_not_called()
            self.assertIn(expected, linux_runtime.linux_install_command(package, expected_sha256=expected))

    def test_omarchy_package_uses_system_package_manager_and_polkit(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "public-channelforge-3.6.0-1-any.pkg.tar.zst"
            package.write_bytes(b"package")
            with mock.patch.object(linux_runtime, "can_apply_linux_update", return_value=True), \
                    mock.patch.object(linux_runtime.os, "geteuid", create=True, return_value=1000), \
                    mock.patch.object(linux_runtime.subprocess, "run") as install:
                linux_runtime.apply_linux_update(package, hashlib.sha256(b"package").hexdigest(), Path("/opt/public-channelforge"))
            self.assertEqual(install.call_args.args[0], ["/usr/bin/pkexec", "/usr/bin/pacman", "--noconfirm", "-U", str(package)])

    def test_version_comparison_handles_double_digit_minor(self):
        self.assertGreater(tv.version_tuple("v3.10.0"), tv.version_tuple("3.9.9"))

    def test_preview_updates_converge_on_stable(self):
        def release(tag, **flags):
            return {"tag_name": tag, "assets": [{"name": "ChannelForge_Setup.exe"}], **flags}

        alpha = release("v4.0.0-alpha.9", prerelease=True)
        newer_alpha = release("v4.0.0-alpha.10", prerelease=True)
        final = release("v4.0.0")
        draft = release("v5.0.0", draft=True)
        for entries, expected in [([alpha, newer_alpha, draft], newer_alpha), ([alpha, final, draft], final)]:
            with mock.patch.object(release_metadata.urllib.request, "urlopen",
                                   return_value=FakeResponse(json.dumps(entries).encode())) as fetch:
                selected = release_metadata.read_latest_release("ChannelForge-TV/4.0.0-alpha.1")
            self.assertEqual(fetch.call_args.args[0].full_url, release_metadata.PREVIEW_SOURCE)
            self.assertEqual(selected, expected)
            self.assertGreater(tv.version_tuple(selected["tag_name"]), tv.version_tuple("4.0.0-alpha.1"))
        # The manifest fallback must also discard a prerelease even if its flag is missing.
        with mock.patch.object(release_metadata.urllib.request, "urlopen", side_effect=[
                FakeResponse(json.dumps(release("v4.0.1-alpha.1")).encode()),
                FakeResponse(json.dumps(final).encode())]):
            self.assertEqual(release_metadata.read_latest_release("ChannelForge-TV/3.6.2"), final)

    def test_update_check_uses_published_manifest_when_api_is_rate_limited(self):
        release = {"tag_name": "v3.9.42", "body": "Playback and library improvements.",
                   "assets": [{"name": "ChannelForge_Setup.exe"}]}
        error = urllib.error.HTTPError(release_metadata.SOURCES[0], 429, "Too many requests", {}, None)
        with mock.patch.object(tv, "VERSION", "3.6.2"), mock.patch.object(release_metadata.urllib.request, "urlopen",
                               side_effect=[error, FakeResponse(json.dumps(release).encode())]) as fetch:
            result = tv.latest_tv_release()
        self.assertTrue(result["update_available"])
        self.assertEqual(result["notes"], release["body"])
        self.assertEqual([call.args[0].full_url for call in fetch.call_args_list], list(release_metadata.SOURCES))

    def test_fire_tv_has_directory_recovery_pairing_and_branding(self):
        root = Path(__file__).resolve().parents[1]
        activity = (root / "tv/firetv/app/src/main/java/tv/channelforge/viewer/MainActivity.java").read_text(encoding="utf-8")
        manifest = (root / "tv/firetv/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        gradle = (root / "tv/firetv/app/build.gradle").read_text(encoding="utf-8")
        self.assertIn("showPairingDialog", activity)
        self.assertIn("SCAN PUBLIC DIRECTORY", activity)
        self.assertIn("checkUpdates(false)", activity)
        self.assertIn("if (!guideVisible)", activity)
        self.assertIn("DIRECTORY_FALLBACK_URL", activity)
        self.assertIn("DIRECTORY_REFRESH_MS", activity)
        self.assertIn("guide.postDelayed(directoryRefresh", activity)
        self.assertIn("android:icon=\"@mipmap/channel_forge_tv_icon\"", manifest)
        self.assertIn("android:banner=\"@drawable/channel_forge_tv_banner\"", manifest)
        self.assertIn("DIRECTORY_FALLBACK_URL", gradle)
        for density in ("mdpi", "hdpi", "xhdpi", "xxhdpi"):
            self.assertTrue((root / f"tv/firetv/app/src/main/res/mipmap-{density}/channel_forge_tv_icon.png").is_file())
        self.assertTrue((root / "tv/firetv/app/src/main/res/drawable-nodpi/channel_forge_tv_banner.png").is_file())

    def test_fire_tv_updates_stage_a_verified_apk_for_the_system_installer(self):
        root = Path(__file__).resolve().parents[1] / "tv/firetv/app/src/main"
        activity = (root / "java/tv/channelforge/viewer/MainActivity.java").read_text(encoding="utf-8")
        updater = (root / "java/tv/channelforge/viewer/FireTvUpdate.java").read_text(encoding="utf-8")
        manifest = (root / "AndroidManifest.xml").read_text(encoding="utf-8")
        self.assertIn("REQUEST_INSTALL_PACKAGES", manifest)
        self.assertIn("STATUS_PENDING_USER_ACTION", activity)
        self.assertIn("session.commit(result.getIntentSender())", activity)
        self.assertNotIn("Intent.ACTION_VIEW", activity)
        self.assertIn("MessageDigest.isEqual(checksum, digest.digest())", updater)
        self.assertIn("Arrays.equals(expected, signatures(candidate))", updater)
        self.assertLess(updater.index("verifyPackage(context, apk)"), updater.index("return stagePackage("))

    def test_roku_is_viewer_only_with_fallback_pairing_and_branding(self):
        root = Path(__file__).resolve().parents[1]
        scene = (root / "tv/roku/components/MainScene.brs").read_text(encoding="utf-8")
        catalog = (root / "tv/roku/components/CatalogTask.brs").read_text(encoding="utf-8")
        manifest = (root / "tv/roku/manifest").read_text(encoding="utf-8")
        workflow = (root / ".github/workflows/tv-apps.yml").read_text(encoding="utf-8")
        self.assertIn("KeyboardDialog", scene)
        self.assertIn("connectPaired", scene)
        self.assertIn("refreshCatalog", scene)
        self.assertIn("directoryTimer", (root / "tv/roku/components/MainScene.xml").read_text(encoding="utf-8"))
        self.assertIn("m.top.fallbackUrl", catalog)
        self.assertIn("mm_icon_focus_fhd=pkg:/images/channel-icon-fhd.png", manifest)
        self.assertIn("mm_icon_focus_hd=pkg:/images/channel-icon-hd.png", manifest)
        self.assertIn("scripts/package_roku.py tv-release/ChannelForgeTV-Roku.zip", workflow)
        self.assertNotIn("Public_ChannelForge.py", "\n".join(path.name for path in (root / "tv/roku").rglob("*")))

    def test_linux_packages_and_installer_include_creator_and_tv(self):
        root = Path(__file__).resolve().parents[1]
        packaging = (root / "packaging/build_linux_packages.sh").read_text(encoding="utf-8")
        installer = (root / "scripts/install_linux.sh").read_text(encoding="utf-8")
        self.assertIn('Public_ChannelForge.py" "$ROOT/ChannelForge_TV.py', packaging)
        self.assertIn('public-channelforge"', installer)
        self.assertIn('channelforge-tv"', installer)
        self.assertIn("Installed both apps", installer)

    def test_tv_prefers_hls_js_and_continuously_scrolls_movie_shelves(self):
        hls_branch = "if(window.Hls&&Hls.isSupported())"
        native_fallback = "else if(v.canPlayType('application/vnd.apple.mpegurl'))"
        self.assertIn(hls_branch, tv.HTML)
        self.assertIn(native_fallback, tv.HTML)
        self.assertLess(tv.HTML.index(hls_branch), tv.HTML.index(native_fallback))
        self.assertIn("function renderVaultPage", tv.HTML)
        self.assertNotIn('id="vault-next"', tv.HTML)
        self.assertNotIn("NEXT SHELF", tv.HTML)
        self.assertIn('id="movie-genre"', tv.HTML)
        self.assertIn('id="movie-rating"', tv.HTML)
        self.assertIn('id="movie-sort"', tv.HTML)
        self.assertNotIn("overflow-y:auto;overscroll-behavior", tv.TV_OVERHAUL_CSS)
        self.assertIn("INSERT MOVIE", tv.HTML)
        self.assertIn("overflow-x:hidden", tv.VAULT_CSS)
        cinema = (Path(__file__).resolve().parents[1] / "web/cinema.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: minmax(0, 1fr)", cinema)
        self.assertIn("overflow: hidden auto", cinema)
        self.assertIn(".cinema-movie:is(:hover,:focus-visible) .cinema-shelf-case", cinema)
        self.assertIn("prefers-reduced-motion", cinema)
        self.assertIn(".application-fullscreen .cinema-mode main.wrap", cinema)
        self.assertIn("grid-template-rows: auto auto minmax(0, 1fr)", cinema)
        self.assertIn("$('#selected-play')?.focus", tv.HTML)
        self.assertNotIn('id="cinema-monitor"', tv.HTML)
        self.assertIn("setApplicationFullscreen(true)", tv.HTML)
        self.assertIn("animateCinemaTape(false", tv.HTML)
        self.assertIn("v.controls=!liveMode", tv.HTML)
        self.assertIn("&&liveMode&&!video.ended&&video.readyState>=2", tv.HTML)
        self.assertIn("addEventListener('ended',", tv.HTML)
        self.assertIn("advanceLive()", tv.HTML)
        self.assertIn("backBufferLength:120", tv.HTML)
        self.assertIn("liveSyncDurationCount:2", tv.HTML)
        self.assertNotIn('body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:50', tv.HTML)
        self.assertIn("backBufferLength:2", tv.HTML)
        self.assertIn(".tv-guide::-webkit-scrollbar{display:none}", tv.GUIDE_STAGE_CSS)

    def test_tv_scans_online_channels_into_guide_and_keeps_home_focused(self):
        self.assertIn('id="scan-channels"', tv.HTML)
        self.assertIn('id="tv-guide"', tv.HTML)
        self.assertIn("async function scanChannels", tv.HTML)
        self.assertIn("value.channels.filter(channel=>channel.stream_url)", tv.HTML)
        self.assertIn('id="home-changelog"', tv.HTML)
        self.assertIn('data-section-block="cinema" hidden><h2>Creator studios', tv.HTML)
        self.assertIn("if(name==='cinema'&&selectedCreator)studio(selectedCreator)", tv.HTML)
        self.assertIn("channelsByStudio.set(creator.playlist_url,channels)", tv.HTML)
        self.assertIn("directoryScan=refreshDirectory()", tv.HTML)

    def test_cached_guide_survives_failure_and_an_authoritative_empty_catalog_is_kept(self):
        guide = "https://studio.example/api/public/channels?guide=extended"
        body = b'{"channels":[{"id":"channel","online":false}]}'
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                tv, "GUIDE_CACHE", Path(directory) / "guide.db"):
            tv.guide_snapshot(guide, body)
            tv.guide_snapshot(guide, b'{"error":"studio unavailable"}')
            self.assertEqual(tv.guide_snapshot(guide), body)
            tv.guide_snapshot(tv.DIRECTORY, b'{"creators":[{"id":"one"}]}')
            tv.guide_snapshot(tv.DIRECTORY, b'{"creators":[],"registrations_complete":false}')
            self.assertEqual(json.loads(tv.guide_snapshot(tv.DIRECTORY))["creators"], [{"id": "one"}])
            tv.guide_snapshot(tv.DIRECTORY, b'{"creators":[],"registrations_complete":true}')
            self.assertEqual(json.loads(tv.guide_snapshot(tv.DIRECTORY))["creators"], [])
            with mock.patch.object(tv.time, "time", return_value=time.time()+8*86400):
                self.assertIsNone(tv.guide_snapshot(guide))

    def test_closed_viewer_is_not_sent_a_second_response(self):
        for error in (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            with self.subTest(error=error):
                handler = object.__new__(tv.Handler)
                handler.wfile = mock.Mock()
                handler.wfile.write.side_effect = error
                handler.send_content_headers = mock.Mock()
                handler.send(200, b"catalog", "application/json")
                self.assertTrue(handler.close_connection)
                handler.send_content_headers.assert_called_once()

    def test_stream_proxy_starts_forwarding_before_segment_finishes(self):
        release_segment = threading.Event()
        prefix, suffix = b"first bytes", b"remaining tail"

        class SlowSegment(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Content-Length", str(len(prefix + suffix)))
                self.end_headers()
                self.wfile.write(prefix)
                release_segment.wait(5)
                self.wfile.write(suffix)

            def log_message(self, *_args):
                pass

        with (
            http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowSegment) as source,
            http.server.ThreadingHTTPServer(("127.0.0.1", 0), tv.Handler) as viewer,
            mock.patch.object(tv, "approved_content_url", side_effect=lambda url: url),
        ):
            for server in (source, viewer):
                threading.Thread(target=server.serve_forever, daemon=True).start()
            with closing(http.client.HTTPConnection("127.0.0.1", viewer.server_port, timeout=2)) as client:
                try:
                    client.request("GET", f"/stream?url=http://127.0.0.1:{source.server_port}/segment.ts")
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(len(prefix)), prefix)
                    release_segment.set()
                    self.assertEqual(response.read(), suffix)
                finally:
                    release_segment.set()
                    viewer.shutdown()
                    source.shutdown()

    @unittest.skipIf(tv.STREAM_POOL is None, "urllib3 connection pooling is unavailable")
    def test_stream_proxy_reuses_the_media_connection(self):
        peers = []

        class Origin(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                peers.append(self.client_address)
                self.send_response(200)
                self.send_header("Content-Length", "5")
                self.end_headers()
                self.wfile.write(b"video")

            def log_message(self, *_args):
                pass

        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin) as origin:
            threading.Thread(target=origin.serve_forever, daemon=True).start()
            try:
                for index in range(2):
                    with tv.open_creator_stream(f"http://127.0.0.1:{origin.server_port}/segment{index}.ts", {}) as (response, _):
                        self.assertEqual(response.read1(16 * 1024), b"video")
                        self.assertEqual(response.read1(16 * 1024), b"")
                self.assertEqual(len(set(peers)), 1)
            finally:
                origin.shutdown()

    def test_tv_has_program_details_favorites_and_mini_player(self):
        self.assertIn('id="program-dialog"', tv.HTML)
        self.assertIn("function showProgramInfo", tv.HTML)
        self.assertIn("favoriteChannels", tv.HTML)
        self.assertIn("function minimizePlayer", tv.HTML)
        self.assertIn("video.mini", tv.TV_OVERHAUL_CSS)
        self.assertIn('id="movie-search"', tv.HTML)
        self.assertIn('data-movie-filter="80s"', tv.HTML)
        self.assertIn("poster=current?.poster_url", tv.HTML)
        self.assertIn("--guide-backdrop", tv.HTML)
        self.assertIn("program-poster", tv.HTML)

    def test_local_creator_is_discovered_on_default_port(self):
        payload = json.dumps({
            "channels": [{"id": "anime"}],
            "config": {"creator_profile": {"name": "Anime Studio"}},
        }).encode()
        with mock.patch.object(tv.urllib.request, "urlopen", return_value=FakeResponse(payload)):
            result = tv.local_creator()
        self.assertTrue(result["available"])
        self.assertEqual(result["creator"]["name"], "Anime Studio")
        self.assertEqual(result["creator"]["playlist_url"], "http://127.0.0.1:8787/exports/channels.m3u")
        self.assertEqual(result["creator"]["status_url"], "http://127.0.0.1:8787/api/public/channels")
        self.assertIn("1 local channel", result["creator"]["description"])

    def test_content_proxy_allows_only_https_or_local_creator(self):
        self.assertEqual(tv.approved_content_url("http://127.0.0.1:8787/live/anime.m3u8"), "http://127.0.0.1:8787/live/anime.m3u8")
        self.assertEqual(tv.approved_content_url("https://creator.example/live.m3u8"), "https://creator.example/live.m3u8")
        with self.assertRaises(ValueError):
            tv.approved_content_url("http://127.0.0.1:9999/private")

    def test_tv_stream_proxy_rewrites_manifest_links(self):
        manifest = b"#EXTM3U\nsegment01.ts\n"
        rewritten = tv.rewrite_stream_manifest(manifest, "http://127.0.0.1:8787/hls/index.m3u8").decode()
        self.assertIn("/stream?url=", rewritten)
        self.assertNotIn("\nsegment01.ts", rewritten)

    def test_tv_update_download_is_verified_before_ready(self):
        executable = b"verified-channel-forge-tv"
        digest = hashlib.sha256(executable).hexdigest()
        release = {
            "latest_version": "9.0.0",
            "update_available": True,
            "assets": {
                "ChannelForge_TV_Setup.exe": "https://github.com/example/tv-setup.exe",
                "SHA256SUMS.txt": "https://github.com/example/sums.txt",
            },
        }

        def response(request, **_kwargs):
            url = request.full_url
            if url.endswith("sums.txt"):
                return FakeResponse(f"{digest}  ChannelForge_TV_Setup.exe\n".encode())
            return FakeResponse(executable)

        update_dir = Path(tempfile.mkdtemp(prefix="channel-forge-tv-update-"))
        with (
            mock.patch.object(tv.sys, "platform", "win32"),
            mock.patch.object(tv, "UPDATE_DIR", update_dir),
            mock.patch.object(tv, "latest_tv_release", return_value=release),
            mock.patch.object(tv.urllib.request, "urlopen", side_effect=response),
        ):
            job_id = tv.start_tv_update()
            for _ in range(100):
                job = tv.UPDATE_JOBS[job_id]
                if job["status"] != "running":
                    break
                time.sleep(0.01)

        self.assertEqual(job["status"], "complete")
        self.assertEqual(job["percent"], 100)
        self.assertEqual(Path(job["result"]["path"]).read_bytes(), executable)
        self.assertIn("Setup", Path(job["result"]["path"]).name)

    def test_linux_tv_update_selects_and_verifies_linux_archive(self):
        archive = b"verified-linux-suite"
        name = "ChannelForge-9.0.0-linux-x64.tar.gz"
        release = {
            "latest_version": "9.0.0",
            "update_available": True,
            "assets": {
                name: "https://github.com/example/suite.tar.gz",
                "SHA256SUMS-Linux.txt": "https://github.com/example/linux-sums.txt",
                "ChannelForge_Setup.exe": "https://github.com/example/setup.exe",
            },
        }

        def response(request, **_kwargs):
            if request.full_url.endswith("linux-sums.txt"):
                return FakeResponse(f"{hashlib.sha256(archive).hexdigest()}  {name}\n".encode())
            self.assertEqual(request.full_url, release["assets"][name])
            return FakeResponse(archive)

        with (
            tempfile.TemporaryDirectory(prefix="linux tv update ") as directory,
            mock.patch.object(tv.sys, "platform", "linux"),
            mock.patch.object(tv, "UPDATE_DIR", Path(directory)),
            mock.patch.object(tv, "latest_tv_release", return_value=release),
            mock.patch.object(tv.urllib.request, "urlopen", side_effect=response),
        ):
            job_id = tv.start_tv_update()
            for _ in range(100):
                job = tv.UPDATE_JOBS[job_id]
                if job["status"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "complete", job)
            self.assertEqual(Path(job["result"]["path"]).read_bytes(), archive)
            self.assertIn("tar -xzf", job["result"]["command"])


if __name__ == "__main__":
    unittest.main()
