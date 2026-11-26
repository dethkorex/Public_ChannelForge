import http.server
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tests.creator_fixture import app


class SourceReconnectTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required for playback test")
    def test_interrupted_plex_part_resumes_and_delivers_every_frame(self):
        ffmpeg = shutil.which("ffmpeg")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / "fixture.mp4"
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=160x96:rate=12", "-t", "6", "-c:v", "libx264",
                            "-movflags", "+faststart", str(source)], check=True, timeout=15)
            media = source.read_bytes()
            cutoff = len(media) // 2
            requests = []

            class PlexPart(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    byte_range = re.fullmatch(r"bytes=(\d+)-", self.headers.get("Range", ""))
                    offset = int(byte_range[1]) if byte_range else 0
                    requests.append(offset)
                    self.send_response(206 if byte_range else 200)
                    if byte_range:
                        self.send_header("Content-Range", f"bytes {offset}-{len(media)-1}/{len(media)}")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(len(media) - offset))
                    self.end_headers()
                    self.wfile.write(media[offset:cutoff] if len(requests) == 1 else media[offset:])
                    self.close_connection = True

                def log_message(self, *args):
                    pass

            with http.server.ThreadingHTTPServer(("127.0.0.1", 0), PlexPart) as origin:
                threading.Thread(target=origin.serve_forever, daemon=True).start()
                start = datetime.now(timezone.utc)
                position = {"start": start, "stop": start + timedelta(seconds=6), "offset_ms": 0,
                            "item": {"id": "fixture"}}
                credentials = {"url": f"http://127.0.0.1:{origin.server_port}", "token": "fixture"}
                try:
                    with mock.patch.object(app, "STREAM_DIR", directory), \
                         mock.patch.object(app, "FFMPEG_SESSIONS", {}), \
                         mock.patch.object(app, "plex_credentials", return_value=credentials), \
                         mock.patch.object(app, "plex_part_key", return_value="/library/parts/fixture"), \
                         mock.patch.object(app, "playback_audio_track", return_value=(0, False)):
                        key = app.launch_encoder("fixture", {"id": "test", "on_demand": True},
                                                 position, "public", True, 16 * 1024**2)
                        session = app.FFMPEG_SESSIONS[key]
                        try:
                            self.assertEqual(session["process"].wait(timeout=18), 0)
                            self.assertIn(cutoff, requests[1:])
                            playlist = session["directory"] / "index_economy.m3u8"
                            self.assertIn("#EXT-X-ENDLIST", playlist.read_text())
                            output = subprocess.check_output([ffmpeg, "-v", "error", "-i", str(playlist),
                                                             "-map", "0:v", "-f", "framemd5", "-"], timeout=10)
                            self.assertEqual(sum(line.startswith(b"0,") for line in output.splitlines()), 72)
                        finally:
                            app.dispose_encoder(session)
                finally:
                    origin.shutdown()
