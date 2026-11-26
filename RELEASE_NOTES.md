# ChannelForge 4.0 alpha 1

- Fire TV can reconnect when a studio changes its public address during a show. It also remembers the guide when a studio temporarily loses its connection.
- Windows and Linux viewers retain previously loaded guide entries during outages. Studios that explicitly unpublish are removed when the directory confirms it.
- Creator shares a live broadcast across viewers, recovers from an interrupted encoder, and can deliver compatible movies without re-encoding the video. Resource limits help busy studios stay responsive.
- Closing the Windows Creator window stops its preview audio. Choosing **Exit and stop channels** in the tray also shuts down its local server, encoder and WebView2 processes.
- Desktop playback diagnostics are available from Creator’s System page and the TV app’s Updates page. Use **Copy report** to share a troubleshooting snapshot.

On a physical Fire TV Stick, both trials recovered when the studio address changed. Stall time in that specific test fell from 40.9 to 1.8 seconds per minute. On a healthy test connection, median startup improved from 2.03 to 1.76 seconds. Brief stalls on that connection persisted.
