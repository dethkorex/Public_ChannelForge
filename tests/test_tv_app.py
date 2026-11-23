import hashlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import ChannelForge_TV as tv


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
    def test_version_comparison_handles_double_digit_minor(self):
        self.assertGreater(tv.version_tuple("v3.10.0"), tv.version_tuple("3.9.9"))

    def test_fire_tv_has_directory_recovery_pairing_and_branding(self):
        root = Path(__file__).resolve().parents[1]
        activity = (root / "tv/firetv/app/src/main/java/tv/channelforge/viewer/MainActivity.java").read_text(encoding="utf-8")
        manifest = (root / "tv/firetv/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
        gradle = (root / "tv/firetv/app/build.gradle").read_text(encoding="utf-8")
        self.assertIn("showPairingDialog", activity)
        self.assertIn("SCAN PUBLIC DIRECTORY", activity)
        self.assertIn("checkUpdates(updateButton)", activity)
        self.assertIn("if(!guideVisible&&", activity)
        self.assertIn("DIRECTORY_FALLBACK_URL", activity)
        self.assertIn("DIRECTORY_REFRESH_MS", activity)
        self.assertIn("guide.postDelayed(directoryRefresh", activity)
        self.assertIn("android:icon=\"@mipmap/channel_forge_tv_icon\"", manifest)
        self.assertIn("android:banner=\"@drawable/channel_forge_tv_banner\"", manifest)
        self.assertIn("DIRECTORY_FALLBACK_URL", gradle)
        for density in ("mdpi", "hdpi", "xhdpi", "xxhdpi"):
            self.assertTrue((root / f"tv/firetv/app/src/main/res/mipmap-{density}/channel_forge_tv_icon.png").is_file())
        self.assertTrue((root / "tv/firetv/app/src/main/res/drawable-nodpi/channel_forge_tv_banner.png").is_file())

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
        self.assertIn("manifest source components images", workflow)
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
        self.assertIn('height:68px!important', tv.CINEMA_FIX_CSS)
        self.assertIn('overflow-y:auto!important', tv.CINEMA_FIX_CSS)
        self.assertIn('content-visibility:auto', tv.CINEMA_FIX_CSS)
        self.assertIn('scrollbar-width:none', tv.CINEMA_FIX_CSS)
        self.assertIn("v.controls=!liveMode", tv.HTML)
        self.assertIn("if(liveMode&&!video.ended&&video.readyState>=2", tv.HTML)
        self.assertIn("addEventListener('ended',()=>advanceLive())", tv.HTML)
        self.assertIn("backBufferLength:120", tv.HTML)
        self.assertIn("liveSyncDurationCount:1", tv.HTML)
        self.assertIn("backBufferLength:2", tv.HTML)
        self.assertIn(".tv-guide::-webkit-scrollbar{display:none}", tv.GUIDE_STAGE_CSS)

    def test_tv_scans_online_channels_into_guide_and_keeps_home_focused(self):
        self.assertIn('id="scan-channels"', tv.HTML)
        self.assertIn('id="tv-guide"', tv.HTML)
        self.assertIn("async function scanChannels", tv.HTML)
        self.assertIn("filter(channel=>channel.online)", tv.HTML)
        self.assertIn('id="home-changelog"', tv.HTML)
        self.assertIn('data-section-block="cinema" hidden><h2>CREATOR STUDIOS', tv.HTML)
        self.assertIn("if(name==='cinema'&&selectedCreator&&!vaultItems.length)studio(selectedCreator)", tv.HTML)
        self.assertIn("if(manual){await load();return liveChannels}", tv.HTML)
        self.assertIn("scanChannels(true)", tv.HTML)

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
        with mock.patch.object(tv, "UPDATE_DIR", update_dir), mock.patch.object(tv, "latest_tv_release", return_value=release), mock.patch.object(tv.urllib.request, "urlopen", side_effect=response):
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


if __name__ == "__main__":
    unittest.main()
