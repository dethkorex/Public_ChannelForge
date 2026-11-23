#!/usr/bin/env bash
set -euo pipefail

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "FFmpeg is required for standalone playback."
  echo "Arch/Omarchy: sudo pacman -S --needed ffmpeg"
  echo "Debian/Ubuntu: sudo apt install ffmpeg"
  exit 1
fi
if ! python3 -c 'import webview' >/dev/null 2>&1; then
  echo "PyWebView is required for the Creator and TV desktop windows."
  echo "Arch/Omarchy: sudo pacman -S --needed python-pywebview python-gobject webkit2gtk-4.1"
  echo "Debian/Ubuntu: sudo apt install python3-webview"
  exit 1
fi

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/public-channelforge/app"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$SYSTEMD_DIR"
cp -R Public_ChannelForge.py ChannelForge_TV.py web plugins assets PLUGIN_DEVELOPMENT.md "$APP_DIR/"
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
echo "Installed both apps. Run Creator: public-channelforge"
echo "Run the TV viewer: channelforge-tv"
echo "If the command is not found, add $HOME/.local/bin to PATH or sign out and back in."

