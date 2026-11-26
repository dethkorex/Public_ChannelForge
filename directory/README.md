# ChannelForge Creator Directory

This service is required for cross-house ChannelForge TV discovery. It lists creators who publish their ChannelForge XMLTV and M3U endpoints and never receives Plex credentials, local paths, media files, or ChannelForge configuration backups.

Run locally with `python server.py`. Production deployments must use HTTPS behind a reverse proxy and should add moderation, rate limiting, abuse reporting, database backups, and terms/privacy pages before accepting public registrations.

Endpoints:

- `GET /v1/catalog`
- `POST /v1/creators`
- `PUT /v1/creators/<id>` with `Authorization: Bearer <creator key>`
- `DELETE /v1/creators/<id>` with the same authorization

The reference service negotiates authenticated `POST /v1/creators/<id>/heartbeat` and reports availability independently of persisted registration. Set `CHANNELFORGE_DIRECTORY_GRACE_SECONDS` to adjust the heartbeat grace period (default 360 seconds). Legacy publications that never negotiated a heartbeat continue to rely on their actual media endpoint.
