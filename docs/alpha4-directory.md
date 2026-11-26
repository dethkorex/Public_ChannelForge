# Alpha 4.0 discovery and registration verification

The directory now retains supported published studios across temporary origin failures and process restarts. Explicit unpublish removes the studio. An incompatible Creator is omitted while its stored identity remains available for recovery when it upgrades. Channel IDs already persist on normal saves. A legacy channel that lacks an ID receives one when first loaded and keeps it on subsequent reads.

## Reproduced causes and measurements

The v3.6.2 Worker intentionally excluded offline KV records from every catalog after two failed probes. Its published membership index also deleted a studio after 30 days without a good probe; the old one-time migration could already have finished, leaving no scheduled check to rediscover that studio. Initial registration depended on three healthy endpoints, so movie-vault or guide failures could block an otherwise usable live studio. A lost first POST response left a Creator without the key to the slug it just registered.

The benchmark runs the real Worker and SQLite Durable Object in local workerd with a simulated studio HTTP origin. The outage sequence and origins are identical. Fifty uncached, single-studio catalog requests are timed inside each run. Network RTT and CDN caching are excluded.

| Measurement | v3.6.2 at eec8838 | Alpha |
|---|---:|---:|
| Origin requests to register a healthy studio | 3 | 1 |
| Catalog count after two failed probes | 0 | 1, reconnecting |
| Catalog count after origin recovery | 1 | 1 |
| Mean local catalog latency, one run | 3.56 ms | 2.54 ms |

Reproduce from the repository root with `npm ci --prefix directory/worker` followed by `node scripts/benchmark_directory.mjs`. The latency figure describes the local fixture only.

## Implementation and checks

- StudioIndex coordinates unique slugs and stable registrations in SQLite. Probe results include a generation token to discard results for a replaced URL. Authenticated heartbeats write one bounded status row and never start encoders. Existing Worker and SQLite registrations keep their IDs and hashed owner keys. Clients can reclaim a lost registration response using the random private key saved before the request.
- The catalog exposes states for standby, starting, broadcasting, reconnecting, overloaded and offline. Endpoint success establishes route reachability; heartbeat reports the Creator's broadcast state. A grace period separates a missed probe from an offline route. Probe frequency decreases for offline studios. After a returning Creator heartbeats, the next available probe tests its route.
- Existing Workers KV data imports into SQLite in batches. Reads during import preserve legacy entries and mark incomplete membership with `registrations_complete: false`. Known studios retain membership while their endpoint is unreachable. Empty legacy pointers may be removed after 30 days. Completed migrations avoid catalog-time KV reads.
- Creator negotiates heartbeat support, so connecting to an older directory does not generate repeated unsupported requests. Diagnostics include the last heartbeat attempt, last success and consecutive failure count. The local status API omits owner keys. A failed publication rolls back only its attempted sharing settings and preserves other concurrent configuration changes.
- `npm test --prefix directory/worker` exercises real workerd, KV and SQLite persistence. The test covers an unavailable vault on registration, duplicate and concurrent registration, failed owner authentication, legacy migration, consecutive failed probes, 31 days of simulated downtime, a full Miniflare instance restart, heartbeat and probe recovery, incompatible then upgraded origin, relocation after a lost registration response, explicit removal and rollback mirroring.
- `python -m unittest discover -s tests -p test_directory.py` exercises HTTP request handling on the Python reference server. It closes and reopens the service with the same SQLite database and verifies the authorized heartbeat, offline state, same-ID recovery and explicit removal. This passed on Windows and Linux. Creator tests in `test_core.py` exercise heartbeat retry cadence, stable credentials, scope of rollback and migration of missing channel IDs.
- A local Wrangler dry run compiled the edge bundle. No production service, deployment, credentials, or viewer configuration was modified by this investigation.

## Rollback and operational limits

The alpha directory remains compatible with older clients. Client rollback can be tested while retaining the alpha directory. For a **directory code rollback after accepting alpha registrations**, keep the existing binding and storage. An operator can set `ROLLBACK_SNAPSHOT` to a new unique snapshot name on the alpha Worker. It temporarily rejects publication changes, completes the legacy import, and copies credentials (hashes only), profiles, slug mappings and availability to the legacy KV layout in batches of five per cron invocation. Inspect the KV JSON record named `rollback:<snapshot name>`. Wait for `complete: true` and verify that the mirrored profiles have propagated before switching to v3.6.2. Leave the SQLite data intact. When canceling the downgrade, remove the maintenance variable. A subsequent snapshot requires a fresh name. The local harness verified that the currently published route and owner hash are mirrored without a production deployment.

The reference server's SQLite migration adds columns. A backup of its existing database before upgrading gives an operator a restore point. Keep the current database when reverting the reference server executable to retain new rows.

Fresh registration requires an initially reachable public status endpoint or playlist. Both can be temporarily unavailable during a later update without erasing the registration. Total studio count and cron scheduling determine the time to the next external probe. These local measurements do not establish CDN propagation latency, host uptime, video startup, or sustained playback on a remote household's device.
