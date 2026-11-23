import { DurableObject } from "cloudflare:workers";
export { default } from "./index.js";

// Only directory membership and probe times live here. Media stays at its studio.
export class StudioIndex extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec("CREATE TABLE IF NOT EXISTS studios (key TEXT PRIMARY KEY, due INTEGER NOT NULL, seen INTEGER NOT NULL, misses INTEGER NOT NULL)");
    this.sql.exec("CREATE INDEX IF NOT EXISTS studios_due ON studios(due, key)");
    this.sql.exec("CREATE TABLE IF NOT EXISTS migration (id INTEGER PRIMARY KEY, retry INTEGER)");
  }

  add(key, now) {
    this.sql.exec("INSERT INTO studios VALUES (?, 0, ?, 0) ON CONFLICT(key) DO UPDATE SET due=0, seen=excluded.seen, misses=0", key, now);
  }

  remove(key) {
    this.sql.exec("DELETE FROM studios WHERE key=?", key);
  }

  keys() {
    return this.sql.exec("SELECT key FROM studios ORDER BY key").toArray().map(row => row.key);
  }

  claimImport(now) {
    const row = this.sql.exec("SELECT retry FROM migration WHERE id=1").toArray()[0];
    if (row && (row.retry === null || row.retry > now)) return false;
    this.sql.exec("INSERT OR REPLACE INTO migration VALUES (1, ?)", now + 3600);
    return true;
  }

  importLegacy(keys, now) {
    for (const key of keys) this.sql.exec("INSERT OR IGNORE INTO studios VALUES (?, 0, ?, 0)", key, now);
    this.sql.exec("INSERT OR REPLACE INTO migration VALUES (1, NULL)");
  }

  due(now, limit) {
    const keys = this.sql.exec("SELECT key FROM studios WHERE due<=? ORDER BY due, key LIMIT ?", now, limit).toArray().map(row => row.key);
    for (const key of keys) this.sql.exec("UPDATE studios SET due=? WHERE key=?", now + 120, key);
    return keys;
  }

  checked(key, healthy, now) {
    if (healthy) {
      this.sql.exec("UPDATE studios SET due=?, seen=?, misses=0 WHERE key=?", now + 120, now, key);
      return;
    }
    // Keep the first retry quick. Long-offline studios consume fewer checks.
    this.sql.exec("UPDATE studios SET due=?+MIN(21600,120*(1 << MIN(8,misses))), misses=MIN(9,misses+1) WHERE key=?", now, key);
    this.sql.exec("DELETE FROM studios WHERE key=? AND seen<?", key, now - 30*86400);
  }
}
