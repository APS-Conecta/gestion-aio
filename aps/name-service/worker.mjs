// SPDX-FileCopyrightText: 2026 APS Conecta
// SPDX-License-Identifier: AGPL-3.0-or-later
// The Worker: every request goes to the one Durable Object that holds all claims, so a claim is atomic.
// slugs.json is the slug table deploy.sh generates from the DEIS register and wrangler bundles.

import { DurableObject } from "cloudflare:workers";
import { nameService } from "./names.mjs";
import slugs from "./slugs.json" with { type: "json" };

export class Names extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.serve = nameService({
      storage: ctx.storage, fetch: (input, init) => fetch(input, init), token: env.CLOUDFLARE_API_TOKEN,
      zone: env.ZONE, suffix: env.NAME_SUFFIX, slugs,
    });
  }

  fetch(request) {
    return this.serve(request);
  }
}

export default {
  fetch: (request, env) => env.NAMES.get(env.NAMES.idFromName("names")).fetch(request),
};
