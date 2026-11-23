"""Branded per-user installers for ChannelForge Creator and ChannelForge TV."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

BUNDLED = hasattr(sys, "_MEIPASS")
ROOT = Path(sys._MEIPASS) if BUNDLED else Path(__file__).resolve().parents[1]


PRODUCTS = {
    "creator": {
        "app": "ChannelForge_Creator",
        "name": "ChannelForge Creator",
        "edition": "CREATOR  •  PUBLIC EDITION",
        "tagline": "Build and host your own live TV channels",
        "description": "Create personal TV channels from local folders, Plex, or both.",
        "finish": "Leave the application open while viewers use your channels.",
        "folder": "ChannelForge Creator",
        "image": "channel-forge-creator-installer.png",
        "icon": "channel-forge-creator.ico",
        "autostart": True,
    },
    "tv": {
        "app": "ChannelForge_TV",
        "name": "ChannelForge TV",
        "edition": "TV  •  COMMUNITY VIEWER",
        "tagline": "Watch live channels and Rewind Cinema",
        "description": "Install the native ChannelForge TV viewer on this computer.",
        "finish": "Open ChannelForge TV to browse live channels and Rewind Cinema.",
        "folder": "ChannelForge TV",
        "image": "channel-forge-tv.png",
        "icon": "channel-forge-tv.ico",
        "autostart": False,
    },
}


def product_key(executable: str | None = None) -> str:
    requested = os.environ.get("CHANNELFORGE_INSTALLER_PRODUCT", "").strip().lower()
    if requested in PRODUCTS:
        return requested
    name = Path(executable or sys.executable).stem.lower()
    return "tv" if "channel-forge_tv" in name or "channelforge_tv" in name else "creator"


PRODUCT = PRODUCTS[product_key()]
APP = PRODUCT["app"]
PAYLOAD_ROOT = ROOT / "payload" if BUNDLED else ROOT / "installer" / "payload"
PAYLOAD = PAYLOAD_ROOT / f"{APP}.exe"
BRAND_IMAGE = ROOT / "assets" / PRODUCT["image"]
APP_ICON = ROOT / "assets" / PRODUCT["icon"]


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{PRODUCT['name']} Setup")
        self.geometry("700x530")
        self.resizable(False, False)
        self.configure(background="#08111f")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#101827")
        style.configure("TLabel", background="#101827", foreground="#eaf2ff", font=("Segoe UI", 10))
        style.configure("TCheckbutton", background="#101827", foreground="#eaf2ff", font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", "#101827")], foreground=[("active", "#67e8f9")])
        style.configure("TButton", background="#173456", foreground="#ffffff", padding=(14, 8), font=("Segoe UI", 10, "bold"))
        style.map("TButton", background=[("active", "#1f5d88"), ("disabled", "#263342")])
        brand_source = tk.PhotoImage(file=str(BRAND_IMAGE))
        scale = max(1, round(max(brand_source.width(), brand_source.height()) / 96))
        self.brand_image = brand_source.subsample(scale, scale) if scale > 1 else brand_source
        self.brand_source = brand_source
        self.iconphoto(True, self.brand_image)
        banner = tk.Frame(self, background="#08111f", height=150)
        banner.pack(fill="x")
        banner.pack_propagate(False)
        tk.Label(banner, image=self.brand_image, background="#08111f").pack(side="left", padx=(28, 18), pady=14)
        brand = tk.Frame(banner, background="#08111f")
        brand.pack(side="left", fill="y", pady=18)
        tk.Label(brand, text="CHANNELFORGE", background="#08111f", foreground="#54e7ff", font=("Segoe UI", 21, "bold")).pack(anchor="w")
        tk.Label(brand, text=PRODUCT["edition"], background="#08111f", foreground="#b7c7dc", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(2, 0))
        tk.Label(brand, text=PRODUCT["tagline"], background="#08111f", foreground="#ffffff", font=("Segoe UI", 10)).pack(anchor="w", pady=(10, 0))
        default = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / PRODUCT["folder"]
        self.destination = tk.StringVar(value=str(default))
        self.shortcut = tk.BooleanVar(value=True)
        self.autostart = tk.BooleanVar(value=bool(PRODUCT["autostart"]))
        self.page = 0
        self.body = ttk.Frame(self, padding=(32, 22))
        self.body.pack(fill="both", expand=True)
        self.footer = ttk.Frame(self, padding=(32, 8, 32, 22))
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
            ttk.Label(self.body, text=f"Welcome to {PRODUCT['name']}", font=("Segoe UI", 20, "bold"), foreground="#67e8f9").pack(anchor="w", pady=(8, 12))
            ttk.Label(self.body, text=f"{PRODUCT['description']}\n\nThis public build contains no private APIs, artwork, presets, or user data.", wraplength=530).pack(anchor="w")
        elif self.page == 1:
            ttk.Label(self.body, text="Choose an install folder", font=("Segoe UI", 16, "bold"), foreground="#67e8f9").pack(anchor="w", pady=(8, 18))
            row = ttk.Frame(self.body); row.pack(fill="x")
            ttk.Entry(row, textvariable=self.destination).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="Browse…", command=self.browse).pack(side="left", padx=(8, 0))
            ttk.Checkbutton(self.body, text="Create a Desktop shortcut", variable=self.shortcut).pack(anchor="w", pady=(24, 5))
            if PRODUCT["autostart"]:
                ttk.Checkbutton(self.body, text="Start automatically when I sign in", variable=self.autostart).pack(anchor="w")
        else:
            ttk.Label(self.body, text="Ready to install", font=("Segoe UI", 16, "bold"), foreground="#67e8f9").pack(anchor="w", pady=(8, 18))
            ttk.Label(self.body, text=f"Install to:\n{self.destination.get()}\n\n{PRODUCT['finish']}", wraplength=530).pack(anchor="w")
            self.next.configure(text="Install")

    def browse(self):
        chosen = filedialog.askdirectory(initialdir=self.destination.get())
        if chosen:
            self.destination.set(chosen)

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

    def shortcut_file(self, location: Path, target: Path, icon: Path):
        location.parent.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update({
            "CHANNELFORGE_SHORTCUT": str(location),
            "CHANNELFORGE_TARGET": str(target),
            "CHANNELFORGE_WORKDIR": str(target.parent),
            "CHANNELFORGE_ICON": f"{icon},0",
        })
        script = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:CHANNELFORGE_SHORTCUT);"
            "$s.TargetPath=$env:CHANNELFORGE_TARGET;"
            "$s.WorkingDirectory=$env:CHANNELFORGE_WORKDIR;"
            "$s.IconLocation=$env:CHANNELFORGE_ICON;"
            "$s.Save()"
        )
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, env=environment, creationflags=0x08000000)

    def install(self):
        try:
            destination = Path(self.destination.get()).expanduser().resolve()
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / f"{APP}.exe"
            installed_icon = destination / PRODUCT["icon"]
            shutil.copy2(PAYLOAD, target)
            shutil.copy2(APP_ICON, installed_icon)
            start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{PRODUCT['name']}.lnk"
            self.shortcut_file(start_menu, target, installed_icon)
            desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop" / f"{PRODUCT['name']}.lnk"
            if self.shortcut.get():
                self.shortcut_file(desktop, target, installed_icon)
            if self.autostart.get():
                startup = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{PRODUCT['name']}.lnk"
                self.shortcut_file(startup, target, installed_icon)
            messagebox.showinfo("Installation complete", f"{PRODUCT['name']} is installed. {PRODUCT['finish']}")
            subprocess.Popen([str(target)], cwd=str(destination))
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Installation failed", str(exc))


if __name__ == "__main__":
    Installer().mainloop()

