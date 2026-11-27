const json = (value, status = 200) => new Response(JSON.stringify(value), {status, headers:{"content-type":"application/json; charset=utf-8","access-control-allow-origin":"*","cache-control":value.creators && status===200?"public, max-age=30":"no-store"}});
const text = value => String(value || "").trim();
const slugify = value => text(value).toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"").slice(0,60);
const token = () => [...crypto.getRandomValues(new Uint8Array(32))].map(value=>value.toString(16).padStart(2,"0")).join("");
const hash = async value => [...new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(value)))].map(byte=>byte.toString(16).padStart(2,"0")).join("");
const safeUrl = value => {const url=new URL(text(value));if(url.protocol!=="https:"||url.username||url.password)throw new Error("Published endpoints must use public HTTPS.");return url.toString().slice(0,1000)};
const MIN_CREATOR = [3, 5, 19];

export function channelNumberKey(value) {
  return typeof value === "string" ? value.trim().toLowerCase()
    .replace(/^0+(?=\d)/, "").replace(/\.0+(?=\d)/, ".") : "";
}

function channelNumberList(entries) {
  if (!Array.isArray(entries) || entries.length > 1024) throw new Error("Invalid public channel number list.");
  const ids = new Set();
  return entries.map(({id, number}) => {
    number = channelNumberKey(number);
    if (typeof id !== "string" || !/^[a-z0-9_-]{1,80}$/i.test(id) || ids.has(id) || !number || number.length > 12)
      throw new Error("Invalid public channel number.");
    ids.add(id);
    return {id, number};
  });
}

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
  const {key_hash, failures, cinema_protocol, channel_inventory, inventory_verified_at, ...visible} = value;
  const version = text(visible.creator_version);
  const verified = /^v?\d+\.\d+\.\d+$/.test(version)
    ? isSupportedCreator(version) : cinema_protocol === "paged";
  return {...visible, cinema_supported: Boolean(verified && visible.vault_url)};
}

function channelInventory(value, address) {
  if (!Array.isArray(value?.channels)) return null;
  const origin = mediaBase(new URL(address), "/api/public/channels"), channels = [], seen = new Set();
  for (const row of value.channels) {
    const id = typeof row?.id === "string" && row.id;
    if (!id || !/^[a-z0-9_-]{1,80}$/i.test(id) || seen.has(id) || row.online !== true || row.state === "offline") continue;
    const livePath = `/live/${id}.m3u8`;
    if (row.stream_url !== origin + livePath && row.stream_url !== livePath) continue;
    seen.add(id);
    const channel = {id, name:text(row.name).slice(0,120)||"Untitled", number:text(row.number).slice(0,16),
      online:true, state:reportedStates.has(row.state)?row.state:"standby", stream_url:origin+livePath};
    if (typeof row.channel_rating === "string" && /^[A-Z0-9-]{1,10}$/.test(row.channel_rating) &&
        Number.isInteger(row.channel_rating_rank) && row.channel_rating_rank >= 1 && row.channel_rating_rank <= 9) {
      channel.channel_rating = row.channel_rating;
      channel.channel_rating_rank = row.channel_rating_rank;
    }
    const broadcast = `/broadcast/${id}/`;
    if (row.continuous_url === origin+broadcast+"master.m3u8" || row.continuous_url === broadcast+"master.m3u8")
      channel.continuous_url=origin+broadcast+"master.m3u8";
    if (row.firetv_url === origin+broadcast+"firetv.m3u8" || row.firetv_url === broadcast+"firetv.m3u8")
      channel.firetv_url=origin+broadcast+"firetv.m3u8";
    if (Number.isSafeInteger(row.air_until_epoch_ms) && row.air_until_epoch_ms>0)
      channel.air_until_epoch_ms=row.air_until_epoch_ms;
    channels.push(channel);
    if (channels.length >= 50) break;
  }
  return channels;
}

