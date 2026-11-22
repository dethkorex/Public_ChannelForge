# Hosted edge directory

This Cloudflare Worker is the production-oriented Creator Directory. Once deployed, no personal computer needs to run the directory service. KV stores registrations and a two-minute scheduled health check temporarily removes creators after two failed XMLTV/M3U checks. Healthy endpoints automatically reappear.

Deployment requires a Cloudflare account and a KV namespace:

1. Run `npx wrangler kv namespace create CREATORS`.
2. Put the returned ID in `wrangler.toml`.
3. Run `npx wrangler deploy`.
4. Point `directory.channelforge.tv` at the Worker, or replace the client build-time directory URL.

Creators still host their own streams. The directory hosts discovery and health state, not copyrighted video or Plex credentials.
