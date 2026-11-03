// SPDX-FileCopyrightText: 2026 APS Conecta
// SPDX-License-Identifier: AGPL-3.0-or-later
// In-memory fakes of what the name service runs on: Cloudflare's DNS API (as a fetch) and Durable Object
// storage. Both yield to the event loop on every call, as the real ones do, so concurrent requests interleave.

const tick = () => new Promise((resolve) => setImmediate(resolve));

export function fakeCloudflare({ token, zone }) {
  const zoneId = "zone-fixture";
  const base = `/client/v4/zones/${zoneId}/dns_records`;
  const records = new Map();
  let next = 1;
  let failing = false;
  const reply = (status, result) =>
    new Response(JSON.stringify({ success: status < 400, errors: [], result }), { status });

  async function fetch(input, init = {}) {
    await tick();
    const url = new URL(input);
    const method = init.method ?? "GET";
    fake.calls += 1;
    if (failing) return reply(503, null);
    if (new Headers(init.headers).get("Authorization") !== `Bearer ${token}`) return reply(403, null);
    const query = Object.fromEntries(url.searchParams);
    if (method === "GET" && url.pathname === "/client/v4/zones") {
      return reply(200, query.name === zone ? [{ id: zoneId, name: zone }] : []);
    }
    if (url.pathname === base && method === "GET") {
      return reply(200, [...records.values()].filter((r) => (!query.type || r.type === query.type) &&
        (!query.name || r.name === query.name)));
    }
    const body = init.body ? JSON.parse(init.body) : {};
    if (url.pathname === base && method === "POST") {
      if (!body.name?.endsWith(`.${zone}`)) return reply(400, null);
      const record = { id: String(next++), type: body.type, name: body.name, content: body.content, ttl: body.ttl };
      records.set(record.id, record);
      return reply(200, record);
    }
    const record = url.pathname.startsWith(`${base}/`) && records.get(url.pathname.slice(base.length + 1));
    if (record && method === "PATCH") return reply(200, Object.assign(record, { content: body.content }));
    if (record && method === "DELETE") return records.delete(record.id) && reply(200, { id: record.id });
    return reply(404, null);
  }

  const fake = {
    fetch,
    calls: 0,
    fail(on) { failing = on; },
    /** Every record, as `type name content`, sorted. */
    records: () => [...records.values()].map((r) => `${r.type} ${r.name} ${r.content}`).sort(),
  };
  return fake;
}

export function fakeStorage() {
  const data = new Map();
  return {
    data,
    async get(key) { await tick(); return structuredClone(data.get(key)); },
    async put(entries) { await tick(); for (const [k, v] of Object.entries(entries)) data.set(k, structuredClone(v)); },
    async delete(keys) { await tick(); for (const k of keys) data.delete(k); },
  };
}
