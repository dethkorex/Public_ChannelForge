#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-1.0.1}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/release"
WORK="${RUNNER_TEMP:-/tmp}/public-channelforge-packaging"
APP_ROOT="$WORK/app"

rm -rf "$WORK" "$OUT"
mkdir -p "$OUT" "$APP_ROOT/ChannelForge"
cp "$ROOT/Public_ChannelForge.py" "$ROOT/ChannelForge_TV.py" "$ROOT/windows_distribution.py" "$ROOT/PLUGIN_DEVELOPMENT.md" "$APP_ROOT/ChannelForge/"
cp -R "$ROOT/web" "$ROOT/plugins" "$ROOT/assets" "$APP_ROOT/ChannelForge/"
cp "$ROOT/scripts/install_linux.sh" "$APP_ROOT/ChannelForge/install.sh"
cp "$ROOT/scripts/uninstall_linux.sh" "$APP_ROOT/ChannelForge/uninstall.sh"
chmod +x "$APP_ROOT/ChannelForge/install.sh" "$APP_ROOT/ChannelForge/uninstall.sh"
tar -C "$APP_ROOT" -czf "$OUT/ChannelForge-${VERSION}-linux-x64.tar.gz" ChannelForge
# Compatibility alias for older in-app updaters; it contains the same two-app suite.
cp "$OUT/ChannelForge-${VERSION}-linux-x64.tar.gz" "$OUT/Public_ChannelForge-${VERSION}-linux-x64.tar.gz"

DEB="$WORK/deb"
mkdir -p "$DEB/DEBIAN" "$DEB/opt/public-channelforge" "$DEB/usr/bin" "$DEB/usr/share/applications" "$DEB/usr/lib/systemd/user"
cp "$ROOT/Public_ChannelForge.py" "$ROOT/ChannelForge_TV.py" "$ROOT/windows_distribution.py" "$ROOT/PLUGIN_DEVELOPMENT.md" "$DEB/opt/public-channelforge/"
cp -R "$ROOT/web" "$ROOT/plugins" "$ROOT/assets" "$DEB/opt/public-channelforge/"
cat > "$DEB/DEBIAN/control" <<EOF
Package: public-channelforge
Version: $VERSION
Section: video
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-webview, ffmpeg, openssh-client
Maintainer: Public ChannelForge contributors
Description: Local-first personal channel guide builder
 Build personal channel rotations and watch the public TV guide in two desktop apps.
EOF
cat > "$DEB/usr/bin/public-channelforge" <<'EOF'
#!/usr/bin/env bash
exec python3 /opt/public-channelforge/Public_ChannelForge.py "$@"
EOF
chmod 755 "$DEB/usr/bin/public-channelforge"
cat > "$DEB/usr/bin/channelforge-tv" <<'EOF'
#!/usr/bin/env bash
exec python3 /opt/public-channelforge/ChannelForge_TV.py "$@"
EOF
chmod 755 "$DEB/usr/bin/channelforge-tv"
cat > "$DEB/usr/share/applications/public-channelforge.desktop" <<'EOF'
[Desktop Entry]
Name=ChannelForge Creator
Comment=Build personal channels from local folders or Plex
Exec=public-channelforge
Icon=/opt/public-channelforge/assets/channel-forge-creator.png
Terminal=false
Type=Application
Categories=AudioVideo;Utility;
EOF
cat > "$DEB/usr/share/applications/channelforge-tv.desktop" <<'EOF'
[Desktop Entry]
Name=ChannelForge TV
Comment=Watch public creator channels and Rewind Cinema
Exec=channelforge-tv
Icon=/opt/public-channelforge/assets/channel-forge-tv.png
Terminal=false
Type=Application
Categories=AudioVideo;Video;
EOF
cat > "$DEB/usr/lib/systemd/user/public-channelforge.service" <<'EOF'
[Unit]
Description=Public ChannelForge standalone TV server
After=network-online.target
[Service]
ExecStart=/usr/bin/public-channelforge --no-browser
Restart=on-failure
RestartSec=5
[Install]
WantedBy=default.target
EOF
dpkg-deb --root-owner-group --build "$DEB" "$OUT/Public_ChannelForge-${VERSION}-linux-all.deb"

ARCH="$WORK/arch"
mkdir -p "$ARCH/opt/public-channelforge" "$ARCH/usr/bin" "$ARCH/usr/share/applications" "$ARCH/usr/lib/systemd/user"
cp "$ROOT/Public_ChannelForge.py" "$ROOT/ChannelForge_TV.py" "$ROOT/windows_distribution.py" "$ROOT/PLUGIN_DEVELOPMENT.md" "$ARCH/opt/public-channelforge/"
cp -R "$ROOT/web" "$ROOT/plugins" "$ROOT/assets" "$ARCH/opt/public-channelforge/"
cp "$DEB/usr/bin/public-channelforge" "$ARCH/usr/bin/"
cp "$DEB/usr/bin/channelforge-tv" "$ARCH/usr/bin/"
cp "$DEB/usr/share/applications/public-channelforge.desktop" "$ARCH/usr/share/applications/"
cp "$DEB/usr/share/applications/channelforge-tv.desktop" "$ARCH/usr/share/applications/"
cp "$DEB/usr/lib/systemd/user/public-channelforge.service" "$ARCH/usr/lib/systemd/user/"
cat > "$ARCH/.PKGINFO" <<EOF
pkgname = public-channelforge
pkgbase = public-channelforge
pkgver = $VERSION-1
pkgdesc = Local-first personal channel guide builder
url = https://github.com/dethkorex/Public_ChannelForge
builddate = $(date +%s)
packager = Public ChannelForge contributors
size = $(du -sb "$ARCH" | cut -f1)
arch = any
license = MIT
depend = python>=3.10
depend = ffmpeg
depend = openssh
depend = python-pywebview
depend = python-gobject
depend = webkit2gtk-4.1
EOF
tar --zstd -C "$ARCH" -cf "$OUT/public-channelforge-${VERSION}-1-any.pkg.tar.zst" .PKGINFO opt usr

(cd "$OUT" && sha256sum ChannelForge-* Public_ChannelForge-* public-channelforge-* > SHA256SUMS-Linux.txt)
echo "Linux packages created in $OUT"

