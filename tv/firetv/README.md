# ChannelForge TV for Fire TV / Android TV

Native, remote-first viewer for the opt-in Creator Directory. It supports creator browsing, M3U loading, HLS playback, full-screen channel surfing, and an overlay guide/HUD. If the global directory is unreachable, select **Pair Creator** and enter the HTTPS pairing URL displayed by ChannelForge Creator under **System → Publish worldwide**.

Build with `gradle -p tv/firetv assembleRelease`. The application tries the hosted directory, a read-only GitHub fallback, and a saved direct Creator pairing in that order. Production releases must be signed with a private Android keystore; never commit the keystore or passwords.

Install the official [Fire TV APK](https://github.com/dethkorex/Public_ChannelForge/releases/latest/download/ChannelForgeTV-FireTV.apk) to get the in-app updater. When the guide shows **Update to v…**, select it and confirm installation in Fire OS. On first use, Fire OS may ask you to allow **Install Unknown Apps** for ChannelForge TV. If you already have an official APK signed with the release key (v3.5.14 or later), installing the new APK over it keeps your settings. Older APKs signed with a different key require a one-time uninstall before installing the official APK.
