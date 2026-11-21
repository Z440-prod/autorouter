import sqlite3
import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path
from .rpc import RPC
from .memory import Memory
from .engine import Engine, Store
from .routing import ALIASES, TIERS, family_version

PROJECT = Path(__file__).resolve().parents[2]


def paths():
    root = Path(
        os.environ.get("AUTOROUTER_HOME", Path.home() / ".local/state/autorouter")
    )
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    cfg = Path(
        os.environ.get(
            "AUTOROUTER_CONFIG", Path.home() / ".config/autorouter/config.json"
        )
    )
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if not cfg.exists():
        cfg.write_text(
            json.dumps(
                {
                    "vault": str(Path.home() / "Documents/AutoRouter-Brain"),
                    "max_handoffs": 16,
                    "alternatives": {},
                },
                indent=2,
            )
        )
    return root, cfg, json.loads(cfg.read_text())


def output(x):
    print(json.dumps(x, indent=2))


def capabilities():
    import tomllib

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    cache = codex_home / "plugins/cache"
    codex_config = (
        tomllib.loads((codex_home / "config.toml").read_text())
        if (codex_home / "config.toml").exists()
        else {}
    )
    rows = []
    from .integrations import ECC_SKILLS, PLUGIN_VERSIONS, reviewed_plugin_available
    for name, ops in [
        ("obsidian", ["Markdown", "wikilinks", "canvas", "bases"]),
        ("ponytail", ["review", "audit", "engineering discipline"]),
        ("rea", ["authorized static analysis", "Electron", "ELF"]),
        ("agent-skills", ["specification", "development workflow", "verification"]),
        ("ecc", sorted(ECC_SKILLS)),
    ]:
        matches = sorted(
            [
                *cache.glob(f"*/{name}/*/.codex-plugin/plugin.json"),
                *cache.glob(f"*/{name}/*/plugin.json"),
            ]
        )
        rows.append(
            {
                "plugin": name,
                "tool_type": "plugin",
                "enabled": reviewed_plugin_available(name) if name in PLUGIN_VERSIONS else codex_config.get("plugins", {})
                .get(
                    {
                        "obsidian": "obsidian@obsidian-skills",
                        "ponytail": "ponytail@ponytail",
                        "rea": "rea@rea",
                        "agent-skills": "agent-skills@agent-skills",
                        "ecc": "ecc@ecc",
                    }[name],
                    {},
                )
                .get("enabled", False),
                "installed": bool(matches),
                "manifest": str(matches[-1]) if matches else None,
                "operations": ops,
                "health": "installed; runtime probe required",
                "permissions": "Codex approvals and sandbox",
                "supported_models": "Codex tool-capable models",
                "overhead_ms": None,
                "dependencies": ["node >=24.22.1 <25", "procps"]
                if name == "rea"
                else (["node"] if name == "ponytail" else []),
            }
        )
    rows.append(
        {
            "skill": "cli-anything",
            "installed": (Path.home() / ".codex/skills/cli-anything/SKILL.md").exists(),
            "operations": ["CLI generation", "harness validation"],
            "hub": shutil.which("cli-hub"),
            "health": "installed" if shutil.which("cli-hub") else "unavailable",
        }
    )
    return rows


