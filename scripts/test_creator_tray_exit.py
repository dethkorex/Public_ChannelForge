"""Exercise the real Windows Creator tray menu with a private studio and audit its process tree.

Requires pystray, pywebview, psutil and FFmpeg on PATH. Run on an interactive Windows desktop.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def exercise_creator(home, admin, public):
    os.environ["PUBLIC_CHANNELFORGE_HOME"] = str(home)
    import pystray
    import Public_ChannelForge as app

    app.ensure_files()
    settings = app.config()
    settings.update(auto_publish_channels=False, public_tunnel=False, bind_host="127.0.0.1",
                    local_libraries=[{"id": "123456abcdef", "name": "QA", "path": str(home)}])
    app.atomic_json(app.CONFIG_FILE, settings)
    item = {"id": "ep1", "title": "Tray test", "source": "local", "file_path": str(home / "test.mp4"),
            "duration_ms": 120000}
    channel = {"id": "fixture", "name": "Tray QA", "number": 1, "items": [item],
               "schedule_anchor": app.datetime.now(app.timezone.utc).isoformat()}
    app.atomic_json(app.CHANNELS_FILE, [channel])
    app.ensure_files()
    expected = app.CHANNELS_FILE.read_bytes()
    original_start = pystray.Icon.run_detached

    def start_tray(icon, *args, **kwargs):
        original_start(icon, *args, **kwargs)
        def action():
            window = app.APP_WINDOW
            try:
                assert window.events.loaded.wait(20)
                window.run_js("showView('watch'); watchChannel('fixture')")
                limit = time.monotonic() + 20
                while time.monotonic() < limit:
                    audio = window.evaluate_js("document.querySelector('#tv-player').webkitAudioDecodedByteCount || 0")
                    if audio and app.FFMPEG_SESSIONS:
                        break
                    time.sleep(.2)
                assert audio and app.FFMPEG_SESSIONS, "Expected decoded audio and a running encoder"
                window.run_js("document.querySelector('#system button[title*=diagnostics]').click()")
                limit = time.monotonic() + 8
                while time.monotonic() < limit:
                    value = window.evaluate_js("document.querySelector('[aria-label=\"Troubleshooting report\"]').value")
                    if value:
                        break
                    time.sleep(.1)
                diagnostics = json.loads(value)
                assert diagnostics["playback"]["engine"] == "hls.js" and diagnostics["creator"]["broadcast"]["encoders"]
                assert str(home) not in value
                window.run_js("document.querySelector('dialog[aria-label=\"Playback diagnostics\"]').close()")
                # Dispatch the ordinary window close, leaving the real Server and tray alive.
                window.destroy()
                limit = time.monotonic() + 5
                while time.monotonic() < limit and window.native.Visible:
                    time.sleep(.1)
                assert not window.native.Visible
                assert window.evaluate_js("document.querySelector('#tv-player').paused")
                processes = psutil.Process().children(recursive=True)
                names = [p.name() for p in processes]
                assert any("webview2" in n.lower() for n in names), names
                assert any("ffmpeg" in n.lower() for n in names), names
                meta = {"audio_bytes": audio, "processes": [[p.pid, p.create_time(), p.name()] for p in processes],
                        "activation_port": json.loads((home/"desktop-activation.json").read_text())["port"]}
                (home/"observed.json").write_text(json.dumps(meta))
            finally:
                next(entry for entry in icon.menu.items if entry.text == "Exit and stop channels")(icon)
        threading.Thread(target=action, daemon=True).start()
    pystray.Icon.run_detached = start_tray
    sys.argv = [sys.argv[0], "--port", str(admin), "--public-media-port", str(public)]
    app.main()
    assert app.CHANNELS_FILE.read_bytes() == expected


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def check():
    assert sys.platform == "win32"
    with tempfile.TemporaryDirectory() as directory:
        home = Path(directory)
        ffmpeg = shutil.which("ffmpeg")
        assert ffmpeg, "Add ffmpeg and ffprobe to PATH"
        subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "color=size=160x96:rate=12",
                        "-f", "lavfi", "-i", "sine=frequency=330", "-t", "120", "-c:v", "libx264",
                        "-threads:v", "1", "-g", "12", "-c:a", "aac", str(home/"test.mp4")], check=True)
        admin, public = free_port(), free_port()
        subprocess.run([sys.executable, __file__, directory, str(admin), str(public)], timeout=70, check=True)
        data = json.loads((home/"observed.json").read_text())
        survivors = []
        for pid, created, name in data["processes"]:
            try:
                proc = psutil.Process(pid)
                if proc.create_time() == created:
                    proc.wait(timeout=8)
            except psutil.NoSuchProcess:
                continue
            except psutil.TimeoutExpired:
                survivors.append(name)
        assert not survivors, survivors
        assert not (home/"desktop-activation.json").exists()
        for port in (admin, public, data["activation_port"]):
            with socket.socket() as sock:
                sock.settimeout(1)
                assert sock.connect_ex(("127.0.0.1", port)) != 0, port
        print(json.dumps({"decoded_audio_bytes": data["audio_bytes"], "closed_children": [
            p[2] for p in data["processes"]], "closed_listeners": 3, "schedule_preserved": True}))


if __name__ == "__main__":
    if len(sys.argv) == 4:
        exercise_creator(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]))
    else:
        check()
