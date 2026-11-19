# Public ChannelForge

Public ChannelForge is a private, local-first channel guide builder for Plex users. It lets each user connect their own Plex server, select library titles, build channel rotations, and export XMLTV and M3U files. It includes a small Python extension system for community tools.

This repository is the sanitized public edition. It contains **no private API credentials, Plex tokens, personal channel presets, media databases, logs, backups, or custom artwork** from the original ChannelForge installation.

## Windows installation

1. Download `Public_ChannelForge_Setup.exe` from the latest GitHub Release.
2. Run it and choose the install folder, Desktop shortcut, and optional startup shortcut.
3. Public ChannelForge opens at `http://127.0.0.1:8787`.
4. Open **Setup**, enter your Plex server URL and token, and select **Test and save**.
5. Return to **Channels**, create a channel, choose a Plex library, and select titles.
6. Open **Guide exports** to download or copy the XMLTV and M3U URLs.

Windows may show a SmartScreen warning because community builds are not code-signed. Verify the SHA-256 checksum shown in the GitHub release before running the installer.

## Linux, Arch Linux, and Omarchy

Requirements: Python 3.10 or newer. No pip packages are required at runtime.

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
- To find a Plex token: in Plex Web, open a media item, select **Get Info** → **View XML**, and copy the `X-Plex-Token` value from the URL.
- The token is written only to the current user's configuration directory. It is never sent anywhere except the configured Plex server.
- Public ChannelForge binds to localhost by default, so other devices cannot access the dashboard unless a user deliberately adds their own proxy/network configuration.

## What the exports do

The XMLTV export is a repeating guide based on the duration metadata Plex reports. The M3U is a channel-discovery list for compatible tools; Public ChannelForge does not transcode or restream media. Playback remains handled by Plex or another DVR/tuner tool the user configures.

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

