"""Optional desktop libVLC renderer. The owner supplies a loopback viewer-proxy URL."""

import multiprocessing
import sys
import threading
import time
from urllib.parse import urlsplit


def run_renderer(pipe, source, title, live, resume_at, prebuffer, fullscreen):
    root = instance = player = media = None
    try:
        import tkinter as tk

        import vlc

        location = urlsplit(source)
        if (
            location.scheme != "http"
            or location.hostname != "127.0.0.1"
            or location.path != "/stream"
        ):
            raise ValueError("Expected the ChannelForge viewer proxy.")
        root = tk.Tk()
        root.title("ChannelForge TV")
        root.configure(bg="#080714")
        # Map the drawing surface before decode. Keep the deck animation visible while buffering.
        root.geometry("960x600+0+0")
        if prebuffer:
            root.attributes("-alpha", 0)
        root.minsize(450, 280)
        surface = tk.Frame(root, bg="black")
        surface.pack(fill="both", expand=True)
        bar = tk.Frame(root, bg="#0e1022", padx=7, pady=5)
        bar.pack(fill="x")
        status = tk.StringVar(value=title)
        tk.Label(bar, textvariable=status, fg="#e4faff", bg="#0e1022", anchor="w").pack(
            side="top", fill="x"
        )

        def button(label, callback):
            tk.Button(
                bar,
                text=label,
                command=callback,
                fg="white",
                bg="#171b34",
                activebackground="#273556",
                activeforeground="white",
            ).pack(side="left", padx=2)

        def leave(action):
            pipe.send({"action": action})
            root.quit()

        def volume(delta):
            player.audio_set_volume(max(0, min(100, player.audio_get_volume() + delta)))

        def seek(delta):
            if not live and player.is_seekable():
                player.set_time(
                    max(0, min(player.get_length(), player.get_time() + delta))
                )

        def toggle_fullscreen():
            root.attributes("-fullscreen", not bool(root.attributes("-fullscreen")))

        if live:
            button("Guide", lambda: leave("back"))
            button("Channel ↑", lambda: leave("previous"))
            button("Channel ↓", lambda: leave("next"))
            root.bind("<Up>", lambda _e: leave("previous"))
            root.bind("<Down>", lambda _e: leave("next"))
        else:
            button("⏏ Eject movie", lambda: leave("back"))
            button("Play / Pause", lambda: player.pause())
            button("−10s", lambda: seek(-10000))
            button("+10s", lambda: seek(10000))
            root.bind("<space>", lambda _e: player.pause())
            root.bind("<Left>", lambda _e: seek(-10000))
            root.bind("<Right>", lambda _e: seek(10000))
        button("Volume −", lambda: volume(-10))
        button("Volume +", lambda: volume(10))
        button("Fullscreen", toggle_fullscreen)
        root.bind("<F11>", lambda _e: toggle_fullscreen())
        for key in ("<Escape>", "b", "B"):
            root.bind(key, lambda _e: leave("back"))
        root.protocol("WM_DELETE_WINDOW", lambda: leave("back"))
        root.update()
        instance = vlc.Instance("--quiet", "--no-video-title-show", "--avcodec-hw=any")
        if instance is None:
            raise RuntimeError("VLC runtime unavailable.")
        media = instance.media_new(source)
        player = instance.media_player_new()
        player.set_media(media)
        (player.set_hwnd if sys.platform == "win32" else player.set_xwindow)(
            surface.winfo_id()
        )
        player.video_set_key_input(False)
        player.video_set_mouse_input(False)
        prepared = False
        revealed = not prebuffer
        if fullscreen and revealed:
            root.attributes("-fullscreen", True)
        if prebuffer:
            player.audio_set_mute(True)
        if player.play() == -1:
            raise RuntimeError("VLC could not open the stream.")
        last_emit = 0
        position_to_restore = resume_at
        startup_deadline = time.monotonic() + 20
        snapshot = {"ready": False, "state": "Opening", "time": 0}

        def tick():
            nonlocal prepared, revealed, last_emit, position_to_restore, snapshot
            try:
                while pipe.poll():
                    command = pipe.recv()
                    if command == "stop":
                        root.quit()
                        return
                    if command == "poll":
                        pipe.send(snapshot)
                        continue
                    if command == "show" and not revealed:
                        revealed = True
                        root.attributes("-alpha", 1)
                        root.attributes("-fullscreen", fullscreen)
                        root.lift()
                        root.focus_force()
                        player.audio_set_mute(False)
                        player.set_pause(0)
                    elif command == "pause" and not live:
                        player.set_pause(1)
                    elif command == "resume" and not live:
                        player.set_pause(0)
                    elif isinstance(command, dict) and "caption" in command:
                        status.set(command["caption"])
                now = time.monotonic()
                if now - last_emit >= 0.25:
                    last_emit = now
                    state = player.get_state()
                    stats = vlc.MediaStats()
                    media.get_stats(stats)
                    pos = max(0, player.get_time()) / 1000
                    if state in (vlc.State.Error, vlc.State.Ended):
                        leave(
                            "ended"
                            if state == vlc.State.Ended and prepared
                            else "error"
                        )
                        return
                    if stats.displayed_pictures > 0:
                        if not prepared:
                            prepared = True
                            if prebuffer and not revealed:
                                player.set_pause(1)
                        if position_to_restore and player.is_seekable():
                            player.set_time(round(position_to_restore * 1000))
                            position_to_restore = 0
                    elif not prepared and now > startup_deadline:
                        leave("error")
                        return
                    snapshot = {
                        "state": str(state).split(".")[-1],
                        "ready": prepared,
                        "time": pos,
                        "frames": stats.displayed_pictures,
                        "dropped": stats.lost_pictures,
                    }
                root.after(65, tick)
            except (EOFError, OSError, tk.TclError):
                root.quit()

        root.after(0, tick)
        root.mainloop()
    except Exception:  # noqa: BLE001 - report startup/decode failures across the process boundary
        try:
            pipe.send({"action": "error"})
        except (EOFError, OSError):
            pass
    finally:
        if player is not None:
            player.stop()
            player.release()
        if media is not None:
            media.release()
        if instance is not None:
            instance.release()
        if root is not None:
            root.destroy()
        pipe.close()


