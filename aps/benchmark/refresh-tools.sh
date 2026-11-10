#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# Refreshes tools.json, the benchmark's tools, from the connector main's own tools/list: its server is built in its uv
# container (uv 0.11.7, its Dockerfile's pin, with the documents extra its ExApp image carries) and listed without a
# Nextcloud, as its tool budget gate does (scripts/check_tool_budget.py). Each tool keeps its name, description and
# inputSchema. The clone is mounted read-only, so the container leaves no file on the host.
# Usage: aps/benchmark/refresh-tools.sh   (Docker, Git)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
git clone -q --depth 1 https://github.com/APS-Conecta/nextcloud-mcp-connector "$work/connector"
echo "refresh-tools: nextcloud-mcp-connector main at $(git -C "$work/connector" rev-parse --short HEAD)" >&2
docker run --rm -i -v "$work/connector:/src:ro" -w /src -e UV_PROJECT_ENVIRONMENT=/venv -e PYTHONDONTWRITEBYTECODE=1 \
  ghcr.io/astral-sh/uv:0.11.7-python3.13-trixie-slim@sha256:74ee1af27961f5fe73f70146808097ea11e03464cecaac33e60e173dc62e9202 \
  uv run --quiet --frozen --extra documents python - > "$work/tools.json" <<'EOF'
import asyncio
import json
import sys

from mcp import Client

from mcp_connector.server import mcp


async def main():
    async with Client(mcp, raise_exceptions=True) as client:
        listed = await client.list_tools()
    tools = [{key: tool[key] for key in ("name", "description", "inputSchema")}
             for tool in listed.model_dump(by_alias=True, exclude_none=True, mode="json")["tools"]]
    print(json.dumps(tools, ensure_ascii=False, indent=2))
    print(f"refresh-tools: {len(tools)} tools", file=sys.stderr)


asyncio.run(main())
EOF
mv "$work/tools.json" "$here/tools.json"
