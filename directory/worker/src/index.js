const json = (value, status = 200) => new Response(JSON.stringify(value), {status, headers:{"content-type":"application/json; charset=utf-8","access-control-allow-origin":"*","cache-control":status===200?"public, max-age=30":"no-store"}});
const text = value => String(value || "").trim();
const slugify = value => text(value).toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"").slice(0,60);
const token = () => [...crypto.getRandomValues(new Uint8Array(32))].map(value=>value.toString(16).padStart(2,"0")).join("");
const hash = async value => [...new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(value)))].map(byte=>byte.toString(16).padStart(2,"0")).join("");
const safeUrl = value => {const url=new URL(text(value));if(url.protocol!=="https:"||url.username||url.password)throw new Error("Published endpoints must use public HTTPS.");return url.toString().slice(0,1000)};
const publicCreator = value => {const {key_hash,failures,...visible}=value;return visible};

async function readBody(request){const value=await request.json();if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("Expected an object.");return value}
function validate(value) {
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
  };
}
async function owned(request,env,id){const creator=await env.CREATORS.get(`creator:${id}`,"json"),provided=text(request.headers.get("authorization")).replace(/^Bearer\s+/i,"");if(!creator||!provided||await hash(provided)!==creator.key_hash)return null;return creator}
async function endpointOk(url, content) {
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
    const decoder = new TextDecoder();
    let sample = "";
    while (sample.length < 2000) {
      const {value, done} = await reader.read();
      if (done) break;
      sample += decoder.decode(value.subarray(0, 2000 - sample.length), {stream: true});
      if (content === "xml" && sample.includes("<tv")) return true;
      if (content === "json" && (sample.includes('"items"') || sample.includes('"channels"'))) return true;
      if (content === "m3u" && sample.includes("#EXTM3U")) return true;
    }
    return false;
  } catch {
    return false;
  } finally {
    reader?.cancel().catch(() => {});
  }
}

async function catalogSnapshot(env) {
  const snapshot = await env.CREATORS.get("catalog:v1", "json");
  return snapshot || {version: 1, generated_at: 0, creators: []};
}

async function refreshCatalog(env, changes, snapshot) {
  snapshot ||= await catalogSnapshot(env);
  const studios = new Map(snapshot.creators.map(creator => [creator.id, creator]));
  for (const creator of changes) {
    if (creator.online) studios.set(creator.id, publicCreator(creator));
    else studios.delete(creator.id);
  }
  const creators = [...studios.values()].sort((a,b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
  if (JSON.stringify(creators) !== JSON.stringify(snapshot.creators)) {
    await env.CREATORS.put("catalog:v1", JSON.stringify({
      version: 1, generated_at: Math.floor(Date.now()/1000), creators,
    }));
  }
}

async function publishable(value) {
  const checks = await Promise.all([
    endpointOk(value.guide_url, "xml"),
    endpointOk(value.playlist_url, "m3u"),
    endpointOk(value.vault_url, "json"),
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
  await Promise.all([
    env.CREATORS.put(`creator:${id}`, JSON.stringify(creator)),
    env.CREATORS.put(`slug:${value.slug}`, id),
  ]);
  // KV may return a stale key list immediately after a write. Use this request's record.
  await refreshCatalog(env, [creator]);
  return json({creator: publicCreator(creator), api_key: apiKey}, 201);
}

async function update(request, env, id) {
  const creator = await owned(request, env, id);
  if (!creator) return json({error: "Invalid creator key."}, 403);
  if (request.method === "DELETE") {
    await Promise.all([env.CREATORS.delete(`creator:${id}`), env.CREATORS.delete(`slug:${creator.slug}`)]);
    await refreshCatalog(env, [{id, online: false}]);
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
  await env.CREATORS.put(`creator:${id}`, JSON.stringify(saved));
  await refreshCatalog(env, [saved]);
  return json({creator: publicCreator(saved)});
}

async function catalog(env) {
  return json({...await catalogSnapshot(env), generated_at: Math.floor(Date.now()/1000)});
}

export default {
  async fetch(request, env) {
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
      if (method === "GET" && pathname === "/v1/catalog") return await catalog(env);
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
    // eight creators (four endpoints each) in a rotating batch so the public
    // directory remains usable as the community grows without paid services.
    const list=await env.CREATORS.list({prefix:"creator:"}),keys=list.keys.sort((a,b)=>a.name.localeCompare(b.name));
    const snapshot=await catalogSnapshot(env),catalogChanges=[];
    if(list.list_complete){
      const knownIds=new Set(keys.map(key=>key.name.slice("creator:".length)));
      const cutoff=Math.floor(Date.now()/1000)-180;
      for(const studio of snapshot.creators){
        if(studio.updated_at<cutoff&&!knownIds.has(studio.id))catalogChanges.push({id:studio.id,online:false});
      }
    }
    const batchSize=Math.min(8,keys.length),slot=Math.floor(Date.now()/120000),start=(slot*batchSize)%keys.length;
    const selected=Array.from({length:batchSize},(_,index)=>keys[(start+index)%keys.length]);
    for(const key of selected){
      const creator=await env.CREATORS.get(key.name,"json");if(!creator)continue;
      const checks=[endpointOk(creator.guide_url,"xml"),endpointOk(creator.playlist_url,"m3u")];
      if(creator.status_url)checks.push(endpointOk(creator.status_url,"json"));
      if(creator.vault_url)checks.push(endpointOk(creator.vault_url,"json"));
      const healthy=(await Promise.all(checks)).every(Boolean),wasOnline=Boolean(creator.online),previousFailures=Number(creator.failures||0),now=Math.floor(Date.now()/1000);
      if(healthy){
        // Healthy creators cause no recurring KV writes. Only recovery or a
        // cleared failure counter is persisted.
        if(!wasOnline||previousFailures){creator.failures=0;creator.online=true;creator.last_ok=now;await env.CREATORS.put(key.name,JSON.stringify(creator))}
      }else if(wasOnline&&previousFailures<1){
        // One missed probe is tolerated so a transient network hiccup does not
        // make a channel vanish from every TV guide.
        creator.failures=1;await env.CREATORS.put(key.name,JSON.stringify(creator));
      }else if(wasOnline){
        creator.failures=2;creator.online=false;await env.CREATORS.put(key.name,JSON.stringify(creator));
      }
      // An already-offline creator causes no further writes until it recovers.
      catalogChanges.push(creator);
    }
    if(catalogChanges.length)await refreshCatalog(env,catalogChanges,snapshot);
  })())}
};
