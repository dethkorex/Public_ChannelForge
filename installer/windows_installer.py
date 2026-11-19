"""Small graphical installer, packaged as Public_ChannelForge_Setup.exe."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP = "Public_ChannelForge"
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PAYLOAD = ROOT / "payload" / f"{APP}.exe"


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP} Setup")
        self.geometry("620x410")
        self.resizable(False, False)
        default = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / APP
        self.destination = tk.StringVar(value=str(default))
        self.shortcut = tk.BooleanVar(value=True)
        self.autostart = tk.BooleanVar(value=False)
        self.page = 0
        self.body = ttk.Frame(self, padding=28)
        self.body.pack(fill="both", expand=True)
        self.footer = ttk.Frame(self, padding=(28, 8, 28, 22))
        self.footer.pack(fill="x")
        self.back = ttk.Button(self.footer, text="Back", command=self.previous)
        self.back.pack(side="left")
        self.next = ttk.Button(self.footer, text="Next", command=self.advance)
        self.next.pack(side="right")
        self.render()

    def clear(self):
        for child in self.body.winfo_children():
            child.destroy()

    def render(self):
        self.clear()
        self.back.configure(state="normal" if self.page else "disabled")
        if self.page == 0:
            ttk.Label(self.body, text="Welcome to Public ChannelForge", font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=(18, 12))
            ttk.Label(self.body, text="Create personal Plex channel lineups and XMLTV guides.\n\nThis public build contains no private APIs, artwork, presets, or user data.", wraplength=530).pack(anchor="w")
        elif self.page == 1:
            ttk.Label(self.body, text="Choose an install folder", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(8, 18))
            row = ttk.Frame(self.body); row.pack(fill="x")
            ttk.Entry(row, textvariable=self.destination).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="Browse…", command=self.browse).pack(side="left", padx=(8, 0))
            ttk.Checkbutton(self.body, text="Create a Desktop shortcut", variable=self.shortcut).pack(anchor="w", pady=(24, 5))
            ttk.Checkbutton(self.body, text="Start automatically when I sign in", variable=self.autostart).pack(anchor="w")
        else:
            ttk.Label(self.body, text="Ready to install", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(8, 18))
            ttk.Label(self.body, text=f"Install to:\n{self.destination.get()}\n\nYour Plex details will be requested after the app starts.", wraplength=530).pack(anchor="w")
            self.next.configure(text="Install")

    def browse(self):
        chosen = filedialog.askdirectory(initialdir=self.destination.get())
        if chosen:
            self.destination.set(str(Path(chosen) / APP))

    def previous(self):
        self.page -= 1
        self.next.configure(text="Next")
        self.render()

    def advance(self):
        if self.page < 2:
            self.page += 1
            self.render()
            return
        self.install()

    def shortcut_file(self, location: Path, target: Path):
        location.parent.mkdir(parents=True, exist_ok=True)
        script = (
            "$s=(New-Object -COM WScript.Shell).CreateShortcut($args[0]);"
            "$s.TargetPath=$args[1];$s.WorkingDirectory=$args[2];$s.Save()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script, str(location), str(target), str(target.parent)], check=True, creationflags=0x08000000)

    def install(self):
        try:
            destination = Path(self.destination.get()).expanduser().resolve()
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / f"{APP}.exe"
            shutil.copy2(PAYLOAD, target)
            start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP}.lnk"
            self.shortcut_file(start_menu, target)
            desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop" / f"{APP}.lnk"
            if self.shortcut.get():
                self.shortcut_file(desktop, target)
            if self.autostart.get():
                startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{APP}.lnk"
                self.shortcut_file(startup, target)
            messagebox.showinfo("Installation complete", "Public ChannelForge is installed. The setup page will open in your browser.")
            subprocess.Popen([str(target)], cwd=str(destination))
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Installation failed", str(exc))


if __name__ == "__main__":
    Installer().mainloop()

