# Public ChannelForge 3

Public ChannelForge is a standalone, local-first live TV server for local media folders, Plex libraries, or both. It runs continuously in the background, maintains deterministic 24/7 channel schedules, and hosts XMLTV, M3U, and browser-compatible HLS endpoints. Plex and ErsatzTV are optional.

## ChannelForge TV ecosystem

Version 3.2 adds three public components without changing the owner's private ChannelForge build:

- **Hosted Creator Directory** — creators explicitly publish their public XMLTV/M3U endpoints. An edge health check temporarily hides offline studios and restores them automatically.
- **ChannelForge TV for Fire TV / Android TV** — downloadable remote-first APK with creator browsing, channel surfing, HLS playback, and a full-screen guide HUD.
- **ChannelForge TV for Roku** — native SceneGraph/BrightScript channel with the same directory, channel surfing, guide, and HLS playback model.

Publishing remains opt-in because channel names and stream endpoints may be private. The directory never receives Plex credentials, local filesystem paths, media databases, or video files. A creator's ChannelForge server must have a public HTTPS URL while viewers watch; the hosted directory removes the need for the project owner to run a separate discovery server, but it is not a video relay or CDN.

Source and deployment instructions are in [`directory/`](directory/), [`tv/firetv/`](tv/firetv/), and [`tv/roku/`](tv/roku/). Tagged releases build the installable Fire TV APK and Roku ZIP automatically.

Version 3 introduces the studio workspace inspired by the private ChannelForge manager: a fixed sidebar and status bar, overview metrics and live lineup, dedicated searchable poster library, draggable channel ordering, a four-step channel builder, expanded ordered/shuffle/marathon/franchise modes, branding and guide-artwork workspaces, system health cards, and automatic credential-free configuration backups. Private artwork, private APIs, Fish Audio, and ErsatzTV-specific operations are intentionally not copied.

The built-in **Watch TV** screen includes an embedded HLS player, channel rail, now-playing progress, and six-hour visual guide. Viewers click a channel and watch immediately in ChannelForge without adding it to Plex DVR.

Playback uses a standalone FFmpeg pipeline. ChannelForge reads either an approved local-library file or an authenticated Plex media part, converts it to browser-compatible H.264/AAC HLS, and serves the playlist and segments from its own origin. Local-only users do not need a Plex account or server.

The **Get Started** screen walks new users through adding a local folder or connecting Plex, creating a channel, testing it in the player, and copying the hosted stream URLs.

Completed setup tasks show a visible check mark. Channels can be deleted either from the Channels manager or directly from the Watch TV channel rail.

After connection, ChannelForge displays the Plex username, server name, and server version supplied by that server. Library browsing includes Plex posters, summaries, ratings, years, and media types. Poster bytes are proxied through ChannelForge so the Plex token is never placed in browser image URLs.

The built-in **Asset Studio** creates original channel logos, guide backgrounds, and corner bugs from a channel name and user-selected colors. Users can also upload artwork they own and assign a logo to an XMLTV channel. Artwork remains in the user's local application-data folder.

Every installation also includes 24 coordinated starter assets: logos, guide/player backgrounds, and corner bugs across Classic TV, Neon Night, Cinema, Kids Pop, Sports Arena, News Desk, Spooky, and Minimal themes. These are original neutral assets created for the public edition. Users can assign all three asset types while creating a channel, then replace them with generated or uploaded artwork later.

The **Updates** screen automatically checks the official GitHub Releases feed, compares versions, downloads the correct platform installer, and verifies its published SHA-256 checksum. A live progress display reports the download stage, percentage, transferred bytes, verification, and any error. Windows can open the verified setup wizard directly; Linux users receive an explicit “installation required” state, the verified Debian, Arch/Omarchy, or portable package, and a copyable install command. The page detects a restarted server, reloads itself, and updates both version labels automatically. ChannelForge never silently installs an update without confirmation.

This repository is the sanitized public edition. It contains **no private API credentials, Plex tokens, personal channel presets, media databases, logs, backups, or custom artwork** from the original ChannelForge installation.

The interface and asset workflow remain part of ChannelForge. Only the original owner's private Fish Audio integration and personal channel assets/presets are excluded. This public edition does not contact Fish Audio or ship any Fish API code.

## Windows installation

1. Download `Public_ChannelForge_Setup.exe` from the latest GitHub Release.
2. Run it and choose the install folder, Desktop shortcut, and optional startup shortcut.
3. Public ChannelForge starts its background TV server and opens at `http://127.0.0.1:8787`.
4. On first launch, choose **Add a local media folder**, **Sign in with Plex**, or configure both.
5. Open **Channels**, create a channel, choose any configured library, and select titles.
6. Open **Guide exports** to copy the hosted XMLTV and M3U URLs into your DVR, tuner client, or IPTV player.

Windows may show a SmartScreen warning because community builds are not code-signed. Verify the SHA-256 checksum shown in the GitHub release before running the installer.

## Linux, Arch Linux, and Omarchy

Requirements: Python 3.10 or newer and FFmpeg. Debian and Arch packages install FFmpeg as a package dependency; the Windows installer bundles it. Portable Linux installs show the exact FFmpeg package command when it is missing.

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

## Local-folder setup

- Open **System** → **Media sources**, enter a friendly library name, and provide the absolute path to an existing folder.
- ChannelForge scans that folder recursively for MKV, MP4, M4V, AVI, MOV, WebM, TS, M2TS, MPEG, WMV, and FLV video files.
- Add `poster.jpg`, `folder.jpg`, or an image with the same filename as a video to display local artwork.
- The folder must remain available while its channels are playing. Removing a source never deletes the user's media.
- Saved paths are validated before playback; ChannelForge will only open video files inside configured local-library roots.

## Optional Plex setup details

- **Sign in with Plex** uses Plex's official PIN authorization page, never asks ChannelForge for your password, automatically receives the account token after approval, and discovers the best available Plex Media Server connection.
- Manual setup remains available from the onboarding screen for unusual network layouts or servers that Plex discovery cannot reach.
- Use the URL that is reachable from the same computer, such as `http://127.0.0.1:32400` or `http://192.168.1.20:32400`.
- To configure Plex automatically: in Plex Web, open a media item, select **Get Info** → **View XML**, copy the complete URL, and paste it into ChannelForge. The app extracts `X-Plex-Token`, securely stores it locally, and removes it from the visible server URL. Manual token entry remains available.
- The token is written only to the current user's configuration directory. It is never sent anywhere except the configured Plex server.
- Public ChannelForge binds to localhost by default, so other devices cannot access the dashboard unless a user deliberately adds their own proxy/network configuration.

## Standalone server and live playback

ChannelForge owns the channel clock and guide. Every channel has a stable schedule anchor; restarting the application does not reset the lineup to its first title. The XMLTV includes current and upcoming programs using probed local-file or Plex duration metadata. The M3U points to ChannelForge's `/live/<channel>.m3u8` URLs. When a player opens one, ChannelForge resolves the current program and offset, then creates an HLS session for that exact live position.

Local folders and Plex can be used independently or together. ChannelForge performs browser-compatible live transcoding locally with FFmpeg and hosts the guide, playlist, channel clock, web manager, artwork, encoder, and live entry points.

## Channel playback modes

- **Play in order** repeats selected movies or episodes in their selected order.
- **Shuffle lineup** creates a deterministic mixed order that remains stable across application restarts.
- **Series marathon** shuffles the selected series order while preserving season and episode order inside each series.

For Plex TV libraries, ChannelForge loads playable episode records rather than show folders. The channel editor includes title filtering, Select all, and Clear controls for large libraries.

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

