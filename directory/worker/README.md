# Hosted edge directory

This Cloudflare Worker provides the Creator Directory. Each studio has its own KV record, so creators can publish at the same time. The public catalog reads those records in batches. A rotating two-minute health check temporarily hides studios after two failed endpoint checks, and healthy studios reappear automatically.

Deployment requires either the `channelforge.tv` zone or a stable `workers.dev` hostname in a Cloudflare account, plus a KV namespace. The repository includes `.github/workflows/deploy-directory.yml`; add these GitHub repository secrets and run **Deploy global Creator Directory**:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_KV_NAMESPACE_ID`

For a manual deployment of the existing directory, run `npx wrangler deploy` from this folder with access to the configured Cloudflare account. The installed clients use [the public directory](https://channelforge-directory.fresh-anorak.workers.dev/v1/catalog).

Creators still host their own streams. The directory hosts discovery and health state, not copyrighted video or Plex credentials.
