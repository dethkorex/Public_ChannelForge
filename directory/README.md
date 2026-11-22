# ChannelForge Creator Directory

This optional service lists creators who deliberately publish their ChannelForge XMLTV and M3U endpoints. It never receives Plex credentials, local paths, media files, or ChannelForge configuration backups.

Run locally with `python server.py`. Production deployments must use HTTPS behind a reverse proxy and should add moderation, rate limiting, abuse reporting, database backups, and terms/privacy pages before accepting public registrations.

Endpoints:

- `GET /v1/catalog`
- `POST /v1/creators`
- `PUT /v1/creators/<id>` with `Authorization: Bearer <creator key>`
- `DELETE /v1/creators/<id>` with the same authorization
