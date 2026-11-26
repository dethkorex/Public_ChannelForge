const json = (value, status = 200) => new Response(JSON.stringify(value), {status, headers:{"content-type":"application/json; charset=utf-8","access-control-allow-origin":"*","cache-control":value.creators && status===200?"public, max-age=30":"no-store"}});
const text = value => String(value || "").trim();
const slugify = value => text(value).toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"").slice(0,60);
const token = () => [...crypto.getRandomValues(new Uint8Array(32))].map(value=>value.toString(16).padStart(2,"0")).join("");
const hash = async value => [...new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(value)))].map(byte=>byte.toString(16).padStart(2,"0")).join("");
const safeUrl = value => {const url=new URL(text(value));if(url.protocol!=="https:"||url.username||url.password)throw new Error("Published endpoints must use public HTTPS.");return url.toString().slice(0,1000)};
const MIN_CREATOR = [3, 5, 19];

function isSupportedCreator(version) {
  const parts = text(version).match(/^v?(\d+)\.(\d+)\.(\d+)$/)?.slice(1).map(Number);
  // Some existing public origins omit the version header. Their guide and
  // playlist still need to pass protocol and reachability checks.
  if (!parts) return true;
  for (let index = 0; index < MIN_CREATOR.length; index++) {
    if (parts[index] !== MIN_CREATOR[index]) return parts[index] > MIN_CREATOR[index];
  }
  return true;
}

function publicCreator(value) {
  const {key_hash, failures, cinema_protocol, ...visible} = value;
  const version = text(visible.creator_version);
  const verified = /^v?\d+\.\d+\.\d+$/.test(version)
    ? isSupportedCreator(version) : cinema_protocol === "paged";
  return {...visible, cinema_supported: Boolean(verified && visible.vault_url)};
}

async function readBody(request) {
  const reader = request.body?.getReader();
  if (!reader) throw new Error("Missing JSON body.");
  const decoder = new TextDecoder();
  let buffer = "", bytes = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 64000) throw new Error("Request exceeds 64 KB.");
      buffer += decoder.decode(value, {stream:true});
    }
  } finally { await reader.cancel(); }
  const result = JSON.parse(buffer + decoder.decode());
  if (!result || typeof result !== "object" || Array.isArray(result)) throw new Error("Expected an object.");
  return result;
}

function validate(value) {
  const creator_version = text(value.creator_version).slice(0, 32);
  if (!isSupportedCreator(creator_version)) throw new Error("Update ChannelForge Creator to 3.5.19 or later to share worldwide.");
  const name = text(value.name).slice(0, 80);
  const slug = slugify(value.slug || name);
  if (!name || slug.length < 3) throw new Error("Creator name is required.");
  const guide = new URL(safeUrl(value.guide_url));
  if (!/^[a-z0-9-]+(?:\.[a-z0-9-]+)+\.[a-z][a-z0-9-]*$|^[a-z0-9-]+\.[a-z][a-z0-9-]*$/i.test(guide.hostname)
      || /\.(?:localhost|local|internal|test|onion|invalid)$/i.test(guide.hostname)) {
    throw new Error("Publish using a public DNS hostname.");
  }
  const validateEndpoint = (address, path) => {
    const endpoint = new URL(safeUrl(address));
    if (endpoint.origin !== guide.origin || endpoint.pathname !== path || endpoint.search || endpoint.hash) {
      throw new Error("Guide, playlist, and catalog URLs must come from the same ChannelForge media origin.");
    }
    return endpoint.href;
  };
  return {
    name, slug, description: text(value.description).slice(0, 400),
    guide_url: validateEndpoint(value.guide_url, "/exports/guide.xml"),
    playlist_url: validateEndpoint(value.playlist_url, "/exports/channels.m3u"),
    status_url: value.status_url ? validateEndpoint(value.status_url, "/api/public/channels") : "",
    vault_url: validateEndpoint(value.vault_url, "/api/public/vault"),
    logo_url: value.logo_url ? safeUrl(value.logo_url) : "",
    ...(creator_version ? {creator_version} : {}),
  };
}
async function endpointOk(url, content, observed) {
  let reader;
  try {
    const response = await fetch(url, {
      headers: {"user-agent": "ChannelForge-Health/1.0"},
      cf: {cacheTtl: 0},
      // Workers supports manual and follow. Reject 3xx using response.ok below.
      redirect: "manual",
      signal: AbortSignal.timeout(9000),
    });
    reader = response.body?.getReader();
    if (!response.ok || !reader) return false;
    const installed = response.headers.get("x-channelforge-version")
      || response.headers.get("server")?.match(/Public_ChannelForge\/(\d+\.\d+\.\d+)/i)?.[1];
    if (observed && installed && /^\d+\.\d+\.\d+$/.test(installed)) observed.version = installed;
    if (!isSupportedCreator(installed)) return false;
    const decoder = new TextDecoder();
    let sample = "";
    const limit = content === "vault" ? 8192 : 2000;
    while (sample.length < limit) {
      const {value, done} = await reader.read();
      if (done) break;
      sample += decoder.decode(value.subarray(0, limit - sample.length), {stream: true});
      if (content === "xml" && sample.includes("<tv")) return true;
      if (content === "json" && (sample.includes('"items"') || sample.includes('"channels"'))) return true;
      if (content === "m3u" && sample.includes("#EXTM3U")) return true;
    }
    if (content === "vault") {
      const vault = JSON.parse(sample);
      if (!Array.isArray(vault.items)) return false;
      observed.cinemaProtocol = Object.hasOwn(vault, "next_offset")
        && (vault.next_offset === null || Number.isInteger(vault.next_offset)) ? "paged" : "legacy";
      return true;
    }
    return false;
  } catch {
    return false;
  } finally {
    reader?.cancel().catch(() => {});
  }
}

