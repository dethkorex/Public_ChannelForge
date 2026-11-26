# ChannelForge 4.0 alpha playback results

The baseline was 3.6.2. Discovery, first picture and uninterrupted playback measure different parts of a viewer’s experience. There is no measured all-platform buffering percentage.

| Platform and test | Baseline | Alpha | Result |
|---|---:|---:|---|
| Physical Fire TV, studio address changed during playback, two 60-second trials per build | 40.905 seconds stalled per minute | 1.833 seconds stalled per minute | 95.5% less stall time. Both alpha trials recovered automatically. |
| Physical Fire TV, healthy connection, three trials per build | 2.033 seconds to first frame | 1.763 seconds | 13.3% faster startup. Small presentation stalls rose from 1.371 to 1.504 seconds per minute. |
| Windows Creator, two simultaneous HTTP viewers, forced encoder loss | Both viewers received HTTP 410 and starved for about 7.2 seconds | Both continued with zero measured starvation | Measured on loopback. On the healthy run both versions also recorded zero starvation. |
| Windows desktop decoder comparison, median of three seven-second samples per engine | WebView2 first frame 0.715 seconds, 595.7 MiB peak process-tree RSS | libVLC first frame 0.684 seconds, approximately 141 MiB | 4.3% faster median first frame in the local fixture. One cold VLC run took 2.348 seconds. |
| Linux desktop decoder comparison under WSL/Xvfb, same fixture | WebKit first frame 1.341 seconds, 842.1 MiB | libVLC first frame 0.414 seconds, 438.1 MiB | 69.1% faster median first frame in this environment. Physical Linux GPU and WAN results may differ. |
| Roku | No physical measurements | No physical measurements | Decoder stalls and frame delivery require device testing. |

Desktop decoder comparisons describe engine choice on the same clip. They do not establish a buffering improvement for every Windows or Linux viewer. In particular, the Fire TV measurements used a controlled network fixture and did not measure playback over an intercity Internet connection.

Reproduction and limitations are in [Fire TV](alpha4-fire.md), [Creator broadcasting](alpha4-broadcast.md), and [the public directory](alpha4-directory.md).
