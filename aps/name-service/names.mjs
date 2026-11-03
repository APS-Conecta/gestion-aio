// SPDX-FileCopyrightText: 2026 APS Conecta
// SPDX-License-Identifier: AGPL-3.0-or-later
// The APS name service's HTTP API: one name, <slug>.<suffix>, per DEIS code of the slug table, and for each a
// credential that changes only that name's address records and, through acme-dns's POST /update, its
// certificate's challenge record.

import { CloudflareError, dnsRecords } from "./cloudflare.mjs";

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
const CHALLENGE = /^[A-Za-z0-9_-]{43}$/; // an ACME DNS-01 value (base64url SHA-256), as acme-dns checks it

const answer = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function secret() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The record an address goes in, with the address as Cloudflare stores it; null for anything else. */
function addressRecord(value) {
  if (typeof value !== "string") return null;
  if (IPV4.test(value)) return { type: "A", content: value };
  if (!/^[0-9A-Fa-f:.]+$/.test(value) || !value.includes(":")) return null;
  try {
    return { type: "AAAA", content: new URL(`http://[${value}]/`).hostname.slice(1, -1) };
  } catch {
    return null;
  }
}

/** Equal hex digests, compared in constant time. */
function same(a, b) {
  let difference = a.length ^ b.length;
  for (let i = 0; i < a.length; i++) difference |= a.charCodeAt(i) ^ b.charCodeAt(i % b.length);
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

export function nameService({ storage, fetch, token, zone, suffix, slugs }) {
  const dns = dnsRecords({ fetch, token, zone });
  const exclusive = serializer();
  const codeKey = (code) => `code:${code}`;
  const nameKey = (slug) => `name:${slug}`;
  const fqdn = (slug) => `${slug}.${suffix}`;

  /** A code takes its name once: neither the code nor the name may be held already. */
  async function claim(request, body) {
    if (!shaped(body, "code") || typeof body.code !== "string") return answer(400, { error: 'a claim is {"code": "<DEIS code>"}' });
    const { code } = body;
    if (!Object.hasOwn(slugs, code)) return answer(404, { error: `${code} is not a DEIS code of the register` });
    return exclusive("claims", async () => {
      const held = (await storage.get(codeKey(code)))?.slug ?? (await storage.get(nameKey(slugs[code])) && slugs[code]);
      if (held) return answer(409, { error: `${fqdn(held)} is already claimed; APS Conecta can release it` });
      const slug = slugs[code];
      const password = secret();
      await storage.put({ [codeKey(code)]: { slug, hash: await sha256(password), txt: [], A: null, AAAA: null },
                          [nameKey(slug)]: code });
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
      if (!record || !same(presented, record.hash)) return answer(401, { error: "unknown credential" });
      return change(code, record);
    });
  }

  /** The server reports its address: IPv4 goes in the name's A record, IPv6 in its AAAA. */
  function address(request, body) {
    return withCredential(request, async (code, record) => {
      const set = shaped(body, "address") && addressRecord(body.address);
      if (!set) return answer(400, { error: 'an address update is {"address": "<IPv4 or IPv6 address>"}' });
      if (record[set.type] !== set.content) {
        await dns.set(fqdn(record.slug), set.type, [set.content]);
        await storage.put({ [codeKey(code)]: { ...record, [set.type]: set.content } });
      }
      return answer(200, { name: fqdn(record.slug), type: set.type, address: set.content });
    });
  }

  /** acme-dns's update: a challenge value for the name's _acme-challenge TXT record. */
  function update(request, body) {
    return withCredential(request, async (code, record) => {
      if (!shaped(body, "subdomain", "txt") || !CHALLENGE.test(body.txt)) {
        return answer(400, { error: 'an update is {"subdomain": "<slug>", "txt": "<ACME challenge value>"}' });
      }
      if (body.subdomain !== record.slug) return answer(403, { error: `this credential changes only ${fqdn(record.slug)}` });
      const txt = [body.txt, ...record.txt.filter((value) => value !== body.txt)].slice(0, 2);
      await dns.set(`_acme-challenge.${fqdn(record.slug)}`, "TXT", txt);
      await storage.put({ [codeKey(code)]: { ...record, txt } });
      return answer(200, { txt: body.txt });
    });
  }

  /** APS frees a code's name: its records go, then the claim, so the code can claim again. */
  async function release(request, body) {
    const bearer = (request.headers.get("Authorization") ?? "").replace(/^Bearer /, "");
    if (!token || !same(await sha256(bearer), await sha256(token))) return answer(401, { error: "release needs APS's token" });
    if (!shaped(body, "code") || typeof body.code !== "string") return answer(400, { error: 'a release is {"code": "<DEIS code>"}' });
    return exclusive(codeKey(body.code), async () => {
      const record = await storage.get(codeKey(body.code));
      if (!record) return answer(404, { error: `${body.code} holds no name` });
      const name = fqdn(record.slug);
      for (const type of ["A", "AAAA"]) await dns.set(name, type, []);
      await dns.set(`_acme-challenge.${name}`, "TXT", []);
      await storage.delete([codeKey(body.code), nameKey(record.slug)]);
      return answer(200, { name });
    });
  }

  const routes = { "/claim": claim, "/address": address, "/update": update, "/release": release };
  return async (request) => {
    const route = Object.hasOwn(routes, new URL(request.url).pathname) && routes[new URL(request.url).pathname];
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