const reportedStates = new Set(["standby", "starting", "broadcasting", "reconnecting", "overloaded"]);
const epoch = () => Math.floor(Date.now()/1000);
const indexFor = env => env.STUDIO_INDEX.getByName("published-studios");
const bearer = request => (request.headers.get("authorization") || "").match(/^Bearer ([a-zA-Z0-9_-]+)$/i)?.[1];

function gracePeriod(env) {
  const value = Number(env.OFFLINE_AFTER_SECONDS);
  return Number.isFinite(value) && value > 0 ? Math.max(60, Math.min(86400, value)) : 360;
}

async function probeOrigin(profile, observed = {}) {
  // An empty valid status or playlist still represents a reachable studio.
  return Boolean(profile.status_url && await endpointOk(profile.status_url + "?guide=brief", "json", observed))
    || await endpointOk(profile.playlist_url, "m3u", observed);
}

async function lookup(env, id) {
  const index = indexFor(env), key = "creator:" + id;
  const profile = await index.get(key);
  if (profile) return profile;
  const legacy = await env.CREATORS.get(key, "json");
  if (legacy) {
    await index.importProfile(legacy, epoch());
    return index.get(key);
  }
  return null;
}

async function legacySlugOwner(env, slug) {
  const owner = await env.CREATORS.get("slug:" + slug);
  if (!owner) return null;
  const migrated = await indexFor(env).get("creator:" + owner);
  return migrated && migrated.slug !== slug ? null : owner;
}

async function register(request, env) {
  const profile = validate(await readBody(request));
  // New Creators retain this random key before sending POST. If its response
  // is lost they can repeat the request and reclaim exactly the same identity.
  const supplied = bearer(request);
  if (supplied && !/^[a-f0-9]{64}$/.test(supplied)) return json({error:"Invalid registration key."}, 400);
  const apiKey = supplied || token();
  const id = supplied ? (await hash("creator-id:" + apiKey)).slice(0, 32) : crypto.randomUUID();
  const key_hash = await hash(apiKey);
  const index = indexFor(env);
  const prior = await index.get("creator:" + id);
  if (prior && prior.key_hash === key_hash) {
    if (Object.entries(profile).every(([field,value]) => prior[field] === value))
      return json({creator:publicCreator(prior), api_key:apiKey, heartbeat_supported:true}, 200);
    // A lost response and a subsequent Creator restart can change the relay.
    // Reconcile the route as part of the authenticated registration replay.
    return saveProfile(env, index, prior, profile, apiKey);
  }
  const slugOwner = await legacySlugOwner(env, profile.slug);
  if (slugOwner && slugOwner !== id) return json({error:"Creator name is already registered."}, 409);
  const now = epoch();
  const reachable = await probeOrigin(profile);
  if (!reachable) return json({error:"The public channel endpoint is unavailable. Retry when the studio is reachable."},422);
  const creator = {id, ...profile, key_hash, online:reachable, updated_at:now, last_ok:reachable ? now : 0};
  const result = await index.put(creator, now, reachable, true);
  if (result === "conflict") return json({error:"Creator name is already registered."}, 409);
  return json({creator:publicCreator(creator), api_key:apiKey, heartbeat_supported:true}, 201);
}

