#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/public-channelforge/app"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR"
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
echo "Installed. Run: public-channelforge"
echo "If the command is not found, add $HOME/.local/bin to PATH or sign out and back in."

