// SPDX-FileCopyrightText: 2026 APS Conecta
// SPDX-License-Identifier: AGPL-3.0-or-later
// Seam 4: the name service's HTTP API against in-memory fakes of Cloudflare's DNS API and of its storage.
// Fixture codes only: the DEIS-code gate proves no real register code is named here.

import assert from "node:assert/strict";
import { test } from "node:test";
import { fakeCloudflare, fakeStorage } from "./fakes.mjs";
import { nameService } from "./names.mjs";

const TOKEN = "cloudflare-token-fixture";
const ZONE = "apsconecta.cl";
const ORIGIN = "https://gestion-nombres.apsconecta.workers.dev";
const SLUGS = { 999001: "cesfam-los-castanos", 999002: "cecosf-estrecho" };
const CASTANOS = "cesfam-los-castanos.gestion.apsconecta.cl";
const ESTRECHO = "cecosf-estrecho.gestion.apsconecta.cl";
const CHALLENGE = (n) => String(n).padEnd(43, "x");
const LIMITS = { perIpPerDay: 3, perWeek: 50 }; // production's, from wrangler.jsonc
const DAY = 86_400_000;
const MANY = Object.fromEntries(Array.from({ length: 60 }, (_, i) => [String(999100 + i), `centro-${i}`]));

function deployment({ suffix = "gestion.apsconecta.cl", table = SLUGS } = {}) {
  const cloudflare = fakeCloudflare({ token: TOKEN, zone: ZONE });
  const storage = fakeStorage();
  const clock = { ms: 0 };
  const start = (slugs) =>
    nameService({ storage, fetch: cloudflare.fetch, token: TOKEN, zone: ZONE, suffix, slugs, claimLimits: LIMITS,
                  now: () => clock.ms });
  let serve = start(table);
  const post = async (path, body, headers = {}) => {
    const response = await serve(new Request(ORIGIN + path, {
      method: "POST", headers: { "Content-Type": "application/json", ...headers },
      body: typeof body === "string" ? body : JSON.stringify(body),
    }));
    return { status: response.status, body: await response.json() };
  };
  return {
    cloudflare, storage, post, clock,
    /** A redeploy, with the slug table of a later register if given. */
    restart(slugs = table) { serve = start(slugs); },
    serve: (request) => serve(request),
    claim: (code, ip = "198.51.100.1") => post("/claim", { code }, { "CF-Connecting-IP": ip }),
    as: (credential) => ({ "X-Api-User": credential.username, "X-Api-Key": credential.password }),
    release: (code, token = TOKEN) => post("/release", { code }, { Authorization: `Bearer ${token}` }),
  };
}

test("a claim answers the code's name and a credential for it", async () => {
  const names = deployment();
  const { status, body } = await names.claim("999001");
  assert.equal(status, 201);
  assert.match(body.password, /^[A-Za-z0-9_-]{43}$/);
  assert.deepEqual({ ...body, password: "" }, {
    name: CASTANOS, username: "999001", password: "", subdomain: "cesfam-los-castanos", server_url: ORIGIN,
  });
});

test("a second claim for the code is refused, after a restart too, and the first credential keeps working", async () => {
  const names = deployment();
  const first = (await names.claim("999001")).body;
  names.restart();
  const { status, body } = await names.claim("999001");
  assert.equal(status, 409);
  assert.match(body.error, new RegExp(`${CASTANOS} is already claimed.*APS Conecta can release it`));
  assert.equal((await names.post("/address", { address: "192.168.1.10" }, names.as(first))).status, 200);
});

test("a claimed name stays the code's when a later register renames it or gives its slug to another code", async () => {
  const names = deployment();
  const castanos = (await names.claim("999001")).body;
  names.restart({ 999001: "cesfam-los-castanos-santiago", 999002: "cesfam-los-castanos" });
  const { status, body } = await names.claim("999002");
  assert.equal(status, 409);
  assert.match(body.error, new RegExp(`${CASTANOS} is already claimed`));
  assert.equal((await names.claim("999001")).status, 409);
  assert.deepEqual((await names.post("/address", { address: "10.0.0.9" }, names.as(castanos))).body,
                   { name: CASTANOS, type: "A", address: "10.0.0.9" });
  await names.post("/update", { subdomain: castanos.subdomain, txt: CHALLENGE(1) }, names.as(castanos));
  assert.deepEqual(names.cloudflare.records(), [`A ${CASTANOS} 10.0.0.9`, `TXT _acme-challenge.${CASTANOS} ${CHALLENGE(1)}`]);
  assert.deepEqual((await names.release("999001")).body, { name: CASTANOS });
  assert.deepEqual(names.cloudflare.records(), []);
});

