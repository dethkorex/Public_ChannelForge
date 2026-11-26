import {execFileSync} from "node:child_process";
import {createRequire} from "node:module";
import {readFile} from "node:fs/promises";

const require = createRequire(new URL("../directory/worker/package.json", import.meta.url));
const {Miniflare} = require("miniflare");
const root = new URL("../", import.meta.url);
const base = "https://directory.example";
const harness = `
import directory from "./src/index.js";
export {StudioIndex} from "./src/worker.js";
export default {
  async fetch(request, env) {
    Date.now = () => Number(request.headers.get("clock")) * 1000;
    if (new URL(request.url).pathname === "/probe") {
      let task;
      await directory.scheduled({}, env, {waitUntil(value) {task = value;}});
      await task;
      return new Response("ok");
    }
    return directory.fetch(request, env);
  }
};`;

async function measure(revision) {
  const modules = [{type:"ESModule", path:"entry.js", contents:harness}];
  for (const name of ["worker","index"]) {
    const path = `directory/worker/src/${name}.js`;
    const contents = revision === "working-tree" ? await readFile(new URL(path,root),"utf8")
      : execFileSync("git", ["show", revision + ":" + path], {cwd:root, encoding:"utf8"});
    modules.push({type:"ESModule", path:`src/${name}.js`, contents});
  }
  let reachable = true, probes = 0;
  const mf = new Miniflare({
    modules, compatibilityDate:"2025-01-01", kvNamespaces:["CREATORS"],
    durableObjects:{STUDIO_INDEX:{className:"StudioIndex", useSQLite:true}},
    outboundService(request) {
      probes++;
      if (!reachable) return new Response(null,{status:502});
      const path = new URL(request.url).pathname;
      const body = path.endsWith(".m3u") ? "#EXTM3U" : path.endsWith(".xml") ? "<tv/>" : '{"items":[],"channels":[]}';
      return new Response(body,{headers:{"x-channelforge-version":"3.6.2"}});
    }
  });
  function call(path, clock, body) {
    return mf.dispatchFetch(base+path, {method:body?"POST":"GET", headers:{clock:String(clock)}, body:body && JSON.stringify(body)});
  }
  async function catalog(clock) {return (await (await call("/v1/catalog",clock)).json()).creators;}
  try {
    const profile = {name:"Fixture",creator_version:"3.6.2",guide_url:base+"/exports/guide.xml",
      playlist_url:base+"/exports/channels.m3u",status_url:base+"/api/public/channels",vault_url:base+"/api/public/vault"};
    const publication = await call("/v1/creators",100000,profile);
    if (publication.status !== 201) throw new Error("Test registration failed: "+publication.status);
    const originRequestsToRegister = probes;
    await call("/probe",100000);
    const start = performance.now();
    for (let i=0; i<50; i++) await catalog(100005+i);
    const avgLocalUncachedCatalogMs = Number(((performance.now()-start)/50).toFixed(2));
    reachable=false;
    await call("/probe",100121);
    await call("/probe",100242);
    const failed = await catalog(100242);
    reachable=true;
    await call("/probe",100920);
    return {revision,originRequestsToRegister,avgLocalUncachedCatalogMs,
      countAfterTwoFailedProbes:failed.length, state:failed[0]?.state, countAfterRecovery:(await catalog(100920)).length};
  } finally {await mf.dispose();}
}
for (const revision of ["eec8838","working-tree"]) console.log(JSON.stringify(await measure(revision)));
