#!/usr/bin/env python3
"""Reinstall reviewed, exact integration versions. No hook-trust bypass."""

import datetime, json, os, shutil, subprocess, urllib.request, hashlib, tarfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "src/autorouter/assets/integrations.lock.json").read_text())


def run(*args):
    subprocess.run(args, check=True)


def install_autorouter_integrations(codex):
    sys.path.insert(0, str(ROOT / "src"))
    from autorouter.integrations import ECC_HOOK
    from autorouter.rpc import RPC

    def configure(plugin, settings):
        with RPC(timeout=30) as rpc:
            rpc.call("config/batchWrite", {
                "edits": [
                    {"keyPath": "plugins", "value": {plugin + "@" + plugin: settings},
                     "mergeStrategy": "upsert"},
                    {"keyPath": "hooks.state", "value": {ECC_HOOK: {"enabled": False}},
                     "mergeStrategy": "upsert"},
                ],
            })

    for name, info in LOCK["autorouter_plugins"].items():
        settings = {"enabled": False}
        if name == "ecc":
            settings["mcp_servers"] = {"chrome-devtools": {"enabled": False}}
        configure(name, settings)
        market = codex / ".tmp/marketplaces" / name
        if not market.is_dir():
            run("codex", "plugin", "marketplace", "add", info["repo"], "--ref", info["commit"])
        actual = subprocess.check_output(["git", "-C", str(market), "rev-parse", "HEAD"], text=True).strip()
        if actual != info["commit"]:
            raise RuntimeError(f"{name} marketplace differs from the reviewed commit")
        cache = codex / "plugins/cache" / name / name / info["version"]
        if not (cache / ".codex-plugin/plugin.json").is_file():
            try:
                run("codex", "plugin", "add", name + "@" + name)
            finally:
                configure(name, settings)

    graphify = LOCK["graphify"]
    if not shutil.which("graphify"):
        run("pipx", "install", graphify["package"] + "==" + graphify["version"])
    scanner = Path.home() / ".local/lib/autorouter/agentshield"
    shield = LOCK["agentshield"]
    if not (scanner / "node_modules/.bin/agentshield").is_file():
        scanner.mkdir(parents=True, exist_ok=True)
        run("npm", "install", "--prefix", str(scanner), "--ignore-scripts", "--save-exact",
            shield["package"] + "@" + shield["version"])


def main():
    codex = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    cfg = codex / "config.toml"
    if cfg.exists():
        shutil.copy2(
            cfg,
            cfg.with_name(
                "config.toml.backup-"
                + datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
            ),
        )
    # Node binary is downloaded from nodejs.org, hash checked, no remote script execution.
    node = LOCK["node"]
    base = Path.home() / ".local/opt"
    base.mkdir(parents=True, exist_ok=True)
    dest = base / node["directory"]
    if not dest.exists():
        archive = base / node["file"]
        urllib.request.urlretrieve(node["url"], archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != node["sha256"]:
            raise RuntimeError("Node checksum mismatch")
        with tarfile.open(archive) as t:
            t.extractall(base, filter="data")
    for name in ["node", "npm", "npx"]:
        target = Path.home() / ".local/bin" / name
        if not target.exists():
            target.symlink_to(dest / "bin" / name)
    os.environ["PATH"] = (
        str(Path.home() / ".local/bin") + os.pathsep + os.environ["PATH"]
    )
    installed = json.loads(
        subprocess.check_output(["codex", "plugin", "list", "--json"])
    )
    ids = {x["pluginId"] for x in installed.get("installed", [])}
    for item in LOCK["plugins"]:
        if item["id"] in ids:
            print("Preserving installed", item["id"])
            continue
        run(
            "codex",
            "plugin",
            "marketplace",
            "add",
            item["repo"],
            "--ref",
            item["commit"],
        )
        run("codex", "plugin", "add", item["id"])
        if item["repo"] == "DietrichGebert/ponytail":
            # The upstream marketplace references main inside its plugin source.
            # Verify a concrete tracked file against the locked commit, rather than claiming it is pinned.
            expected = urllib.request.urlopen(
                "https://raw.githubusercontent.com/"
                + item["repo"]
                + "/"
                + item["commit"]
                + "/hooks/ponytail-instructions.js"
            ).read()
            candidates = list(
                (codex / "plugins/cache/ponytail/ponytail").glob(
                    "*/hooks/ponytail-instructions.js"
                )
            )
            if not any(x.read_bytes() == expected for x in candidates):
                raise RuntimeError(
                    "Ponytail artifact differs from lock; review upstream before updating the lock"
                )
    skill = codex / "skills/cli-anything"
    if not skill.exists():
        source = ROOT / "vendor/CLI-Anything"
        if not source.exists():
            run(
                "git", "clone", "https://github.com/HKUDS/CLI-Anything.git", str(source)
            )
            run(
                "git",
                "-C",
                str(source),
                "checkout",
                "--detach",
                LOCK["cli_anything_commit"],
            )
        # This pinned installer was reviewed; refuses to overwrite an existing skill.
        run("bash", str(source / "codex-skill/scripts/install.sh"))
    if not shutil.which("cli-hub"):
        run("pipx", "install", "cli-anything-hub==" + LOCK["cli_hub"])
    install_autorouter_integrations(codex)
    print(
        "Review Ponytail hooks in Codex /hooks. Restart Codex to discover integrations."
    )


if __name__ == "__main__":
    main()
