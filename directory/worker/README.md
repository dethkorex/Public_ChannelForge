# Hosted edge directory

The SQLite-backed StudioIndex stores public registrations, hashed owner keys, heartbeats and probe leases. The migration imports existing KV registrations in pages of 100 and retains their IDs. A failed health probe changes availability while the studio remains registered. Explicit unpublish removes its entry. A catalog refresh carries `registrations_complete` so clients can preserve cached registrations while migration is underway.

Creator negotiates authenticated `POST /v1/creators/<id>/heartbeat` on publication and sends its broadcast state once per minute. Initial registration verifies the public channel status or playlist even when the movie vault is unavailable. Set `OFFLINE_AFTER_SECONDS` to adjust the failed-probe grace period (default 360, range 60–86400). A valid heartbeat wakes an offline route for rechecking. Older Creators remain supported by endpoint probes.

Run `npm ci` and `npm test` in this folder for the isolated Worker and SQLite interruption tests. See [verification and rollback](../../docs/alpha4-directory.md) for baseline comparisons and downgrade instructions.

Deployment requires either the `channelforge.tv` zone or a stable `workers.dev` hostname in a Cloudflare account, plus a KV namespace. The repository includes `.github/workflows/deploy-directory.yml`; add these GitHub repository secrets and run **Deploy global Creator Directory**:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_KV_NAMESPACE_ID`

For a manual deployment of the existing directory, run `npx wrangler deploy` from this folder with access to the configured Cloudflare account. The installed clients use [the public directory](https://channelforge-directory.fresh-anorak.workers.dev/v1/catalog).

Creators still host their own streams. The directory hosts discovery and health state, not copyrighted video or Plex credentials.
