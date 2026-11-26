# Windows 4.0.0-alpha.1 package verification

The Windows build ran in a clean temporary source tree on Windows build 26300, using Python 3.14.0 and PyInstaller 6.22.3. At completion its application code, assets, web files and Windows packaging inputs matched the development checkout. The suite installer and portable viewer are in the temporary tree's `release` folder. The suite installer is unsigned.

| Tested file | Bytes | SHA-256 |
| --- | ---: | --- |
| ChannelForge_Setup.exe | 141400318 | `8e3317fd638d8cdc95969d135899620cbe52e005463c83501785fafc24e68c5c` |
| ChannelForge_Creator.exe, inside installer | 107067508 | `ff854b40852197be94fe60dcb55c7c100d05699163196ed041e2433cd9053b60` |
| ChannelForge_TV.exe, standalone and inside installer | 20396376 | `2b25784f5f989a9274b9eacbeff202391a0fd3dc31cdf97e764764b512b751d1` |

The three legacy installer filenames contained the same bytes as the suite installer. Both embedded executables matched the staged payloads. The Creator bundle contained FFmpeg, ffprobe, SSH and ssh-keygen. The viewer contained its web player, VLC bridge, Python bindings, WebView modules and the exact four web playback resources from the source tree.

The in-place update check ran the installer's replacement routine against two older placeholder executables in a disposable folder. Both files were replaced, both resulting hashes matched, the second run skipped replacement, and the folder contained no versioned subdirectory or pending update file. Fourteen Windows installer unit tests passed. Registry registration, shortcuts and installation over a production copy were outside this smoke test.

The test started the packaged viewer executable through its frozen multiprocessing entry point, retaining its embedded Python import path. The optional VLC renderer used the machine's installed libVLC 3.0.21 and the bundled python-vlc 3.0.21203 binding. It requested the playlist and ten HLS segments, reported 29 displayed frames with zero dropped frames, and reached a playback position of 1.026 seconds. The synthetic fixture ran over loopback. Public WAN performance and clean-machine WebView2 startup require separate checks.

## Reproduce

Use a disposable copy of the repository. The Windows build script clears its own build, distribution, release and installer-payload directories. Provide Python, a Windows desktop session, the real FFmpeg and ffprobe binaries on PATH, and an installed libVLC runtime for the optional renderer test.

```powershell
.\scripts\build_windows.ps1
python -m unittest discover -s tests -p test_windows_installer.py -q
python scripts/verify_windows_bundle.py .
New-Item -ItemType Directory -Force fixture | Out-Null
ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=320x180:rate=24 -t 10 -c:v libx264 -an -preset ultrafast -g 24 -sc_threshold 0 -f hls -hls_time 1 -hls_list_size 0 -hls_segment_filename fixture/part-%03d.ts fixture/test.m3u8
python scripts/verify_windows_bundle.py . --fixture fixture
```

The verifier compares against the installer from that build. Independently rebuilt executables can have different SHA-256 values.