test("an address claims at most three names a day; the fourth answers 429 and claims nothing", async () => {
  const names = deployment({ table: MANY });
  for (const code of ["999100", "999101", "999102"]) assert.equal((await names.claim(code, "203.0.113.5")).status, 201);
  const { status, body } = await names.claim("999103", "203.0.113.5");
  assert.equal(status, 429);
  assert.match(body.error, /3 names today/);
  assert.equal((await names.claim("999103", "203.0.113.6")).status, 201, "another address");
  names.restart();
  assert.equal((await names.claim("999104", "203.0.113.5")).status, 429, "after a restart too");
  names.clock.ms += DAY;
  assert.equal((await names.claim("999104", "203.0.113.5")).status, 201);
});

test("all addresses together claim at most 50 names a rolling week, and a release gives none back", async () => {
  const names = deployment({ table: MANY });
  for (let i = 0; i < 50; i++) assert.equal((await names.claim(String(999100 + i), `10.0.${i}.1`)).status, 201);
  assert.equal((await names.release("999100")).status, 200);
  names.restart();
  const { status, body } = await names.claim("999150", "10.1.0.1");
  assert.equal(status, 429);
  assert.match(body.error, /50 names this week/);
  names.clock.ms += 7 * DAY;
  assert.equal((await names.claim("999150", "10.1.0.1")).status, 201);
});

test("concurrent claims for one code hand out one credential", async () => {
  const names = deployment();
  const answers = await Promise.all([names.claim("999001"), names.claim("999001"), names.claim("999001")]);
  assert.deepEqual(answers.map((a) => a.status).sort(), [201, 409, 409]);
});

test("a code outside the register, or a claim that is not one code, is refused", async () => {
  const names = deployment();
  for (const code of ["999999", "__proto__", "constructor", "", 999001, null]) {
    const { status, body } = await names.claim(code);
    assert.equal(status, typeof code === "string" ? 404 : 400, String(code));
    assert.ok(body.error, String(code));
  }
  assert.equal((await names.post("/claim", { code: "999001", name: "otro" })).status, 400);
  assert.equal(names.storage.data.size, 0);
});

test("storage keeps only a hash of the credential", async () => {
  const names = deployment();
  const { password } = (await names.claim("999001")).body;
  assert.ok(!JSON.stringify([...names.storage.data]).includes(password));
});

test("the address update accepts any valid address: IPv4 goes in the A record, IPv6 in the AAAA", async () => {
  const names = deployment();
  const castanos = names.as((await names.claim("999001")).body);
  for (const [address, type, record] of [["192.168.1.10", "A", "192.168.1.10"], ["203.0.113.7", "A", "203.0.113.7"],
                                         ["fd00::A:10", "AAAA", "fd00::a:10"], ["192.168.1.20", "A", "192.168.1.20"]]) {
    const { status, body } = await names.post("/address", { address }, castanos);
    assert.equal(status, 200, address);
    assert.deepEqual(body, { name: CASTANOS, type, address: record });
    assert.deepEqual(names.cloudflare.records(), [`${type} ${CASTANOS} ${record}`], "the name follows the latest address only");
  }
});

test("an unchanged address makes no Cloudflare call", async () => {
  const names = deployment();
  const castanos = names.as((await names.claim("999001")).body);
  await names.post("/address", { address: "10.0.0.5" }, castanos);
  const calls = names.cloudflare.calls;
  assert.equal((await names.post("/address", { address: "10.0.0.5" }, castanos)).status, 200);
  assert.equal(names.cloudflare.calls, calls);
});

