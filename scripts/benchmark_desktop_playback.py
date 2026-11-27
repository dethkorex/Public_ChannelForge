"""Measure HLS playback in the built-in desktop WebView.

Run once per fresh process. Pass a directory containing test.m3u8 and TS segments.
QA dependencies: psutil and pywebview. No production configuration.
"""
import argparse
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ChannelForge_TV as tv

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("engine", choices=("webview",))
parser.add_argument("fixture", type=Path)
args = parser.parse_args()
sent = 0


class Fixture(tv.Handler):
    def do_GET(self):
        global sent
        req = urlsplit(self.path)
        if req.path == "/stream":
            origin = parse_qs(req.query)["url"][0]
            file = args.fixture / Path(urlsplit(origin).path).name
            data = file.read_bytes()
            playlist = file.suffix == ".m3u8"
            if playlist:
                data = tv.rewrite_stream_manifest(data, origin)
            sent += len(data)
            return self.send(200, data, "application/vnd.apple.mpegurl" if playlist else "video/mp2t")
        if req.path in ("/proxy", "/api/update", "/api/local-creator", "/guide-cache"):
            return self.send(200, b'{"creators":[]}', "application/json")
        return super().do_GET()

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "https://fixture.example/test.m3u8"
base = f"http://127.0.0.1:{server.server_port}"
result = {}


def resources():
    cpu = rss = 0
    proc = psutil.Process()
    for child in [proc, *proc.children(recursive=True)]:
        try:
            times = child.cpu_times()
            cpu += times.user + times.system
            rss += child.memory_info().rss
        except psutil.NoSuchProcess:
            continue
    return cpu, rss


def measure(start, read):
    initial, peak = resources()
    begin = time.monotonic()
    start()
    while time.monotonic() - begin < 25:
        frames, dropped, pos = read()
        cpu, rss = resources()
        peak = max(peak, rss)
        if frames > 0:
            result.setdefault("first_frame_s", round(time.monotonic() - begin, 3))
        if pos >= 7:
            result.update(engine=args.engine, platform=sys.platform, frames=frames, dropped=dropped,
                          media_s=round(pos, 2), cpu_seconds=round(cpu - initial, 2),
                          peak_tree_mib=round(peak / 1048576, 1), http_bytes=sent)
            return
        time.sleep(.05)
    raise RuntimeError(f"{args.engine} did not advance to seven seconds: {frames} frames at {pos}")


import webview
win = webview.create_window("ChannelForge playback benchmark", base, width=640, height=400)

def run():
    try:
        assert win.events.loaded.wait(15)
        def sample():
            return json.loads(win.evaluate_js("JSON.stringify([$('#player').getVideoPlaybackQuality().totalVideoFrames,$('#player').getVideoPlaybackQuality().droppedVideoFrames,$('#player').currentTime])"))
        measure(lambda: win.evaluate_js(f"$('#player').muted=true;play('{origin}',{{kind:'movie', title:'Benchmark'}});true"), sample)
    finally:
        win.destroy()

webview.start(run, gui="edgechromium" if sys.platform == "win32" else "gtk")
server.shutdown()
server.server_close()
if "frames" not in result or "first_frame_s" not in result:
    raise SystemExit(f"Incomplete metrics: {result}")
print(json.dumps(result))
