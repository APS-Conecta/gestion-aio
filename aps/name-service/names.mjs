// SPDX-FileCopyrightText: 2026 APS Conecta
// SPDX-License-Identifier: AGPL-3.0-or-later
// The APS name service's HTTP API: one name, <slug>.<suffix>, per DEIS code of the slug table, and for each a
// credential that changes only that name's address records and, through acme-dns's POST /update, its
// certificate's challenge record, and that can release the name. Claims need no account, so a limit per client
// address and a rolling weekly budget slow squatting but cannot prevent it; APS releases a name a squatter took
// (accepted risk). The test establishments' codes (`tests`, by code their slug) claim their names only with APS's
// token, so no one but a release's acceptance run holds them.

import { CloudflareError, dnsRecords } from "./cloudflare.mjs";

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
const CHALLENGE = /^[A-Za-z0-9_-]{43}$/; // an ACME DNS-01 value (base64url SHA-256), as acme-dns checks it
const IPV4_MAPPED = /^::ffff:[0-9a-f]{1,4}:[0-9a-f]{1,4}$/; // an IPv4 address dressed as IPv6, normalized
const CHANGES_PER_MINUTE = 5; // Cloudflare changes one code may make: a certificate takes one or two
const DAY = 86_400_000;
const WEEK = 7 * DAY;

const answer = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function secret() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The record an address goes in, with the address as Cloudflare stores it; null for anything else, an
IPv4-mapped IPv6 address included: its IPv4 address belongs in the A record. */
function addressRecord(value) {
  if (typeof value !== "string") return null;
  if (IPV4.test(value)) return { type: "A", content: value };
  if (!/^[0-9A-Fa-f:.]+$/.test(value) || !value.includes(":")) return null;
  try {
    const content = new URL(`http://[${value}]/`).hostname.slice(1, -1);
    return IPV4_MAPPED.test(content) ? null : { type: "AAAA", content };
  } catch {
    return null;
  }
}

/** Two SHA-256 hex digests compared in constant time. */
function equalDigests(a, b) {
  let difference = 0;
  for (let i = 0; i < a.length; i++) difference |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return difference === 0;
}

async function sha256(text) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  return Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** A JSON object with exactly these keys. */
const shaped = (body, ...keys) =>
  body !== null && typeof body === "object" && !Array.isArray(body) &&
  Object.keys(body).length === keys.length && keys.every((k) => Object.hasOwn(body, k));

/** The DEIS code a claim or a release names, or null when the body is not {"code": "<code>"}. */
const codeIn = (body) => (shaped(body, "code") && typeof body.code === "string" ? body.code : null);

/** The table's own entry for a code: never one Object.prototype lends it. */
const entry = (table, code) => (Object.hasOwn(table, code) ? table[code] : undefined);

/** Whether `key` may make one more change: at most CHANGES_PER_MINUTE in the last minute of `now()`. */
function rateLimiter(now) {
  const recent = new Map();
  return (key) => {
    const since = now() - 60_000;
    const times = (recent.get(key) ?? []).filter((t) => t > since);
    if (times.length >= CHANGES_PER_MINUTE) return false;
    recent.set(key, [...times, now()]);
    return true;
  };
}

/** Runs tasks with the same key one after another, so a read, its Cloudflare calls and its write never interleave. */
function serializer() {
  const tails = new Map();
  return (key, task) => {
    const run = (tails.get(key) ?? Promise.resolve()).then(task);
    const tail = run.catch(() => {});
    tails.set(key, tail);
    tail.then(() => tails.get(key) === tail && tails.delete(key));
    return run;
  };
}