class NativePlayback:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = self.pipe = None
        self.session = 0
        self.latest = {}
        self.closed = False

    def start(self, generation, source, title, live, resume_at, prebuffer, fullscreen):
        with self.lock:
            if generation <= self.session or self.closed:
                return False
            self._stop()
            self.session = generation
            context = multiprocessing.get_context("spawn")
            self.pipe, child_pipe = context.Pipe()
            self.process = context.Process(
                name="ChannelForge VLC",
                daemon=True,
                target=run_renderer,
                args=(
                    child_pipe,
                    source,
                    title,
                    live,
                    resume_at,
                    prebuffer,
                    fullscreen,
                ),
            )
            try:
                self.process.start()
            except (OSError, RuntimeError):
                self.process = None
                self.pipe.close()
                self.pipe = None
                return False
            finally:
                child_pipe.close()
            self.latest = {"ready": False, "state": "Opening", "time": 0}
            return True

    def state(self, generation):
        with self.lock:
            if generation != self.session or self.pipe is None:
                return {"action": "closed"}
            try:
                while self.pipe.poll():
                    self.latest.update(self.pipe.recv())
                if "action" not in self.latest:
                    self.pipe.send("poll")
            except (OSError, EOFError):
                pass
            if not self.process.is_alive() and "action" not in self.latest:
                self.latest["action"] = "error"
            return dict(self.latest)

    def command(self, generation, command):
        with self.lock:
            if generation != self.session or self.pipe is None:
                return
            if command == "stop":
                self._stop()
            else:
                try:
                    self.pipe.send(command)
                except (OSError, EOFError):
                    pass

    def close(self):
        with self.lock:
            self.closed = True
            self._stop()

    def _stop(self):
        if self.pipe is None:
            return
        try:
            self.pipe.send("stop")
        except (OSError, EOFError):
            pass
        self.pipe.close()
        try:
            self.process.join(1)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(1)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(1)
            self.process.close()
        finally:
            self.process = self.pipe = None
