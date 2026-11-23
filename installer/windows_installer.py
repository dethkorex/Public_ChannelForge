"""Branded per-user installers for ChannelForge Creator and ChannelForge TV."""

from __future__ import annotations

import os
import json
import errno
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
import time
from contextlib import contextmanager
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from windows_distribution import sha256_file, windows_data

try:
    import winreg
except ImportError:  # pragma: no cover - Windows installer runtime only
    winreg = None

BUNDLED = hasattr(sys, "_MEIPASS")
ROOT = Path(sys._MEIPASS) if BUNDLED else Path(__file__).resolve().parents[1]


PRODUCTS = {
    "suite": {
        "app": "ChannelForge",
        "name": "ChannelForge",
        "edition": "CREATOR + TV  •  PUBLIC EDITION",
        "tagline": "Create channels and watch the community dial",
        "description": "Install ChannelForge Creator and ChannelForge TV together.",
        "finish": "Both applications are ready from your Desktop and Start Menu.",
        "folder": "ChannelForge",
        "image": "channel-forge-creator-installer.png",
        "icon": "channel-forge-creator.ico",
        "autostart": True,
    },
    "creator": {
        "app": "ChannelForge_Creator",
        "name": "ChannelForge Creator",
        "edition": "CREATOR  •  PUBLIC EDITION",
        "tagline": "Build and host your own live TV channels",
        "description": "Create personal TV channels from local folders, Plex, or both.",
        "finish": "Leave the application open while viewers use your channels.",
        "folder": "ChannelForge",
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
        "folder": "ChannelForge",
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
    if name in {"channelforge_setup", "channelforge"}:
        return "suite"
    return "tv" if "channel-forge_tv" in name or "channelforge_tv" in name else "creator"


PRODUCT = PRODUCTS["suite"]
PAYLOAD_ROOT = ROOT / "payload" if BUNDLED else ROOT / "installer" / "payload"
BRAND_IMAGE = ROOT / "assets" / PRODUCT["image"]
INSTALL_PRODUCT_KEYS = ("creator", "tv")
LEGACY_PUBLIC_NAMES = ("Public_ChannelForge.exe", "Public ChannelForge.exe")
LEGACY_SHORTCUTS = ("ChannelForge_Creator.lnk", "ChannelForge_TV.lnk", "Public ChannelForge.lnk", "Public_ChannelForge.lnk")


def payload_for(key: str) -> Path:
    return PAYLOAD_ROOT / f"{PRODUCTS[key]['app']}.exe"


def icon_for(key: str) -> Path:
    return ROOT / "assets" / PRODUCTS[key]["icon"]


def target_product(target: Path) -> str:
    names = {f"{PRODUCTS[key]['app']}.exe".lower(): key for key in INSTALL_PRODUCT_KEYS}
    names.update((name.lower(), "creator") for name in LEGACY_PUBLIC_NAMES)
    key = names.get(target.name.lower())
    if not key:
        raise ValueError("The installed ChannelForge application could not be verified.")
    return key


def silent_update_target(arguments: list[str] | None = None) -> tuple[Path, int, str] | None:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if "--silent-update" not in arguments:
        return None
    try:
        target_index = arguments.index("--target-exe") + 1
        pid_index = arguments.index("--wait-pid") + 1
        target = Path(arguments[target_index]).expanduser().resolve()
        wait_pid = int(arguments[pid_index])
    except (ValueError, IndexError) as exc:
        raise ValueError("The in-place update request was incomplete.") from exc
    product = target_product(target)
    if not target.is_file() and not app_target(registered_installation() or target.parent, product).is_file():
        raise ValueError("The installed ChannelForge application could not be verified.")
    return target, wait_pid, product


def stop_locked_channel_forge_apps(targets: list[Path]) -> None:
    """Stop only ChannelForge processes running from the exact install paths."""
    if sys.platform != "win32":
        return
    environment = os.environ.copy()
    environment["CHANNELFORGE_UPDATE_TARGETS"] = json.dumps([str(path.resolve()).lower() for path in targets])
    script = (
        "$targets=ConvertFrom-Json $env:CHANNELFORGE_UPDATE_TARGETS;"
        "Get-CimInstance Win32_Process | Where-Object {"
        "$_.ExecutablePath -and ($targets -contains $_.ExecutablePath.ToLower())"
        "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False, env=environment, creationflags=0x08000000, timeout=20,
    )


def app_target(folder: Path, key: str) -> Path:
    return folder / f"{PRODUCTS[key]['app']}.exe"


def has_app(folder: Path, key: str) -> bool:
    if app_target(folder, key).is_file():
        return True
    return key == "creator" and any((folder / name).is_file() for name in LEGACY_PUBLIC_NAMES)


def desktop_folder() -> Path:
    if winreg is not None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                return Path(os.path.expandvars(winreg.QueryValueEx(key, "Desktop")[0]))
        except OSError:
            pass
    return Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"


def start_menu() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def default_installation() -> Path:
    return windows_data() / "Programs" / "ChannelForge"


def registered_installation() -> Path | None:
    try:
        folder = Path(json.loads((windows_data() / "ChannelForge" / "installation.json").read_text(encoding="utf-8"))["folder"])
        if folder.is_absolute() and any(app_target(folder, key).is_file() for key in INSTALL_PRODUCT_KEYS):
            return folder.resolve()
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def discovered_installation() -> Path | None:
    registered = registered_installation()
    if registered:
        return registered
    if any(app_target(default_installation(), key).is_file() for key in INSTALL_PRODUCT_KEYS):
        return default_installation().resolve()
    if sys.platform != "win32":
        return None
    shortcuts = [start_menu() / f"{PRODUCTS[key]['name']}.lnk" for key in INSTALL_PRODUCT_KEYS]
    if not any(path.is_file() for path in shortcuts):
        return None
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;$w=New-Object -ComObject WScript.Shell;"
        "ConvertFrom-Json $env:CHANNELFORGE_SHORTCUTS | ForEach-Object { "
        "if(Test-Path -LiteralPath $_) { $w.CreateShortcut($_).TargetPath } }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
            env={**os.environ, "CHANNELFORGE_SHORTCUTS": json.dumps([str(path) for path in shortcuts])},
            text=True, encoding="utf-8", timeout=10, creationflags=0x08000000,
        )
        for line in result.stdout.splitlines():
            target = Path(line.strip())
            if not target.is_absolute() or not target.is_file() or target.parent.resolve() == desktop_folder().resolve():
                continue
            try:
                target_product(target)
            except ValueError:
                continue
            return target.parent.resolve()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


@contextmanager
def installation_lock():
    if sys.platform != "win32":
        yield
        return
    import msvcrt

    lock = windows_data() / "ChannelForge" / "installation.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as handle:
        deadline = time.monotonic() + 120
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def install_binaries(folder: Path, keys: tuple[str, ...]) -> bool:
    folder.mkdir(parents=True, exist_ok=True)
    replacements = []
    try:
        for key in keys:
            installed = app_target(folder, key)
            if installed.is_file() and sha256_file(installed) == sha256_file(payload_for(key)):
                continue
            with tempfile.NamedTemporaryFile(dir=folder, prefix=f".{installed.name}.", suffix=".updating", delete=False) as file:
                staged = Path(file.name)
            replacements.append((installed, staged))
            shutil.copy2(payload_for(key), staged)
        replace_binaries(replacements)
        for key in keys:
            shutil.copy2(icon_for(key), folder / PRODUCTS[key]["icon"])
    finally:
        for _installed, staged in replacements:
            staged.unlink(missing_ok=True)
    return bool(replacements)


def replace_binaries(replacements: list[tuple[Path, Path]]) -> None:
    deadline = time.monotonic() + 120
    force_close_at = time.monotonic() + 8
    force_close_attempted = False
    last_error: Exception | None = None
    pending = list(replacements)
    while pending and time.monotonic() < deadline:
        still_pending = []
        for installed, replacement in pending:
            try:
                os.replace(replacement, installed)
            except PermissionError as exc:
                last_error = exc
                still_pending.append((installed, replacement))
        pending = still_pending
        if pending:
            if not force_close_attempted and time.monotonic() >= force_close_at:
                force_close_attempted = True
                stop_locked_channel_forge_apps([installed for installed, _replacement in pending])
            time.sleep(0.5)
    if pending:
        raise ValueError(f"ChannelForge did not close in time to finish the update: {last_error}")


def create_shortcut(location: Path, target: Path, icon: Path) -> None:
    location.parent.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "CHANNELFORGE_SHORTCUT": str(location), "CHANNELFORGE_TARGET": str(target),
                   "CHANNELFORGE_WORKDIR": str(target.parent), "CHANNELFORGE_ICON": f"{icon},0"}
    script = (
        "$ErrorActionPreference='Stop';"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:CHANNELFORGE_SHORTCUT);"
        "$s.TargetPath=$env:CHANNELFORGE_TARGET;$s.WorkingDirectory=$env:CHANNELFORGE_WORKDIR;"
        "$s.IconLocation=$env:CHANNELFORGE_ICON;$s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, env=environment, creationflags=0x08000000)


