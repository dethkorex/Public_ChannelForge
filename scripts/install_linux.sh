#!/usr/bin/env bash
set -euo pipefail

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
    "${elevate[@]}" pacman -S --needed python ffmpeg openssh python-pywebview python-gobject webkit2gtk-4.1
  elif command -v apt-get >/dev/null 2>&1; then
    echo "Installing ChannelForge dependencies for Debian/Ubuntu…"
    "${elevate[@]}" apt-get update
    "${elevate[@]}" apt-get install -y python3 ffmpeg openssh-client python3-webview gir1.2-webkit2-4.1
  else
    echo "Unsupported package manager. Install Python 3, FFmpeg, PyWebView, GTK and WebKitGTK, then rerun install.sh."
    exit 1
  fi
}

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ssh >/dev/null 2>&1 || ! python3 -c 'import webview' >/dev/null 2>&1; then
  install_dependencies
fi
command -v ffmpeg >/dev/null 2>&1 || { echo "FFmpeg is still unavailable after dependency installation."; exit 1; }
python3 -c 'import webview' >/dev/null 2>&1 || { echo "PyWebView is still unavailable after dependency installation."; exit 1; }

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/public-channelforge/app"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$SYSTEMD_DIR"
cp -R "$ROOT/Public_ChannelForge.py" "$ROOT/ChannelForge_TV.py" "$ROOT/web" "$ROOT/plugins" "$ROOT/assets" "$ROOT/PLUGIN_DEVELOPMENT.md" "$APP_DIR/"
cat > "$BIN_DIR/public-channelforge" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/Public_ChannelForge.py" "\$@"
EOF
chmod +x "$BIN_DIR/public-channelforge"
cat > "$BIN_DIR/channelforge-tv" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/ChannelForge_TV.py" "\$@"
EOF
chmod +x "$BIN_DIR/channelforge-tv"
cat > "$DESKTOP_DIR/public-channelforge.desktop" <<EOF
[Desktop Entry]
Name=ChannelForge Creator
Comment=Build personal channels from local folders or Plex
Exec=$BIN_DIR/public-channelforge
Icon=$APP_DIR/assets/channel-forge-creator.png
Terminal=false
Type=Application
Categories=AudioVideo;Utility;
EOF
cat > "$DESKTOP_DIR/channelforge-tv.desktop" <<EOF
[Desktop Entry]
Name=ChannelForge TV
Comment=Watch public creator channels and Rewind Cinema
Exec=$BIN_DIR/channelforge-tv
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
ExecStart=$BIN_DIR/public-channelforge --no-browser
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user daemon-reload
  if systemctl --user enable --now public-channelforge.service; then
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

