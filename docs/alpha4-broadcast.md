# Creator broadcast verification

The Creator now keeps a studio’s schedule visible through a temporary Plex outage. A channel retains its original UTC boundaries. If a source file is missing, tuning and the guide use the same replacement program. Finished channels still leave the public lineup.

The shared live encoder restarts on premature exit or missing progress. It resumes at the later of the UTC playhead and the end of the last advertised segment, keeps previously published segments available and marks the transition with an HLS discontinuity. Preparing the next program is limited to one speculative session per Creator near the transition. The Fire TV master starts with the lower bandwidth rendition and shares the existing encoder.

The normal encoder policy is `auto`. A compatible, regularly keyframed local H.264 movie can copy video into HLS. Public viewers still receive adaptive renditions. On supported hosts a short encode probe verifies hardware availability before a session uses it. Low-resolution material prefers software when the encoder is idle. `CHANNELFORGE_VIDEO_ENCODER=software` selects software for comparison or rollback. `CHANNELFORGE_MAX_ENCODERS` limits encoder concurrency and `CHANNELFORGE_CACHE_MIB` limits retained stream files. The latter defaults to 3072 MiB. The disk reaper and in-flight reservations apply to shared playback and speculative preparation. Shutdown cancels pending starts as well as running sessions.

## Measurements

One Windows host with an RTX 3060 Ti, the same FFmpeg installation and generated 854×480 H.264/AAC material. Baseline was eec8838. Four simultaneous tune requests shared one encoder. Each 16-second output decoded to 384 frames.

| Local fixture | First playlist | Child CPU seconds | Peak child RSS | HLS segment bytes |
| --- | ---: | ---: | ---: | ---: |
| Private movie, baseline | 2.481 s | 2.984 | 147.7 MiB | 2,192,080 |
| Private movie, auto copy | 0.667 s | 0.547 | 20.9 MiB | 1,231,212 |
| Public adaptive, baseline | 0.352 s | 9.516 | 152.4 MiB | 4,102,160 |
| Public adaptive, auto | 0.327 s | 6.781 | 153.6 MiB | 4,093,512 |
| Public adaptive, forced NVENC | 0.942 s | 2.719 | 346.8 MiB | 4,332,460 |

In this sample, private startup was 73% shorter. Adaptive software used 29% fewer CPU seconds. NVENC reduced CPU use but added startup time and memory for this SD source.

Two further anonymous HTTP clients subscribed to the balanced rendition of the same live feed over loopback for 18 seconds. Without an injected failure, first segment delivery measured 0.366 s before and 0.383 s after. Three seconds of playable media arrived in 0.574 s before and 0.490 s after. Neither run exhausted its simulated player buffers.

With FFmpeg killed six seconds after tuning, baseline viewers received HTTP 410, each obtained ten seconds of media, and their buffers emptied for 7.16 and 7.18 seconds. Updated viewers stayed on the original playlist and played 17.32 and 17.35 seconds with zero measured starvation. These are source-side controlled fixtures. Internet routes, client decode and actual household throughput affect end-user buffering.

## Reproduce

Put FFmpeg and ffprobe on PATH and install psutil in the test Python environment for the sampler. The scripts use generated temporary media. They leave studio settings and channels alone.

```sh
python scripts/benchmark_broadcast.py
python scripts/benchmark_broadcast.py --public
python scripts/benchmark_live_http.py
python scripts/benchmark_live_http.py --fault
python -m unittest discover -s tests -p test_broadcast_alpha.py -v
```

For the HTTP baseline, export the earlier Public_ChannelForge.py and pass its path with `--module`. Use a separate checkout to run the encoder benchmark against the baseline. Windows and Linux each passed all six broadcast tests. They cover missing files, replacement episodes shorter than the scheduled program, a Plex credential outage, overload, shutdown before the first playlist, two actual viewers, encoder death, two program handoffs and idle cleanup. FFprobe checks segment codecs, the leading keyframe and increasing video DTS. FFmpeg decodes successive output segments.
