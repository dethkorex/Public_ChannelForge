"""Runtime and update helpers for the Linux desktop apps."""

import re
import os
import shlex
import shutil
import sys
import tempfile
from collections.abc import Collection
from pathlib import Path


LINUX_APP_FILES = (
    "Public_ChannelForge.py", "ChannelForge_TV.py", "windows_distribution.py", "linux_runtime.py", "release_metadata.py",
    "PLUGIN_DEVELOPMENT.md", "web", "plugins", "assets", "install.sh", "uninstall.sh",
)


def start_webview(webview) -> None:
    if sys.platform.startswith("linux"):
        webview.start(gui="gtk", debug=False)
    else:
        webview.start(debug=False)


def linux_release_asset(names: Collection[str], version: str, app_directory: Path) -> str:
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
        raise ValueError("GitHub reported an invalid updater version.")
    portable = (f"ChannelForge-{version}-linux-x64.tar.gz", f"Public_ChannelForge-{version}-linux-x64.tar.gz")
    if app_directory == Path("/opt/public-channelforge"):
        if Path("/etc/arch-release").exists():
            return f"public-channelforge-{version}-1-any.pkg.tar.zst"
        if Path("/etc/debian_version").exists():
            return f"Public_ChannelForge-{version}-linux-all.deb"
    return next((name for name in portable if name in names), portable[0])


def is_portable_installation(folder: Path) -> bool:
    return not (folder / ".git").exists() and all(
        (folder / name).is_file() for name in ("Public_ChannelForge.py", "ChannelForge_TV.py", "install.sh", "uninstall.sh")
    )


def linux_install_command(path: Path, app_directory: Path | None = None) -> str:
    quoted = shlex.quote(str(path))
    if path.name.endswith(".pkg.tar.zst"):
        return f"sudo pacman -U {quoted}"
    if path.suffix == ".deb":
        return f"sudo apt install {quoted}"
    existing = ""
    if app_directory is not None and is_portable_installation(app_directory):
        existing = f" --replace {shlex.quote(str(app_directory))}"
    script = (
        'set -e; update_dir=$(mktemp -d); trap \'rm -rf -- "$update_dir"\' EXIT; '
        f'tar -xzf {quoted} -C "$update_dir"; bash "$update_dir/ChannelForge/install.sh"{existing}; '
        f"rm -f -- {quoted}"
    )
    return "bash -c " + shlex.quote(script)


def install_linux_files(source: Path, destination: Path) -> None:
    """Replace only bundled application files and retain user files beside them."""
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".channelforge-update-", dir=destination.parent))
    prepared = staging / "prepared"
    backups = staging / "backups"
    cleanup = True
    replaced, saved = [], []
    try:
        prepared.mkdir()
        backups.mkdir()
        for name in LINUX_APP_FILES:
            entry = source / name
            if name in ("install.sh", "uninstall.sh") and not entry.is_file():
                entry = source / "scripts" / (name.removesuffix(".sh") + "_linux.sh")
            if entry.is_dir():
                shutil.copytree(entry, prepared / name)
            else:
                shutil.copy2(entry, prepared / name)
        try:
            for name in LINUX_APP_FILES:
                target = destination / name
                if target.exists() or target.is_symlink():
                    os.replace(target, backups / name)
                    saved.append(name)
                os.replace(prepared / name, target)
                replaced.append(name)
        except OSError:
            try:
                for name in reversed(replaced):
                    os.replace(destination / name, prepared / name)
                for name in reversed(saved):
                    os.replace(backups / name, destination / name)
            except OSError as restore_error:
                cleanup = False
                raise OSError(f"Could not restore the update. Original files are in {backups}.") from restore_error
            raise
    finally:
        if cleanup:
            shutil.rmtree(staging)
