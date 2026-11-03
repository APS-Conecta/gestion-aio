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

function deployment({ suffix = "gestion.apsconecta.cl" } = {}) {
  const cloudflare = fakeCloudflare({ token: TOKEN, zone: ZONE });
  const storage = fakeStorage();
  const start = (slugs) => nameService({ storage, fetch: cloudflare.fetch, token: TOKEN, zone: ZONE, suffix, slugs });
  let serve = start(SLUGS);
  const post = async (path, body, headers = {}) => {
    const response = await serve(new Request(ORIGIN + path, {
      method: "POST", headers: { "Content-Type": "application/json", ...headers },
      body: typeof body === "string" ? body : JSON.stringify(body),
    }));
    return { status: response.status, body: await response.json() };
  };
  return {
    cloudflare, storage, post,
    /** A redeploy, with the slug table of a later register if given. */
    restart(slugs = SLUGS) { serve = start(slugs); },
    serve: (request) => serve(request),
    claim: (code) => post("/claim", { code }),
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
                                         ["fd00::A:10", "AAAA", "fd00::a:10"]]) {
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
  for (const address of ["999.1.1.1", "1.2.3", "01.2.3.4", "1.2.3.4 ", "::g", "fe80::1%eth0", "::1]@evil.example/[",
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