def doctor(state, config, memory, live=False):
    from . import graph
    checks = []

    def add(name, status, detail):
        checks.append({"component": name, "status": status, "detail": detail})

    native = Path.home() / ".local/lib/autorouter/codex"
    add("native Codex build", "working" if native.is_file() else "unavailable", str(native))
    for x in [
        "codex",
        "node",
        "npm",
        "git",
        "python3",
        "sqlite3",
        "cli-hub",
        "rg",
        "ps",
    ]:
        add(
            x,
            "working" if shutil.which(x) else "unavailable",
            shutil.which(x) or "Install required dependency",
        )
    p = subprocess.run(["codex", "login", "status"], capture_output=True, text=True)
    add(
        "authentication",
        "working" if p.returncode == 0 else "needs user action",
        (p.stdout + p.stderr).strip(),
    )
    add(
        "memory",
        "working" if memory.status()["integrity"] == "ok" else "misconfigured",
        memory.status(),
    )
    db = Store(state / "tasks.sqlite")
    add(
        "task persistence",
        "working"
        if db.db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        else "misconfigured",
        str(state / "tasks.sqlite"),
    )
    caps = capabilities()
    add("plugins", "working" if all(x["installed"] for x in caps) else "degraded", caps)
    for name, location in graph.projects(state).items():
        health = graph.status(*location)
        add(f"Graphify {name}", "working" if health["status"] == "available" else "degraded", health)
    add(
        "embeddings",
        "working" if memory.embedding else "unavailable",
        memory.status()["semantic"],
    )
    tests = PROJECT / "docs/test-results.txt"
    passed = tests.exists() and tests.read_text().rstrip().endswith("OK")
    add("recent tests", "working" if passed else "degraded", str(tests))
    add(
        "memory watcher",
        "working"
        if subprocess.run(
            [
                "systemctl",
                "--user",
                "is-active",
                "--quiet",
                "autorouter-memory.service",
            ],
            capture_output=True,
        ).returncode
        == 0
        else "degraded",
        "systemctl --user status autorouter-memory.service",
    )
    add(
        "Obsidian application",
        "working" if shutil.which("obsidian") else "needs user action",
        shutil.which("obsidian")
        or "Install the verified official package in vendor, then open ~/Documents/AutoRouter-Brain",
    )
    if live:
        try:
            with RPC(binary=str(Path.home() / ".local/bin/autorouter")) as r:
                models = r.models()
                missing = [
                    family for family in ALIASES if not any(
                        (family_version(m["model"]) or (None,))[0] == family for m in models
                    )
                ]
                add(
                    "models",
                    "working" if not missing else "degraded",
                    {
                        "count": len(models),
                        "preferred_missing": missing,
                        "efforts": {
                            m["id"]: [
                                e["reasoningEffort"]
                                for e in m["supportedReasoningEfforts"]
                            ]
                            for m in models
                            if family_version(m["model"])
                        },
                        "context_limits": "not exposed by model/list; not assumed",
                        "pricing": "ChatGPT subscription; no API cost inferred",
                    },
                )
                servers = []
                cursor = None
                while True:
                    page = r.call(
                        "mcpServerStatus/list", {"cursor": cursor, "limit": 100}
                    )
                    servers += page["data"]
                    cursor = page.get("nextCursor")
                    if not cursor:
                        break
                summary = [
                    {
                        "name": x["name"],
                        "plugin": x.get("pluginId"),
                        "tools": len(x.get("tools", {})),
                        "initialized": bool(x.get("serverInfo")),
                    }
                    for x in servers
                ]
                required = {"rea", "autorouter-memory"}
                ok = all(
                    any(
                        x["name"] == name and x["initialized"] and x["tools"]
                        for x in summary
                    )
                    for name in required
                )
                add("MCP runtime", "working" if ok else "degraded", summary)
                add(
                    "MCP duplicates",
                    "working"
                    if all(sum(x["name"] == name and x["initialized"] for x in summary) == 1
                           for name in required)
                    else "misconfigured",
                    "One initialized server per integration",
                )
                hooks = r.call("hooks/list", {"cwds": [str(PROJECT)]})
                pony = [
                    h
                    for group in hooks["data"]
                    for h in group["hooks"]
                    if h.get("pluginId") == "ponytail@ponytail"
                ]
                trusted = bool(pony) and all(
                    h.get("trustStatus") == "trusted" and h.get("enabled") for h in pony
                )
                add(
                    "Ponytail hooks",
                    "working" if trusted else "needs user action",
                    [
                        {
                            "event": h["eventName"],
                            "trust": h.get("trustStatus"),
                            "enabled": h.get("enabled"),
                        }
                        for h in pony
                    ],
                )
                skills = r.call(
                    "skills/list", {"cwds": [str(PROJECT)], "forceReload": True}
                )
                names = [
                    x["name"]
                    for g in skills["data"]
                    for x in g["skills"]
                    if x.get("enabled")
                ]
                add(
                    "skill discovery",
                    "working"
                    if "cli-anything" in names
                    and any(x.startswith("ponytail:") for x in names)
                    and any(x.startswith("obsidian:") for x in names)
                    and any(x.startswith("agent-skills:") for x in names)
                    and any(x.startswith("ecc:") for x in names)
                    else "degraded",
                    names,
                )
                ecc_hooks = [
                    h for group in hooks["data"] for h in group["hooks"]
                    if h.get("pluginId") == "ecc@ecc" and h.get("trustStatus") == "trusted"
                ]
                chrome_active = any(x["name"] == "chrome-devtools" and x["initialized"] for x in summary)
                add("ECC isolation", "misconfigured" if ecc_hooks or chrome_active else "working",
                    {"trusted_hooks": len(ecc_hooks), "browser_mcp_initialized": chrome_active})
        except Exception as e:
            add("App Server", "degraded", str(e))
    else:
        add(
            "runtime probes",
            "unavailable",
            "Run autorouter-admin doctor --live to verify models, plugins, hooks and MCP startup",
        )
    return checks


