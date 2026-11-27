"""Verify HTTPS, per-Studio routing, and live segment fan-out without publishing."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from relay.community import configuration


@contextmanager
def origin(media, number):
    requests = Counter()
    available = threading.Event()
    available.set()

    class Origin(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            requests[path] += 1
            status, kind = 200, "application/json"
            if not available.is_set():
                status, body = 503, b"Studio temporarily disconnected"
            elif self.headers.get("Authorization") or self.headers.get("Cookie"):
                status, body = 400, b"Forwarded private headers"
            elif path.endswith(".ts"):
                time.sleep(.2)
                kind, body = "video/mp2t", media
            elif path.endswith(".m3u8"):
                kind = "application/vnd.apple.mpegurl"
                body = f"#EXTM3U\n# request={requests[path]}\n/ffmpeg-stream/{'a'*20}/segment0.ts\n".encode()
            else:
                body = json.dumps({"studio": number, "query": self.path.partition("?")[2]}).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-ChannelForge-Studio-Response-Ms", "200" if path.endswith(".ts") else "1")
            self.end_headers()
            try:
                self.wfile.write(body)
            except ConnectionError:
                pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Origin) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port, requests, available
        finally:
            server.shutdown()
            thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx", required=True, type=Path)
    parser.add_argument("--viewers", type=int, default=40)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args()
    if not 2 <= args.viewers <= 80:
        parser.error("Choose between 2 and 80 test viewers.")
    first, second = args.first.read_bytes(), args.second.read_bytes()
    assert first != second
    with tempfile.TemporaryDirectory(prefix="cf-community-") as folder, origin(first, 1) as a, origin(second, 2) as b:
        base = Path(folder)
        base.chmod(0o755)
        (base / "logs").mkdir()
        cert, key = base / "relay.pem", base / "relay-key.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
            "-keyout", str(key), "-out", str(cert),
        ], check=True, capture_output=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        name_a, name_b = "one.example.com", "two.example.com"
        config = base / "nginx.conf"
        config.write_text(configuration({name_a: a[0], name_b: b[0]}, port, cert, key))
        command = [str(args.nginx), "-p", str(base) + "/", "-c", str(config)]
        subprocess.run([*command, "-t"], check=True, timeout=5)
        relay = subprocess.Popen([*command, "-g", "daemon off;"])
        context = ssl.create_default_context(cafile=str(cert))

        def request(host, path, method="GET", headers_only=False):
            connection = HTTPSConnection("127.0.0.1", port, timeout=10, context=context)
            try:
                connection.request(method, path, headers={"Host": host, "Authorization": "private", "Cookie": "private"})
                response = connection.getresponse()
                if headers_only:
                    return {key: response.getheader(key) for key in (
                        "X-ChannelForge-Relay", "X-ChannelForge-Cache", "X-ChannelForge-Upstream-Header-Time",
                        "X-ChannelForge-Studio-Response-Ms")}
                return response.status, response.read()
            finally:
                connection.close()

        segment = "/ffmpeg-stream/" + "a" * 20 + "/segment0.ts"
        try:
            for attempt in range(25):
                try:
                    status, response = request(name_a, "/api/public/channels?guide=brief")
                    break
                except ConnectionRefusedError:
                    if relay.poll() is not None or attempt == 24:
                        raise
                    time.sleep(.1)
            assert status == 200 and json.loads(response) == {"studio": 1, "query": "guide=brief"}
            assert request(name_a, "/broadcast/live/master.m3u8?viewer=client")[1] != request(
                name_a, "/broadcast/live/master.m3u8?viewer=client")[1]
            assert request(name_b, "/vault/movie.m3u8")[0] == 200
            assert request("unknown.example.com", segment)[0] == 404
            assert request(name_a, "/api/settings")[0] == 404
            assert request(name_a, "/api/public/channels", "POST")[0] == 405
            timing = request(name_a, "/broadcast/live/master.m3u8", headers_only=True)
            assert timing["X-ChannelForge-Relay"] == "vps" and timing["X-ChannelForge-Studio-Response-Ms"] == "1"
            assert float(timing["X-ChannelForge-Upstream-Header-Time"]) >= 0
            with ThreadPoolExecutor(max_workers=args.viewers) as pool:
                responses = list(pool.map(lambda n: request(name_a, f"{segment}?viewer={n}"), range(args.viewers)))
                alternating_hosts = ([name_a, name_b] * args.viewers)[:args.viewers]
                mixed = list(pool.map(lambda host: request(host, segment), alternating_hosts))
            assert all(status == 200 and data == first for status, data in responses)
            assert mixed == [(200, first if host == name_a else second) for host in alternating_hosts]
            assert request(name_a, segment) == (200, first) and a[1][segment] == b[1][segment] == 1
            cached = request(name_a, segment, headers_only=True)
            assert cached["X-ChannelForge-Cache"] == "HIT" and cached["X-ChannelForge-Upstream-Header-Time"] in (None, "-")
            a[2].clear()
            assert request(name_a, segment) == (200, first)
            assert request(name_a, "/broadcast/live/master.m3u8")[0] == 503
            assert request(name_b, "/broadcast/live/master.m3u8")[0] == 200
            a[2].set()
            assert request(name_a, "/broadcast/live/master.m3u8")[0] == 200
            print(json.dumps({"https": True, "single_studio_viewers": args.viewers, "mixed_studio_viewers": len(mixed),
                              "origin_segment_requests": [a[1][segment], b[1][segment]],
                              "cache_isolated_by_studio": True, "manifests_fresh": True, "controls_blocked": True,
                              "upstream_recovery_on_same_hostname": True}))
        finally:
            relay.terminate()
            relay.wait(timeout=6)


if __name__ == "__main__":
    main()
