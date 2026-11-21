"""Operator-verified observations in the existing task event ledger."""

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from statistics import median
import subprocess
import time
from uuid import UUID

from .engine import Store
from .integrations import PROJECT, codex_home
from .telemetry import routing_events

CATEGORIES = ("configuration", "implementation", "debugging", "verification")
FEEDBACK = ("unspecified", "accepted", "needs-rework")


def turn_metrics(session, turn):
    session, turn = str(UUID(session)), str(UUID(turn))
    root = (codex_home() / "sessions").resolve()
    paths = list(root.glob(f"*/*/*/rollout-*-{session}.jsonl"))
    if len(paths) != 1:
        raise ValueError("Native session was not found")
    source = paths[0]
    if source.is_symlink() or not source.resolve().is_relative_to(root):
        raise ValueError("Invalid native rollout path")
    metrics = {"models": [], "model_source": "initial-settings", "tools": [],
               "skills": [], "sampling_calls": 0, "tool_calls": 0, "usage": {}}
    elapsed = None
    active = False
    with source.open() as events:
        for line in events:
            row = json.loads(line)
            data = row["payload"]
            kind = row["type"]
            event = data.get("type")
            if kind == "turn_context":
                active = data.get("turn_id") == turn
                if active:
                    metrics["models"].append([data.get("model"), data.get("effort")])
            if kind == "token_usage_record" and data.get("turn_id") == turn:
                metrics["sampling_calls"] += 1
                metrics["usage"] = data.get("turn_token_usage", {})
            if kind == "event_msg" and data.get("turn_id") == turn and event == "task_complete":
                elapsed = data.get("duration_ms")
                metrics["time_to_first_token_ms"] = data.get("time_to_first_token_ms")
                metrics["outcome"] = "error" if data.get("error") else "completed"
            if active and kind == "response_item":
                if event == "configuration_update":
                    config = data.get("update", data)
                    model = config.get("model")
                    if isinstance(model, str):
                        metrics["models"].append([model, config.get("reasoning_effort")])
                if event in ("function_call", "custom_tool_call") and isinstance(data.get("name"), str):
                    metrics["tool_calls"] += 1
                    tools = metrics["tools"]
                    if len(tools) < 24 and data["name"] not in tools:
                        tools.append(data["name"][:80])
    if elapsed is None or not metrics["models"]:
        raise ValueError("A completed turn with recorded model and latency is required")
    try:
        routes = [row for row in reversed(routing_events(thread=session, limit=400))
                  if row.get("turn") == turn]
        samples = [row for row in routes if row["event"] == "sample" and row.get("model")]
        metrics["skills"] = sorted({skill for row in routes for skill in row.get("skills", [])})
        metrics["classification_ms"] = [
            int(row["classification_ms"]) for row in routes
            if row.get("classification_ms", "").isdigit()
        ]
        latencies = sorted(metrics["classification_ms"])
        metrics["routing_performance"] = {
            "applied_classifications": len(latencies),
            "reused_decisions": sum(row["event"] == "route_reused" for row in routes),
            "classification_p50_ms": median(latencies) if latencies else None,
            "classification_p95_ms": latencies[(95 * len(latencies) + 99) // 100 - 1] if latencies else None,
        }
        if samples:
            metrics["models"] = [[row["model"], row.get("effort")] for row in samples]
            metrics["model_source"] = "native-sampling-log"
            metrics["handoffs"] = sum(a[0] != b[0] for a, b in zip(metrics["models"], metrics["models"][1:]))
    except (OSError, sqlite3.Error):
        pass
    metrics["elapsed_ms"] = elapsed
    return metrics


def append(db, task, kind, payload):
    db.execute("INSERT INTO events(task,time,kind,data) VALUES(?,?,?,?)",
               (task, time.time(), kind, json.dumps(payload)))


def revision():
    digest = hashlib.sha256(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT))
    files = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--",
         "src", "tests", "scripts", "config", "pyproject.toml"],
        cwd=PROJECT,
    )
    for name in sorted(set(files.split(b"\0")) - {b""}):
        path = PROJECT / os.fsdecode(name)
        digest.update(name + b"\0")
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(PROJECT):
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def record(store, session, turn, category, feedback="unspecified"):
    if category not in CATEGORIES or feedback not in FEEDBACK:
        raise ValueError("Unknown task category or feedback value")
    metrics = turn_metrics(session, turn)
    task = str(UUID(turn))
    if store.db.execute("SELECT 1 FROM events WHERE task=? AND kind='verified_observation'", (task,)).fetchone():
        raise ValueError("This turn already has a verification observation")
    start = time.monotonic()
    fingerprint = revision()
    gate = subprocess.run(["bash", "scripts/validate.sh"], cwd=PROJECT, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    groups = [int(count) for count in re.findall(r"^Ran (\d+) tests? in ", gate.stdout, re.M)]
    if revision() != fingerprint:
        raise ValueError("Project source changed during verification")
    payload = {
        "category": category, "scope": "AutoRouter Python regression", "feedback": feedback,
        "session": str(UUID(session)), "gate": "bash scripts/validate.sh", "revision": fingerprint,
        "checked_at": time.time(), "gate_ms": round((time.monotonic() - start) * 1000),
        "report_sha256": hashlib.sha256(gate.stdout.encode()).hexdigest(),
        "test_counts": groups, "passed": gate.returncode == 0 and bool(groups),
        **metrics,
    }
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        if store.db.execute("SELECT 1 FROM events WHERE task=? AND kind='verified_observation'", (task,)).fetchone():
            raise ValueError("This turn already has a verification observation")
        append(store.db, task, "verified_observation", payload)
    return payload


def patterns(store):
    records = defaultdict(list)
    for row in store.db.execute(
        "SELECT task,data FROM events WHERE kind='verified_observation' ORDER BY seq DESC LIMIT 1000"
    ):
        value = json.loads(row["data"])
        model, effort = value["models"][-1]
        if value.get("model_source") != "native-sampling-log":
            continue
        if not model or not effort or len({tuple(x) for x in value["models"]}) > 1:
            continue
        records[(value["category"], model, effort)].append(
            {"turn": row["task"], "passed": value["passed"], "checked_at": value["checked_at"],
             "revision": value["revision"], "feedback": value.get("feedback", "unspecified")}
        )
    actions = {}
    for row in store.db.execute(
        "SELECT task,kind FROM events WHERE kind IN ('instinct_promoted','instinct_rolled_back') ORDER BY seq"
    ):
        actions[row["task"]] = row["kind"]
    result = {}
    for (category, model, effort), observations in records.items():
        identifier = f"{category}/{model}/{effort}"
        failures = [row["turn"] for row in observations if not row["passed"] or row["feedback"] == "needs-rework"]
        eligible = len({row["revision"] for row in observations}) >= 4 and not failures
        result[identifier] = {
            "scope": "AutoRouter Python regression", "count": len(observations),
            "observed_pass_fraction": sum(row["passed"] for row in observations) / len(observations),
            "observations": observations, "counterexamples": failures,
            "last_validation": observations[0]["checked_at"], "eligible": eligible,
            "recommended": eligible and actions.get(identifier) == "instinct_promoted",
        }
    return result


def set_pattern(store, identifier, promote):
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        candidate = patterns(store).get(identifier)
        if not candidate or (promote and not candidate["eligible"]):
            raise ValueError("Promotion needs four passing turns on distinct source snapshots")
        append(store.db, identifier, "instinct_promoted" if promote else "instinct_rolled_back", {})
    return patterns(store)[identifier]


def recommendations(path):
    if not Path(path).is_file():
        return []
    store = Store(path)
    if not store.db.execute("SELECT 1 FROM events WHERE kind='instinct_promoted' LIMIT 1").fetchone():
        return []
    return [name for name, pattern in patterns(store).items() if pattern["recommended"]
            and re.fullmatch(r"[a-z]+/[a-z0-9.-]+/[a-z]+", name)][:8]
