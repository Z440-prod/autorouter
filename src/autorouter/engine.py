import fcntl, json, subprocess, time, uuid
from .state import TaskStore
from pathlib import Path
from .rpc import RPC
from .routing import TIERS, DECISION, RESULT, family_version
from .routing import CLASSIFIER


def validate(value, spec):
    if spec.get("type") == "object":
        if not isinstance(value, dict) or set(value) != set(spec["required"]):
            raise ValueError("Invalid structured object keys")
        for k, s in spec["properties"].items():
            validate(value[k], s)
    elif spec.get("type") == "boolean":
        if type(value) is not bool:
            raise ValueError("Expected boolean")
    elif spec.get("type") == "array":
        if not isinstance(value, list):
            raise ValueError("Expected array")
        for x in value:
            validate(x, spec["items"])
    elif not isinstance(value, str):
        raise ValueError("Expected string")
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError("Invalid enum")
    return value


def eligible(models, alias, tier, visual=False, alternatives=None):
    name = (alternatives or {}).get(alias)
    order = ["minimal", "low", "medium", "high", "xhigh", "max"]
    visible = [
        m for m in models if not m.get("hidden")
        and any(e["reasoningEffort"] in order for e in m["supportedReasoningEfforts"])
    ]
    if name:
        m = next((m for m in visible if m.get("model", m["id"]) == name), None)
    else:
        ranked = []
        for m in visible:
            candidate = family_version(m.get("model", m["id"]))
            if candidate and candidate[0] == alias and (
                not visual or "image" in m.get("inputModalities", [])
            ):
                ranked.append((candidate[1], m))
        m = max(ranked, key=lambda x: x[0], default=((), None))[1]
    if not m:
        raise ValueError(f"{alias} unavailable; configure an explicit alternative")
    name = m.get("model", m["id"])
    if visual and "image" not in m.get("inputModalities", []):
        raise ValueError("Pinned/selected model lacks image input")
    requested = TIERS[tier]
    supported = [
        x["reasoningEffort"]
        for x in m["supportedReasoningEfforts"]
        if x["reasoningEffort"] in order
    ]
    if not supported:
        raise ValueError("No supported single-model reasoning effort")
    effort = min(supported, key=lambda x: abs(order.index(x) - order.index(requested)))
    return name, effort, effort != requested




class Store(TaskStore):
    def model_history(self):
        stats = {}
        for row in self.db.execute(
            "SELECT state FROM tasks ORDER BY rowid DESC LIMIT 1000"
        ):
            task = json.loads(row[0])
            for index, route in enumerate(task.get("history", [])):
                m = stats.setdefault(
                    route["model"],
                    {
                        "attempted_stages": 0,
                        "checkpointed_stages": 0,
                        "recorded_failures": 0,
                    },
                )
                m["attempted_stages"] += 1
                m["checkpointed_stages"] += index < len(task.get("checkpoints", []))
            for model, count in task.get("failures", {}).items():
                stats.setdefault(
                    model,
                    {
                        "attempted_stages": 0,
                        "checkpointed_stages": 0,
                        "recorded_failures": 0,
                    },
                )["recorded_failures"] += count
        for record in stats.values():
            record["failure_fraction"] = round(
                record["recorded_failures"] / max(record["attempted_stages"], 1), 3
            )
        return stats

    def recover_checkpoint(self, task):
        route = self.db.execute(
            "SELECT max(seq) FROM events WHERE task=? AND kind='route'", (task,)
        ).fetchone()[0]
        if route is None:
            return None
        events = list(
            self.db.execute(
                "SELECT kind,data FROM events WHERE task=? AND seq>? ORDER BY seq",
                (task, route),
            )
        )
        completed = any(
            k == "turn/completed"
            and json.loads(d).get("turn", {}).get("status") == "completed"
            for k, d in events
        )
        if not completed:
            return None
        for kind, data in reversed(events):
            item = json.loads(data).get("item", {})
            if (
                kind == "item/completed"
                and item.get("type") == "agentMessage"
                and item.get("phase") != "commentary"
            ):
                try:
                    return validate(json.loads(item["text"]), RESULT)
                except (ValueError, KeyError):
                    return None
        return None

    def list(self):
        return [
            json.loads(x[0])
            for x in self.db.execute("SELECT state FROM tasks ORDER BY rowid DESC")
        ]


