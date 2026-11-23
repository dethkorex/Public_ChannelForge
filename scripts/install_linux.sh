#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -eq 0 && -n ${SUDO_USER:-} && $SUDO_USER != root ]]; then
  echo "Run this installer as your desktop user. It will call sudo for system dependencies when needed." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/Public_ChannelForge.py" ]]; then
  ROOT="$SCRIPT_DIR"
elif [[ -f "$SCRIPT_DIR/../Public_ChannelForge.py" ]]; then
  ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  echo "ChannelForge application files were not found beside this installer. Extract the complete archive and try again."
  exit 1
fi

install_dependencies() {
  local elevate=()
  if [[ "$(id -u)" -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || { echo "Install sudo or run this installer as root once so dependencies can be installed."; exit 1; }
    elevate=(sudo)
  fi
  if command -v pacman >/dev/null 2>&1; then
    echo "Installing ChannelForge dependencies for Arch/Omarchy…"
    "${elevate[@]}" pacman -S --needed python ffmpeg openssh python-pywebview python-pillow python-urllib3 python-gobject webkit2gtk-4.1 \
      gst-plugins-base gst-plugins-good gst-plugins-bad gst-libav mpv
  elif command -v apt-get >/dev/null 2>&1; then
    echo "Installing ChannelForge dependencies for Debian/Ubuntu…"
    "${elevate[@]}" apt-get update
    "${elevate[@]}" apt-get install -y python3 ffmpeg openssh-client python3-webview python3-pil python3-urllib3 gir1.2-webkit2-4.1 \
      gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav mpv
  else
    echo "Unsupported package manager. Install Python 3, FFmpeg, PyWebView, GTK and WebKitGTK, then rerun install.sh."
    exit 1
  fi
}

has_desktop_runtime() {
  /usr/bin/python3 -Es -c 'import webview, gi, PIL, urllib3; gi.require_version("Gtk", "3.0"); gi.require_version("WebKit2", "4.1"); from gi.repository import Gtk, WebKit2' >/dev/null 2>&1 \
    && command -v ffmpeg >/dev/null 2>&1 \
    && command -v mpv >/dev/null 2>&1 \
    && command -v ssh >/dev/null 2>&1 \
    && gst-inspect-1.0 avdec_h264 >/dev/null 2>&1 \
    && gst-inspect-1.0 avdec_aac >/dev/null 2>&1 \
    && gst-inspect-1.0 h264parse >/dev/null 2>&1
}

if ! has_desktop_runtime; then
  install_dependencies
fi
has_desktop_runtime || { echo "The system Python, GTK/WebKit, FFmpeg, SSH, or H.264/AAC playback dependencies failed to load." >&2; exit 1; }

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/public-channelforge/app"
if [[ ${1:-} == --replace ]]; then
  APP_DIR="$(realpath -e -- "${2:?Missing existing application folder}")"
  if [[ ! -f "$APP_DIR/Public_ChannelForge.py" || ! -f "$APP_DIR/ChannelForge_TV.py" ||
        ! -f "$APP_DIR/install.sh" || ! -f "$APP_DIR/uninstall.sh" || -e "$APP_DIR/.git" ]]; then
    echo "The selected directory is not an installed ChannelForge archive." >&2
    exit 1
  fi
fi
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$SYSTEMD_DIR"
/usr/bin/python3 -Es - "$ROOT" "$APP_DIR" <<'PY'
import fcntl
import sys
from pathlib import Path

source, destination = (Path(path).resolve() for path in sys.argv[1:])
sys.path.insert(0, str(source))
from linux_runtime import install_linux_files

with (destination.parent / ".channelforge-install.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    install_linux_files(source, destination)
PY
cat > "$BIN_DIR/public-channelforge" <<EOF
#!/usr/bin/env bash
exec /usr/bin/python3 -Es "$APP_DIR/Public_ChannelForge.py" "\$@"
EOF
chmod +x "$BIN_DIR/public-channelforge"
cat > "$BIN_DIR/channelforge-tv" <<EOF
#!/usr/bin/env bash
exec /usr/bin/python3 -Es "$APP_DIR/ChannelForge_TV.py" "\$@"
EOF
chmod +x "$BIN_DIR/channelforge-tv"
cat > "$DESKTOP_DIR/public-channelforge.desktop" <<EOF
[Desktop Entry]
Name=ChannelForge Creator
Comment=Build personal channels from local folders or Plex
Exec="$BIN_DIR/public-channelforge"
Icon=$APP_DIR/assets/channel-forge-creator.png
Terminal=false
Type=Application
Categories=AudioVideo;Utility;
EOF
cat > "$DESKTOP_DIR/channelforge-tv.desktop" <<EOF
[Desktop Entry]
Name=ChannelForge TV
Comment=Watch public creator channels and Rewind Cinema
Exec="$BIN_DIR/channelforge-tv"
Icon=$APP_DIR/assets/channel-forge-tv.png
Terminal=false
Type=Application
Categories=AudioVideo;Video;
EOF
cat > "$SYSTEMD_DIR/public-channelforge.service" <<EOF
[Unit]
Description=Public ChannelForge standalone TV server
After=network-online.target

[Service]
Type=simple
ExecStart="$BIN_DIR/public-channelforge" --no-browser
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
if [[ ${CHANNELFORGE_IN_APP_UPDATE:-0} == 1 ]]; then
  echo "Application files updated in place."
elif command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user daemon-reload
  if systemctl --user enable public-channelforge.service && systemctl --user restart public-channelforge.service; then
    echo "Background service enabled and started."
  else
    echo "User service installed but could not start in this session. Start it with: systemctl --user enable --now public-channelforge"
  fi
else
  echo "User service installed. Start it with: systemctl --user enable --now public-channelforge"
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi
echo "Installed both apps. Run Creator: public-channelforge"
echo "Run the TV viewer: channelforge-tv"
echo "If the command is not found, add $HOME/.local/bin to PATH or sign out and back in."

