# Community media relay

Run this opt-in relay on an existing host with a public HTTPS address and enough outbound bandwidth for its viewers. Each Studio publishes a stable media URL and makes an outbound SSH connection from its home network. No creator needs to open an inbound home-router port, and viewer apps use ordinary HTTPS HLS.

## Managed enrollment

When a new Studio publishes on the ChannelForge directory, it generates its SSH key and directory identity locally. The directory asks the enrollment service to reserve a slot for that identity. Studio checks that it owns the assigned media route and that it can transfer a test sample before its channels appear in the directory. On retry, the same installation keeps its identity and assignment. The broker limits unconfirmed reservations so existing publishers can still enroll. An operator can retire abandoned reservations after verifying that their SSH sessions have ended. When an existing Studio migrates, a failed check keeps its previous registration.

Provision a bounded pool of Studio slots. Each slot must have a separate public HTTPS media origin and a separate loopback port in the media relay. Provide IPv4 and IPv6 viewer reachability for each origin before assigning the slot. Viewer addresses and Studio tunnel ingress are independent. Use an HTTPS origin a viewer can reach even when its Studio uses the IPv4 tunnel fallback.

Use `managed_config.py` to generate both the slot inventory and the matching scoped NGINX routes from the VPS hostname and SSH host public key. Install the generated files at `/etc/channelforge-relay/slots.json` and `/etc/channelforge-relay/managed-nginx.conf`. The dedicated managed relay unit serves those routes on loopback port 18097. Expose the listener with `tailscale funnel --bg --https=443 http://127.0.0.1:18097` and leave any existing HTTP relay in place during migration. Keep generated slot URLs and ports stable across deployments.

Studio prefers direct VPS ingress, then tries the configured VPS Tailscale Funnel. If both fail and the current published origin no longer answers, an already published Studio on the ChannelForge directory can try its own localhost.run tunnel as a last resort. The legacy route must pass identity and basic transfer checks before it replaces a directory listing. Studio continues retrying the preferred relay while serving viewers on a verified fallback. Direct VPS ingress and a Funnel on that VPS share the same host, so a VPS outage takes both routes offline.

Create a dedicated reverse-forwarding SSH account. Configure `AllowTcpForwarding remote`, `GatewayPorts no`, `PermitTTY no`, `X11Forwarding no`, `AllowAgentForwarding no`, `PasswordAuthentication no`, and disable other interactive authentication for that account. Match it to a dedicated `AuthorizedKeysFile` owned by the relay service account or root. The enrollment service writes per-key `permitlisten` restrictions and forces `/usr/sbin/nologin`. Keep it separate from VPS administrator accounts and their SSH keys.

The included `publisher-sshd.conf` and `channelforge-relay-publisher.service` configure the publisher listener on IPv6 port 18088 and loopback. Install the config at `/etc/channelforge-relay/publisher-sshd.conf`, create the `channelforge-relay` system account with a disabled shell, and validate the file with `sshd -t -f /etc/channelforge-relay/publisher-sshd.conf` before enabling the service. Allow inbound IPv6 TCP 18088 for direct Studio connections. Use the loopback listener as the Tailscale Funnel target for IPv4 creator connections. The publisher service uses its own configuration and the enrollment service’s key file.

The enrollment service takes a JSON array of Studio profiles, a SQLite database path, and the dedicated authorized-keys path. Use `channelforge-enrollment.service` after installing `enrollment.py` under `/opt/channelforge-relay`. Its state directory owns the allocation database and the relay account's key file.

```sh
python3 relay/enrollment.py /etc/channelforge-relay/slots.json \
  /var/lib/channelforge-enrollment/slots.sqlite \
  /var/lib/channelforge-enrollment/authorized_keys
```

Set `CHANNELFORGE_RELAY_ENROLL_TOKEN` on this service and set the same random 32-byte hexadecimal value as the Worker secret `RELAY_ENROLL_TOKEN`. Point `RELAY_ENROLL_URL` at a TLS reverse proxy location that forwards only to `http://127.0.0.1:18089/enroll`. Keep the broker bound to loopback and its secret out of app packages and access logs. Persist the SQLite directory across deployments. Never reassign occupied loopback ports or clear the database while Studio tunnels remain connected.