test("anything but an IPv4 or IPv6 address, or another record type or name, is refused and changes nothing", async () => {
  const names = deployment();
  const castanos = names.as((await names.claim("999001")).body);
  for (const address of ["999.1.1.1", "1.2.3", "01.2.3.4", "1.2.3.4 ", "::g", "fe80::1%eth0", "::1]@evil.example/[", "::ffff:192.168.1.10", "::FFFF:c0a8:10a",
                         "cesfam.example.com", "", 3232235786, null]) {
    const { status, body } = await names.post("/address", { address }, castanos);
    assert.equal(status, 400, String(address));
    assert.ok(body.error, String(address));
  }
  for (const extra of [{ type: "CNAME" }, { type: "MX" }, { name: ESTRECHO }]) {
    assert.equal((await names.post("/address", { address: "10.0.0.5", ...extra }, castanos)).status, 400);
  }
  assert.deepEqual(names.cloudflare.records(), []);
});

test("a credential changes only its own name; a wrong or missing one changes nothing", async () => {
  const names = deployment();
  const castanos = (await names.claim("999001")).body;
  const estrecho = (await names.claim("999002")).body;
  await names.post("/address", { address: "10.0.0.2" }, names.as(estrecho));
  const before = names.cloudflare.records();
  for (const headers of [{}, names.as({ ...castanos, password: estrecho.password }),
                         names.as({ ...estrecho, password: castanos.password }), names.as({ ...castanos, username: "999003" })]) {
    const { status } = await names.post("/address", { address: "10.0.0.66" }, headers);
    assert.equal(status, 401);
  }
  const { status } = await names.post("/update", { subdomain: estrecho.subdomain, txt: CHALLENGE(1) }, names.as(castanos));
  assert.equal(status, 403);
  assert.deepEqual(names.cloudflare.records(), before);
});

test("POST /update keeps the two latest challenge values in the name's _acme-challenge record, as acme-dns does", async () => {
  const names = deployment();
  const castanos = (await names.claim("999001")).body;
  for (const n of [1, 2, 3]) {
    const { status, body } = await names.post("/update", { subdomain: castanos.subdomain, txt: CHALLENGE(n) }, names.as(castanos));
    assert.equal(status, 200);
    assert.deepEqual(body, { txt: CHALLENGE(n) });
  }
  assert.deepEqual(names.cloudflare.records(), [`TXT _acme-challenge.${CASTANOS} ${CHALLENGE(2)}`,
                                                `TXT _acme-challenge.${CASTANOS} ${CHALLENGE(3)}`]);
});

test("concurrent updates still leave the two latest values", async () => {
  const names = deployment();
  const castanos = (await names.claim("999001")).body;
  await Promise.all([1, 2, 3].map((n) =>
    names.post("/update", { subdomain: castanos.subdomain, txt: CHALLENGE(n) }, names.as(castanos))));
  assert.deepEqual(names.cloudflare.records(), [`TXT _acme-challenge.${CASTANOS} ${CHALLENGE(2)}`,
                                                `TXT _acme-challenge.${CASTANOS} ${CHALLENGE(3)}`]);
});

test("an update that is not an ACME challenge value is refused", async () => {
  const names = deployment();
  const castanos = (await names.claim("999001")).body;
  for (const body of [{ subdomain: castanos.subdomain, txt: "v=spf1 -all" }, { subdomain: castanos.subdomain, txt: "x".repeat(44) },
                      { subdomain: castanos.subdomain, txt: [CHALLENGE(1)] },
                      { subdomain: castanos.subdomain, txt: CHALLENGE(1), type: "CNAME" }, { subdomain: castanos.subdomain }]) {
    assert.equal((await names.post("/update", body, names.as(castanos))).status, 400, JSON.stringify(body));
  }
  assert.deepEqual(names.cloudflare.records(), []);
});