def main():
    p = argparse.ArgumentParser(
        prog="autorouter-admin",
        description="AutoRouter administration and legacy backend task inspection",
    )
    sub = p.add_subparsers(dest="cmd")
    run = sub.add_parser("run")
    run.add_argument("prompt", nargs="?")
    run.add_argument("--model", choices=["auto", *ALIASES], default=None)
    run.add_argument("--effort", choices=list(TIERS))
    run.add_argument("--cwd", default=os.getcwd())
    run.add_argument("--resume")
    run.add_argument("--image", action="append", default=[])
    run.add_argument("--confirm-recovery", action="store_true")
    run.add_argument("--max-stages", type=int, default=12)
    d = sub.add_parser("doctor")
    d.add_argument("--live", action="store_true")
    d.add_argument("--json", action="store_true")
    d.add_argument("--verbose", action="store_true")
    sub.add_parser("models")
    sub.add_parser("status")
    sub.add_parser("routes")
    sub.add_parser("plugins")
    sub.add_parser("config")
    sub.add_parser("benchmark")
    m = sub.add_parser("memory")
    m.add_argument(
        "operation",
        choices=["status", "search", "reindex", "doctor", "inspect", "repair", "watch"],
    )
    m.add_argument("query", nargs="?", default="")
    t = sub.add_parser("task")
    t.add_argument("operation", choices=["list", "inspect"])
    t.add_argument("id", nargs="?")
    a = p.parse_args()
    os.umask(0o077)
    state, cfg, conf = paths()
    if a.cmd == "memory" and a.operation == "repair":
        import datetime

        stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        subprocess.run(
            ["systemctl", "--user", "stop", "autorouter-memory.service"],
            capture_output=True,
        )
        for suffix in ("", "-wal", "-shm"):
            index = state / ("memory.sqlite" + suffix)
            if index.exists():
                index.rename(state / ("memory.sqlite.backup-" + stamp + suffix))
    try:
        memory = Memory(conf["vault"], state / "memory.sqlite", conf.get("embeddings"))
    except sqlite3.DatabaseError as e:
        print(
            "Memory index unavailable: "
            + str(e)
            + "; run autorouter-admin memory repair (backs up index, preserves vault)",
            file=sys.stderr,
        )
        return 1
    try:
        if a.cmd == "run" or a.cmd is None:
            if a.cmd is None:
                from .native import main as native_main
                return native_main()
            else:
                if not a.prompt and not a.resume:
                    p.error("Provide a prompt or --resume TASK_ID")
                Engine({**conf, "capabilities": capabilities()}, state, memory).run(
                    a.prompt,
                    a.cwd,
                    a.model,
                    a.effort,
                    a.resume,
                    a.confirm_recovery,
                    a.max_stages,
                    a.image,
                )
        elif a.cmd == "doctor":
            checks = doctor(state, conf, memory, a.live)
            if a.json:
                output(checks)
            else:
                for row in checks:
                    print(f"{row['status']:18} {row['component']}")
                    if a.verbose:
                        print("  " + json.dumps(row["detail"]))
        elif a.cmd == "config":
            print(cfg)
            output(conf)
        elif a.cmd == "models":
            with RPC() as r:
                models = r.models()
                (state / "models.json").write_text(json.dumps(models, indent=2))
                output(models)
        elif a.cmd == "plugins":
            output(capabilities())
        elif a.cmd in ("status", "routes", "task"):
            store = Store(state / "tasks.sqlite")
            if a.cmd == "task" and a.operation == "inspect":
                output(store.get(a.id))
            elif a.cmd == "routes":
                output(
                    [
                        dict(x)
                        for x in store.db.execute(
                            "SELECT task,time,data FROM events WHERE kind='route' ORDER BY seq DESC LIMIT 30"
                        )
                    ]
                )
            else:
                output(
                    [
                        {
                            "id": s["id"],
                            "status": s["status"],
                            "request": s["request"][:120],
                            "turns": len(s["history"]),
                        }
                        for s in store.list()
                    ]
                )
        elif a.cmd == "memory":
            if a.operation == "watch":
                while True:
                    memory.reindex()
                    time.sleep(2)
            elif a.operation == "search":
                memory.ensure_current()
                output(memory.search(a.query))
            elif a.operation in ("status", "doctor"):
                output(memory.status())
            elif a.operation == "inspect":
                path = (memory.root / a.query).resolve()
                if not path.is_relative_to(memory.root):
                    raise ValueError("Path outside vault")
                from .memory import SECRET

                body = path.read_text()
                if SECRET.search(body):
                    raise ValueError("Credential-like content excluded")
                print(body)
            elif a.operation == "repair":
                with memory.db:
                    memory.db.execute("DELETE FROM notes")
                    memory.db.execute("DELETE FROM search")
                    memory.db.execute("DELETE FROM links")
                output(memory.reindex())
                subprocess.run(
                    ["systemctl", "--user", "start", "autorouter-memory.service"],
                    capture_output=True,
                )
            else:
                output(memory.reindex())
        elif a.cmd == "benchmark":
            indexed = memory.reindex()
            samples = []
            for _ in range(100):
                start = time.perf_counter()
                memory.search("AutoRouter architecture")
                samples.append((time.perf_counter() - start) * 1000)
            result = {
                "index": indexed,
                "retrieval_median_ms": sorted(samples)[50],
                "retrieval_p95_ms": sorted(samples)[95],
                "samples": 100,
                "routing": "See routes classification_ms; requires live inference",
                "handoff": "Not inferred from mocked tests",
            }
            (PROJECT / "docs/benchmark.json").write_text(json.dumps(result, indent=2))
            output(result)
    except (Exception, KeyboardInterrupt) as e:
        print(f"AutoRouter: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
