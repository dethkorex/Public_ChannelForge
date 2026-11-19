# Public ChannelForge 2

Public ChannelForge is a standalone, local-first live TV server for Plex users. It runs continuously in the background, maintains deterministic 24/7 channel schedules, hosts XMLTV and M3U endpoints, and starts Plex HLS playback at the correct live-program offset. ErsatzTV is not required.

The built-in **Watch TV** screen includes an embedded HLS player, channel rail, now-playing progress, and six-hour visual guide. Viewers click a channel and watch immediately in ChannelForge without adding it to Plex DVR.

After connection, ChannelForge displays the Plex username, server name, and server version supplied by that server. Library browsing includes Plex posters, summaries, ratings, years, and media types. Poster bytes are proxied through ChannelForge so the Plex token is never placed in browser image URLs.

The built-in **Asset Studio** creates original channel logos, guide backgrounds, and corner bugs from a channel name and user-selected colors. Users can also upload artwork they own and assign a logo to an XMLTV channel. Artwork remains in the user's local application-data folder.

Every installation also includes 24 coordinated starter assets: logos, guide/player backgrounds, and corner bugs across Classic TV, Neon Night, Cinema, Kids Pop, Sports Arena, News Desk, Spooky, and Minimal themes. These are original neutral assets created for the public edition. Users can assign all three asset types while creating a channel, then replace them with generated or uploaded artwork later.

This repository is the sanitized public edition. It contains **no private API credentials, Plex tokens, personal channel presets, media databases, logs, backups, or custom artwork** from the original ChannelForge installation.

The interface and asset workflow remain part of ChannelForge. Only the original owner's private Fish Audio integration and personal channel assets/presets are excluded. This public edition does not contact Fish Audio or ship any Fish API code.

## Windows installation

1. Download `Public_ChannelForge_Setup.exe` from the latest GitHub Release.
2. Run it and choose the install folder, Desktop shortcut, and optional startup shortcut.
3. Public ChannelForge starts its background TV server and opens at `http://127.0.0.1:8787`.
4. Open **Setup**, enter your Plex server URL and token, and select **Test and save**.
5. Return to **Channels**, create a channel, choose a Plex library, and select titles.
6. Open **Guide exports** to copy the hosted XMLTV and M3U URLs into your DVR, tuner client, or IPTV player.

Windows may show a SmartScreen warning because community builds are not code-signed. Verify the SHA-256 checksum shown in the GitHub release before running the installer.

## Linux, Arch Linux, and Omarchy

Requirements: Python 3.10 or newer. No pip packages are required at runtime.

### Debian, Ubuntu, Mint, and similar distributions

Download the `.deb` file from the latest release, then run:

```bash
sudo apt install ./Public_ChannelForge-*-linux-all.deb
public-channelforge
```

### Arch Linux and Omarchy

Download the `.pkg.tar.zst` file from the latest release, then run:

```bash
sudo pacman -U ./public-channelforge-*-any.pkg.tar.zst
public-channelforge
```

### Universal Linux user installation

Download and extract the `linux-x64.tar.gz` archive, open its `Public_ChannelForge` directory, and run:

```bash
chmod +x install.sh uninstall.sh
./install.sh
public-channelforge
```

Developers can alternatively clone the repository:

```bash
git clone https://github.com/dethkorex/Public_ChannelForge.git
cd Public_ChannelForge
chmod +x scripts/install_linux.sh scripts/uninstall_linux.sh
./scripts/install_linux.sh
public-channelforge
```

On Arch Linux or Omarchy, install prerequisites if needed:

```bash
sudo pacman -S --needed python git
```

If `public-channelforge` is not found, add this to `~/.bashrc` or `~/.zshrc`, then open a new terminal:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

The app uses XDG paths on Linux:

- App files: `~/.local/share/public-channelforge/app`
- User settings and token: `~/.config/public-channelforge`
- Launcher: `~/.local/share/applications/public-channelforge.desktop`

Wayland compositors used by Omarchy do not need special configuration because the interface runs in the default browser. If the browser does not open automatically, visit `http://127.0.0.1:8787`.

## Plex setup details

- Use the URL that is reachable from the same computer, such as `http://127.0.0.1:32400` or `http://192.168.1.20:32400`.
- To configure Plex automatically: in Plex Web, open a media item, select **Get Info** → **View XML**, copy the complete URL, and paste it into ChannelForge. The app extracts `X-Plex-Token`, securely stores it locally, and removes it from the visible server URL. Manual token entry remains available.
- The token is written only to the current user's configuration directory. It is never sent anywhere except the configured Plex server.
- Public ChannelForge binds to localhost by default, so other devices cannot access the dashboard unless a user deliberately adds their own proxy/network configuration.

## Standalone server and live playback

ChannelForge owns the channel clock and guide. Every channel has a stable schedule anchor; restarting the application does not reset the lineup to its first title. The XMLTV includes current and upcoming programs based on Plex duration metadata. The M3U points to ChannelForge's `/live/<channel>.m3u8` URLs. When a player opens one, ChannelForge resolves the current program and offset, then creates a Plex HLS session for that exact live position.

Plex remains the media source and transcoder, but ErsatzTV and its database/server are no longer involved. ChannelForge hosts the guide, playlist, channel clock, web manager, artwork, and live entry points.

For clients on another device, choose **Local network** in Setup, enter a base URL such as `http://192.168.1.50:8787`, save, and restart ChannelForge. Only expose this server on a trusted home network; it has no internet-facing authentication.

## Community tools

See [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md). Tools execute as local Python code and should only be installed from trusted authors.

## Build the Windows installer

From PowerShell on Windows:

```powershell
./scripts/build_windows.ps1
```

The script installs the pinned PyInstaller build dependency and creates `release/Public_ChannelForge_Setup.exe` plus a SHA-256 checksum in the console. The installer is per-user and does not require administrator rights.

## Developer run and tests

```bash
python Public_ChannelForge.py
python -m unittest discover -s tests -v
```

## Uninstall

Windows: remove the installation folder and its shortcuts. User configuration is stored separately under `%LOCALAPPDATA%\Public_ChannelForge` so upgrades do not erase it.

Linux:

```bash
./scripts/uninstall_linux.sh
```

The Linux uninstaller keeps user settings. Remove `~/.config/public-channelforge` manually only if you also want to erase the saved Plex token and channel definitions.

## License and trademarks

MIT licensed. Plex is a trademark of Plex, Inc. This project is an independent community tool and is not affiliated with or endorsed by Plex.

