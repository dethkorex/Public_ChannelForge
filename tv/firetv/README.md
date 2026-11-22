# ChannelForge TV for Fire TV / Android TV

Native, remote-first viewer for the opt-in Creator Directory. It supports creator browsing, M3U loading, HLS playback, full-screen channel surfing, and an overlay guide/HUD.

Build with `gradle -p tv/firetv assembleRelease`. Replace `DIRECTORY_URL` in `app/build.gradle` with the deployed HTTPS directory URL before distribution. Production releases must be signed with a private Android keystore; never commit the keystore or passwords.
