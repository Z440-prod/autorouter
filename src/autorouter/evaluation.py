"""One bounded live capability probe per evaluation run."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from .routing import family_version
from .rpc import RPC

EXPECTED = {"total": 18, "missing": ["b", "d"]}
INTERVAL_SECONDS = 7 * 86400


def state_path(home=None):
    home = home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return home / "autorouter/verified-models.json"


def read(path):
    try:
        state = json.loads(path.read_text())
        if state.get("version") == 1 and all(isinstance(state.get(key), dict) for key in ("families", "checks")):
            return state
    except (OSError, ValueError, AttributeError):
        pass
    return {"version": 1, "families": {}, "checks": {}}


def write(path, state):
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(state, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def available(models):
    entries = {}
    for model in models:
        family, version = family_version(model["model"]) or (None, ())
        supported = {preset["reasoningEffort"] for preset in model.get("supportedReasoningEfforts", [])}
        effort = next((effort for effort in ("low", "medium", "high", "xhigh", "max") if effort in supported), None)
        if effort and not model.get("hidden") and "text" in model.get("inputModalities", []):
            entries[model["model"]] = (family, version, effort)
    return entries


def record(state, model, members, measured, now):
    family, version, effort = members[model]
    state["checks"][model] = {**measured, "evaluatedAt": now, "effort": effort}
    if family is None:
        return
    preferences = state["families"].setdefault(family, {})
    previous = preferences.get("current")
    if measured["passed"]:
        if previous != model and (previous not in members or version > members[previous][1]):
            preferences.update(current=model, previous=previous)
    elif previous == model:
        fallback = preferences.pop("previous", None)
        verified = [candidate for candidate, profile in members.items()
                    if profile[0] == family and state["checks"].get(candidate, {}).get("passed")]
        preferences["current"] = fallback if fallback in verified else max(
            verified, key=lambda candidate: members[candidate][1], default=None,
        )


def probe(binary, model, effort):
    prompt = ('Output only a JSON object. Set "total" to the sum of [3, 7, 8]. '
              'Set "missing" to the letters of ["a","b","c","d"] absent from ["a","c"], in that order. '
              'Use no tools.')
    start = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="autorouter-eval-") as work:
            env = {key: value for key, value in os.environ.items() if key != "AUTOROUTER_ENABLED"}
            run = subprocess.run(
                [binary, "--no-daemon", "--ask-for-approval", "never", "exec", "--sandbox", "read-only",
                 "--skip-git-repo-check", "--ephemeral", "-C", work, "--json", "--model", model,
                 "-c", f'model_reasoning_effort="{effort}"', "--", prompt],
                env=env, text=True, capture_output=True, timeout=60,
            )
        events = [json.loads(line) for line in run.stdout.splitlines() if line.startswith("{")]
        items = [event["item"] for event in events if event["type"] == "item.completed"]
        usage = next((event["usage"] for event in events if event["type"] == "turn.completed"), None)
        messages = [item["text"] for item in items if item["type"] == "agent_message"]
        tool_free = all(item["type"] in ("agent_message", "reasoning") for item in items)
        # Transport/quotas leave the last verified default unchanged.
        if run.returncode or not usage:
            return {"passed": None, "error": "inference did not complete", "exitCode": run.returncode}
        try:
            correct = bool(messages) and json.loads(messages[-1]) == EXPECTED
        except ValueError:
            correct = False
        return {"passed": bool(tool_free and correct), "usage": usage,
                "elapsedSeconds": round(time.monotonic() - start, 3)}
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return {"passed": None, "error": "probe unavailable"}


def run(binary):
    path = state_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (path.parent / "evaluation.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with RPC(timeout=20, binary=binary) as rpc:
            config = rpc.call("config/read")["config"]
            if config.get("model_catalog_json") or config.get("model_provider") not in (None, "openai"):
                raise RuntimeError("Live evaluation requires the authenticated OpenAI provider catalog")
            members = available(rpc.models())
        state = read(path)
        now = time.time()
        candidates = sorted(members, key=lambda model: members[model][1], reverse=True)
        candidate = next((model for model in candidates if model not in state["checks"]), None)
        if candidate is None:
            candidate = min(candidates, key=lambda model: state["checks"][model]["evaluatedAt"], default=None)
            if candidate is None or now - state["checks"][candidate]["evaluatedAt"] < INTERVAL_SECONDS:
                print("No model probe is due.")
                return
        measured = probe(binary, candidate, members[candidate][2])
        if measured["passed"] is not None:
            record(state, candidate, members, measured, now)
            write(path, state)
        print(json.dumps({"model": candidate, **measured, "families": state["families"]}, indent=2))
