# Alpha 4 Fire TV playback verification

## What failed

Fire TV 3.6.2 used Media3 ExoPlayer 1.7.1. A guide request and its M3U fallback both returning HTTP 503 caused `loadStudioGuide` to remove the studio's channels. Directory and EPG data lived only in memory. On an Amazon AFTSS Stick (Fire OS, Android API 28), a known channel disappeared immediately after the two 503s. Restarting the QA app with the directory unavailable produced an empty guide.

The player retried a saved manifest URL after the studio moved. During the route-rotation test below, playback stopped in both baseline runs and never resumed within the 60-second observation. The terminal error also relied on Activity key handling even when PlayerView held focus.

## Changes

- Fire TV now keeps bounded, atomic directory and EPG snapshots in app-private files. The first successful scan populates them. A studio failure preserves the last known schedule, which the guide labels accordingly. Seven-day expiry, a 4 MiB limit per snapshot, a 20 MiB total and 128-entry cap limit disk use. A single guide larger than the per-entry cap can still be shown from memory.
- Creator IDs reconcile route changes. A complete directory response removes a deliberate unpublish. Legacy catalogs retain recently observed registrations during partial scans. Stable channel IDs link replacement stream URLs and favorites. Published offline channels can remain in a guide returned by a supported Creator. An expired broadcast is still subject to the Creator's schedule and publication rules.
- Media3 uses Creator's Fire-compatible HLS manifest when advertised, with the previous stream as fallback. The application keeps a single ExoPlayer with platform decoders and decoder fallback. Native Roku playback is separate.
- A video-frame watchdog complements the buffering deadline. The viewer attempts recovery three times with exponential delays; it re-reads studio routes and schedule or vault entries, recreates the media item, and resumes movies at the saved position. Generation checks cancel obsolete recovery work when tuning another channel or ejecting. HTTP 503 and expired routes have distinct messages. A continuous 30 seconds of playback resets the automatic retry allowance.
- **Menu** opens optional playback diagnostics; **Updates → Diagnostics** also opens it. The overlay includes device, current program, directory/cache state, buffer, decoder, media format, transfer estimate, rendered/dropped frames, process memory/CPU and recent recovery errors. **Export report** uses the system document picker where available, otherwise the app-specific Downloads directory. Export uses allowed metric fields. It omits titles, media addresses, library data, HTTP headers and exception messages. Android does not provide portable per-process GPU utilization on these Fire OS builds. An unknown track bitrate is reported as `-1`.

## Paired physical measurements

A stock 3.6.2 source build and candidate both used a separate debug package on the same physical Stick. The fixture alternated two H.264 640×360 24-fps / AAC stereo programs every eight seconds using two-second MPEG-TS segments, fetched over the same ADB loopback port mapping. The physical Stick decoded AVC with `OMX.MTK.VIDEO.DECODER.AVC`. This tests the device and player under controlled faults. It does not measure a viewer's ISP or the public relay.

| Condition and measurement | 3.6.2 | Candidate |
| --- | ---: | ---: |
| Clean playback, median tap to first sampled frame (3 × 29 s per build) | 2.033 s | 1.763 s |
| Clean display stalls, seconds per observed minute | 1.371 | 1.504 |
| Clean median longest presentation gap | 0.421 s | 0.432 s |
| Clean median CPU, percentage of one core | 60.28% | 61.67% |
| Clean median process PSS | 101,020 KiB | 83,412 KiB |
| Stale route, automatic recovery (2 × 60 s per build) | 0/2 | 2/2 |
| Stale route, display stalls, seconds per observed minute | 40.905 | 1.833 |
| Stale route, median longest presentation gap | 40.771 s | 1.235 s |
| Stale route, total presented frames across two trials | 838 | 2,684 |

First-frame observations include ADB dispatch and sampling latency. CPU ticks came from the application's process stat file. PSS was sampled with dumpsys. The stall proxy counts the duration exceeding one normal frame for presentation intervals longer than 250 ms, including a final frozen screen through the end of observation. It includes presentation gaps from any cause. All six clean trials crossed multiple discontinuities. Clean first-frame time improved 13.3% and PSS declined 17.4%; measured clean stalls increased 9.7% and CPU increased 2.3%. With a route change at seven seconds, measured stall time fell 95.5%. Small samples and the synthetic program boundary prevent generalizing these percentages to other titles or networks.

