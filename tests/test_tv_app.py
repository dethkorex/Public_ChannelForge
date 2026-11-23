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
