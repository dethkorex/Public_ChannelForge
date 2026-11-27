import { DurableObject } from "cloudflare:workers";
import { channelNumberKey } from "./index.js";
export { default } from "./index.js";

// Membership survives a failed probe. Health observations are fenced so a slow
// response from an obsolete tunnel cannot mark its replacement offline.
export class StudioIndex extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.storage = ctx.storage;
    this.sql = ctx.storage.sql;
    this.sql.exec("CREATE TABLE IF NOT EXISTS studios (key TEXT PRIMARY KEY, due INTEGER NOT NULL, seen INTEGER NOT NULL, misses INTEGER NOT NULL)");
    this.sql.exec("CREATE INDEX IF NOT EXISTS studios_due ON studios(due, key)");
    this.sql.exec("CREATE TABLE IF NOT EXISTS migration (id INTEGER PRIMARY KEY, retry INTEGER)");
    this.sql.exec(`CREATE TABLE IF NOT EXISTS profiles (
      key TEXT PRIMARY KEY, slug TEXT UNIQUE, profile TEXT NOT NULL, generation TEXT NOT NULL,
      heartbeat INTEGER NOT NULL DEFAULT 0, reported TEXT NOT NULL DEFAULT 'standby',
      first_failure INTEGER NOT NULL DEFAULT 0, reachable INTEGER NOT NULL DEFAULT 0
    )`);
    this.sql.exec("CREATE TABLE IF NOT EXISTS removed (key TEXT PRIMARY KEY, at INTEGER NOT NULL)");
    this.sql.exec("CREATE TABLE IF NOT EXISTS registry_import (id INTEGER PRIMARY KEY, cursor TEXT, due INTEGER NOT NULL)");
    this.sql.exec("CREATE TABLE IF NOT EXISTS channel_numbers (key TEXT NOT NULL, channel_id TEXT NOT NULL, number TEXT NOT NULL, PRIMARY KEY(key,channel_id,number))");
    this.sql.exec("CREATE INDEX IF NOT EXISTS channel_number_lookup ON channel_numbers(number)");
    this.sql.exec("CREATE TABLE IF NOT EXISTS managed_channel_numbers (key TEXT PRIMARY KEY)");
    this.storage.transactionSync(() => {
      if (this.sql.exec("SELECT 1 FROM migration WHERE id=2").toArray().length) return;
      for (const row of this.sql.exec("SELECT key,profile FROM profiles").toArray())
        this.rememberLegacyNumbers(row.key, JSON.parse(row.profile).channel_inventory);
      this.sql.exec("INSERT INTO migration VALUES(2,0)");
    });
  }

  rememberLegacyNumbers(key, inventory) {
    if (!Array.isArray(inventory) || this.sql.exec("SELECT 1 FROM managed_channel_numbers WHERE key=?", key).toArray().length) return;
    for (const channel of inventory) {
      const number = channelNumberKey(channel.number);
      if (number && number.length <= 12)
        this.sql.exec("INSERT OR IGNORE INTO channel_numbers VALUES(?,?,?)", key, channel.id, number);
    }
  }

  channelNumberOwner(number) {
    return Boolean(this.sql.exec("SELECT 1 FROM channel_numbers WHERE number=? LIMIT 1", number).toArray().length);
  }

  applyChannelNumbers(key, channels, mode) {
    const own = new Set(this.sql.exec("SELECT channel_id,number FROM channel_numbers WHERE key=?", key).toArray()
      .map(row => JSON.stringify([row.channel_id,row.number])));
    const incoming = new Set();
    for (const channel of channels) {
      if (!own.has(JSON.stringify([channel.id,channel.number])) &&
          (incoming.has(channel.number) || this.channelNumberOwner(channel.number))) return "number:" + channel.number;
      incoming.add(channel.number);
    }
    if (mode === "sync") this.sql.exec("DELETE FROM channel_numbers WHERE key=?", key);
    for (const channel of channels)
      this.sql.exec("INSERT OR IGNORE INTO channel_numbers VALUES(?,?,?)", key, channel.id, channel.number);
    this.sql.exec("INSERT OR IGNORE INTO managed_channel_numbers VALUES(?)", key);
    return "ok";
  }

  channelNumbers(key, channels, mode) {
    return this.storage.transactionSync(() => {
      const profile = this.get(key);
      if (!profile) return "missing";
      const result = this.applyChannelNumbers(key, channels, mode);
      if (result === "ok" && mode === "sync" && Array.isArray(profile.channel_inventory)) {
        const ids = new Set(channels.map(channel => channel.id));
        profile.channel_inventory = profile.channel_inventory.filter(channel => ids.has(channel.id));
        this.sql.exec("UPDATE profiles SET profile=?,generation=? WHERE key=?", JSON.stringify(profile), crypto.randomUUID(), key);
      }
      return result;
    });
  }

  // All methods are synchronous SQLite transactions within this object.
  get(key, withGeneration = false) {
    const row = this.sql.exec("SELECT profile,generation FROM profiles WHERE key=?", key).toArray()[0];
    if (!row) return null;
    const profile = JSON.parse(row.profile);
    return withGeneration ? {profile, generation:row.generation} : profile;
  }

  put(profile, now, reachable, creating, expectedGeneration, numbers = null) {
    return this.storage.transactionSync(() => this.writeProfile(profile, now, reachable, creating, expectedGeneration, numbers));
  }

  writeProfile(profile, now, reachable, creating, expectedGeneration, numbers) {
    const key = "creator:" + profile.id;
    const existing = this.get(key, true);
    if (existing && creating) return existing.profile.key_hash === profile.key_hash ? "replay" : "conflict";
    if (!existing && !creating) return "missing";
    if (expectedGeneration && existing?.generation !== expectedGeneration) return "changed";
    const owner = this.sql.exec("SELECT key FROM profiles WHERE slug=?", profile.slug).toArray()[0];
    if (owner && owner.key !== key) return "conflict";
    if (numbers) {
      const result = this.applyChannelNumbers(key, numbers, "sync");
      if (result !== "ok") return result;
    } else {
      this.rememberLegacyNumbers(key, profile.channel_inventory);
    }
    const encoded = JSON.stringify(profile);
    const routeChanged = existing && ["guide_url", "status_url", "playlist_url", "vault_url"]
      .some(field => profile[field] !== existing.profile[field]);
    this.sql.exec(`INSERT INTO profiles(key,slug,profile,generation,reachable) VALUES(?,?,?,?,?)
      ON CONFLICT(key) DO UPDATE SET slug=excluded.slug, profile=excluded.profile,
        generation=excluded.generation, reachable=excluded.reachable, first_failure=0,
        heartbeat=CASE WHEN ? OR reported='offline' THEN 0 ELSE heartbeat END,
        reported=CASE WHEN ? OR reported='offline' THEN 'standby' ELSE reported END`,
      key, profile.slug, encoded, crypto.randomUUID(), +reachable, Number(Boolean(routeChanged)), Number(Boolean(routeChanged)));
    this.sql.exec(`INSERT INTO studios VALUES(?,?,?,0) ON CONFLICT(key)
      DO UPDATE SET due=excluded.due,seen=excluded.seen,misses=0`, key, now, reachable ? now : 0);
    return "ok";
  }

  importProfile(profile, now) {
    // Only keys in the membership index may be imported. A concurrent DELETE
    // removes that key before a late probe or KV read can restore the studio.
    const key = "creator:" + profile.id;
    if (!this.sql.exec("SELECT 1 FROM studios WHERE key=?", key).toArray().length || this.get(key)) return;
    this.put(profile, now, Boolean(profile.online), true);
  }

  remove(key) {
    this.sql.exec("DELETE FROM channel_numbers WHERE key=?", key);
    this.sql.exec("DELETE FROM managed_channel_numbers WHERE key=?", key);
    this.sql.exec("DELETE FROM profiles WHERE key=?", key);
    this.sql.exec("DELETE FROM studios WHERE key=?", key);
    this.sql.exec("INSERT OR REPLACE INTO removed SELECT ?,? WHERE NOT EXISTS(SELECT 1 FROM registry_import WHERE id=1 AND cursor IS NULL)", key, Math.floor(Date.now()/1000));
  }

  entries() {
    const rows = this.sql.exec(`SELECT s.key,s.seen,s.misses,p.profile,p.heartbeat,p.reported,p.reachable,p.first_failure
      FROM studios s LEFT JOIN profiles p ON p.key=s.key ORDER BY s.key`).toArray();
    const migrated = Boolean(this.sql.exec("SELECT 1 FROM registry_import WHERE id=1 AND cursor IS NULL").toArray().length);
    return {rows, migrated};
  }

  // A new bounded pass imports KV registrations that older versions dropped
  // from the probe index after 30 days. The cursor advances only after success.
  claimImport(now) {
    const row = this.sql.exec("SELECT cursor,due FROM registry_import WHERE id=1").toArray()[0];
    if (row && (row.cursor === null || row.due > now)) return null;
    const cursor = row?.cursor || "";
    this.sql.exec("INSERT OR REPLACE INTO registry_import VALUES (1,?,?)", cursor, now + 300);
    return cursor;
  }

  importLegacy(keys, cursor, now) {
    for (const key of keys) this.sql.exec(`INSERT OR IGNORE INTO studios
      SELECT ?,0,?,0 WHERE NOT EXISTS(SELECT 1 FROM removed WHERE key=?)`, key, now, key);
    this.sql.exec("INSERT OR REPLACE INTO registry_import VALUES (1,?,?)", cursor, now);
    if (cursor === null) this.sql.exec("DELETE FROM removed");
  }

  // An operator can explicitly freeze publication and prepare a KV snapshot
  // for a downgrade. Normal alpha operation never writes heartbeats to KV.
  rollbackBatch(after, limit) {
    return this.sql.exec("SELECT key,profile FROM profiles WHERE key>? ORDER BY key LIMIT ?", after, limit).toArray();
  }

  unresolved(key, now) {
    // A legacy key can have no value during KV propagation. Only prune an
    // orphan index pointer after a month and once the initial import finished.
    this.sql.exec(`UPDATE studios SET due=? WHERE key=? AND NOT EXISTS
      (SELECT 1 FROM profiles WHERE key=studios.key)`, now + 3600, key);
    this.sql.exec(`DELETE FROM studios WHERE key=? AND seen<? AND NOT EXISTS
      (SELECT 1 FROM profiles WHERE key=studios.key) AND EXISTS
      (SELECT 1 FROM registry_import WHERE id=1 AND cursor IS NULL)`, key, now-30*86400);
  }

  heartbeat(key, now, state) {
    this.sql.exec(`UPDATE profiles SET heartbeat=?,reported=? WHERE key=?`, now, state, key);
    // Wake a long-offline route for a probe as soon as its Creator returns.
    this.sql.exec("UPDATE studios SET due=MIN(due,?) WHERE key=? AND misses>0", now, key);
  }

  due(now, limit) {
    const rows = this.sql.exec(`SELECT s.key,p.generation FROM studios s LEFT JOIN profiles p
      ON p.key=s.key WHERE due<=? ORDER BY due,s.key LIMIT ?`, now, limit).toArray();
    for (const {key} of rows) this.sql.exec("UPDATE studios SET due=? WHERE key=?", now+120, key);
    return rows;
  }

  checked(key, generation, healthy, observed, now) {
    const row = this.sql.exec("SELECT generation,profile FROM profiles WHERE key=?", key).toArray()[0];
    if (!row || row.generation !== generation) return;
    const profile = JSON.parse(row.profile);
    if (observed.version) profile.creator_version = observed.version;
    if (observed.cinemaProtocol) profile.cinema_protocol = observed.cinemaProtocol;
    if (healthy) {
      if (observed.inventory) {
        this.rememberLegacyNumbers(key, observed.inventory);
        if (this.sql.exec("SELECT 1 FROM managed_channel_numbers WHERE key=?", key).toArray().length) {
          const ids = new Set(this.sql.exec("SELECT channel_id FROM channel_numbers WHERE key=?", key).toArray().map(row => row.channel_id));
          profile.channel_inventory = observed.inventory.filter(channel => ids.has(channel.id));
        } else {
          profile.channel_inventory = observed.inventory;
        }
        profile.inventory_verified_at = now;
      }
      this.sql.exec("UPDATE studios SET due=?,seen=?,misses=0 WHERE key=?", now+120, now, key);
      this.sql.exec("UPDATE profiles SET profile=?,reachable=1,first_failure=0 WHERE key=?", JSON.stringify(profile), key);
    } else {
      this.sql.exec(`UPDATE studios SET due=?+MIN(21600,120*(1 << MIN(8,misses))),
        misses=MIN(9,misses+1) WHERE key=?`, now, key);
      this.sql.exec("UPDATE profiles SET profile=?,first_failure=CASE WHEN first_failure=0 THEN ? ELSE first_failure END WHERE key=?", JSON.stringify(profile), now, key);
    }
  }
}