### Decoder comparison

A minimal native libVLC 3.6.5 QA activity and the Media3 candidate played the same stream with hardware decoding enabled. In one 28-second smoke run each, Media3 first presented after 1.08 s, showed 639 frames over 26.96 s and used 90,048 KiB PSS. libVLC first presented after 1.71 s, showed 592 frames over 26.04 s and used 127,050 KiB. Their largest observed gaps were 284 ms and 234 ms respectively. APK startup paths differed; these first-frame numbers are a coarse comparison. The Media3 candidate includes the complete guide and controls; the libVLC harness was a standalone surface. libVLC 3.7.8 could not compile with the current compile SDK 35; its AAR declares a minimum compile SDK of 36. Kept Media3 for Fire TV with its existing remote, updater, ABR and playback behavior. [Media3 format support](https://developer.android.com/media/media3/exoplayer/supported-formats) · [Media3 live playback](https://developer.android.com/media/media3/exoplayer/live-streaming) · [VideoLAN libVLC Android artifacts](https://repo.maven.apache.org/maven2/org/videolan/android/libvlc-all/)

### Other checks

- The physical candidate retained its channel and EPG through guide HTTP 503, playlist HTTP 503, directory failure and a process restart. The screen explicitly reported **LAST KNOWN GUIDE**.
- On the Android TV API 34 emulator, retiring a movie's original route before insertion produced one HTTP 404, automatic route refresh, 56 rendered frames and zero decoder drops by observation. Back ejected to the movie shelf.
- A permanent HTTP 503 reached the **Studio is busy** terminal prompt on the emulator after three application retries (about 22 seconds with this fixture). HTTP retries internal to Media3 are included in that elapsed time.
- The final OK-key interception compiled. A second emulator check intended to verify this specific key dispatch could not finish because UI Automator returned a null root and then exited. Direct remote OK after the terminal prompt remains a manual confirmation.
- No signing, store publication, production install, production route change or production directory write is required for these checks.

## Reproduce

Use a Java/Gradle installation compatible with the repository build, Android SDK 35, FFmpeg with libx264, Python 3, an authorized Fire TV, and a writable scratch directory.

1. Set `ANDROID_HOME` and put Gradle, FFmpeg and ADB on PATH. Build two isolated test APKs with `python tests/manual/build_fire_qa.py --ref v3.6.2 --output build/fire-baseline.apk` and `python tests/manual/build_fire_qa.py --output build/fire-candidate.apk`. The helper rewrites a temporary checkout to a QA application ID and loopback HTTP fixture. The release app keeps HTTPS.
2. Start `python tests/manual/fire_tv_studio.py --flags build/fire-flags.txt`. Leave this file empty for normal playback. In another shell run `adb -s DEVICE reverse tcp:19753 tcp:19753` and `adb -s DEVICE install -r build/fire-baseline.apk`.
3. Run `python tests/manual/measure_fire_playback.py --serial DEVICE --flags build/fire-flags.txt --trials 3` and repeat with `--rotate --trials 2`. Repeat after installing `build/fire-candidate.apk` with `install -r`. Measurements use SurfaceFlinger per-layer timestamps as exposed by the tested Fire Stick; other OS builds may restrict them.
4. To reproduce discovery failure, open Live TV Guide, then write `guide` to the flags file and select Scan. Write `directory guide` and restart the QA app. Clear the file and scan to reconnect. To test a sustained playback failure, write `stream`, tune a channel, wait for the terminal error, clear the file and press OK on the remote.
5. End the fixture, remove the ADB reverse rule and uninstall only the QA APK with `adb -s DEVICE uninstall tv.channelforge.viewer.alpha4qa`.

Rollback the Fire viewer separately by reverting its Java changes and rebuilding. The bounded guide snapshots are additive app-private files; older viewers ignore them.