function mediaBase(url, suffix) {
  const prefix = url.pathname.endsWith(suffix) ? url.pathname.slice(0, -suffix.length) : "";
  if (prefix && !/^\/studio\/[a-f0-9]{16}$/.test(prefix)) throw new Error("Invalid Studio media path.");
  return url.origin + prefix;
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
  const origin = mediaBase(guide, "/exports/guide.xml");
  if (!/^[a-z0-9-]+(?:\.[a-z0-9-]+)+\.[a-z][a-z0-9-]*$|^[a-z0-9-]+\.[a-z][a-z0-9-]*$/i.test(guide.hostname)
      || /\.(?:localhost|local|internal|test|onion|invalid)$/i.test(guide.hostname)) {
    throw new Error("Publish using a public DNS hostname.");
  }
  const validateEndpoint = (address, path) => {
    const endpoint = new URL(safeUrl(address));
    if (endpoint.href !== origin + path) {
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
    const limit = content === "channels" ? 65536 : content === "vault" ? 8192 : 2000;
    while (sample.length < limit) {
      const {value, done} = await reader.read();
      if (done) break;
      sample += decoder.decode(value.subarray(0, limit - sample.length), {stream: true});
      if (content === "xml" && sample.includes("<tv")) return true;
      if (content === "json" && (sample.includes('"items"') || sample.includes('"channels"'))) return true;
      if (content === "m3u" && sample.includes("#EXTM3U")) return true;
    }
    if (content === "channels") {
      const inventory = channelInventory(JSON.parse(sample + decoder.decode()), url);
      if (inventory === null) return false;
      observed.inventory = inventory;
      return true;
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

const reportedStates = new Set(["standby", "starting", "broadcasting", "reconnecting", "overloaded", "offline"]);
const epoch = () => Math.floor(Date.now()/1000);
const indexFor = env => env.STUDIO_INDEX.getByName("published-studios");
const bearer = request => (request.headers.get("authorization") || "").match(/^Bearer ([a-zA-Z0-9_-]+)$/i)?.[1];

function gracePeriod(env) {
  const value = Number(env.OFFLINE_AFTER_SECONDS);
  return Number.isFinite(value) && value > 0 ? Math.max(60, Math.min(86400, value)) : 360;
}

async function probeOrigin(profile, observed = {}) {
  // An empty valid status or playlist still represents a reachable studio.
  return Boolean(profile.status_url && await endpointOk(profile.status_url + "?guide=brief", "channels", observed))
    || await endpointOk(profile.playlist_url, "m3u", observed);
}

async function lookup(env, id, withGeneration = false) {
  const index = indexFor(env), key = "creator:" + id;
  const profile = await index.get(key, withGeneration);
  if (profile) return profile;
  const legacy = await env.CREATORS.get(key, "json");
  if (legacy) {
    await index.importProfile(legacy, epoch());
    return index.get(key, withGeneration);
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
  const body = await readBody(request);
  const profile = validate(body);
  const numbers = body.channel_numbers === undefined ? null : channelNumberList(body.channel_numbers);
  // New Creators retain this random key before sending POST. If its response
  // is lost they can repeat the request and reclaim exactly the same identity.
  const supplied = bearer(request);
  if (supplied && !/^[a-f0-9]{64}$/.test(supplied)) return json({error:"Invalid registration key."}, 400);
  const apiKey = supplied || token();
  const id = supplied ? (await hash("creator-id:" + apiKey)).slice(0, 32) : crypto.randomUUID();
  const key_hash = await hash(apiKey);
  const index = indexFor(env);
  const prior = await index.get("creator:" + id, true);
  if (prior && prior.profile.key_hash === key_hash) {
    if (numbers === null && Object.entries(profile).every(([field,value]) => prior.profile[field] === value))
      return json({creator:publicCreator(prior.profile), api_key:apiKey, heartbeat_supported:true}, 200);
    // A lost response and a subsequent Creator restart can change the relay.
    // Reconcile the route as part of the authenticated registration replay.
    return saveProfile(env, index, prior, profile, apiKey, numbers);
  }
  const slugOwner = await legacySlugOwner(env, profile.slug);
  if (slugOwner && slugOwner !== id) return json({error:"Creator name is already registered."}, 409);
  const now = epoch();
  const observed = {};
  const reachable = await probeOrigin(profile, observed);
  if (!reachable) return json({error:"The public channel endpoint is unavailable. Retry when the studio is reachable."},422);
  const creator = {id, ...profile, key_hash, online:reachable, updated_at:now, last_ok:reachable ? now : 0};
  if (observed.inventory) Object.assign(creator, {channel_inventory:observed.inventory, inventory_verified_at:now});
  const result = await index.put(creator, now, reachable, true, null, numbers);
  if (result.startsWith("number:")) return numberConflict(result.slice(7));
  if (result === "conflict") return json({error:"Creator name is already registered."}, 409);
  return json({creator:publicCreator(creator), api_key:apiKey, heartbeat_supported:true}, 201);
}

async function ownedRegistration(request, env, id) {
  const secret = bearer(request);
  const current = secret && await lookup(env, id, true);
  const creator = current?.profile;
  const encoder = new TextEncoder();
  if (!creator || !/^[a-f0-9]{64}$/.test(creator.key_hash) ||
      !crypto.subtle.timingSafeEqual(encoder.encode(creator.key_hash), encoder.encode(await hash(secret)))) {
    return null;
  }
  return current;
}

async function enrollRelay(request, env, id = "") {
  const first = !id;
  if (!id) {
    const secret = bearer(request);
    if (!/^[a-f0-9]{64}$/.test(secret || "")) return json({error:"Invalid registration key."}, 403);
    id = (await hash("creator-id:" + secret)).slice(0, 32);
  }
  const current = await ownedRegistration(request, env, id);
  if (!first && !current) return json({error:"Invalid creator key."}, 403);
  const owner = current ? {registered_origin:mediaBase(new URL(current.profile.guide_url), "/exports/guide.xml")} : {provisional:true};
  if (!env.RELAY_ENROLL_URL || !env.RELAY_ENROLL_TOKEN)
    return json({error:"ChannelForge relay enrollment is unavailable."}, 503);
  const {public_key, kind, nonce} = await readBody(request);
  const probe = (kind === "identity" && typeof nonce === "string" && /^[a-f0-9]{12}$/.test(nonce)) ||
    (kind === "sample" && typeof nonce === "string" && /^[A-Za-z0-9_-]{32}$/.test(nonce));
  if (!probe && (typeof public_key !== "string" || !/^ssh-ed25519 [A-Za-z0-9+/]{68}$/.test(public_key)))
    return json({error:"Invalid relay enrollment or health check request."}, 400);
  const endpoint = safeUrl(env.RELAY_ENROLL_URL);
  try {
    const result = await fetch(endpoint, {
      method:"POST", redirect:"manual", signal:AbortSignal.timeout(8500),
      headers:{"authorization":`Bearer ${env.RELAY_ENROLL_TOKEN}`, "content-type":"application/json"},
      body:JSON.stringify(probe ? {studio:id, kind, nonce, ...owner} : {studio:id, public_key, ...owner}),
    });
    if (result.ok) {
      const data = await readBody(result);
      const field = probe ? (kind === "identity" ? "instance" : "sha256") : "profile";
      return json({[field]:data[field]});
    }
    await result.body?.cancel();
    if (result.status === 409) return json({error:"This Studio already has a relay key."}, 409);
    if (result.status === 429) return json({error:"Studio relay setup is busy. Studio will retry shortly."}, 429);
  } catch {
    return json({error:"Could not reach relay enrollment."}, 503);
  }
  return json({error:"The relay could not assign a Studio slot."}, 503);
}

async function update(request, env, id, heartbeat) {
  const index = indexFor(env), key = "creator:" + id;
  const current = await ownedRegistration(request, env, id);
  if (!current) return json({error:"Invalid creator key."}, 403);
  const creator = current.profile;
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
  const numbers = body.channel_numbers === undefined ? null : channelNumberList(body.channel_numbers);
  return saveProfile(env, index, current, validate(body), undefined, numbers);
}

function numberConflict(number) {
  return json({error:`Channel ${number} is already reserved in the public guide. Choose a different channel number.`}, 409);
}

async function manageChannelNumbers(request, env, id) {
  if (!await ownedRegistration(request, env, id)) return json({error:"Invalid creator key."}, 403);
  const data = await readBody(request);
  const entries = channelNumberList(data.channels);
  if (data.mode !== "reserve" && data.mode !== "sync") throw new Error("Invalid channel number operation.");
  const status = await indexFor(env).channelNumbers("creator:" + id, entries, data.mode);
  if (status === "missing") return json({error:"Studio sharing was removed."}, 404);
  if (status.startsWith("number:")) return numberConflict(status.slice(7));
  return json({ok:true});
}

async function saveProfile(env, index, {profile:creator,generation}, profile, registrationKey, numbers = null) {
  if (profile.slug !== creator.slug) {
    const owner = await legacySlugOwner(env, profile.slug);
    if (owner && owner !== creator.id) return json({error:"Creator name is already registered."}, 409);
  }
  const observed = {};
  if (!await probeOrigin(profile, observed))
    return json({error:"The proposed media route is unavailable. The previous route is still registered."},422);
  const now = epoch(), saved = {...creator, ...profile, online:true, updated_at:now, last_ok:now};
  saved.channel_inventory = observed.inventory;
  saved.inventory_verified_at = observed.inventory ? now : undefined;
  const result = await index.put(saved, now, true, false, generation, numbers);
  if (result.startsWith("number:")) return numberConflict(result.slice(7));
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
  if (heartbeat && row.reported === "offline") state = "offline";
  else if (failedAt) state = now - failedAt >= grace ? "offline" : "reconnecting";
  else if (!reachable) state = "starting";
  else if (fresh) state = row.reported;
  else if (heartbeat && now - (row.seen || 0) > grace) state = "offline";
  const online = Boolean(reachable && state !== "offline");
  const expiration = (profile.inventory_verified_at || 0) + grace;
  const entry = {...publicCreator(profile), last_heartbeat:heartbeat, last_ok:row.seen || profile.last_ok || 0, online, state};
  if (online && expiration > now && Array.isArray(profile.channel_inventory)) {
    entry.channel_inventory = profile.channel_inventory.filter(channel => !channel.air_until_epoch_ms || channel.air_until_epoch_ms > now*1000);
    entry.inventory_expires = expiration;
  }
  return entry;
}

function distinctCatalogRoutes(creators) {
  const ordered = creators.sort((left, right) => Number(right.online) - Number(left.online)
    || Number(left.state === "reconnecting") - Number(right.state === "reconnecting")
    || (right.updated_at || 0) - (left.updated_at || 0));
  const routes = new Set(), inventories = new Set();
  return ordered.filter(studio => {
    const name = text(studio.name).toLowerCase();
    const route = name && studio.playlist_url ? JSON.stringify([name, studio.playlist_url]) : "";
    if (route && routes.has(route)) return false;
    // An origin rotation can leave two registrations temporarily reachable.
    // Only treat them as the same Studio when both verified inventories match.
    const channels = studio.channel_inventory?.map(channel => channel.id);
    if (name && channels?.length && channels.every(id => /^[a-f0-9]{16}$/i.test(id))) {
      const key = JSON.stringify([name, channels.sort()]);
      if (inventories.has(key)) return false;
      inventories.add(key);
    }
    if (route) routes.add(route);
    return true;
  });
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
  const distinct = distinctCatalogRoutes(creators);
  distinct.sort((a,b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  return json({version:1, registrations_complete:migrated && missing.every(key=>legacy.has(key) && legacy.get(key)), generated_at:now, creators:distinct});
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
      if (method === "POST" && pathname === "/v1/creators/relay") return await enrollRelay(request, env);
      if (method === "GET" && pathname === "/v1/channel-number") {
        const number = channelNumberKey(new URL(request.url).searchParams.get("number"));
        if (!number || number.length > 12) throw new Error("Invalid public channel number.");
        return json({available:!await indexFor(env).channelNumberOwner(number)});
      }
      const numbers = pathname.match(/^\/v1\/creators\/([a-f0-9-]{8,64})\/channel-numbers$/);
      if (numbers && method === "POST") return await manageChannelNumbers(request, env, numbers[1]);
      const relay = pathname.match(/^\/v1\/creators\/([0-9a-f-]{8,64})\/relay$/);
      if (relay && method === "POST") return await enrollRelay(request, env, relay[1]);
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
