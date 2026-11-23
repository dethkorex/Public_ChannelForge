# Hosted edge directory

This Cloudflare Worker is the production-oriented Creator Directory. Once deployed, no personal computer needs to run the directory service. KV stores registrations and a two-minute scheduled health check temporarily removes creators after two failed XMLTV/M3U checks. Healthy endpoints automatically reappear.

Deployment requires the `channelforge.tv` zone in a Cloudflare account and a KV namespace. The repository includes `.github/workflows/deploy-directory.yml`; add these GitHub repository secrets and run **Deploy global Creator Directory**:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_KV_NAMESPACE_ID`

For a manual deployment:

1. Run `npx wrangler kv namespace create CREATORS`.
2. Put the returned ID in `wrangler.toml`.
3. Run `npx wrangler deploy`.
4. The configured custom-domain route publishes the Worker at `https://directory.channelforge.tv`.

Creators still host their own streams. The directory hosts discovery and health state, not copyrighted video or Plex credentials.
