"""Exercise the desktop viewer, its VLC bridge and real HLS decoding in a native GUI."""

import argparse
import json
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ChannelForge_TV as tv


def wait_for(read, timeout=22):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if value:
            return value
        time.sleep(0.12)
    raise AssertionError(f"Timed out waiting for {read.__name__}")


def main():
    import webview

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "fixture", type=Path, help="Folder containing test.m3u8 and its TS segments"
    )
    parser.add_argument(
        "--measure",
        choices=("web", "vlc"),
        help="Measure the entire desktop process tree",
    )
    args = parser.parse_args()
    samples, passed, errors, requests = [], [], [], []

    class Fixture(tv.Handler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/stream":
                source = parse_qs(parsed.query)["url"][0]
                assert urlsplit(source).hostname == "fixture.example"
                path = Path(urlsplit(source).path)
                requests.append(str(path))
                if "invalid" in source:
                    return self.send(503, b"", "text/plain")
                if path.suffix == ".m3u8":
                    data = tv.rewrite_stream_manifest(
                        (args.fixture / "test.m3u8").read_bytes(), source
                    )
                    return self.send(200, data, "application/vnd.apple.mpegurl")
                return self.send(
                    200, (args.fixture / path.name).read_bytes(), "video/mp2t"
                )
            if parsed.path in (
                "/proxy",
                "/api/update",
                "/api/local-creator",
                "/guide-cache",
            ):
                return self.send(200, b'{"creators":[]}', "application/json")
            if parsed.path == "/probe":
                samples.append(json.loads(parse_qs(parsed.query)["value"][0]))
                return self.send(200, b"{}", "application/json")
            super().do_GET()

    with tempfile.TemporaryDirectory() as workspace:
        tv.GUIDE_CACHE = Path(workspace) / "guide.db"
        server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        controls = tv.DesktopControls(server.server_port)
        controls._preference_file = Path(workspace) / "viewer-player.json"
        assert controls.playback_settings() == {"engine": "web", "available": True}
        for bad in (
            "file:///etc/passwd",
            "https://fixture.example/plugins/evil.m3u8",
            "http://192.0.2.1/live/a.m3u8",
        ):
            assert not controls.start_native_playback(1, bad, "", True, 0, False, False)
        window = webview.create_window(
            "ChannelForge desktop playback QA",
            f"http://127.0.0.1:{server.server_port}/",
            width=800,
            height=600,
            js_api=controls,
        )
        tv.APP_WINDOW = window
        run_js = getattr(window, "run_js", window.evaluate_js)

        def sample(expression):
            count = len(samples)
            run_js(
                "fetch('/probe?value='+encodeURIComponent(JSON.stringify("
                + expression
                + ")))"
            )
            wait_for(lambda: len(samples) > count, 4)
            return samples[-1]

        def run():
            try:
                assert window.events.loaded.wait(15)
                wait_for(
                    lambda: sample(
                        "Boolean(document.querySelector('#desktop-playback-engine'))"
                    )
                )
                if args.measure:
                    import psutil

                    engine = args.measure
                    run_js(
                        f"var pref=document.getElementById('desktop-playback-engine');pref.value='{engine}';"
                        "pref.dispatchEvent(new Event('change'));"
                    )
                    wait_for(
                        lambda: sample(
                            "ChannelForgeNative.preferred() === "
                            + str(engine == "vlc").lower()
                        )
                    )
                    proc = psutil.Process()

                    def resources():
                        cpu = rss = 0
                        for child in [proc, *proc.children(recursive=True)]:
                            try:
                                times = child.cpu_times()
                                cpu += times.user + times.system
                                rss += child.memory_info().rss
                            except psutil.NoSuchProcess:
                                pass
                        return cpu, rss

                    initial, peak = resources()
                    before = time.monotonic()
                    run_js(
                        "play('https://fixture.example/vault/fixture.m3u8',{kind:'movie',title:'QA'})"
                    )
                    first = None
                    while time.monotonic() - before < 30:
                        if engine == "vlc":
                            stats = controls._native.state(controls._native.session)
                            pos, frames, dropped = (
                                stats.get(k, 0) for k in ("time", "frames", "dropped")
                            )
                        else:
                            pos, frames, dropped = sample(
                                "[$('#player').currentTime,"
                                "$('#player').getVideoPlaybackQuality().totalVideoFrames,"
                                "$('#player').getVideoPlaybackQuality().droppedVideoFrames]"
                            )
                        cpu, rss = resources()
                        peak = max(peak, rss)
                        if frames > 0 and first is None:
                            first = time.monotonic() - before
                        if pos >= 7:
                            print(
                                json.dumps(
                                    {
                                        "platform": sys.platform,
                                        "engine": engine,
                                        "first_frame_s": round(first, 3),
                                        "peak_tree_mib": round(peak / 1048576, 1),
                                        "cpu_s": round(cpu - initial, 3),
                                        "media_s": round(pos, 2),
                                        "frames": frames,
                                        "dropped": dropped,
                                    }
                                ),
                                flush=True,
                            )
                            run_js("stopPlayer()")
                            wait_for(lambda: controls._native.process is None)
                            passed.append(True)
                            return
                        time.sleep(0.07)
                    raise AssertionError(f"{engine} did not advance")
                run_js(
                    "var pref=document.getElementById('desktop-playback-engine');"
                    "pref.value='vlc';pref.dispatchEvent(new Event('change'));"
                )
                wait_for(lambda: controls.playback_settings()["engine"] == "vlc")
                wait_for(lambda: sample("ChannelForgeNative.preferred()"))
                movie = "https://fixture.example/vault/fixture.m3u8"
                run_js(
                    f"showScreen('cinema');playCinemaMovie({{title:'QA movie',stream_url:'{movie}'}});"
                )

                def frames():
                    state = controls._native.state(controls._native.session)
                    return (
                        state
                        if state.get("time", 0) > 1.3 and state.get("frames", 0) > 14
                        else None
                    )

                movie_result = wait_for(frames)
                wait_for(
                    lambda: sample("!cinemaTapeStage && !activeMedia.transportPending")
                )
                generation = controls._native.session
                controls.native_playback_command(generation, "pause")
                wait_for(
                    lambda: controls._native.state(generation).get("state") == "Paused"
                )
                controls.native_playback_command(generation, "resume")
                wait_for(
                    lambda: (
                        controls._native.state(generation).get("time", 0)
                        > movie_result["time"] + 0.4
                    )
                )
                run_js("closeVideoView()")
                wait_for(lambda: sample("!activeMedia && !cinemaTapeStage"))
                wait_for(lambda: controls._native.process is None)
                live = "https://fixture.example/live/channel.m3u8"
                broadcast = "https://fixture.example/broadcast/channel/master.m3u8"
                run_js(
                    f"var qaChannel={{name:'QA channel',stream_url:'{live}',continuous_url:'{broadcast}',"
                    "programs:[],online:true};liveChannels=[qaChannel];tuneGuideChannel(qaChannel)"
                )
                live_result = wait_for(frames)
                assert controls._native.session != generation
                assert any(
                    str(Path("/broadcast/channel/master.m3u8")) == p for p in requests
                )
                assert sample("ChannelForgeNative.diagnostics().engine") == "libVLC"
                run_js("stopPlayer()")
                wait_for(lambda: controls._native.process is None)
                run_js(
                    "play('https://fixture.example/vault/invalid.m3u8',{kind:'movie',title:'Offline'})"
                )
                wait_for(lambda: sample("Boolean(activeMedia?.webOnly)"), 30)
                wait_for(lambda: controls._native.process is None)
                print(
                    json.dumps(
                        {
                            "platform": sys.platform,
                            "movie": movie_result,
                            "live": live_result,
                            "pause_resume": True,
                            "eject": True,
                            "fallback": True,
                        }
                    ),
                    flush=True,
                )
                passed.append(True)
            except Exception as exc:  # noqa: BLE001 - forward GUI callback failures to the test process
                import traceback

                traceback.print_exc()
                errors.append(exc)
            finally:
                controls._native.close()
                window.destroy()

        try:
            webview.start(run, gui="edgechromium" if sys.platform == "win32" else "gtk")
        finally:
            server.shutdown()
            server.server_close()
        if errors:
            raise errors[0]
        assert passed


if __name__ == "__main__":
    main()