export function nameService({ storage, fetch, token, zone, suffix, slugs, tests = {}, claimLimits, now = Date.now }) {
  const dns = dnsRecords({ fetch, token, zone });
  const exclusive = serializer();
  const mayChange = rateLimiter(now);
  const tooMany = (record) => answer(429, { error: `too many changes for ${fqdn(record.slug)}; retry in a minute` });
  const codeKey = (code) => `code:${code}`;
  const nameKey = (slug) => `name:${slug}`;
  const fqdn = (slug) => `${slug}.${suffix}`;

  /** Whether the request carries APS's token as its bearer. */
  async function byAps(request) {
    const bearer = (request.headers.get("Authorization") ?? "").replace(/^Bearer /, "");
    return Boolean(token) && equalDigests(await sha256(bearer), await sha256(token));
  }

  /** A code takes its name once: neither the code nor the name may be held already. Each claim is logged (its
   * time and a hash of the client address) for a week, to keep within claimLimits; a release gives none back. */
  async function claim(request, body) {
    const code = codeIn(body);
    if (code === null) return answer(400, { error: 'a claim is {"code": "<DEIS code>"}' });
    const slug = entry(slugs, code) ?? entry(tests, code);
    if (slug === undefined) return answer(404, { error: `${code} is not a DEIS code of the register` });
    if (entry(slugs, code) === undefined && !(await byAps(request))) {
      return answer(401, { error: `${code} is a test establishment's: its name needs APS's token` });
    }
    return exclusive("claims", async () => {
      const held = (await storage.get(codeKey(code)))?.slug ?? ((await storage.get(nameKey(slug))) && slug);
      if (held) return answer(409, { error: `${fqdn(held)} is already claimed; APS Conecta can release it` });
      const log = ((await storage.get("claims")) ?? []).filter(([at]) => at > now() - WEEK);
      const client = (await sha256(request.headers.get("CF-Connecting-IP") ?? "")).slice(0, 16);
      if (log.filter(([at, by]) => by === client && at > now() - DAY).length >= claimLimits.perIpPerDay) {
        return answer(429, { error: `this address claimed ${claimLimits.perIpPerDay} names today; retry tomorrow` });
      }
      if (log.length >= claimLimits.perWeek) {
        return answer(429, { error: `the name service gave out ${claimLimits.perWeek} names this week; retry later` });
      }
      const password = secret();
      await storage.put({ [codeKey(code)]: { slug, hash: await sha256(password), txt: [], A: null, AAAA: null },
                          [nameKey(slug)]: code, claims: [...log, [now(), client]] });
      return answer(201, { name: fqdn(slug), username: code, password, subdomain: slug,
                           server_url: new URL(request.url).origin });
    });
  }

  /** Runs `change` with the code and record the X-Api-User/X-Api-Key credential holds, one change per code at a time. */
  function withCredential(request, change) {
    const code = request.headers.get("X-Api-User") ?? "";
    return exclusive(codeKey(code), async () => {
      const record = await storage.get(codeKey(code));
      const presented = await sha256(request.headers.get("X-Api-Key") ?? "");
      if (!record || !equalDigests(presented, record.hash)) return answer(401, { error: "unknown credential" });
      return change(code, record);
    });
  }

  /** The server reports its address and the name points there only: IPv4 in the A record, IPv6 in the AAAA. */
  function address(request, body) {
    return withCredential(request, async (code, record) => {
      const reported = shaped(body, "address") && addressRecord(body.address);
      if (!reported) return answer(400, { error: 'an address update is {"address": "<IPv4 or IPv6 address>"}' });
      const addresses = { A: null, AAAA: null, [reported.type]: reported.content };
      const changed = ["A", "AAAA"].filter((t) => record[t] !== addresses[t]);
      if (changed.length && !mayChange(code)) return tooMany(record);
      for (const type of changed) {
        await dns.set(fqdn(record.slug), type, addresses[type] ? [addresses[type]] : []);
        await storage.put({ [codeKey(code)]: (record = { ...record, [type]: addresses[type] }) });
      }
      return answer(200, { name: fqdn(record.slug), type: reported.type, address: reported.content });
    });
  }

  /** acme-dns's update: a challenge value for the name's _acme-challenge TXT record. */
  function update(request, body) {
    return withCredential(request, async (code, record) => {
      if (!shaped(body, "subdomain", "txt") || typeof body.txt !== "string" || !CHALLENGE.test(body.txt)) {
        return answer(400, { error: 'an update is {"subdomain": "<slug>", "txt": "<ACME challenge value>"}' });
      }
      if (body.subdomain !== record.slug) return answer(403, { error: `this credential changes only ${fqdn(record.slug)}` });
      if (!mayChange(code)) return tooMany(record);
      const txt = [body.txt, ...record.txt.filter((value) => value !== body.txt)].slice(0, 2);
      await dns.set(`_acme-challenge.${fqdn(record.slug)}`, "TXT", txt);
      await storage.put({ [codeKey(code)]: { ...record, txt } });
      return answer(200, { txt: body.txt });
    });
  }

  /** Frees a code's name: its records go, then the claim, so the code can claim again. */
  async function free(code, record) {
    const name = fqdn(record.slug);
    for (const type of ["A", "AAAA"]) await dns.set(name, type, []);
    await dns.set(`_acme-challenge.${name}`, "TXT", []);
    await storage.delete([codeKey(code), nameKey(record.slug)]);
    return answer(200, { name });
  }

  /** A release, {"code": "<DEIS code>"}, frees that code's name. APS's token frees any code's; a credential, sent in
   * the X-Api-User/X-Api-Key headers as to /address and /update, frees only its own. */
  async function release(request, body) {
    if (request.headers.has("X-Api-User")) {
      return withCredential(request, (code, record) => {
        if (codeIn(body) === null) return answer(400, { error: 'a release is {"code": "<DEIS code>"}' });
        if (codeIn(body) !== code) return answer(403, { error: `this credential releases only ${fqdn(record.slug)}` });
        if (!mayChange(code)) return tooMany(record);
        return free(code, record);
      });
    }
    if (!(await byAps(request))) return answer(401, { error: "release needs APS's token" });
    const code = codeIn(body);
    if (code === null) return answer(400, { error: 'a release is {"code": "<DEIS code>"}' });
    return exclusive(codeKey(code), async () => {
      const record = await storage.get(codeKey(code));
      if (!record) return answer(404, { error: `${code} holds no name` });
      return free(code, record);
    });
  }

  const routes = { "/claim": claim, "/address": address, "/update": update, "/release": release };
  return async (request) => {
    const { pathname } = new URL(request.url);
    const route = Object.hasOwn(routes, pathname) && routes[pathname];
    if (!route) return answer(404, { error: "no such endpoint" });
    if (request.method !== "POST") return answer(405, { error: "only POST" });
    let body;
    try {
      body = await request.json();
    } catch {
      return answer(400, { error: "the body is not JSON" });
    }
    try {
      return await route(request, body);
    } catch (error) {
      if (error instanceof CloudflareError) return answer(502, { error: error.message });
      throw error;
    }
  };
}
