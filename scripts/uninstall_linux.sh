#!/usr/bin/env bash
set -euo pipefail
rm -f "${HOME}/.local/bin/public-channelforge"
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/public-channelforge.desktop"
rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/public-channelforge/app"
echo "Application removed. User configuration remains in ${XDG_CONFIG_HOME:-$HOME/.config}/public-channelforge"

