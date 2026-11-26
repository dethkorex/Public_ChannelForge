import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {Miniflare} from "miniflare";

// Run the real Worker, KV binding and SQLite Durable Object. The harness only
// supplies a clock and invokes cron deterministically; the studio HTTP origin
// is simulated independently of directory storage.
test("registration survives origin outages, missing heartbeats and directory restart", async t => {
  const persist = await mkdtemp(join(tmpdir(), "channelforge-directory-"));
  const modules = [{type:"ESModule",path:"test-entry.js",contents:`
    import directory from "./src/index.js";
    export { StudioIndex } from "./src/worker.js";
    export default {
      async fetch(request, env) {
        const now = Number(request.headers.get("test-time"));
        Date.now = () => now * 1000;
        if (new URL(request.url).pathname === "/test/probe") {
          let task;
          await directory.scheduled({}, env, {waitUntil(value) {task=value;}});
          await task;
          return new Response("ok");
        }
        return directory.fetch(request, env);
      }
    };`},
    ...await Promise.all(["index","worker"].map(async name => ({
      type:"ESModule",path:"src/"+name+".js",contents:await readFile(new URL("../src/"+name+".js",import.meta.url),"utf8")
    })))];
  let healthy = true, cinemaAvailable = false, version = "3.6.2";
  function start(rollback = "") {
    return new Miniflare({
      compatibilityDate:"2025-01-01", modules,
      kvNamespaces:["CREATORS"], kvPersist:join(persist,"kv"),
      durableObjects:{STUDIO_INDEX:{className:"StudioIndex",useSQLite:true}},durableObjectsPersist:join(persist,"do"),
      bindings:{OFFLINE_AFTER_SECONDS:"120",ROLLBACK_SNAPSHOT:rollback},
      outboundService(request) {
        const path = new URL(request.url).pathname;
        if (!healthy || path.endsWith("/vault") && !cinemaAvailable) return new Response(null,{status:504});
        return new Response(path.endsWith(".m3u")?"#EXTM3U":'{"channels":[]}',{headers:{"x-channelforge-version":version}});
      }
    });
  }
  let mf = start(), now=100000;
  t.after(async()=>{await mf.dispose();await rm(persist,{force:true,recursive:true});});
  const request = (path,method="GET",data,key) => mf.dispatchFetch("https://directory.example"+path,{
    method,headers:{"test-time":String(now),...(key?{Authorization:"Bearer "+key}:{})},
    body:data?JSON.stringify(data):undefined
  });
  async function read(path,method,data,key,status=200) {
    const r=await request(path,method,data,key);
    const body=await r.json();
    assert.equal(r.status,status,JSON.stringify(body));
    return body;
  }
  async function probe() { assert.equal((await request("/test/probe")).status,200); }
  const profile = {name:"Test studio",guide_url:"https://studio.example.com/exports/guide.xml",
    playlist_url:"https://studio.example.com/exports/channels.m3u",vault_url:"https://studio.example.com/api/public/vault",
    status_url:"https://studio.example.com/api/public/channels"};
  const owner="ab".repeat(32);
  const responses=await Promise.all([request("/v1/creators","POST",profile,owner),request("/v1/creators","POST",profile,owner)]);
  const [first,retry]=await Promise.all(responses.map(r=>r.json()));
  assert.equal(first.creator.id,retry.creator.id);
  assert.equal(first.api_key,owner);
  const path="/v1/creators/"+first.creator.id;
  const catalog=()=>read("/v1/catalog");
  assert.equal((await catalog()).registrations_complete,false);
  const kv=await mf.getKVNamespace("CREATORS");
  const legacyId="076e3aab";
  const legacyKey="af".repeat(32);
  const key_hash=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(legacyKey))), b=>b.toString(16).padStart(2,"0")).join("");
  await kv.put("creator:"+legacyId,JSON.stringify({id:legacyId,...profile,slug:"legacy",name:"Legacy studio",key_hash,online:true,last_ok:now}));
  await probe();
  assert.ok((await catalog()).creators.some(x=>x.id===legacyId));
  await read("/v1/creators/"+legacyId+"/heartbeat","POST",{state:"standby"},legacyKey);
  await read("/v1/creators/"+legacyId,"DELETE",undefined,legacyKey);
  let snap=await catalog();
  assert.equal(snap.registrations_complete,true);
  assert.equal(snap.creators.length,1);
  assert.ok(!JSON.stringify(snap).includes("key_hash"));
  await read("/v1/creators","POST",profile,"ef".repeat(32),409);
  await read(path+"/heartbeat","POST",{state:"overloaded"},"invalid",403);
  await read(path+"/heartbeat","POST",{state:"overloaded"},owner);
  assert.equal((await catalog()).creators[0].state,"overloaded");
  now+=121;healthy=false;await probe();
  assert.equal((await catalog()).creators[0].state,"reconnecting");
  now+=130;await probe();
  assert.equal((await catalog()).creators[0].state,"offline");
  now+=86400*31;await probe();
  assert.equal((await catalog()).creators[0].id,first.creator.id);
  await mf.dispose();mf=start();
  assert.equal((await catalog()).creators[0].state,"offline");
  healthy=true;
  await read(path+"/heartbeat","POST",{state:"broadcasting"},owner);
  await probe();
  assert.equal((await catalog()).creators[0].state,"broadcasting");
  now+=121;version="3.5.18";await probe();
  assert.equal((await catalog()).creators.length,0);
  now+=121;version="3.6.2";await probe();
  assert.equal((await catalog()).creators[0].id,first.creator.id);
  const changed = {...profile,...Object.fromEntries(["guide_url","playlist_url","vault_url","status_url"].map(k=>[k,profile[k].replace("studio.","new-studio.")]))};
  const relocated=await read("/v1/creators","POST",changed,owner);
  assert.equal(relocated.api_key,owner);
  assert.equal(relocated.creator.id,first.creator.id);
  assert.equal((await catalog()).creators[0].guide_url,changed.guide_url);
  await mf.dispose();mf=start("alpha4-test");
  const blocked=await request(path,"PUT",profile,owner);
  assert.equal(blocked.status,503);
  await probe();
  const rollback=await mf.getKVNamespace("CREATORS");
  assert.equal((await rollback.get("creator:"+first.creator.id,"json")).guide_url,changed.guide_url);
  assert.equal((await rollback.get("rollback:alpha4-test","json")).complete,true);
  await mf.dispose();mf=start();
  await read(path,"DELETE",undefined,owner);
  await probe();
  assert.deepEqual((await catalog()).creators,[]);
});
