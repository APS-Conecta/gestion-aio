<!--
  - SPDX-FileCopyrightText: 2026 APS Conecta
  - SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Agent guidelines for APS Conecta Gestión AIO

This repo is «APS Conecta Gestión»: a fork of [Nextcloud All-in-One](https://github.com/nextcloud/all-in-one)
that stays as close to upstream as possible. The spec is issue #1. Read `CONTEXT.md` for the vocabulary
and `docs/agents/` for the issue tracker.

## Vanilla first

- New APS files go under `aps/` or `docs/`. Files whose location is fixed (`AGENTS.md`, `CONTEXT.md`,
  `.github/workflows/aps-*.yml`) and any change to an upstream path need an entry in `aps/keep-list.txt`
  with a one-line reason.
- Prefer an upstream switch, setting or hook over changing upstream code. Switch unused upstream
  features off; never delete them.
- Upstream's contribution policy (`AI_POLICY.md`, `CONTRIBUTING.md`) governs changes sent upstream.
  Work in this repo follows the rules below: agents open, review and merge PRs here.

## Commits, PRs and issues

- Conventional Commits in English: `<type>(<scope>): <summary>`. The scope names the component
  (`aps`, `provisionador`, `bootstrap`, `mastercontainer`, `nextcloud`, …). PR titles use the same format.
- Every AI-assisted commit carries `Assisted-by: AGENT_NAME:MODEL_VERSION`. Never add `Signed-off-by`.
- Never force-push `main`; a rollback is a revert.
- Issues: English titles and bodies, labels from `docs/agents/triage-labels.md`.

## Language and code

- Code, identifiers, comments, tests, commits and issues are English. Text an operator or staff
  member reads is Spanish (es-CL, formal «usted»). Domain terms keep the spelling `CONTEXT.md` gives them.
- Smallest change that fixes the root cause. Reuse before adding (DRY); no layer, option or abstraction
  the ticket doesn't need (KISS, YAGNI); one reason to change per module (SOLID).
- New files carry SPDX headers: `2026 APS Conecta`, `AGPL-3.0-or-later`.
- No tracked file outside the DEIS register names an establishment's DEIS code.
- Secrets never appear on a command line or in a log.

## Tests and gates

- Test only at the spec's four seams: the clean-machine acceptance run, the Provisionador's HTTP API
  with the fake suite port, the static gates, and the name service's HTTP API against a fake Cloudflare.
  Start each ticket with a red test at the lowest suitable seam.
- `aps/gates/run.sh` runs the static gates exactly as CI does. Run it in the foreground before pushing.
- Never weaken a gate to get green; fix the input.
- Upstream's workflows are disabled on this fork. Only `aps-*` workflows and the org's CodeQL default
  setup run; CI fails if another workflow is active.