test("a code changes its records at most five times a minute; more answer 429 and change nothing", async () => {
  const names = deployment();
  const castanos = (await names.claim("999001")).body;
  const estrecho = names.as((await names.claim("999002")).body);
  const update = (n) => names.post("/update", { subdomain: castanos.subdomain, txt: CHALLENGE(n) }, names.as(castanos));
  for (const n of [1, 2, 3, 4]) assert.equal((await update(n)).status, 200);
  assert.equal((await names.post("/address", { address: "10.0.0.9" }, names.as(castanos))).status, 200);
  assert.equal((await names.post("/address", { address: "10.0.0.9" }, names.as(castanos))).status, 200, "unchanged");
  const before = names.cloudflare.records();
  const { status, body } = await update(5);
  assert.equal(status, 429);
  assert.match(body.error, new RegExp(CASTANOS));
  assert.equal((await names.post("/address", { address: "10.0.0.10" }, names.as(castanos))).status, 429);
  assert.deepEqual(names.cloudflare.records(), before);
  assert.equal((await names.post("/address", { address: "10.0.0.2" }, estrecho)).status, 200, "another code");
  names.clock.ms += 60_000;
  assert.equal((await update(5)).status, 200);
});

test("APS releases a code's name with its records; the old credential dies and the code can claim again", async () => {
  const names = deployment();
  const castanos = (await names.claim("999001")).body;
  const estrecho = (await names.claim("999002")).body;
  for (const [credential, address] of [[castanos, "10.0.0.9"], [estrecho, "fd00::9"]]) {
    await names.post("/address", { address }, names.as(credential));
    await names.post("/update", { subdomain: credential.subdomain, txt: CHALLENGE(1) }, names.as(credential));
  }
  const { status, body } = await names.release("999001");
  assert.equal(status, 200);
  assert.deepEqual(body, { name: CASTANOS });
  assert.deepEqual(names.cloudflare.records(), [`AAAA ${ESTRECHO} fd00::9`, `TXT _acme-challenge.${ESTRECHO} ${CHALLENGE(1)}`]);
  assert.equal((await names.post("/address", { address: "10.0.0.9" }, names.as(castanos))).status, 401);
  assert.equal((await names.claim("999001")).status, 201);
});

test("a release needs APS's token and a claimed code", async () => {
  const names = deployment();
  await names.claim("999001");
  for (const token of ["", "wrong", `${TOKEN}x`]) assert.equal((await names.release("999001", token)).status, 401);
  assert.equal((await names.post("/release", { code: "999001" })).status, 401);
  const { status, body } = await names.release("999002");
  assert.equal(status, 404);
  assert.match(body.error, /999002 holds no name/);
  assert.equal((await names.claim("999001")).status, 409);
});

test("the CI deployment's names live under ci.gestion.apsconecta.cl, apart from production's", async () => {
  const names = deployment({ suffix: "ci.gestion.apsconecta.cl" });
  const castanos = (await names.claim("999001")).body;
  assert.equal(castanos.name, "cesfam-los-castanos.ci.gestion.apsconecta.cl");
  await names.post("/address", { address: "10.0.0.9" }, names.as(castanos));
  await names.post("/update", { subdomain: castanos.subdomain, txt: CHALLENGE(1) }, names.as(castanos));
  assert.deepEqual(names.cloudflare.records(), [
    "A cesfam-los-castanos.ci.gestion.apsconecta.cl 10.0.0.9",
    `TXT _acme-challenge.cesfam-los-castanos.ci.gestion.apsconecta.cl ${CHALLENGE(1)}`,
  ]);
});

test("a Cloudflare outage answers 502 and keeps the stored state, so a retry applies the change", async () => {
  const names = deployment();
  const castanos = names.as((await names.claim("999001")).body);
  names.cloudflare.fail(true);
  const { status, body } = await names.post("/address", { address: "10.0.0.9" }, castanos);
  assert.equal(status, 502);
  assert.match(body.error, /Cloudflare answered 503/);
  names.cloudflare.fail(false);
  assert.equal((await names.post("/address", { address: "10.0.0.9" }, castanos)).status, 200);
  assert.deepEqual(names.cloudflare.records(), [`A ${CASTANOS} 10.0.0.9`]);
});

test("unknown paths, other methods and bodies that are not JSON are refused", async () => {
  const names = deployment();
  assert.equal((await names.post("/register", {})).status, 404);
  assert.equal((await names.post("/claim", "{not json")).status, 400);
  const get = await names.serve(new Request(`${ORIGIN}/claim`));
  assert.equal(get.status, 405);
});
