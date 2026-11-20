"""Run the reviewed AgentShield scanner against an isolated Codex snapshot."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib

from .integrations import LOCK, PROJECT, codex_home, plugin_root
from .memory import SECRET


def audit():
    runner = Path.home() / ".local/lib/autorouter/agentshield"
    lock = json.loads((runner / "package-lock.json").read_text())
    package = lock["packages"]["node_modules/ecc-agentshield"]
    reviewed = LOCK["agentshield"]
    if any(package[key] != reviewed[key] for key in ("version", "integrity")):
        raise ValueError("Installed AgentShield package differs from the reviewed release")
    bubblewrap, node = (shutil.which(command) for command in ("bwrap", "node"))
    if not bubblewrap or not node:
        raise ValueError("AgentShield needs bubblewrap and Node")
    home = codex_home()
    with tempfile.TemporaryDirectory(prefix="autorouter-audit-") as stage:
        snapshot = Path(stage)
        sources = [
            (home / "config.toml", "config.toml"),
            (home / "AGENTS.md", ".codex/AGENTS.md"),
            (home / "hooks.json", ".codex/hooks.json"),
            (PROJECT / "AGENTS.md", "AGENTS.md"),
            *((path, ".codex/agents/" + path.name) for path in
              (PROJECT / "config/agents").glob("*.toml")),
        ]
        for name, version in (("ponytail", "5.3.3"), ("obsidian", "1.2.0"), ("rea", "6.9.0"),
                              ("agent-skills", None), ("ecc", None)):
            root = plugin_root(name) if version is None else next(
                iter(home.glob(f"plugins/cache/*/{name}/{version}")), None
            )
            if root is None:
                continue
            manifest_path = root / ".codex-plugin/plugin.json"
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text())
            sources.append((manifest_path, f"plugins/{name}/.codex-plugin/plugin.json"))
            for key, destination in (("hooks", ".codex/hooks.json"), ("mcpServers", ".mcp.json")):
                value = manifest.get(key)
                if isinstance(value, str):
                    source = (root / value).resolve()
                    if source.is_relative_to(root.resolve()):
                        sources.append((source, f"plugins/{name}/{destination}"))
        captured = []
        for source, relative in sources:
            if source.is_file() and not source.is_symlink() and source.stat().st_size <= 2_000_000:
                target = snapshot / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                captured.append(relative)

        command = [
            bubblewrap, "--die-with-parent", "--unshare-all", "--clearenv",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64", "--ro-bind", str(Path(node).resolve()), "/node",
            "--ro-bind", str(runner), "/scanner", "--ro-bind", stage, "/audit",
            "--tmpfs", "/tmp", "--setenv", "HOME", "/tmp", "--dev", "/dev", "--proc", "/proc",
            "--chdir", "/audit", "/node", "/scanner/node_modules/ecc-agentshield/dist/index.js",
            "scan", "--path", "/audit", "--format", "json",
        ]
        run = subprocess.run(command, capture_output=True, text=True, timeout=30)
        try:
            data = json.loads(run.stdout)
        except ValueError as error:
            raise RuntimeError(f"AgentShield failed inside bubblewrap (exit {run.returncode})") from error
        # Keep detection and locations while withholding snippets that may contain credentials.
        findings = [
            {key: item[key] for key in ("id", "severity", "file", "line", "runtimeConfidence")
             if key in item}
            for item in data["findings"]
        ]
        duplicates = {}
        config = tomllib.loads((snapshot / "config.toml").read_text())
        for name, entry in config.get("mcp_servers", {}).items():
            if not entry.get("enabled", True):
                continue
            identity = (entry.get("command"), tuple(entry.get("args", [])), entry.get("url"))
            duplicates.setdefault(identity, []).append(name)
        return {
            "engine": "AgentShield 1.6.0",
            "scanner_exit": run.returncode,
            "scanned_files": captured,
            "findings": findings,
            "duplicate_configured_mcp": [names for names in duplicates.values() if len(names) > 1],
            "credential_markers": [
                name for name in captured if SECRET.search((snapshot / name).read_text(errors="replace"))
            ],
        }