Deploy the enrollment service and directory Worker before enabling Studio key recovery. Recovery uses the existing directory registration and its assigned VPS slot. A replacement can authenticate for five minutes while Studio verifies the forward. After Studio reconnects with the replacement and the enrollment service confirms its origin, the broker removes the previous public key from the publisher's authorized keys. A network failure with a valid key leaves the assignment and key in place.

For IPv4-only Studio hosts, run Tailscale on the VPS or a dedicated relay gateway. Expose the dedicated, restricted SSH listener with TLS on a Funnel port. The Studio validates the public TLS certificate, then OpenSSH verifies the same pinned SSH host key it uses on the direct route. Studio tries the direct path first and uses the Funnel when it cannot establish the direct forward. Neither Studio nor viewers install a Tailscale client for this route. The directory asks the broker to verify the Studio origin and test sample, so a Studio on IPv4 does not need to open the IPv6 media origin itself. The configured media origins still need separate viewer reachability checks. [Tailscale Funnel TLS forwarding](https://tailscale.com/docs/reference/tailscale-cli/funnel#use-a-tcp-forwarder)

```sh
tailscale funnel --bg --tls-terminated-tcp=8443 tcp://127.0.0.1:18088
```

Use port `18088` here for the dedicated SSH listener. Keep administrator SSH and the enrollment service off this Funnel. Add `funnel_host` with the VPS's Funnel hostname and `funnel_port` with `8443` to each assigned Studio profile. Omit those fields for a direct-only relay. Verify that the VPS still serves the existing Studio before applying a new configuration.

## Pilot setup

1. Assign an existing DNS hostname to each participating Studio. Point the names at the relay and provision HTTPS for them using your existing frontend or a certificate covering the names.
2. On the relay, assign each Studio a different loopback port such as 18091. Give the creator its own SSH key and limit that key to the assigned reverse forward. Keep the Studio ports bound to loopback. Configure the relay host and SSH host key through the operator's normal trusted channel.
3. Configure Studio to manage the forward, or run it manually using the instructions below.

### Studio-managed connection

In the Studio data directory, generate a dedicated client key named `community-relay-identity`. The automatic connection uses a key without a passphrase. Give the operator the corresponding `.pub` file. The operator can restrict that key with `restrict,port-forwarding,permitlisten="127.0.0.1:18091"` and configure the relay account with `AllowTcpForwarding remote`, `GatewayPorts no`, and a forced command that denies interactive shell use.

```sh
ssh-keygen -t ed25519 -f community-relay-identity
```

In the same data directory, save `community-relay.json` with the values from your relay operator.

```json
{
  "origin": "https://studio-a.example.org",
  "ssh_host": "relay.example.org",
  "ssh_user": "relay-user",
  "ssh_port": 22,
  "remote_port": 18091,
  "host_key": "ssh-ed25519 OPERATOR_SUPPLIED_BASE64_HOST_KEY"
}
```

Enter the same HTTPS address under **Sources & sharing → Custom delivery and directory → Custom HTTPS media origin** and publish. Studio verifies the pinned SSH host key and checks that the public route reaches its read-only media listener. Before updating the directory, it requires the relay to transfer a 512 KiB synthetic sample within one second. This checks whether the route can carry a peak-rate 720p live segment without starting an encoder or sending library content. Studio retains the existing publication if the check fails. It reconnects on the same hostname while Studio runs. Taking the studio offline releases the forward. [OpenSSH authorized key settings](https://man.openbsd.org/sshd.8#AUTHORIZED_KEYS_FILE_FORMAT)

For relay redundancy, add a `backup` object with the same fields inside `community-relay.json`. Use a separate relay host and HTTPS address so the same outage cannot take both routes down. Give the backup operator the Studio's existing public key and have them authorize the backup relay's assigned loopback port. Studio uses one managed forward during steady operation. It proves the backup route reaches the same Studio and updates the existing directory registration before retiring the prior forward. A recovered primary stays available for the next handoff while the accepted route remains healthy. Both routes serve the same Studio broadcast and Rewind catalog.

### Manual connection

For manual operation, use the existing custom HTTPS origin without a matching `community-relay.json`. Start the read-only listener on Studio's default port 8790. Run the forward using your verified relay host key and a process manager.

```sh
ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=2 \
  -R 127.0.0.1:18091:127.0.0.1:18790 relay-user@relay.example.org
```

### Relay host

Generate the NGINX configuration and run it under the relay operator's service manager. Create a writable relay prefix containing a `logs` directory. Reuse the same prefix for restarts. By default NGINX listens on loopback for an existing HTTPS frontend. That frontend must preserve the original Host header.

```sh
python3 relay/community.py /srv/channelforge/nginx.conf \
  --studio relay.example.org/studio/1111111111111111:18091 \
  --studio relay.example.org/studio/2222222222222222:18092
nginx -t -p /srv/channelforge/ -c /srv/channelforge/nginx.conf
```

For direct HTTPS, pass `--listen-port 443 --certificate /path/to/fullchain.pem --key /path/to/privkey.pem`. Keep certificate renewal managed by the relay operator.

Each scoped URL uses its own loopback tunnel and cache namespace. Use the identical `https://relay.example.org/studio/<slot>` base in the enrollment profile. Existing root-only routes such as `studio-a.example.org:18091` are also supported.

Playback diagnostics use a fresh playback ID for each tune or movie. Retries keep the same ID so a viewer report can be matched to Studio's local manifest trace. The relay reports cache status and time waiting for upstream response headers. Studio reports time spent preparing its response. Viewer reports also capture transfer times and rebuffering. Cache hits do not count toward upstream or Studio timing. The maximum times can come from separate requests, so compare them as clues about each stage. Roku reports player progress and buffer time, which can be matched to Studio by playback ID.

### Keep the relay running on Ubuntu

Use DNS-only records pointing to the VPS for pilot Studio hostnames. Obtain a trusted certificate that covers those hostnames before starting the HTTPS listener. With NGINX installed and the generated configuration at `/etc/channelforge-relay/nginx.conf`, install the service:

```sh
sudo install -m 0644 relay/channelforge-relay.service /etc/systemd/system/channelforge-relay.service
sudo systemctl daemon-reload
sudo systemctl enable --now channelforge-relay.service
sudo systemctl status channelforge-relay.service
```

The service keeps its bounded cache in `/var/lib/channelforge-relay` and validates the configuration before starting or reloading. Run `sudo systemctl reload channelforge-relay.service` after updating the configuration or renewing the certificate.

Verify `/api/public/origin`, `/api/public/channels?guide=brief` and Rewind Cinema through the assigned hostname.

The relay forwards an allowlist of read-only requests. It leaves guide data and manifests fresh and coalesces concurrent requests for immutable media segments. Each Studio has a separate cache namespace. Viewer traffic still consumes the volunteer relay’s bandwidth. Keep a direct HTTPS publishing option for creators with their own working route.

### Keep Tailscale Funnel available during cutover

Leave the existing Funnel mapping on the same Studio media port. Before republishing Studio through the VPS, check that both HTTPS addresses reach the same read-only media listener and that the VPS can deliver the live and Rewind samples. Publish the VPS hostname from the existing Studio profile so viewers keep the same channel identities.

If the VPS fails, republish the existing Funnel HTTPS address from **Sources & sharing → Custom delivery and directory**. Verify that the directory changes to that address and that a viewer on another network can tune in. This is an operator-controlled fallback. Automatic failover in `community-relay.json` uses a second SSH relay profile.

For a temporary VPS pilot, add `--https-origin relay.example.org=studio.example.org` to the NGINX configuration command. The relay validates the existing upstream's HTTPS certificate, caches immutable segments, and rewrites its guide, playlist, and catalog URLs to the relay hostname. If the upstream is a Funnel, playback through either hostname still depends on that Funnel. Leave the registered Studio address in the directory so ordinary clients and IPv4-only viewers can reach the Studio.

Guide discovery uses the registered Studio address. With an explicit relay override, the player tries the configured relay for live TV and movies. If it fails, the player retries the registered address and retains the movie position. Confirm both HTTPS endpoints return the same `/api/public/origin` instance before configuring an override.

To exercise this route in a public-channel candidate, pass `-PchannelForgeRelayRoute=studio.example.org=relay.example.org` for Android. In an isolated desktop packaging checkout, add `assets/playback-relay.json` with `{"relay_route":"studio.example.org=relay.example.org"}`. Only the exact registered Studio host is eligible for this override. Leave the build option and file unset if there is no verified pair to include.

Reproduce the private HTTPS test using two different synthetic MPEG-TS segments.

```sh
python3 scripts/benchmark_community_relay.py --nginx /usr/sbin/nginx first.ts second.ts
```
