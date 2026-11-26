# ChannelForge TV for Roku

Native SceneGraph/BrightScript client for the hosted Creator Directory. It provides creator browsing, M3U loading, HLS playback, channel surfing, and a remote-first guide overlay.

From the repository root, run `python scripts/package_roku.py build/ChannelForgeTV-Roku.zip` to package the manifest, source, components and artwork for sideloading. MainScene configures the public directory URL. The [Roku alpha test and signing guide](../../docs/alpha4-roku.md) includes off-device checks, hardware playback tests and Store signing steps.

The 4.0.0 public viewer uses Roku build 1 to upgrade from alpha build 0. Live channels and Rewind Cinema use Roku's HLS player. For Creator adaptive streams, the viewer requests an initial variant at or below 800 Kibps, or 320 Kibps after a stall. The player can move to any advertised rendition as conditions improve. The setting affects startup only. [Roku playback metadata](https://developer.roku.com/dev/docs/content-metadata)

The watchdog looks for forward movement of the playhead. Twenty seconds without progress, or multiple rebuffers with less than ten seconds of playback in a twenty-second window, trigger a bounded retry. A confirmed scheduled channel sign-off returns an end-of-broadcast message. An unexpectedly ended live stream is retried. The isolated BrightScript checks cover these cases, studio route changes and movie resume. Physical Roku playback remains a separate hardware check.
