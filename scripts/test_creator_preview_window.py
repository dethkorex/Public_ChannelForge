"""Interactive Windows smoke test for Creator preview shutdown using an HLS audio/video fixture."""
import argparse
import json
import os
import sys
import tempfile
import time
import threading
from pathlib import Path
from urllib.parse import urlsplit

parser = argparse.ArgumentParser()
parser.add_argument("fixture", type=Path, help="directory with test.m3u8 and its TS segments")
parser.add_argument("--baseline-js", type=Path, help="run the previous release's hide behavior")
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as temp:
    os.environ["PUBLIC_CHANNELFORGE_HOME"] = temp
    import Public_ChannelForge as app
    import webview
    app.ensure_files()
    states = []
    class Fixture(app.Handler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path == "/sample":
                states.append(self.body())
                return self.send_json(200, {})
            self.send_error(404)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/app.js" and args.baseline_js:
                return self.send_bytes(200, args.baseline_js.read_bytes(), "text/javascript")
            if path.startswith("/live/"):
                file = args.fixture / ("test.m3u8" if path.endswith(".m3u8") else Path(path).name)
                return self.send_bytes(200, file.read_bytes(), "application/vnd.apple.mpegurl" if file.suffix == ".m3u8" else "video/mp2t")
            if path == "/api/update":
                return self.send_json(200, {})
            super().do_GET()

    server = app.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    win = webview.create_window("ChannelForge preview lifecycle test", f"http://127.0.0.1:{server.server_port}", width=800,height=500)
    passed = []
    def run():
        def sample():
            n = len(states)
            win.run_js("""fetch('/sample',{method:'POST',body:JSON.stringify({t:$('#tv-player').currentTime,
            paused:$('#tv-player').paused, decoder:!!hlsPlayer, source:$('#tv-player').hasAttribute('src')||!!$('#tv-player').srcObject,
            audioBytes:$('#tv-player').webkitAudioDecodedByteCount||0, id:activeChannel, timer:!!retuneTimer})})""")
            stop = time.monotonic()+5
            while len(states)==n and time.monotonic()<stop:time.sleep(.05)
            assert len(states)>n
            return states[-1]
        try:
            assert win.events.loaded.wait(15)
            time.sleep(1)
            win.run_js("""showView('watch'); state.channels=[{id:'qa',number:'1',name:'Fixture',items:[]}];
            state.now_playing={qa:{title:'Sample',start:new Date().toISOString(),stop:new Date(Date.now()+100000).toISOString()}};
            watchChannel('qa')""")
            stop=time.monotonic()+14
            while time.monotonic()<stop and sample()['audioBytes']==0:time.sleep(.2)
            playing=sample()
            assert playing['audioBytes']>0, playing
            if not args.baseline_js:app.stop_creator_preview(win)
            win.hide()
            time.sleep(.4)
            first=sample()
            time.sleep(1.5)
            last=sample()
            print(json.dumps({'playing':playing,'hidden':first,'later':last}),flush=True)
            if args.baseline_js:assert last['audioBytes']>first['audioBytes'] and last['t']>first['t']
            else:assert last['paused'] and not any(last[k] for k in ('decoder','source','id','timer')) and last['audioBytes']==first['audioBytes']
            passed.append(True)
        finally:
            win.destroy()
    webview.start(run,gui="edgechromium")
    server.shutdown()
    server.server_close()
    assert passed
