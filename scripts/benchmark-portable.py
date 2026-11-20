#!/usr/bin/env python3
"""Measure stdio startup and scoped retrieval against a synthetic project."""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
from time import perf_counter

p = argparse.ArgumentParser()
p.add_argument("--runs", type=int, default=20)
opts = p.parse_args()
if not 2 <= opts.runs <= 100:
    p.error("--runs must be between 2 and 100")


def request(proc, ident, method, **params):
    proc.stdin.write(json.dumps({"id": ident, "jsonrpc": "2.0", "method": method, "params": params}) + "\n")
    proc.stdin.flush()
    record = json.loads(proc.stdout.readline())
    assert record["id"] == ident and "result" in record, record
    return record["result"]


def summary(values):
    values.sort()
    return {"median_ms": round(statistics.median(values), 2),
            "p95_ms": round(values[min(len(values)-1, int(len(values)*.95))], 2)}


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    workspace = root / "workspace"
    vault = root / "notes"
    workspace.mkdir()
    vault.mkdir()
    (vault / "saturn.md").write_text("Saturn owns the synthetic fixture.", encoding="utf-8")
    env = {**os.environ, "AUTOROUTER_HOME": str(root / "state")}
    data = {}
    for host in ("codex", "claude-code", "gemini-cli", "opencode", "cline"):
        subprocess.run(["autorouter", "workspace", "--target", host, "--vault", vault],
                       cwd=workspace, env=env, check=True)
        startup, lookup = [], []
        for _ in range(opts.runs):
            start = perf_counter()
            with subprocess.Popen(["autorouter", "serve", "--target", host], cwd=workspace,
                                  env=env, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE) as proc:
                request(proc, 1, "initialize", protocolVersion="2025-11-25")
                startup.append((perf_counter() - start) * 1000)
                start = perf_counter()
                result = request(proc, 2, "tools/call", name="knowledge_search", arguments={"query": "Saturn"})
                lookup.append((perf_counter() - start) * 1000)
                assert "saturn.md" in result["content"][0]["text"]
                proc.stdin.close()
            assert proc.returncode == 0
        data[host] = {"startup": summary(startup), "lookup": summary(lookup)}
print(json.dumps(data, indent=2))