def clean_legacy_shortcuts() -> None:
    for root in (desktop_folder(), start_menu(), start_menu() / "Startup"):
        for name in LEGACY_SHORTCUTS:
            (root / name).unlink(missing_ok=True)


def clean_legacy_desktop_files(folder: Path, obsolete_folders: tuple[Path, ...] = (), include_desktop: bool = True) -> None:
    desktop = desktop_folder().resolve()
    names = ("ChannelForge_Creator.exe", "ChannelForge_TV.exe", *LEGACY_PUBLIC_NAMES)
    roots = {directory.resolve() for directory in obsolete_folders}
    if include_desktop:
        roots.add(desktop)
    old_apps = [root / name for root in roots if root != folder
                for name in names if (root / name).is_file() and not (root / name).is_symlink()]
    old_apps.extend(folder / name for name in LEGACY_PUBLIC_NAMES if (folder / name).is_file() and not (folder / name).is_symlink())
    if old_apps:
        stop_locked_channel_forge_apps(old_apps)
        for path in old_apps:
            for attempt in range(10):
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.3)
    for root in roots | {folder}:
        for name in names:
            (root / (name + ".updating")).unlink(missing_ok=True)
    if desktop != folder and any(path.parent == desktop for path in old_apps):
        for name in ("channel-forge-creator.ico", "channel-forge-tv.ico"):
            (desktop / name).unlink(missing_ok=True)


