"""Verify the private relay with a synthetic HLS segment and no production services."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import urlopen


def start_loopback_relay(nginx, base, origin_port):
    template = (Path(__file__).resolve().parents[1] / "relay/fanout.conf.in").read_text()
    base.chmod(0o755)
    (base / "logs").mkdir()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = base / "nginx.conf"
    config.write_text(template.replace("__ORIGIN_PORT__", str(origin_port)).replace("__LISTENER_PORT__", str(port)))
    command = [nginx, "-p", str(base) + "/", "-c", str(config)]
    subprocess.run([*command, "-t"], check=True, timeout=5)
    relay = subprocess.Popen([*command, "-g", "daemon off;"])
    try:
        for _ in range(40):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.25):
                    return relay, port
            except OSError:
                if relay.poll() is not None:
                    raise RuntimeError("NGINX exited before starting")
                time.sleep(.05)
        raise RuntimeError("NGINX did not start")
    except BaseException:
        if relay.poll() is None:
            relay.terminate()
            relay.wait(timeout=6)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx", required=True, help="Path to an NGINX binary")
    parser.add_argument("segment", type=Path, help="A synthetic MPEG-TS segment")
    args = parser.parse_args()
    media = args.segment.read_bytes()
    first = "/ffmpeg-stream/" + "1" * 20 + "/segmenthd_0.ts"
    second = first.replace("_0.ts", "_1.ts")
    requests = Counter()
    lock = threading.Lock()

    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                requests[self.path] += 1
                attempt = requests[self.path]
            if self.path == "/broadcast/test/master.m3u8":
                self.respond(200, f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:{attempt}\n#EXTINF:2,\n{first}\n".encode())
            elif self.path == second and attempt == 1:
                self.respond(503, b"starting encoder")
            elif self.path in (first, second):
                time.sleep(.25)
                self.respond(200, media)
            else:
                self.respond(404, b"unknown path")

        def respond(self, status, data):
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Origin) as origin, tempfile.TemporaryDirectory() as name:
        thread = threading.Thread(target=origin.serve_forever, daemon=True)
        thread.start()
        relay, port = start_loopback_relay(args.nginx, Path(name), origin.server_port)

        def get(path):
            with urlopen(f"http://127.0.0.1:{port}{path}", timeout=8) as response:
                return response.read()

        try:
            assert get("/broadcast/test/master.m3u8") != get("/broadcast/test/master.m3u8")
            clients = 16
            with ThreadPoolExecutor(clients) as pool:
                fresh = list(pool.map(get, [first] * clients))
            assert all(body == media for body in fresh)
            assert requests[first] == 1, requests
            try:
                get(second)
            except HTTPError as error:
                assert error.code == 503
            else:
                raise AssertionError("The transient failure was hidden")
            assert get(second) == get(second) == media and requests[second] == 2
            try:
                get("/api/settings")
            except HTTPError as error:
                assert error.code == 404 and requests["/api/settings"] == 0
            else:
                raise AssertionError("Unexpected forwarding to the Creator settings API")
            subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-frames:v", "1", "-f", "null", "-"],
                           input=fresh[0], check=True, timeout=8)
            print(json.dumps({"viewer_requests": clients, "creator_segment_requests": requests[first],
                              "bytes_per_segment": len(media), "decoded": True, "manifests_cached": False,
                              "unavailable_segments_retried": True}))
        finally:
            relay.terminate()
            relay.wait(timeout=6)
            origin.shutdown()
            thread.join(timeout=3)


if __name__ == "__main__":
    main()