async function update(request, env, id, heartbeat) {
  const index = indexFor(env), key = "creator:" + id;
  const secret = bearer(request);
  const creator = secret && await lookup(env, id);
  const encoder = new TextEncoder();
  if (!creator || !/^[a-f0-9]{64}$/.test(creator.key_hash) ||
      !crypto.subtle.timingSafeEqual(encoder.encode(creator.key_hash), encoder.encode(await hash(secret)))) {
    return json({error:"Invalid creator key."}, 403);
  }
  if (request.method === "DELETE") {
    // Delete the legacy copy first, so an interrupted operation remains
    // visible and retryable before the authoritative index removal.
    if (await env.CREATORS.get(key)) {
      await Promise.all([env.CREATORS.delete(key), env.CREATORS.delete("slug:" + creator.slug)]);
    }
    await index.remove(key);
    return json({ok:true});
  }
  const body = await readBody(request);
  if (heartbeat) {
    if (!reportedStates.has(body.state)) return json({error:"Unknown studio state."}, 400);
    await index.heartbeat(key, epoch(), body.state);
    return json({ok:true});
  }
  return saveProfile(env, index, creator, validate(body));
}

async function saveProfile(env, index, creator, profile, registrationKey) {
  if (profile.slug !== creator.slug) {
    const owner = await legacySlugOwner(env, profile.slug);
    if (owner && owner !== creator.id) return json({error:"Creator name is already registered."}, 409);
  }
  const reachable = await probeOrigin(profile);
  const now = epoch(), saved = {...creator, ...profile, online:reachable, updated_at:now, last_ok:reachable ? now : 0};
  const result = await index.put(saved, now, reachable, false);
  if (result !== "ok") return json({error:"Registration changed. Refresh and try again."}, 409);
  if (profile.slug !== creator.slug && await env.CREATORS.get("slug:" + creator.slug) === creator.id) {
    await env.CREATORS.delete("slug:" + creator.slug);
  }
  return json({creator:publicCreator(saved), heartbeat_supported:true, ...(registrationKey ? {api_key:registrationKey} : {})});
}

function catalogEntry(row, profile, now, grace) {
  const heartbeat = row.heartbeat || 0;
  const fresh = heartbeat > 0 && now - heartbeat <= grace;
  const reachable = row.profile ? row.reachable : profile.online;
  const failedAt = row.first_failure || 0;
  let state = "standby";
  if (failedAt) state = now - failedAt >= grace ? "offline" : "reconnecting";
  else if (!reachable) state = "starting";
  else if (fresh) state = row.reported;
  else if (heartbeat && now - (row.seen || 0) > grace) state = "offline";
  return {...publicCreator(profile), last_heartbeat:heartbeat, last_ok:row.seen || profile.last_ok || 0,
    online:Boolean(reachable && state !== "offline"), state};
}

