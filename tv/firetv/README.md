# ChannelForge TV for Fire TV / Android TV

Native, remote-first viewer for the opt-in Creator Directory. It supports creator browsing, M3U loading, HLS playback, full-screen channel surfing, and an overlay guide/HUD. If the global directory is unreachable, select **Pair Creator** and enter the HTTPS pairing URL displayed by ChannelForge Creator under **System → Publish worldwide**.

Build with `gradle -p tv/firetv assembleRelease`. The application tries the hosted directory, a read-only GitHub fallback, and a saved direct Creator pairing in that order. Production releases must be signed with a private Android keystore; never commit the keystore or passwords.