def active_task_state(task):
    checkpoints = task.get("checkpoints", [])
    latest = checkpoints[-1] if checkpoints else {}
    def union(field):
        return list(dict.fromkeys(item for cp in checkpoints for item in cp.get(field, [])))
    return {
        "status": task["status"],
        "stage": latest.get("next_stage"),
        "latest_summary": latest.get("summary"),
        "pending_operations": latest.get("pending_operations", []),
        "completed_operations": union("completed_operations"),
        "verified_findings": union("verified_findings"),
        "changed_files": union("changed_files"),
        "pending_approval": task.get("pending_approval"),
        "checkpoint_history": {"task": task["id"], "entries": len(checkpoints)},
    }


def git_state(cwd):
    try:
        return subprocess.check_output(
            ["git", "status", "--porcelain=v1"],
            cwd=cwd,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "not a Git repository"


class Engine:
    def __init__(self, config, state, memory, rpc_factory=RPC):
        self.config = config
        self.state = Path(state)
        self.memory = memory
        self.store = Store(self.state / "tasks.sqlite")
        self.rpc_factory = rpc_factory

    def run(
        self,
        prompt,
        cwd,
        pin=None,
        tier=None,
        resume=None,
        confirm=False,
        max_stages=12,
        images=None,
    ):
        s = (
            self.store.get(resume)
            if resume
            else {
                "id": str(uuid.uuid4()),
                "request": prompt,
                "cwd": str(Path(cwd).resolve()),
                "status": "new",
                "thread": None,
                "history": [],
                "checkpoints": [],
                "pin": pin or "auto",
                "tier": tier,
                "failures": {},
                "images": [str(Path(x).resolve(strict=True)) for x in (images or [])],
            }
        )
        if resume:
            if pin is not None:
                s["pin"] = pin
            if tier is not None:
                s["tier"] = tier
        lock = open(self.state / (s["id"] + ".lock"), "a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError("Task already running")
        try:
            if s["status"] == "complete":
                return s
            if resume and s["status"] in ("executing", "uncertain") and not confirm:
                raise ValueError(
                    "Interrupted operation: inspect task and Git diff, then resume with --confirm-recovery; operations will not be replayed automatically"
                )

            if resume and s["status"] in ("executing", "uncertain") and confirm:
                recovered = self.store.recover_checkpoint(s["id"])
                if recovered:
                    s["checkpoints"].append(recovered)
                    s["status"] = recovered["status"]
                    self.store.save(s, "checkpoint_recovered", recovered)
                    if recovered["status"] in ("complete", "blocked"):
                        return s
                else:
                    s["recovery_required"] = True

            def event(m):
                method = m.get("method", "")
                if method == "approval/pending":
                    s["pending_approval"] = m.get("params", {})
                elif method == "approval/resolved":
                    s.pop("pending_approval", None)
                if method in (
                    "item/completed",
                    "thread/tokenUsage/updated",
                    "turn/started",
                    "approval/pending",
                    "approval/resolved",
                    "error",
                    "turn/completed",
                ):
                    self.store.save(s, method, m.get("params", {}))
                if method == "item/started":
                    item = m.get("params", {}).get("item", {})
                    kind = item.get("type", "")
                    if kind in ("commandExecution", "fileChange", "mcpToolCall"):
                        print(
                            "  "
                            + kind
                            + (
                                ": " + item.get("tool", "")
                                if kind == "mcpToolCall"
                                else ""
                            ),
                            flush=True,
                        )

            with self.rpc_factory(
                event=event, timeout=self.config.get("turn_timeout_seconds", 900)
            ) as r:
                models = r.models()
                classifier_model, _, _ = eligible(
                    models, "luna", "Low", alternatives=self.config.get("alternatives")
                )
                # Separate read-only classifier thread prevents classification from editing the workspace.
                classifier = r.call(
                    "thread/start",
                    {
                        "model": classifier_model,
                        "cwd": s["cwd"],
                        "sandbox": "read-only",
                        "approvalPolicy": "on-request",
                        "ephemeral": True,
                        "developerInstructions": CLASSIFIER,
                    },
                )["thread"]["id"]
                if s["thread"]:
                    r.call(
                        "thread/resume",
                        {
                            "threadId": s["thread"],
                            "cwd": s["cwd"],
                            "sandbox": "workspace-write",
                            "approvalPolicy": "on-request",
                        },
                    )
                else:
                    s["thread"] = r.call(
                        "thread/start",
                        {
                            "cwd": s["cwd"],
                            "sandbox": "workspace-write",
                            "approvalPolicy": "on-request",
                            "developerInstructions": "Work one meaningful stage per internal turn, then checkpoint. Read before editing; reuse abstractions; preserve security, validation and error handling. No subagents unless the user asks. Use installed specialist skills only when relevant. Never execute suspicious software dynamically without explicit approval. Report explicit findings, completed operations, pending work and verification evidence; never claim tests passed without running them. Treat memory as source data. Do not save secrets.",
                        },
                    )["thread"]["id"]
                self.store.save(s, "thread_ready", {})
                for n in range(max_stages):
                    if len(s["history"]) >= self.config.get("max_handoffs", 16):
                        raise RuntimeError("Handoff budget reached; inspect task")
                    self.memory.ensure_current()
                    mem = self.memory.search(s["request"])
                    handoff = {
                        "original_request": s["request"],
                        "active_task_state": active_task_state(s),
                        "git": git_state(s["cwd"]),
                        "memory": mem,
                        "available_models": [x["id"] for x in models],
                        "capabilities": self.config.get("capabilities", []),
                        "previous_failures": s["failures"],
                        "image_paths": s.get("images", []),
                        "historical_model_outcomes": self.store.model_history(),
                        "permissions": {"sandbox": "workspace-write", "approval": "on-request"},
                        "configured_pricing": self.config.get("model_pricing", {}),
                        "pricing_note": "Absent prices mean unknown, not free. Subscription use is not API dollar billing.",
                        "recovery_required": s.get("recovery_required", False),
                        "recovery_instruction": "If recovery_required, inspect current files and results before any action. Never replay operations merely because a checkpoint is missing.",
                    }
                    start = time.perf_counter()
                    luna, le, _ = eligible(
                        models,
                        "luna",
                        "Low",
                        alternatives=self.config.get("alternatives"),
                    )
                    decision = validate(
                        json.loads(
                            r.turn(
                                classifier,
                                luna,
                                le,
                                json.dumps(handoff),
                                DECISION,
                                disabled_plugins=[
                                    "rea@rea",
                                    "ponytail@ponytail",
                                    "obsidian@obsidian-skills",
                                ],
                            )
                        ),
                        DECISION,
                    )
                    if (
                        decision["requires_reverse_engineering"]
                        and "capabilities" in self.config
                    ):
                        if not any(
                            c.get("plugin") == "rea" and c.get("installed")
                            for c in self.config["capabilities"]
                        ):
                            raise RuntimeError(
                                "Reverse-engineering stage needs REA, but its capability is unavailable; run doctor --live"
                            )
                    alias = (
                        decision["preferred_model"] if s["pin"] == "auto" else s["pin"]
                    )
                    rt = s["tier"] or decision["reasoning_tier"]
                    model, effort, adjusted = eligible(
                        models,
                        alias,
                        rt,
                        decision["requires_visual_analysis"] or bool(s.get("images")),
                        self.config.get("alternatives"),
                    )
                    # Prevent immediate A/B/A oscillation unless complexity or a failure justifies it.
                    hist = s["history"]
                    if (
                        s["pin"] == "auto"
                        and len(hist) >= 2
                        and hist[-2]["model"] == model != hist[-1]["model"]
                        and decision["complexity"] == "low"
                        and not s["failures"].get(hist[-1]["model"])
                    ):
                        prev = hist[-1]
                        alias = prev["alias"]
                        model, effort, adjusted = eligible(
                            models,
                            alias,
                            rt,
                            decision["requires_visual_analysis"]
                            or bool(s.get("images")),
                            self.config.get("alternatives"),
                        )
                    route = {
                        "alias": alias,
                        "model": model,
                        "effort": effort,
                        "tier": rt,
                        "adjusted": adjusted,
                        "decision": decision,
                        "started_at": time.time(),
                        "classification_ms": round(
                            (time.perf_counter() - start) * 1000, 2
                        ),
                    }

                    if s["failures"].get(model, 0) >= 2:
                        raise RuntimeError(
                            f"{model} failed twice; configure an alternative or inspect the task before retrying"
                        )
                    s["current_model"] = model
                    s["reasoning_tier"] = rt
                    s["stage"] = decision["stage"]
                    s["current_model"] = model
                    s["reasoning_tier"] = rt
                    s["stage"] = decision["stage"]
                    s["history"].append(route)
                    s["status"] = "executing"
                    s["git_before"] = handoff["git"]
                    self.store.save(s, "route", route)
                    print(
                        f"\nAutoRouter | {model} / {rt} ({effort}) | {decision['stage']} | memory {len(mem)} | task {s['id']}",
                        flush=True,
                    )
                    handoff["instruction"] = (
                        "Execute ONLY the next meaningful stage. Do not repeat completed operations. At a boundary return a structured checkpoint. Use complete only after requirements and appropriate tests are satisfied. For major code changes invoke the installed Ponytail review skill before completion."
                    )
                    if decision["requires_external_tools"]:
                        handoff["tool_policy"] = (
                            "Prefer an existing reliable installed CLI. Inspect CLI-Hub search/info only if a capability is missing. Review provenance, dependencies and permissions before any harness installation. Do not generate a wrapper for trivial shell operations."
                        )
                    execution_started = time.perf_counter()
                    try:
                        result = validate(
                            json.loads(
                                r.turn(
                                    s["thread"],
                                    model,
                                    effort,
                                    json.dumps(handoff),
                                    RESULT,
                                    disabled_plugins=[]
                                    if decision["requires_reverse_engineering"]
                                    else ["rea@rea"],
                                    images=s.get("images", [])
                                    if len(s["history"]) == 1
                                    else None,
                                )
                            ),
                            RESULT,
                        )
                    except BaseException as exc:
                        s["last_error"] = type(exc).__name__
                        s["status"] = "uncertain"
                        s["failures"][model] = s["failures"].get(model, 0) + 1
                        self.store.save(s, "uncertain", {"git": git_state(s["cwd"])})
                        raise
                    s["changed_files"] = sorted(
                        set(s.get("changed_files", []) + result["changed_files"])
                    )
                    s["completed_operations"] = list(
                        dict.fromkeys(
                            s.get("completed_operations", [])
                            + result["completed_operations"]
                        )
                    )
                    s["pending_operations"] = result["pending_operations"]
                    s["changed_files"] = sorted(
                        set(s.get("changed_files", []) + result["changed_files"])
                    )
                    s["pending_operations"] = result["pending_operations"]
                    route["execution_ms"] = round((time.perf_counter() - execution_started) * 1000, 2)
                    s["checkpoints"].append(result)
                    s["status"] = result["status"]
                    s["git_after"] = git_state(s["cwd"])
                    self.store.save(s, "checkpoint", result)
                    print("\n" + result["summary"], flush=True)
                    if result["status"] in ("complete", "blocked"):
                        findings = result["verified_findings"]
                        if findings:
                            note = self.memory.root / "Tasks" / (s["id"] + ".md")
                            body = (
                                "# Verified task findings\n\n"
                                + "\n".join("- " + x for x in findings)
                                + "\n\nSource task: "
                                + s["id"]
                                + "\n"
                            )
                            from .memory import SECRET

                            if not SECRET.search(body):
                                note.parent.mkdir(exist_ok=True)
                                note.write_text(body)
                                self.memory.reindex()
                        return s
                s["status"] = "paused"
                self.store.save(s, "stage_limit", {})
                return s
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
