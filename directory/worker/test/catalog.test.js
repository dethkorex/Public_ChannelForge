import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {Miniflare} from "miniflare";
import {shutdownReport} from "../src/diagnostics.js";

test("public Alpha diagnostics keep allowlisted health measurements and reject oversized reports", async () => {
  let captured;
  const env = {STUDIO_INDEX:{getByName(name) {
    assert.equal(name,"alpha-health:fire-tv");
    return {recordAlphaHealth(report){captured = report; return true;}};
  }}};
  const send = payload => shutdownReport(new Request("https://example.com/v1/diagnostics/exit", {
    method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify(payload)
  }), env);
  const report = {platform:"fire-tv", component:"tv", app:"4.5.5", report_type:"exit", width:1280, height:720,
    firstFrameMs:2140, delivery_path:"managed-vps", title:"Private movie", path:"C:/private/movie.mkv",
    trace_id:"0123456789abcdef0123456789abcdef", url:"https://private.example/stream?token=secret"};
  assert.equal((await send(report)).status,204);
  assert.deepEqual(Object.keys(captured).sort(), ["platform","component","app","report_type","width","height","firstFrameMs","delivery_path"].sort());
  assert.equal((await send({...report, title:"A".repeat(5000)})).status,413);
});

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
    ...await Promise.all(["index","worker","diagnostics"].map(async name => ({
      type:"ESModule",path:"src/"+name+".js",contents:await readFile(new URL("../src/"+name+".js",import.meta.url),"utf8")
    })))];
  let healthy = true, cinemaAvailable = false, version = "3.6.2";
  let channels = ["one"];
  let enrollmentCalls = 0;
  let lastEnrollment;
  let probeStarted, finishSlowProbe;
  function start(rollback = "") {
    return new Miniflare({
      compatibilityDate:"2025-01-01", modules,
      kvNamespaces:["CREATORS"], kvPersist:join(persist,"kv"),
      durableObjects:{STUDIO_INDEX:{className:"StudioIndex",useSQLite:true}},durableObjectsPersist:join(persist,"do"),
      bindings:{OFFLINE_AFTER_SECONDS:"120",ROLLBACK_SNAPSHOT:rollback,
        RELAY_ENROLL_URL:"https://relay.example.com/internal/enroll",RELAY_ENROLL_TOKEN:"test-broker-token"},
      async outboundService(request) {
        const url = new URL(request.url);
        const path = url.pathname;
        if (url.hostname === "relay.example.com") {
          enrollmentCalls++;
          assert.equal(path,"/internal/enroll");
          assert.equal(request.headers.get("authorization"),"Bearer test-broker-token");
          const payload=await request.json();
          lastEnrollment=payload;
          assert.match(payload.studio,/^[a-f0-9-]{12,64}$/);
          if (payload.kind === "identity") {
            assert.equal(payload.nonce,"ef".repeat(6));
            return Response.json({instance:"da".repeat(20)});
          }
          assert.equal(payload.public_key,"ssh-ed25519 "+"A".repeat(68));
          return Response.json({profile:{origin:"https://allocated.example.com"}});
        }
        if (url.hostname === "unreachable.example.com") return new Response(null,{status:502});
        if (url.hostname === "slow.example.com") {
          await new Promise(resolve => { finishSlowProbe = resolve; probeStarted?.(); });
        }
        if (!healthy || path.endsWith("/vault") && !cinemaAvailable) return new Response(null,{status:504});
        const inventory = channels.map((id, index) => ({id, name: "Channel " + id, number: index+1, online:true,
          state:"standby", channel_rating:"PG-13", channel_rating_rank:5,
          stream_url:url.origin+"/live/"+id+".m3u8", continuous_url:url.origin+"/broadcast/"+id+"/master.m3u8"}));
        inventory.push({id:"elsewhere", name:"Invalid stream", online:true, stream_url:"https://other.example/live/elsewhere.m3u8"});
        if (path.startsWith("/studio/")) {
          const slot = path.split("/")[2];
          inventory[0].stream_url = url.origin+"/studio/"+slot+"/live/"+inventory[0].id+".m3u8";
          inventory[0].continuous_url = "/broadcast/"+inventory[0].id+"/master.m3u8";
          inventory.push({id:"other-slot", name:"Different Studio", online:true,
            stream_url:url.origin+"/studio/"+("b".repeat(16))+"/live/other-slot.m3u8"});
        }
        inventory.push({id:"old", name:"Finished channel", online:true, stream_url:url.origin+"/live/old.m3u8", air_until_epoch_ms:100});
        return new Response(path.endsWith(".m3u")?"#EXTM3U":JSON.stringify({channels:inventory}),{headers:{"x-channelforge-version":version}});
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
  const relayRequest = {public_key:"ssh-ed25519 "+"A".repeat(68)};
  await read(path+"/relay","POST",relayRequest,"not-the-owner",403);
  await read(path+"/relay","POST",{public_key:"not-a-key"},owner,400);
  assert.equal(enrollmentCalls,0);
  assert.equal((await read(path+"/relay","POST",relayRequest,owner)).profile.origin,"https://allocated.example.com");
  assert.equal(enrollmentCalls,1);
  assert.equal((await read(path+"/relay","POST",{kind:"identity",nonce:"ef".repeat(6)},owner)).instance,"da".repeat(20));
  assert.equal(enrollmentCalls,2);
  const bootstrapKey = "cafe".repeat(16);
  await read("/v1/creators/relay","POST",relayRequest,"invalid",403);
  await read("/v1/creators/relay","POST",relayRequest,bootstrapKey);
  const pendingId = lastEnrollment.studio;
  assert.equal(lastEnrollment.provisional,true);
  assert.equal((await read("/v1/catalog")).creators.length,1);
  const scopedBase = "https://fresh.example.com/studio/" + "a".repeat(16);
  const bootstrapProfile = {...profile, name:"Fresh Studio", slug:"fresh-studio",
    ...Object.fromEntries(["guide_url","playlist_url","vault_url","status_url"].map(k=>[k, profile[k].replace("https://studio.example.com",scopedBase)]))};
  await read("/v1/creators","POST",
    {...bootstrapProfile,status_url:bootstrapProfile.status_url.replace("a".repeat(16),"b".repeat(16))},bootstrapKey,400);
  const bootstrapped=await read("/v1/creators","POST",bootstrapProfile,bootstrapKey,201);
  assert.equal(bootstrapped.creator.id,pendingId);
  const scopedInventory = (await read("/v1/catalog")).creators.find(row=>row.id===pendingId).channel_inventory;
  assert.deepEqual(scopedInventory.map(row=>row.stream_url),[scopedBase+"/live/one.m3u8"]);
  assert.equal(scopedInventory[0].continuous_url,scopedBase+"/broadcast/one/master.m3u8");
  assert.equal(scopedInventory[0].channel_rating,"PG-13");
  assert.equal(scopedInventory[0].channel_rating_rank,5);
  await read("/v1/creators/"+pendingId+"/relay","POST",relayRequest,bootstrapKey);
  assert.equal(lastEnrollment.registered_origin,scopedBase);
  assert.equal(lastEnrollment.provisional,undefined);
  const numberOps = (id, secret, mode, channels, status=200) =>
    read("/v1/creators/"+id+"/channel-numbers","POST",{mode,channels},secret,status);
  const available = async number => (await read("/v1/channel-number?number="+number)).available;
  const otherKey = "a0".repeat(32);
  const other = (await read("/v1/creators","POST",{...profile,name:"Number test Studio",slug:"number-test"},otherKey,201)).creator.id;
  const numberPath = "/v1/creators/" + other;
  assert.equal(await available("257"),true);
  await numberOps(other,otherKey,"reserve",[{id:"new-channel",number:"0257"}]);
  assert.equal(await available("257"),false);
  await read("/v1/creators","POST",{...profile,name:"Conflicting Studio",slug:"number-conflict",
    channel_numbers:[{id:"one",number:"257"}]},"a1".repeat(32),409);
  await numberOps(pendingId,bootstrapKey,"reserve",[{id:"other-channel",number:"257"}],409);
  await read(numberPath+"/heartbeat","POST",{state:"offline"},otherKey);
  assert.equal(await available("257"),false);
  await numberOps(other,otherKey,"sync",[{id:"one",number:"1"}]);
  assert.equal(await available("257"),true);
  await numberOps(pendingId,bootstrapKey,"reserve",[{id:"other-channel",number:"257"}]);
  assert.equal(await available("0257"),false);
  const concurrent = await Promise.all([
    request(numberPath+"/channel-numbers","POST",{mode:"reserve",channels:[{id:"one",number:"258"}]},otherKey),
    request("/v1/creators/"+pendingId+"/channel-numbers","POST",{mode:"reserve",channels:[{id:"other-channel",number:"258"}]},bootstrapKey),
  ]);
  assert.deepEqual(concurrent.map(response=>response.status).sort(),[200,409]);
  await Promise.all(concurrent.map(response=>response.body.cancel()));
  await read(numberPath,"DELETE",undefined,otherKey);
  await read("/v1/creators/"+pendingId,"DELETE",undefined,bootstrapKey);
  assert.equal(await available("257"),true);
  assert.equal(await available("258"),true);
  const catalog=()=>read("/v1/catalog");
  const catalogChannels = async () => (await catalog()).creators.find(x=>x.id===first.creator.id)?.channel_inventory?.map(x=>x.id);
  assert.deepEqual(await catalogChannels(), ["one"]);
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
  channels = ["two"];
  now+=121;
  await probe();
  assert.deepEqual(await catalogChannels(), ["two"]);
  await read("/v1/creators","POST",profile,"ef".repeat(32),409);
  await read(path+"/heartbeat","POST",{state:"overloaded"},"invalid",403);
  await read(path+"/heartbeat","POST",{state:"overloaded"},owner);
  assert.equal((await catalog()).creators[0].state,"overloaded");
  now+=121;healthy=false;await probe();
  assert.equal((await catalog()).creators[0].state,"reconnecting");
  now+=130;await probe();
  assert.equal((await catalog()).creators[0].state,"offline");
  assert.equal(await catalogChannels(), undefined);
  now+=86400*31;await probe();
  assert.equal((await catalog()).creators[0].id,first.creator.id);
  await mf.dispose();mf=start();
  assert.equal((await catalog()).creators[0].state,"offline");
  healthy=true;
  await read(path+"/heartbeat","POST",{state:"broadcasting"},owner);
  await probe();
  assert.equal((await catalog()).creators[0].state,"broadcasting");
  await read(path+"/heartbeat","POST",{state:"offline"},owner);
  snap = await catalog();
  assert.equal(snap.creators[0].id,first.creator.id);
  assert.equal(snap.creators[0].online,false);
  assert.equal(snap.creators[0].state,"offline");
  await probe();
  assert.equal((await catalog()).creators[0].state,"offline");
  await read(path+"/heartbeat","POST",{state:"standby"},owner);
  assert.equal((await catalog()).creators[0].state,"standby");
  now+=121;version="3.5.18";await probe();
  assert.equal((await catalog()).creators.length,0);
  now+=121;version="3.6.2";await probe();
  assert.equal((await catalog()).creators[0].id,first.creator.id);
  const changed = {...profile,...Object.fromEntries(["guide_url","playlist_url","vault_url","status_url"].map(k=>[k,profile[k].replace("studio.","new-studio.")]))};
  const route = host => Object.fromEntries(Object.entries(profile).map(([key,value]) =>
    [key, typeof value === "string" ? value.replace("studio.example.com", host + ".example.com") : value]));
  await read(path,"PUT",route("unreachable"),owner,422);
  await read("/v1/creators","POST",route("unreachable"),owner,422);
  assert.equal((await catalog()).creators[0].guide_url,profile.guide_url);
  assert.equal((await catalog()).creators[0].online,true);
  const slowStarted = new Promise(resolve => { probeStarted = resolve; });
  const outdated = read(path,"PUT",route("slow"),owner,409);
  await slowStarted;
  const relocated=await read("/v1/creators","POST",changed,owner);
  finishSlowProbe();
  await outdated;
  assert.equal(relocated.api_key,owner);
  assert.equal(relocated.creator.id,first.creator.id);
  assert.equal((await catalog()).creators[0].guide_url,changed.guide_url);
  channels = ["0a1b2c3d4e5f6789"];
  now += 125;
  await probe();
  now++;
  const shadow = await read("/v1/creators","POST",{...route("restarted"),slug:"restarted-studio"},"cd".repeat(32),201);
  assert.deepEqual((await catalog()).creators.map(row=>row.id), [shadow.creator.id]);
  channels = ["123456789abcdef0"];
  now++;
  const separate = await read("/v1/creators","POST",{...route("separate"),slug:"separate-studio"},"ac".repeat(32),201);
  assert.deepEqual(new Set((await catalog()).creators.map(row=>row.id)), new Set([shadow.creator.id,separate.creator.id]));
  await read("/v1/creators/"+shadow.creator.id,"DELETE",undefined,"cd".repeat(32));
  await read("/v1/creators/"+separate.creator.id,"DELETE",undefined,"ac".repeat(32));
  assert.deepEqual((await catalog()).creators.map(row=>row.id), [first.creator.id]);
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
