"""Local checkpoint progress. This module has no provider or inference client."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from statistics import median


def estimate(milestones, status, comparable=(), *, previous_total=None, elapsed=0):
    total = sum(m["weight"] for m in milestones)
    done = sum(m["weight"] for m in milestones if m["state"] == "completed")
    # Use an estimate only when the caller has a declared scope.
    percentage = round(100 * done / total) if total and status != "cancelled" else None
    if percentage == 100 and status != "complete":
        percentage = None
    durations = sorted(t for t in comparable if 0 < t < 86400)
    active = status in ("executing", "running", "continue")
    eta = None
    if len(durations) >= 5 and active and percentage is not None:
        low, high = durations[len(durations) // 4], durations[-1 - len(durations) // 4]
        if elapsed < high:
            eta = {"median_seconds": round(max(0, median(durations) - elapsed)),
                   "range_seconds": [round(max(0, low - elapsed)), round(high - elapsed)]}
    return {"status": status, "percentage": percentage,
            "remaining": None if percentage is None else 100-percentage,
            "scope_changed": previous_total is not None and previous_total != total,
            "eta": eta, "eta_state": "estimated" if eta else "calculating" if active else "unavailable",
            "milestones": milestones}


def route_key(route):
    decision = route.get("decision", {})
    return (decision.get("task_type"), decision.get("stage"), route.get("model"),
            route.get("effort"), route.get("speed"))


def comparable_stage_times(db, task):
    latest = (task.get("history") or [{}])[-1]
    key = route_key(latest)
    if task.get("status") != "executing" or not all(key[:4]):
        return []
    samples = []
    for (state,) in db.execute("SELECT state FROM tasks ORDER BY rowid DESC LIMIT 100"):
        other = json.loads(state)
        if other["id"] == task["id"] or other.get("status") != "complete":
            continue
        routes = [r for r in other.get("history", ()) if r.get("execution_ms") and route_key(r) == key]
        if routes:
            samples.append(routes[-1]["execution_ms"] / 1000)
    return samples


def snapshot(task, comparable=()):
    history = task.get("checkpoints", [])
    last = history[-1] if history else {}
    latest_route = (task.get("history") or [{}])[-1]
    status = task.get("status") or last.get("status") or "running"
    # Legacy explicit checkpoints carry a current pending scope and completed operation receipts.
    def plan(count):
        records = history[:count]
        if not records:
            return []
        done = list(dict.fromkeys(operation for record in records for operation in record.get("completed_operations", [])))
        pending = list(dict.fromkeys(records[-1].get("pending_operations", [])))
        return [{"name": operation, "state": "completed", "weight": 1}
                for operation in done if operation not in pending] + [
                    {"name": operation, "state": "pending", "weight": 1} for operation in pending]
    prior = plan(len(history)-1) if len(history)>1 else None
    started = latest_route.get("started_at")
    elapsed = max(0, time.time() - started) if started is not None else 0
    result = estimate(plan(len(history)), status, comparable, elapsed=elapsed,
                      previous_total=len(prior) if prior else None)
    return {"task": task["id"], "stage": task.get("stage") or last.get("next_stage"),
            "model": latest_route.get("model"), "speed": latest_route.get("speed"), **result}


def format_snapshot(state, width=78):
    pct = state["percentage"]
    bar = "[··········]" if pct is None else "[" + "█"*(pct//10) + "░"*(10-pct//10) + "]"
    progress = "indeterminate" if pct is None else f"~{pct}% ({100-pct}% left)"
    label = f"◈ Pulse {bar} {progress} | {state['status']}"
    if state["scope_changed"]:
        label += " · scope updated"
    details = " | ".join(x for x in (state.get("stage"), state.get("model"), state.get("speed")) if x)
    if details:
        label += f" | {details}"
    label = "".join(ch for ch in label if ch.isprintable())
    return label if len(label) <= width else label[:max(0, width - 1)] + "…" if width else ""


def main(argv):
    parser = argparse.ArgumentParser(prog="autorouter progress")
    parser.add_argument("--task")
    parser.add_argument("--detail", action="store_true")
    parser.add_argument("--json", action="store_true")
    opts = parser.parse_args(argv)
    state = Path(os.environ.get("AUTOROUTER_HOME") or Path.home() / ".local/state/autorouter")
    # CLI is local operator access. MCP checks its independently granted workspace.
    candidates = [state / "tasks.sqlite", *sorted((state / "workspaces").glob("*/tasks.sqlite"))]
    results = []
    for file in candidates:
        if not file.is_file():
            continue
        with closing(sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            try:
                row = db.execute(
                    "SELECT e.time,t.state FROM events e JOIN tasks t ON t.id=e.task "
                    + ("WHERE e.task=? " if opts.task else "") + "ORDER BY e.seq DESC LIMIT 1",
                    (opts.task,) if opts.task else (),
                ).fetchone()
            except sqlite3.OperationalError:
                continue
            if row:
                results.append((row[0], file, json.loads(row[1])))
    if not results:
        parser.exit(1, "No local task checkpoint found. Native turn progress is in the Codex footer.\n")
    _, file, task = max(results, key=lambda entry: entry[0])
    with closing(sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        result = snapshot(task, comparable_stage_times(db, task))
    if opts.json:
        print(json.dumps(result, indent=2))
        return
    print(format_snapshot(result, shutil.get_terminal_size().columns))
    if opts.detail:
        eta = result["eta"]
        if eta:
            low, high = eta["range_seconds"]
            print(f"Stage ETA ~{eta['median_seconds']}s ({low}–{high}s)")
        else:
            print("Stage ETA " + result["eta_state"])
        for milestone in result["milestones"]:
            name = "".join(ch for ch in milestone["name"] if ch.isprintable())
            print(f"  {milestone['state']:10} {name}")
