#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/public-channelforge/app"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$SYSTEMD_DIR"
cp -R Public_ChannelForge.py web plugins PLUGIN_DEVELOPMENT.md "$APP_DIR/"
cat > "$BIN_DIR/public-channelforge" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/Public_ChannelForge.py" "\$@"
EOF
chmod +x "$BIN_DIR/public-channelforge"
cat > "$DESKTOP_DIR/public-channelforge.desktop" <<EOF
[Desktop Entry]
Name=Public ChannelForge
Comment=Build personal Plex channel guides
Exec=$BIN_DIR/public-channelforge
Terminal=false
Type=Application
Categories=AudioVideo;Utility;
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
  systemctl --user enable --now public-channelforge.service
  echo "Background service enabled and started."
else
  echo "User service installed. Start it with: systemctl --user enable --now public-channelforge"
fi
echo "Installed. Run: public-channelforge"
echo "If the command is not found, add $HOME/.local/bin to PATH or sign out and back in."

