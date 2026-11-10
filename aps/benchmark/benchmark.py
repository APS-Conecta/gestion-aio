#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Suite AI's model benchmark (spec #75): it measures each candidate model on an Ollama server, before the Suite
ships one, against the bar of at least 95 % right tool calls and a p95 answer time within 15 s on the 6 GB machine.

Each model is pulled by its tag and measured only when the digest Ollama reports is its pin (Ollama refuses a pull by
digest). One unscored request loads it and reads the tools. Then each question of questions.json goes alone through
Ollama's OpenAI-compatible chat endpoint with the first slice's tools: tools.json, in the shape of the connector's
tools/list (name, description, inputSchema). No tool runs, writes included. A question is right when the reply's first
tool call names its tool and every argument it expects holds the expected value once the tool's defaults are filled
in; a reply without a tool call, or an error, is wrong. The time is the whole request.

It writes one result file per model and machine, benchmark-<model>-<machine>.json: the model and its digest,
Ollama's version and context length, the hardware, each question's result, the right-call rate, p50 and p95 answer
time and the verdict against the bar.

Usage: python3 aps/benchmark/benchmark.py [--ollama http://127.0.0.1:11434]   (Linux, Python 3.10+)
"""

import argparse
import json
import math
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

MODELS = {
    "gemma4:e2b": "sha256:b37049369adfe3d2b653af0ab301a062ca5cbe96ace6aa0d3f2559ee9b563fc2",
    "qwen3.5:2b": "sha256:0689d44085e06d165161a8a9a1731344278cfb5aade63a3c3dbdb48ab54b130a",
}
GPU_VENDORS = {"0x1002": "AMD", "0x8086": "Intel"}  # PCI vendor ids in /sys/class/drm/card*/device/vendor
BAR = {"right_call_rate": 0.95, "p95_seconds": 15}
TIMEOUT = 900  # seconds a request may take, the cold first one on a CPU included
HERE = Path(__file__).resolve().parent


def api(base, path, body=None, timeout=TIMEOUT):
    """One request to Ollama, its JSON answer; an HTTP error raises with Ollama's own message."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"HTTP {error.code} from {path}: {error.read().decode(errors='replace').strip()}") from None


def sysfs(path):
    """A sysfs file's text, or None when the driver does not expose it."""
    try:
        return path.read_text().strip()
    except OSError:
        return None


def hardware():
    gpus = []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=30).stdout
        gpus = [{"name": name.strip(), "vram_mib": int(mib)}
                for name, mib in (line.rsplit(",", 1) for line in out.splitlines() if line.strip())]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    if not gpus:  # an AMD or Intel GPU, which the Suite reaches through /dev/dri: one entry per device
        devices = sorted({card.resolve() for card in Path("/sys/class/drm").glob("card*/device")
                          if re.fullmatch(r"card\d+", card.parent.name)})
        for device in devices:
            vendor, vram = sysfs(device / "vendor"), sysfs(device / "mem_info_vram_total")
            gpus.append({"name": GPU_VENDORS.get(vendor, vendor), "vram_mib": int(vram) // 1048576 if vram else None})
    ram = re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text())
    cpu = re.search(r"model name\s*:\s*(.+)", Path("/proc/cpuinfo").read_text())
    return {"gpus": gpus, "ram_gib": round(int(ram[1]) / 1048576, 1) if ram else None,
            "cpu": cpu[1].strip() if cpu else platform.machine(), "cores": os.cpu_count()}


def canonical(value):
    """A value as it is compared: text without case, outer spaces, quotes or a final period; a number or a date-time as
    such."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            pass
        text = " ".join(value.split()).strip(" .\"'«»“”").casefold()
        try:
            return float(text)
        except ValueError:
            return text
    return float(value) if isinstance(value, int) and not isinstance(value, bool) else value


def holds(expected, actual):
    """Whether the actual argument holds every expected key and value; an object may come as a JSON string."""
    if isinstance(expected, dict):
        if isinstance(actual, str):
            try:
                actual = json.loads(actual)
            except ValueError:
                return False
        return isinstance(actual, dict) and all(holds(value, actual.get(key)) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(map(holds, expected, actual))
    return canonical(expected) == canonical(actual)


def ask(base, model, tools, question):
    body = {"model": model, "messages": [{"role": "user", "content": question}], "tools": tools, "temperature": 0}
    return api(base, "/v1/chat/completions", body)


def answer(base, model, tools, schemas, item):
    """One question's result: what was called, whether it is right, and how long the answer took."""
    result = {"question": item["question"], "tool": item["tool"], "arguments": item["arguments"], "called": None}
    started = time.monotonic()
    try:
        reply = ask(base, model, tools, item["question"])
    except (OSError, RuntimeError, ValueError) as error:  # a refusal, a timeout or an unreadable answer
        reply, result["error"] = None, str(error)
    result["seconds"] = round(time.monotonic() - started, 2)
    message = reply["choices"][0]["message"] if reply else {}
    calls = message.get("tool_calls") or []
    if calls:
        function = calls[0].get("function", {})
        arguments = function.get("arguments") or {}
        try:
            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except ValueError:
            pass
        result["called"] = {"tool": function.get("name"), "arguments": arguments}
    elif reply:
        result["reply"] = (message.get("content") or "")[:500]
    if reply:
        result["prompt_tokens"] = reply.get("usage", {}).get("prompt_tokens")
    called = result["called"]
    defaults = {key: spec["default"] for key, spec in schemas.get(item["tool"], {}).get("properties", {}).items()
                if "default" in spec}
    result["right"] = bool(called and called["tool"] == item["tool"] and isinstance(called["arguments"], dict)
                           and holds(item["arguments"], {**defaults, **called["arguments"]}))
    return result


def percentile(values, share):
    """The nearest-rank percentile, or None without values."""
    ranked = sorted(values)
    return ranked[max(0, math.ceil(share * len(ranked)) - 1)] if ranked else None


def measure(base, model, pin, questions, fixtures):
    """The result file's content for one model, or a RuntimeError when it cannot be measured at its pin."""
    print(f"{model}: pulling", flush=True)
    api(base, "/api/pull", {"model": model, "stream": False}, timeout=None)
    digest = next((f"sha256:{m['digest']}" for m in api(base, "/api/tags")["models"] if m["name"] == model), None)
    if digest != pin:
        raise RuntimeError(f"{model}: Ollama holds digest {digest}, not the pin {pin}; not measured")
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                               "parameters": t["inputSchema"]}} for t in fixtures]
    schemas = {t["name"]: t["inputSchema"] for t in fixtures}
    started = time.monotonic()
    try:
        ask(base, model, tools, "Hola.")
    except (OSError, RuntimeError, ValueError) as error:  # each question then fails on its own
        print(f"{model}: the warm-up failed: {error}", flush=True)
    warmup = round(time.monotonic() - started, 2)
    loaded = next((m for m in api(base, "/api/ps")["models"] if m["name"] == model), {})
    results = []
    for n, item in enumerate(questions, 1):
        result = answer(base, model, tools, schemas, item)
        results.append(result)
        print(f"{model}: {n}/{len(questions)} {'right' if result['right'] else 'wrong'} {result['seconds']} s "
              f"{item['tool']}" + (f" ({result['error']})" if "error" in result else ""), flush=True)
    rate = sum(r["right"] for r in results) / len(results)
    seconds = [r["seconds"] for r in results]
    p50, p95 = percentile(seconds, 0.5), percentile(seconds, 0.95)
    return {
        "model": model, "digest": digest,
        "ollama": api(base, "/api/version")["version"], "context_length": loaded.get("context_length"),
        "model_bytes": loaded.get("size"), "model_bytes_in_vram": loaded.get("size_vram"),
        "warmup_seconds": warmup, "questions": results,
        "right_call_rate": round(rate, 4), "p50_seconds": p50, "p95_seconds": p95, "bar": BAR,
        "passed": rate >= BAR["right_call_rate"] and p95 <= BAR["p95_seconds"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ollama", default="http://127.0.0.1:11434", help="the Ollama server's URL")
    args = parser.parse_args()
    base = args.ollama.rstrip("/")
    questions = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    fixtures = json.loads((HERE / "tools.json").read_text(encoding="utf-8"))
    unknown = {q["tool"] for q in questions} - {t["name"] for t in fixtures}
    if unknown:
        print(f"benchmark: questions.json expects tools tools.json lacks: {sorted(unknown)}")
        return 2
    machine, machine_hardware = socket.gethostname(), hardware()
    failed = 0
    for model, pin in MODELS.items():
        try:
            result = {"machine": machine, "hardware": machine_hardware,
                      **measure(base, model, pin, questions, fixtures)}
        except (OSError, RuntimeError, ValueError) as error:
            print(f"benchmark: {error}", flush=True)
            failed += 1
            continue
        path = Path(f"benchmark-{model.replace(':', '-')}-{machine}.json")
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{model}: right calls {result['right_call_rate']:.0%}, p50 {result['p50_seconds']} s, "
              f"p95 {result['p95_seconds']} s, {'passes' if result['passed'] else 'fails'} the bar: {path}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
