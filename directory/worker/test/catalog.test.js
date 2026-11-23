import assert from "node:assert/strict";
import test from "node:test";

test("simultaneous publications remain independently discoverable and removable", async (t) => {
  const {default: worker} = await import("../src/index.js");
  const records = new Map();
  const metadata = new Map();
  const env = {CREATORS: {
    get: async (key, type) => Array.isArray(key)
      ? new Map(key.map(name => [name, JSON.parse(records.get(name) || "null")]))
      : type === "json" ? JSON.parse(records.get(key) || "null") : records.get(key),
    put: async (key, value, options) => {
      assert.notEqual(key, "catalog:v1");
      records.set(key, value);
      metadata.set(key, options?.metadata);
    },
    list: async ({prefix, cursor}) => {
      const keys = [...records.keys()].filter(name => name.startsWith(prefix));
      const index = cursor ? Number(cursor) : 0;
      return {keys: keys.slice(index, index + 1).map(name => ({name, metadata: metadata.get(name)})),
        list_complete: keys.length <= index + 1, cursor: String(index + 1)};
    },
    delete: async key => records.delete(key),
  }};
  const members = new Set();
  let migrated = false;
  env.STUDIO_INDEX = {getByName: () => ({
    add: key => members.add(key), remove: key => members.delete(key),
    keys: () => [...members], due: (_now, limit) => [...members].slice(0, limit),
    checked: () => {}, claimImport: () => !migrated,
    importLegacy: keys => { for (const key of keys) members.add(key); migrated = true; },
  })};
  let vaultAvailable = true;
  let originAvailable = true;
  let originVersion = "";
  let pagedVault = false;
  t.mock.method(globalThis, "fetch", async url => {
    const path = new URL(url).pathname;
    if (!originAvailable || (!vaultAvailable && path === "/api/public/vault")) {
      return new Response(null, {status: 504});
    }
    return new Response(path.endsWith(".xml") ? "<tv></tv>" : path.endsWith(".m3u") ? "#EXTM3U" :
      path === "/api/public/channels" ? '{"channels":[]}' :
        pagedVault ? '{"items":[],"next_offset":null}' : '{"items":[]}', {
        headers: originVersion ? {"Server": "Public_ChannelForge/" + originVersion} : {},
      });
  });
  const directory = "https://example.com";
  const catalog = async () => (await (await worker.fetch(new Request(directory + "/v1/catalog"), env)).json()).creators;
  async function publish(name, origin) {
    const response = await worker.fetch(new Request(directory + "/v1/creators", {
      method: "POST",
      body: JSON.stringify({name, guide_url: origin + "/exports/guide.xml",
        playlist_url: origin + "/exports/channels.m3u", status_url: origin + "/api/public/channels",
        vault_url: origin + "/api/public/vault"}),
    }), env);
    assert.equal(response.status, 201);
    return response.json();
  }
  const [{creator, api_key: key}, rival] = await Promise.all([
    publish("Test studio 1", "https://first.example.com"),
    publish("Test studio 2", "https://second.example.com"),
  ]);
  const list = env.CREATORS.list;
  // KV may still be propagating a registration when the probe claims its key.
  members.add("creator:pending");
  env.CREATORS.list = () => { throw new Error("KV list() limit exceeded for the day."); };
  assert.deepEqual(await catalog(), [creator, rival.creator]);
  await probe();
  assert.deepEqual(await catalog(), [creator, rival.creator]);
  env.CREATORS.list = list;
  assert.equal(creator.cinema_supported, false);
  await probe();
  const removal = await worker.fetch(new Request(directory + "/v1/creators/" + creator.id, {
    method: "DELETE", headers: {Authorization: "Bearer " + key},
  }), env);
  assert.equal(removal.status, 200);
  assert.deepEqual(await catalog(), [rival.creator]);

  async function probe() {
    let completion;
    await worker.scheduled({}, env, {waitUntil: task => { completion = task; }});
    await completion;
  }
  vaultAvailable = false;
  await probe();
  await probe();
  assert.deepEqual(await catalog(), [rival.creator]);
  originAvailable = false;
  await probe();
  assert.deepEqual(await catalog(), [rival.creator]);
  await probe();
  assert.deepEqual(await catalog(), []);
  originAvailable = true;
  originVersion = "3.5.18";
  await probe();
  assert.deepEqual(await catalog(), []);
  originVersion = "3.5.27";
  await probe();
  const [recovered] = await catalog();
  assert.equal(recovered.id, rival.creator.id);
  assert.equal(recovered.cinema_supported, true);

  originVersion = "";
  vaultAvailable = true;
  pagedVault = true;
  const {creator: proxied} = await publish("Proxied studio", "https://proxied.example.com");
  assert.equal(proxied.cinema_supported, false);
  await probe();
  assert.equal((await catalog()).find(studio => studio.id === proxied.id).cinema_supported, true);
  pagedVault = false;
  await probe();
  assert.equal((await catalog()).find(studio => studio.id === proxied.id).cinema_supported, false);
});