async function catalog(env) {
  const {rows, migrated} = await indexFor(env).entries();
  const missing = rows.filter(row => !row.profile).map(row => row.key);
  const legacy = new Map();
  for (let offset=0; offset<missing.length; offset+=100) {
    const result = await env.CREATORS.get(missing.slice(offset, offset+100), {type:"json"});
    for (const [key, value] of result) legacy.set(key, value);
  }
  const now = epoch(), grace = gracePeriod(env), creators=[];
  for (const row of rows) {
    const profile = row.profile ? JSON.parse(row.profile) : legacy.get(row.key);
    if (profile && isSupportedCreator(profile.creator_version)) creators.push(catalogEntry(row, profile, now, grace));
  }
  creators.sort((a,b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  return json({version:1, registrations_complete:migrated && missing.every(key=>legacy.has(key) && legacy.get(key)), generated_at:now, creators});
}

async function prepareRollback(env) {
  const index = indexFor(env);
  const snapshot = await index.entries();
  if (!snapshot.migrated || snapshot.rows.some(row => !row.profile)) return false;
  const checkpoint = `rollback:${env.ROLLBACK_SNAPSHOT}`;
  const progress = await env.CREATORS.get(checkpoint, "json");
  if (progress?.complete) return true;
  const batch = await index.rollbackBatch(progress?.after || "", 5);
  const now = epoch(), grace = gracePeriod(env);
  for (const {key,profile} of batch) {
    const creator = JSON.parse(profile);
    const row = snapshot.rows.find(entry => entry.key === key);
    const visible = catalogEntry(row, creator, now, grace);
    creator.online = visible.online;
    creator.last_ok = visible.last_ok;
    await env.CREATORS.put(key, JSON.stringify(creator), {metadata:{online:creator.online}});
    await env.CREATORS.put("slug:" + creator.slug, creator.id);
  }
  await env.CREATORS.put(checkpoint, JSON.stringify({complete:batch.length<5, after:batch.at(-1)?.key || progress?.after || ""}));
  return true;
}

export default {
  async fetch(request, env, ctx) {
    try {
      const {pathname} = new URL(request.url);
      const {method} = request;
      if (method === "OPTIONS") return new Response(null, {headers: {
        "access-control-allow-origin": "*",
        "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS",
        "access-control-allow-headers": "authorization,content-type",
      }});
      if (method === "GET" && pathname === "/health") return json({ok: true, service: "ChannelForge Creator Directory"});
      if (method === "GET" && pathname === "/fire") {
        return Response.redirect("https://github.com/dethkorex/Public_ChannelForge/releases/latest/download/ChannelForgeTV-FireTV.apk", 302);
      }
      if (method === "GET" && pathname === "/v1/catalog") {
        const cache = globalThis.caches?.default;
        const key = new Request(new URL(pathname, request.url));
        const cached = await cache?.match(key);
        if (cached) return cached;
        const result = await catalog(env);
        if (cache && ctx) ctx.waitUntil(cache.put(key, result.clone()));
        return result;
      }
      if (env.ROLLBACK_SNAPSHOT && ["POST","PUT","DELETE"].includes(method) && !pathname.endsWith("/heartbeat"))
        return json({error:"Directory registration is temporarily frozen for maintenance."},503);
      if (method === "POST" && pathname === "/v1/creators") return await register(request, env);
      const match = pathname.match(/^\/v1\/creators\/([0-9a-f-]+)(\/heartbeat)?$/);
      if (match && (match[2] ? method === "POST" : ["PUT","DELETE"].includes(method))) return await update(request, env, match[1], Boolean(match[2]));
      return json({error: "Not found"}, 404);
    } catch (error) {
      return json({error: error.message || "Request failed"}, 400);
    }
  },
  async scheduled(_event,env,ctx) {
    ctx.waitUntil((async () => {
      const index = indexFor(env), now = epoch();
      const cursor = await index.claimImport(now);
      if (cursor !== null) {
        try {
          const page = await env.CREATORS.list({prefix:"creator:",limit:100, ...(cursor ? {cursor} : {})});
          await index.importLegacy(page.keys.map(row=>row.name), page.list_complete ? null : page.cursor, now);
        } catch (error) {
          console.warn("Legacy directory import deferred", error.name);
        }
      }
      if (env.ROLLBACK_SNAPSHOT && await prepareRollback(env)) return;
      // Rotating, bounded work. Health results never overwrite registration
      // metadata in KV and never remove membership.
      for (const {key,generation} of await index.due(now, 5)) {
        const creator = await lookup(env, key.slice("creator:".length));
        if (!creator) { await index.unresolved(key, now); continue; }
        if (!generation) continue; // Imported creators get a fresh probe lease.
        const observed = {};
        const healthy = await probeOrigin(creator, observed);
        if (healthy && !creator.creator_version && !observed.version && creator.vault_url)
          await endpointOk(creator.vault_url + "?limit=1", "vault", observed);
        await index.checked(key, generation, healthy, observed, now);
      }
    })());
  }
};
