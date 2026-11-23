const json = (value, status = 200) => new Response(JSON.stringify(value), {status, headers:{"content-type":"application/json; charset=utf-8","access-control-allow-origin":"*","cache-control":status===200?"public, max-age=30":"no-store"}});
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

async function readBody(request){const value=await request.json();if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("Expected an object.");return value}
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
async function owned(request,env,id){const creator=await env.CREATORS.get(`creator:${id}`,"json"),provided=text(request.headers.get("authorization")).replace(/^Bearer\s+/i,"");if(!creator||!provided||await hash(provided)!==creator.key_hash)return null;return creator}
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

function saveCreator(env, creator) {
  return env.CREATORS.put(`creator:${creator.id}`, JSON.stringify(creator), {metadata: {online: creator.online}});
}

function studioIndex(env) {
  return env.STUDIO_INDEX.getByName("published-studios");
}

async function listCreatorKeys(env) {
  const keys = [];
  let cursor;
  do {
    const page = await env.CREATORS.list({prefix: "creator:", ...(cursor ? {cursor} : {})});
    keys.push(...page.keys);
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return keys;
}

async function publishable(value) {
  const checks = await Promise.all([
    endpointOk(value.guide_url, "xml"),
    endpointOk(value.playlist_url, "m3u"),
    endpointOk(value.vault_url + "?limit=1", "json"),
  ]);
  return checks.every(Boolean);
}

async function register(request, env) {
  const value = validate(await readBody(request));
  const slugOwner = await env.CREATORS.get(`slug:${value.slug}`);
  if (slugOwner) return json({error: "Creator name is already registered."}, 409);
  if (!await publishable(value)) return json({error: "Guide, playlist, and vault must be online before publishing."}, 422);
  const id = crypto.randomUUID(), apiKey = token(), now = Math.floor(Date.now()/1000);
  const creator = {id, ...value, key_hash: await hash(apiKey), online: true, last_ok: now, updated_at: now, failures: 0};
  await studioIndex(env).add(`creator:${id}`, now);
  await Promise.all([
    saveCreator(env, creator),
    env.CREATORS.put(`slug:${value.slug}`, id),
  ]);
  return json({creator: publicCreator(creator), api_key: apiKey}, 201);
}

async function update(request, env, id) {
  const creator = await owned(request, env, id);
  if (!creator) return json({error: "Invalid creator key."}, 403);
  if (request.method === "DELETE") {
    await Promise.all([env.CREATORS.delete(`creator:${id}`), env.CREATORS.delete(`slug:${creator.slug}`)]);
    await studioIndex(env).remove(`creator:${id}`);
    return json({ok: true});
  }
  const value = validate(await readBody(request));
  if (!await publishable(value)) return json({error: "Guide, playlist, and vault must be online before republishing."}, 422);
  if (value.slug !== creator.slug) {
    const owner = await env.CREATORS.get(`slug:${value.slug}`);
    if (owner && owner !== id) return json({error: "Creator name is already registered."}, 409);
    await Promise.all([env.CREATORS.delete(`slug:${creator.slug}`), env.CREATORS.put(`slug:${value.slug}`, id)]);
  }
  const now = Math.floor(Date.now()/1000);
  const saved = {...creator, ...value, online: true, last_ok: now, failures: 0, updated_at: now};
  await studioIndex(env).add(`creator:${id}`, now);
  await saveCreator(env, saved);
  return json({creator: publicCreator(saved)});
}

async function catalog(env) {
  const keys = await studioIndex(env).keys();
  const creators = [];
  for (let offset = 0; offset < keys.length; offset += 100) {
    const rows = await env.CREATORS.get(keys.slice(offset, offset + 100), {type: "json"});
    for (const creator of rows.values()) {
      if (creator?.online) creators.push(publicCreator(creator));
    }
  }
  creators.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  return json({version: 1, generated_at: Math.floor(Date.now()/1000), creators});
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
      if (method === "POST" && pathname === "/v1/creators") return await register(request, env);
      const match = pathname.match(/^\/v1\/creators\/([0-9a-f-]+)$/);
      if (match && (method === "PUT" || method === "DELETE")) return await update(request, env, match[1]);
      return json({error: "Not found"}, 404);
    } catch (error) {
      return json({error: error.message || "Request failed"}, 400);
    }
  },
  async scheduled(_event,env,ctx){ctx.waitUntil((async()=>{
    // Workers Free permits 50 outbound subrequests per invocation. Check at most
    // five creators (including index updates and up to three origin requests each) in a rotating batch so the public
    // directory remains usable as the community grows without paid services.
    const index = studioIndex(env);
    const now = Math.floor(Date.now()/1000);
    if (await index.claimImport(now)) {
      try {
        const legacy = await listCreatorKeys(env);
        await index.importLegacy(legacy.map(row => row.name), now);
      } catch {
        console.warn("Legacy studio import deferred. Current registrations remain available.");
      }
    }
    const selected = await index.due(now, 5);
    for(const key of selected){
      const creator=await env.CREATORS.get(key,"json");
      if(!creator){await index.checked(key, false, now);continue;}
      // A slow movie catalog or full guide should not hide reachable studios.
      const observed = {};
      const healthy = (creator.status_url && await endpointOk(creator.status_url + "?guide=brief", "json", observed))
        || await endpointOk(creator.playlist_url, "m3u", observed);
      const wasOnline=Boolean(creator.online),previousFailures=Number(creator.failures||0);
      if(healthy){
        // Healthy creators cause no recurring KV writes. Only recovery or a
        // cleared failure counter is persisted.
        const changedVersion = observed.version && observed.version !== creator.creator_version;
        if (changedVersion) creator.creator_version = observed.version;
        // A reverse proxy may replace Server headers. The paged vault response
        // confirms the minimum supported Cinema protocol in that case.
        if (!creator.creator_version && creator.vault_url) {
          await endpointOk(creator.vault_url + "?limit=1", "vault", observed);
        }
        const changedCinema = observed.cinemaProtocol && observed.cinemaProtocol !== creator.cinema_protocol;
        if (changedCinema) creator.cinema_protocol = observed.cinemaProtocol;
        if(!wasOnline||previousFailures||changedVersion||changedCinema){creator.failures=0;creator.online=true;creator.last_ok=now;await saveCreator(env,creator)}
      }else if(wasOnline&&previousFailures<1){
        // One missed probe is tolerated so a transient network hiccup does not
        // make a channel vanish from every TV guide.
        creator.failures=1;await saveCreator(env,creator);
      }else if(wasOnline){
        creator.failures=2;creator.online=false;await saveCreator(env,creator);
      }
      // An already-offline creator causes no further KV writes until it recovers.
      await index.checked(key, healthy, now);
    }
  })())}
};
