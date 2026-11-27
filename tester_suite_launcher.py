"""Launch and update either app in the private desktop Tester suite."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from tester_diagnostics import automatic_reports, install_crash_reporting
from tester_updates import INSTALL_MARKER, ROOT, WINDOWS, install_root, installed_runtime, launch_component
from tester_updates import register_process, restart_suite, settings

def install_windows(folder: Path) -> Path:
    folder = folder.expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / "ChannelForge Tester.exe"
    executable = Path(sys.executable).resolve()
    marker = folder / INSTALL_MARKER
    if destination.exists() and destination.resolve() != executable and not marker.is_file():
        raise ValueError("This folder already contains a different ChannelForge Tester installation.")
    if destination.resolve() != executable:
        with tempfile.NamedTemporaryFile(prefix=".tester-install-", suffix=".exe", dir=folder, delete=False) as output:
            staged = Path(output.name)
        try:
            shutil.copyfile(executable, staged)
            staged.replace(destination)
        finally:
            staged.unlink(missing_ok=True)
    marker.write_text(json.dumps({"product": "ChannelForge Tester", "launcher": destination.name}), encoding="utf-8")
    write_windows_shortcuts(folder)
    return destination


def write_windows_shortcuts(folder: Path) -> None:
    icons = folder / "icons"
    icons.mkdir(exist_ok=True)
    for name in ("channel-forge-creator.ico", "channel-forge-tv.ico"):
        shutil.copyfile(ROOT / "assets" / name, icons / name)
    shell = shutil.which("powershell.exe")
    if shell:
        shortcut_code = """
$folder = $env:CHANNELFORGE_SHORTCUT_FOLDER
$application = Join-Path $folder 'ChannelForge Tester.exe'
$shell = New-Object -ComObject WScript.Shell
foreach ($name in @('ChannelForge Studio Tester', 'ChannelForge TV Tester')) {
    $shortcut = $shell.CreateShortcut((Join-Path $folder ($name + '.lnk')))
    $shortcut.TargetPath = $application
    $studio = $name -match 'Studio'
    $shortcut.Arguments = if ($studio) { '--open studio' } else { '--open tv' }
    $shortcut.WorkingDirectory = $folder
    $icon = if ($studio) { 'channel-forge-creator.ico' } else { 'channel-forge-tv.ico' }
    $shortcut.IconLocation = (Join-Path (Join-Path $folder 'icons') $icon) + ',0'
    $shortcut.Save()
}
"""
        subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", shortcut_code],
                       env={**os.environ, "CHANNELFORGE_SHORTCUT_FOLDER": str(folder)},
                       creationflags=subprocess.CREATE_NO_WINDOW, check=True, timeout=10)


def choose_windows(config):
    import tkinter as tk

    window = tk.Tk()
    window.title("ChannelForge Tester")
    window.configure(bg="#0b1125")
    window.resizable(False, False)
    panel = tk.Frame(window, bg="#0b1125", padx=27, pady=22)
    panel.pack()
    tk.Label(panel, text="ChannelForge Tester", fg="#fff2a8", bg="#0b1125", font=("Segoe UI", 19, "bold")).pack(anchor="w")
    status = tk.Label(panel, text="Private Testers · Choose an app", fg="#b8dbf0", bg="#0b1125", pady=12)
    status.pack(anchor="w")
    def open_app(component):
        launch_component(component, installed_runtime(config))
        window.destroy()
    buttons = tk.Frame(panel, bg="#0b1125")
    buttons.pack(anchor="w")
    tv = tk.Button(buttons, text="Open TV Tester", command=lambda: open_app("tv"))
    studio = tk.Button(buttons, text="Open Studio Tester", command=lambda: open_app("studio"))
    tv.pack(side="left", padx=(0, 8))
    studio.pack(side="left")
    diagnostics = tk.BooleanVar(value=automatic_reports())
    tk.Checkbutton(panel, text="Send app crash reports automatically", variable=diagnostics,
                   command=lambda: automatic_reports(diagnostics.get()), fg="#e2f1fa", bg="#0b1125",
                   selectcolor="#192647", activebackground="#143045", activeforeground="#ffffff").pack(anchor="w", pady=(12, 0))
    if not (install_root() / INSTALL_MARKER).is_file():
        from tkinter import filedialog, messagebox
        def choose_folder():
            folder = filedialog.askdirectory(parent=window, title="Choose the ChannelForge Tester install folder")
            if not folder:
                return
            try:
                destination = install_windows(Path(folder))
                subprocess.Popen([str(destination)], cwd=destination.parent, env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})
                window.destroy()
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                messagebox.showerror("ChannelForge Tester", str(error), parent=window)
        tk.Button(panel, text="Install to folder", command=choose_folder).pack(anchor="w", pady=(9, 0))
    window.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", choices=("studio", "tv"))
    parser.add_argument("--component", choices=("studio", "tv"))
    parser.add_argument("--restart-suite", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--install-to", type=Path)
    args, forwarded = parser.parse_known_args()
    if args.restart_suite is not None:
        if WINDOWS and (install_root() / INSTALL_MARKER).is_file():
            try:
                write_windows_shortcuts(install_root())
            except (OSError, subprocess.SubprocessError):
                pass
        return restart_suite(args.restart_suite)
    if args.install_to is not None:
        if not WINDOWS or not getattr(sys, "frozen", False):
            parser.error("Use the Windows Tester installer to select an install folder.")
        install_windows(args.install_to)
        return None
    config = settings()
    component = args.component or args.open or ("tv" if not WINDOWS else None)
    if component:
        runtime = installed_runtime(config)
        if runtime is not None:
            return launch_component(component, runtime, wait=not WINDOWS)
        if WINDOWS:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"ChannelForge.Tester.{component}")
        register_process(config["revision"])
        install_crash_reporting(ROOT / "assets/tester-config.json", "studio" if component == "studio" else "viewer",
                                config.get("version", "4.0.0"))
        sys.argv[1:] = forwarded
        if component == "studio":
            from Public_ChannelForge import main as studio
            return studio()
        from ChannelForge_TV import main as viewer
        return viewer()
    choose_windows(config)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
