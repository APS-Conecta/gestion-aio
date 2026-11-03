// SPDX-FileCopyrightText: 2026 APS Conecta
// SPDX-License-Identifier: AGPL-3.0-or-later
// Cloudflare's DNS API, as the name service uses it: set one name's records of one type to exact contents.

const API = "https://api.cloudflare.com/client/v4";
const TTL = 60;

export class CloudflareError extends Error {}

// Cloudflare answers a TXT record's content as it was stored, quoted or not.
const unquoted = (content) => content.replace(/^"(.*)"$/, "$1");

export function dnsRecords({ fetch, token, zone }) {
  let recordsPath; // the zone's DNS records endpoint, once the zone is looked up

  async function call(method, path, body) {
    const response = await fetch(API + path, {
      method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: body && JSON.stringify(body),
    });
    const answer = await response.json().catch(() => ({}));
    if (!response.ok || !answer.success) {
      throw new CloudflareError(`Cloudflare answered ${response.status} to ${method} ${path.split("?")[0]}`);
    }
    return answer.result;
  }

  async function zoneRecordsPath() {
    if (!recordsPath) {
      const [found] = await call("GET", `/zones?name=${encodeURIComponent(zone)}`);
      if (!found) throw new CloudflareError(`Cloudflare has no zone ${zone} for this token`);
      recordsPath = `/zones/${found.id}/dns_records`;
    }
    return recordsPath;
  }

  /** Makes `name`'s records of `type` exactly `contents`, editing records it can reuse and deleting the rest. */
  async function set(name, type, contents) {
    const path = await zoneRecordsPath();
    const existing = await call("GET", `${path}?type=${type}&name=${encodeURIComponent(name)}&per_page=100`);
    const kept = new Set();
    const spare = [];
    for (const record of existing) {
      const content = unquoted(record.content);
      if (contents.includes(content) && !kept.has(content)) kept.add(content);
      else spare.push(record);
    }
    for (const content of contents.filter((c) => !kept.has(c))) {
      const record = spare.shift();
      if (record) await call("PATCH", `${path}/${record.id}`, { content });
      else await call("POST", path, { type, name, content, ttl: TTL, proxied: false });
    }
    for (const record of spare) await call("DELETE", `${path}/${record.id}`);
  }

  return { set };
}
