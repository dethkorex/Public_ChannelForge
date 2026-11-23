# Hosted edge directory

This Cloudflare Worker provides the Creator Directory. Each studio has its own KV record. A SQLite-backed Durable Object tracks membership and schedules probes. Viewer scans share a cached catalog and read studio records in batches. Unreachable studios are checked progressively less often. After 30 days offline, their directory membership expires. Publishing again restores it.

Deployment requires either the `channelforge.tv` zone or a stable `workers.dev` hostname in a Cloudflare account, plus a KV namespace. The repository includes `.github/workflows/deploy-directory.yml`; add these GitHub repository secrets and run **Deploy global Creator Directory**:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_KV_NAMESPACE_ID`

For a manual deployment of the existing directory, run `npx wrangler deploy` from this folder with access to the configured Cloudflare account. The installed clients use [the public directory](https://channelforge-directory.fresh-anorak.workers.dev/v1/catalog).

Creators still host their own streams. The directory hosts discovery and health state, not copyrighted video or Plex credentials.
