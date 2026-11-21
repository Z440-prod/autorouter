#!/usr/bin/env python3
"""Offline latency harness. Point PYTHONPATH at either revision's src/."""
import hashlib
import json
from importlib.util import find_spec
from pathlib import Path
import statistics
import tempfile
from time import perf_counter, process_time

from autorouter import engine, graph
from autorouter.memory import Memory


def measured(call, runs=25):
    samples = []
    cpu = process_time()
    for _ in range(runs):
        start = perf_counter()
        call()
        samples.append((perf_counter() - start) * 1000)
    samples.sort()
    return {"p50_ms": round(statistics.median(samples), 3),
            "p95_ms": round(samples[(95*runs+99)//100-1], 3),
            "cpu_ms_per_call": round((process_time()-cpu)*1000/runs, 3)}


def bench(root):
    vault = root / "notes"
    vault.mkdir()
    for i in range(1200):
        (vault / f"decision{i:04d}.md").write_text(f"Saturn fixture {i}\n" + "Architecture.\n"*80)
    memory = Memory(vault, root / "memory.sqlite")
    result = {"initial_index": measured(memory.reindex, 1)}
    def retrieve():
        if hasattr(memory, "ensure_current"):
            memory.ensure_current()
        else:
            memory.reindex()
        assert memory.search("Saturn")
    result["warm_memory_lookup"] = measured(retrieve)
    memory.db.close()

    source = root / "source"
    source.mkdir()
    evidence = source / "demo.py"
    evidence.write_text("pass\n")
    directory = root / "graph"
    directory.mkdir()
    n = 30_000
    nodes = [{"id": str(i), "label": f"symbol{i}", "source_file": "demo.py", "source_location": "L1"}
             for i in range(n)]
    edges = [{"source": str(i), "target": str(i+1), "relation": "calls",
              "source_file": "demo.py", "source_location": "L1"} for i in range(n-1)]
    graph_file = directory / "graph.json"
    graph_file.write_text(json.dumps({"nodes": nodes, "links": edges}))
    (directory / ".graphify_root").write_text(str(source))
    (directory / "manifest.json").write_text(json.dumps({"demo.py": {
        "mtime": evidence.stat().st_mtime, "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}}))
    st = graph_file.stat()
    (directory / ".graphify_build_state.json").write_text(json.dumps(
        {"graph_mtime_ns": st.st_mtime_ns, "graph_size": st.st_size}))
    def lookup():
        assert len(graph.query(source, directory, "symbol777")["relationships"]) == 2
    result["graph_first_lookup"] = measured(lookup, 1)
    result["warm_graph_lookup"] = measured(lookup)
    checkpoints = [
        {"summary": f"Stage {i}: " + "Repeated audit detail. " * 90,
         "next_stage": "verification", "completed_operations": [f"Operation {i}"],
         "verified_findings": ["Fixture verified"], "changed_files": ["demo.py"],
         "pending_operations": ["Test"]}
        for i in range(12)
    ]
    task = {"id": "benchmark", "status": "executing", "checkpoints": checkpoints}
    if hasattr(engine, "active_task_state"):
        def handoff():
            return json.dumps({"active_task_state": engine.active_task_state(task)})
    else:
        def handoff():
            return json.dumps({"checkpoints": checkpoints})

    result["handoff"] = {**measured(handoff), "payload_bytes": len(handoff().encode())}
    if find_spec("autorouter.pulse"):
        from autorouter import pulse

        result["pulse_snapshot_and_render"] = measured(
            lambda: pulse.format_snapshot(pulse.snapshot(task))
        )
    return result


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as staging:
        print(json.dumps(bench(Path(staging)), indent=2))
