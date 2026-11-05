// SPDX-FileCopyrightText: 2026 APS Conecta
// SPDX-License-Identifier: AGPL-3.0-or-later
// The Worker: every request goes to the one Durable Object that holds all claims, so a claim is atomic.
// slugs.json and tests.json are the slug tables deploy.sh generates from the DEIS register, of its listed and of its
// test establishments, and wrangler bundles.

import { DurableObject } from "cloudflare:workers";
import { nameService } from "./names.mjs";
import slugs from "./slugs.json" with { type: "json" };
import tests from "./tests.json" with { type: "json" };

export class Names extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.serve = nameService({
      storage: ctx.storage, fetch: (input, init) => fetch(input, init), token: env.CLOUDFLARE_API_TOKEN,
      zone: env.ZONE, suffix: env.NAME_SUFFIX, slugs, tests,
      claimLimits: { perIpPerDay: env.CLAIMS_PER_IP_PER_DAY, perWeek: env.CLAIMS_PER_WEEK },
    });
  }

  fetch(request) {
    return this.serve(request);
  }
}

export default {
  fetch: (request, env) => env.NAMES.get(env.NAMES.idFromName("names")).fetch(request),
};
