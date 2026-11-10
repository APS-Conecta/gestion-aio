<!--
  - SPDX-FileCopyrightText: 2026 APS Conecta
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# 1. The Suite AI runs Ollama in the Suite with one pinned Model

Accepted, 2026-11-10 (spec #75, ticket #90).

## Context

Estadística writes its nota whenever Nextcloud has a text-generation provider, and the Suite installs none. The owner
wants one AI for the whole Suite that runs on the establishments' own machines, from no GPU up to 16 GB of VRAM, and
never sends data off the server. Upstream AIO has no Ollama container; its only AI community container is LocalAI.

## Decision

- «IA local» is an optional install step, off by default, that the operator turns on or off on «Revisar».
- On, our «ollama» community container (`community-containers/ollama`) runs beside Nextcloud through AIO's
  community-container switch, as Caddy does. Off, it stops and leaves the list, and its volume, with the Model, stays.
- The Suite port finds the GPU: NVIDIA through the NVIDIA Container Toolkit (AIO's NVIDIA switch), AMD or Intel through
  the DRI devices (the bootstrap hands them to the mastercontainer, as upstream's readme says), otherwise the CPU.
- Ollama answers one request at a time, with one model loaded and its cloud off, under a memory cap: the server's memory
  less the suite's own floor, the bootstrap's. AIO has no memory setting for a container, so ours adds `memory_limit`.
- Nextcloud's OpenAI-compatible connector, a store app, points at Ollama with the Model and its chat endpoint on.
- Each Suite release pins Ollama's image and the Model by digest (`aps/gates/pins.py`). The Model is pulled by its tag
  and kept only at its pinned digest, so a tag the registry moved is refused. `gemma4:e2b` is provisional until the
  benchmark (#97) chooses.

## Consequences

- Only a Suite release moves the image or the Model: AIO's nightly job pulls the same digest, and Ollama never pulls a
  model it holds.
- AIO's GPU switches reach every container that asks for a GPU: with «IA local» on an NVIDIA server, Nextcloud and Talk
  recording get the NVIDIA runtime too, and on a server with render devices they get those devices.
- A server with no memory beside the suite's floor cannot turn it on; the step says so.
- Turning it on or off after «Listo» needs the wizard open; it reopens with the next Upgrade.
