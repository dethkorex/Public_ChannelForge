"""Runtime and update helpers for the Linux desktop apps."""

import re
import shlex
import sys
from collections.abc import Collection
from pathlib import Path


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


def linux_install_command(path: Path) -> str:
    quoted = shlex.quote(str(path))
    if path.name.endswith(".pkg.tar.zst"):
        return f"sudo pacman -U {quoted}"
    if path.suffix == ".deb":
        return f"sudo apt install {quoted}"
    return f'update_dir=$(mktemp -d) && tar -xzf {quoted} -C "$update_dir" && bash "$update_dir/ChannelForge/install.sh"'
