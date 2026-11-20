"""Native registration, scoped workspace administration and stdio service."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .universal import ASSETS, MATRIX, STAGES, Workspace, profile, serve, state_root, workspace_dir

BINS = {"codex": "codex", "claude-code": "claude", "gemini-cli": "gemini", "opencode": "opencode", "cline": "cline"}


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp.write(content)
        temp.flush()
        os.fsync(temp.fileno())
    try:
        os.replace(temp.name, path)
    finally:
        Path(temp.name).unlink(missing_ok=True)


def configure(options, root):
    directory = workspace_dir(root)
    file = directory / "profile.json"
    data = json.loads(file.read_text()) if file.exists() else {
        "schema": 1, "root": str(root), "hosts": [], "writers": [], "skills": {}, "catalogs": {}}
    if data.get("schema") != 1 or data["root"] != str(root):
        raise ValueError("Incompatible workspace configuration")
    host = options.target
    if host not in data["hosts"]:
        data["hosts"].append(host)
    if options.write and host not in data["writers"]:
        data["writers"].append(host)
    if options.revoke:
        data["hosts"].remove(host)
        data["writers"] = [x for x in data["writers"] if x != host]
    for name in ("vault", "graph"):
        path = getattr(options, name)
        if path:
            path = path.resolve(strict=True)
            if not path.is_dir():
                raise ValueError("Expected an existing project directory")
            data[name] = str(path)
    for skill in options.skill:
        p = skill.resolve(strict=True)
        if p.name != "SKILL.md":
            raise ValueError("Choose a SKILL.md file")
        data["skills"][p.parent.name] = str(p)
    if options.catalog:
        cat = json.loads(options.catalog.read_text())
        if not isinstance(cat, list) or len(cat) > 256:
            raise ValueError("Expected a bounded model catalog")
        for m in cat:
            if (not isinstance(m, dict) or set(m) != {"id", "stages"} or not isinstance(m["id"], str)
                    or not m["id"].strip() or not isinstance(m["stages"], list) or not set(m["stages"]) <= set(STAGES)):
                raise ValueError("Invalid model entry")
        data["catalogs"][host] = cat
    if file.exists():
        atomic(directory / "profile.previous.json", file.read_bytes())
    atomic(file, (json.dumps(data, indent=2) + "\n").encode())


def bundle(target):
    # Versioned, operator-owned packages stay available for host-managed rollback.
    root = state_root().parent / "adapters" / "0.2.0" / target
    source = ASSETS / ("claude" if target == "claude-code" else "gemini")
    for path in source.rglob("*"):
        if path.is_file():
            atomic(root / path.relative_to(source), path.read_bytes())
    plugin = root / "plugins/autorouter" if target == "claude-code" else root
    atomic(plugin / "skills/project-context/SKILL.md", (ASSETS / "SKILL.md").read_bytes())
    return root


def install(host, remove, root):
    binary = shutil.which(BINS[host])
    if not binary:
        raise ValueError(f"Install or locate the official {BINS[host]} client first")
    if host == "opencode":
        version = subprocess.check_output([binary, "--version"], text=True, timeout=15).strip()
        if not version.startswith("1."):
            raise ValueError("The packaged config hook targets OpenCode 1.x")
        dest = root / ".opencode/plugins/autorouter.js"
        source = (ASSETS / "opencode.js").read_bytes()
        if dest.resolve() != dest or (dest.exists() and dest.read_bytes() != source):
            raise ValueError("Existing plugin differs or is symlinked")
        if remove:
            dest.unlink(missing_ok=True)
        else:
            atomic(dest, source)
        return
    server_command = shutil.which("autorouter")
    if not server_command:
        raise ValueError("Put the installed autorouter executable on PATH")
    args = ["serve", "--target", host]
    if host == "claude-code":
        name = "autorouter@autorouter-local"
        if remove:
            commands = [["plugin", "uninstall", name, "--scope", "local"]]
        else:
            commands = [["plugin", "marketplace", "add", str(bundle(host))],
                        ["plugin", "install", name, "--scope", "local"]]
    elif host == "gemini-cli":
        commands = [["extensions", "uninstall", "autorouter"]] if remove else [["extensions", "link", str(bundle(host))]]
    elif host == "cline":
        config = Path(os.environ.get("CLINE_DATA_DIR") or Path.home() / ".cline/data") / "settings/cline_mcp_settings.json"
        state = json.loads(config.read_text()) if config.is_file() else {}
        existing = state.get("mcpServers", {}).get("autorouter-project")
        expected = {"type": "stdio", "command": server_command, "args": args}
        if existing and existing.get("transport") != expected:
            raise ValueError("Another Cline MCP registration owns autorouter-project")
        if bool(existing) == (not remove):
            return
        if config.is_file():
            atomic(state_root().parent / "backup/cline-mcp.json", config.read_bytes())
        commands = [["mcp", "remove", "autorouter-project"]] if remove else [
            ["mcp", "add", "autorouter-project", "--yes", "--", server_command, *args]]
    else:
        listing = subprocess.run([binary, "mcp", "list", "--json"], check=True, capture_output=True, text=True, cwd=root, timeout=20)
        entries = {entry["name"]: entry for entry in json.loads(listing.stdout)}
        existing = entries.get("autorouter-project")
        if existing:
            transport = existing["transport"]
            if transport.get("command") != server_command or transport.get("args") != args:
                raise ValueError("Another Codex MCP registration owns autorouter-project")
        if bool(existing) == (not remove):
            return
        if "autorouter-memory" in entries and not remove:
            raise ValueError("The existing Codex memory service is already registered")
        config = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
        if config.is_file():
            atomic(state_root().parent / "backup/codex.toml", config.read_bytes())
        commands = [["mcp", "remove", "autorouter-project"]] if remove else [
            ["mcp", "add", "autorouter-project", "--", server_command, *args]]
    for command in commands:
        subprocess.run([binary, *command], check=True, cwd=root)


def main(argv=None):
    p = argparse.ArgumentParser(prog="autorouter")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("hosts")
    for action in ("install", "uninstall", "workspace", "serve", "session-context"):
        cmd = sub.add_parser(action)
        cmd.add_argument("--target", required=True, choices=MATRIX["hosts"])
        if action == "workspace":
            cmd.add_argument("--vault", type=Path)
            cmd.add_argument("--graph", type=Path)
            cmd.add_argument("--catalog", type=Path)
            cmd.add_argument("--skill", action="append", default=[], type=Path)
            cmd.add_argument("--write", action="store_true")
            cmd.add_argument("--revoke", action="store_true")
    args = p.parse_args(argv)
    root = Path.cwd().resolve()
    try:
        if args.action == "hosts":
            print(json.dumps({"schema": 1, "hosts": {h: {"executable": shutil.which(BINS[h]), "capabilities": caps}
                                                     for h, caps in MATRIX["hosts"].items()}}, indent=2))
        elif args.action == "workspace":
            configure(args, root)
        elif args.action == "serve":
            with closing(Workspace(root, args.target)) as workspace:
                serve(workspace, sys.stdin.buffer, sys.stdout)
        elif args.action == "session-context":
            if profile(root, args.target)[1]:
                print("AutoRouter project tools are available for this workspace.")
        else:
            install(args.target, args.action == "uninstall", root)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        p.exit(1, f"AutoRouter: {error}\n")


if __name__ == "__main__":
    main()
