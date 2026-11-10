<!--
  - SPDX-FileCopyrightText: 2026 APS Conecta
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# 2. The Suite AI's Model is gemma4:e2b

Accepted, 2026-11-10 (spec #75, ticket #97).

The owner ruled on 2026-11-10 that the Suite AI uses only `gemma4:e2b`, pinned at
`sha256:b37049369adfe3d2b653af0ab301a062ca5cbe96ace6aa0d3f2559ee9b563fc2` (`aps/provisionador/model.json`). The ruling
was made against #88's run on this server's CPU (Ollama 0.40.16 pinned by digest, 4 CPUs, 8 GB of memory): `gemma4:e2b`
81 % right tool calls, p50 249 s, p95 724 s; `qwen3.5:2b` 72 %, p50 82 s, p95 473 s. Neither met the spec's bar of 95 %
right calls and a p95 within 15 s on the 6 GB machine.

## Considered Options

- `qwen3.5:2b`: faster on that CPU, but with fewer right tool calls. Dropped.
- The 4B fallback, Gemma 4 E4B or Qwen 3.5 4B: dropped without being measured.

## Consequences

- The bar no longer chooses the Model. The benchmark (`aps/benchmark`) measures the Model alone, at its pin, and
  reports the bar; runs on the reference machines (#91) measure its speed there and gate nothing.
- The wizard offers no model choice: «IA local» on serves `gemma4:e2b`.