def configure_autostart(target: Path, enabled: bool | None) -> None:
    startup_shortcut = start_menu() / "Startup" / "ChannelForge Creator.lnk"
    previous_shortcut = startup_shortcut.is_file()
    if winreg is not None:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enabled is None:
                try:
                    winreg.QueryValueEx(key, "ChannelForge Creator")
                    enabled = True
                except FileNotFoundError:
                    enabled = previous_shortcut
            if enabled:
                winreg.SetValueEx(key, "ChannelForge Creator", 0, winreg.REG_SZ, f'"{target}"')
            else:
                try:
                    winreg.DeleteValue(key, "ChannelForge Creator")
                except FileNotFoundError:
                    pass
    startup_shortcut.unlink(missing_ok=True)


def register_installation(folder: Path, keys: tuple[str, ...], desktop_shortcuts: bool, autostart: bool | None) -> None:
    for key in keys:
        target = app_target(folder, key)
        icon = folder / PRODUCTS[key]["icon"]
        shortcut = f"{PRODUCTS[key]['name']}.lnk"
        create_shortcut(start_menu() / shortcut, target, icon)
        if desktop_shortcuts:
            create_shortcut(desktop_folder() / shortcut, target, icon)
        else:
            (desktop_folder() / shortcut).unlink(missing_ok=True)
    if "creator" in keys:
        configure_autostart(app_target(folder, "creator"), autostart)
    clean_legacy_shortcuts()
    marker = windows_data() / "ChannelForge" / "installation.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    staged = marker.with_suffix(".tmp")
    staged.write_text(json.dumps({"folder": str(folder)}), encoding="utf-8")
    staged.replace(marker)


