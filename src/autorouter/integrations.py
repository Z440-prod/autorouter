"""AutoRouter's native plugin overlay and selected engineering capabilities."""

import json
import os
from pathlib import Path
import tomllib

PROJECT = Path(__file__).resolve().parents[2]
LOCK_FILE = PROJECT / "config/integrations.lock.json"
LOCK = json.loads(LOCK_FILE.read_text()) if LOCK_FILE.is_file() else {"autorouter_plugins": {}}
PLUGIN_VERSIONS = {name: info["version"] for name, info in LOCK["autorouter_plugins"].items()}
ECC_SKILLS = frozenset((
    "context-budget", "iterative-retrieval", "rust-patterns", "rust-testing",
    "database-migrations", "postgres-patterns",
))
ECC_HOOK = "ecc@ecc:hooks/codex-hooks.json:session_start:0:0"


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def plugin_root(name):
    return codex_home() / "plugins" / "cache" / name / name / PLUGIN_VERSIONS[name]


def reviewed_plugin_available(name):
    root = plugin_root(name)
    installed = {p.parent.parent.name for p in root.parent.glob("*/.codex-plugin/plugin.json")}
    return installed == {PLUGIN_VERSIONS[name]}


def native_flags():
    flags = ["-c", "skills.max_context_tokens=2500"]
    active = set()
    for name in PLUGIN_VERSIONS:
        enabled = reviewed_plugin_available(name)
        flags += ["-c", f"plugins.{name}@{name}.enabled={str(enabled).lower()}"]
        if enabled:
            active.add(name)
    disabled = []
    if "agent-skills" in active:
        disabled.append(plugin_root("agent-skills") / "skills/code-simplification/SKILL.md")
    ecc = plugin_root("ecc")
    if "ecc" in active:
        skills = (ecc / "skills").glob("*/SKILL.md")
        commands = (ecc / ".codex-plugin/migrated-command-skills").glob("*/SKILL.md")
        disabled.extend(
            path
            for path in sorted([*skills, *commands])
            if path.parent.name not in ECC_SKILLS
        )
        flags += ["-c", "plugins.ecc@ecc.mcp_servers.chrome-devtools.enabled=false",
                  "-c", "hooks.state={" + json.dumps(ECC_HOOK) + "={enabled=false}}"]
    if disabled:
        rules = ["{path=" + json.dumps(str(path.resolve())) + ",enabled=false}" for path in disabled]
        flags += ["-c", "skills.config=[" + ",".join(rules) + "]"]
    for path in sorted((PROJECT / "config/agents").glob("*.toml")):
        role = tomllib.loads(path.read_text())
        flags += [
            "-c", f"agents.{path.stem}.description={json.dumps(role['description'])}",
            "-c", f"agents.{path.stem}.config_file={json.dumps(str(path))}",
        ]
    return flags
