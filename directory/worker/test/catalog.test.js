import assert from "node:assert/strict";
import test from "node:test";

test("publishing updates the catalog even while the KV key listing is stale", async (t) => {
  const {default: worker} = await import("../src/index.js");
  const records = new Map([["catalog:v1", JSON.stringify({version: 1, creators: []})]]);
  const env = {CREATORS: {
    get: async (key, type) => type === "json" ? JSON.parse(records.get(key) || "null") : records.get(key),
    put: async (key, value) => records.set(key, value),
    list: async () => ({keys: []}),
    delete: async key => records.delete(key),
  }};
  t.mock.method(globalThis, "fetch", async url => new Response(
    url.endsWith(".xml") ? "<tv></tv>" : url.endsWith(".m3u") ? "#EXTM3U" : '{"items":[]}'
  ));
  const directory = "https://example.com";
  const origin = "https://studio.example.com";
  const catalog = async () => (await (await worker.fetch(new Request(directory + "/v1/catalog"), env)).json()).creators;
  const publication = await worker.fetch(new Request(directory + "/v1/creators", {
    method: "POST",
    body: JSON.stringify({
      name: "Test studio",
      guide_url: origin + "/exports/guide.xml",
      playlist_url: origin + "/exports/channels.m3u",
      vault_url: origin + "/api/public/vault",
    }),
  }), env);
  assert.equal(publication.status, 201);
  const {creator, api_key: key} = await publication.json();
  assert.deepEqual(await catalog(), [creator]);
  const removal = await worker.fetch(new Request(directory + "/v1/creators/" + creator.id, {
    method: "DELETE", headers: {Authorization: "Bearer " + key},
  }), env);
  assert.equal(removal.status, 200);
  assert.deepEqual(await catalog(), []);
});