def apply_silent_update(target: Path, wait_pid: int, product: str | None = None) -> None:
    """Update the registered suite, or migrate a legacy Desktop installation once."""
    product = product or target_product(target)
    with installation_lock():
        source = target.parent.resolve()
        desktop = desktop_folder().resolve()
        downloads = (Path(os.environ.get("USERPROFILE", Path.home())) / "Downloads").resolve()
        known = discovered_installation()
        # A standalone viewer in its own folder can still be replaced in place.
        # Desktop/Downloads copies belong in the installed suite folder.
        loose = source in (desktop, downloads)
        managed = bool(known or loose)
        folder = known or (default_installation().resolve() if loose else source)
        keys = tuple(key for key in INSTALL_PRODUCT_KEYS if (
            key == product or any(has_app(root, key) for root in (folder, source, desktop))
        ))
        changed = install_binaries(folder, keys)
        if managed:
            desktop_shortcuts = source == desktop or any(
                (desktop / f"{PRODUCTS[key]['name']}.lnk").is_file() for key in keys
            )
            register_installation(folder, keys, desktop_shortcuts, None)
            clean_legacy_desktop_files(folder, (source,))
        elif target != app_target(folder, product):
            clean_legacy_desktop_files(folder, include_desktop=False)
        if changed or target != app_target(folder, product):
            subprocess.Popen([str(app_target(folder, product))], cwd=str(folder))


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
        default = discovered_installation() or default_installation()
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
            ttk.Label(self.body, text="Install both ChannelForge Creator and ChannelForge TV in one folder.\n\nThis public build contains no private APIs, artwork, presets, or user data.", wraplength=530).pack(anchor="w")
        elif self.page == 1:
            ttk.Label(self.body, text="Choose an install folder", font=("Segoe UI", 16, "bold"), foreground="#67e8f9").pack(anchor="w", pady=(8, 18))
            row = ttk.Frame(self.body); row.pack(fill="x")
            ttk.Entry(row, textvariable=self.destination).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="Browse…", command=self.browse).pack(side="left", padx=(8, 0))
            ttk.Checkbutton(self.body, text="Create Desktop shortcuts for both applications", variable=self.shortcut).pack(anchor="w", pady=(24, 5))
            ttk.Checkbutton(self.body, text="Start ChannelForge Creator automatically when I sign in", variable=self.autostart).pack(anchor="w")
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

    def install(self):
        try:
            destination = Path(self.destination.get()).expanduser().resolve()
            with installation_lock():
                previous = discovered_installation()
                install_binaries(destination, INSTALL_PRODUCT_KEYS)
                register_installation(destination, INSTALL_PRODUCT_KEYS, self.shortcut.get(), self.autostart.get())
                clean_legacy_desktop_files(destination, (previous,) if previous else ())
            messagebox.showinfo("Installation complete", "ChannelForge Creator and ChannelForge TV are installed together.")
            subprocess.Popen([str(app_target(destination, "creator"))], cwd=str(destination))
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Installation failed", str(exc))


if __name__ == "__main__":
    try:
        update = silent_update_target()
        if update:
            apply_silent_update(*update)
        else:
            Installer().mainloop()
    except Exception as exc:
        messagebox.showerror("ChannelForge update failed", str(exc))

