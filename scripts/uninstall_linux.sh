#!/usr/bin/env bash
set -euo pipefail
if command -v systemctl >/dev/null 2>&1; then systemctl --user disable --now public-channelforge.service 2>/dev/null || true; fi
rm -f "${HOME}/.local/bin/public-channelforge"
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/public-channelforge.desktop"
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/public-channelforge.service"
rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/public-channelforge/app"
echo "Application removed. User configuration remains in ${XDG_CONFIG_HOME:-$HOME/.config}/public-channelforge"

