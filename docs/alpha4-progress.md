# ChannelForge 4.0 alpha stability verification

Baseline revision eec8838 (3.6.2). All work is local until publication or deployment is explicitly approved. Production data and broadcaster settings are outside the test fixture.

| Requirement | Verification to record | Status |
|---|---|---|
| Persistent registration, heartbeat grace, identity and directory reconciliation | Interrupt and restart every directory implementation and viewer with cached channels | In progress |
| False empty libraries, schedule validation, fallback and accurate EPG | Missing file, failed request, valid empty catalog and mixed lineup with fixed wall clock | In progress |
| Desktop engines, GPU decoding, pacing, resource lifetime | Compare embedded libVLC vs native webviews on the same local HLS, record frames, startup, process resources and host limitations | In progress |
| Fire OS and Android | Media3 vs libVLC evaluation, HLS format and timeline checks, remote and physical device if available | In progress |
| Roku | Preserve native playback, validate compile and recovery/cache path, describe physical test if unavailable | Pending |
| Bounded automatic recovery | Expired URL/segment, unavailable origin, decoder stall, live edge, teardown, movie position | In progress |
| Creator stream workload | Compare before/after concurrency, idle reclamation, process counts, CPU, memory, GPU support and network costs; check remux and next program | In progress |
| Diagnostics | Optional desktop troubleshooting dialog, Creator admin endpoint and copyable JSON | Implemented, end-to-end UI checks pending |
| Packaging, regression, rollback | Windows/Linux build, APK build, Roku package, automated tests, no data-destructive migration | Pending |

Platform findings and commands are recorded as their checks finish. A published studio, a reachable guide, and a decoded video frame are separate verification steps.

## Verified checkpoints

- Windows Creator tray Exit, real WebView2 with audible media, isolated studio. `scripts/test_creator_tray_exit.py` clicked the actual tray menu action after closing the window. All seven sampled WebView2 processes, the fixture FFmpeg process, its console host and all three listeners exited. The saved schedule survived. The separate hidden-window test also reproduced continued audio decoding on 3.6.2 and measured it stop on the alpha.
- Desktop cache and viewer regression suite ran 21 tests on Windows with one skip in the default interpreter. Cached registrations survive a partial directory result. An authoritative empty directory replaces them.
- In the Windows public-listener loopback benchmark with two simultaneous viewers, killing the encoder six seconds into playback previously produced a 410 and about 7.2 seconds of starvation per viewer. The alpha recorded zero starvation in that run, including through encoder recovery. Both versions measured zero starvation on the healthy link. These results describe this fault test only.
- The physical Fire TV previously discarded a registered channel after a 503 and again after an offline restart. The alpha retained the guide entry in both checks. Playback timing and route rotation are measured separately.
- Roku hardware and a signing key are unavailable. Hardware decode, Store signing, and real Roku stall percentages remain unverified.
