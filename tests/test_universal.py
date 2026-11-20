import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from autorouter import hosts
from autorouter.universal import ASSETS, MATRIX, Workspace, classify_and_route, serve


class UniversalTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.project = self.root / "project with spaces"
        self.project.mkdir()
        self.vault = self.root / "vault"
        self.vault.mkdir()
        (self.vault / "Decision.md").write_text("Saturn is the controller.")
        (self.vault / "secrets.md").write_text("Saturn restricted")
        self.enterContext(patch.dict(os.environ, {"AUTOROUTER_HOME": str(self.root / "state")}))
        self.enterContext(patch.object(Path, "cwd", return_value=self.project))

    def test_scoped_memory_and_permission_revocation(self):
        a = Workspace(self.project, "claude-code")
        b = Workspace(self.project, "gemini-cli")
        self.assertEqual(a.dispatch("diagnostics", {})["tools"], ["diagnostics"])
        hosts.main(["workspace", "--target", a.host, "--vault", str(self.vault), "--write"])
        hosts.main(["workspace", "--target", b.host])
        find = lambda ws: ws.dispatch("knowledge_search", {"query": "Saturn"})
        self.assertEqual([note["path"] for note in find(a)], ["Decision.md"])
        self.assertEqual(find(a), find(b))
        with self.assertRaises(ValueError):
            find(Workspace(self.root, a.host))
        with self.assertRaises(ValueError):
            a.dispatch("knowledge_search", {"query": "Saturn", "vault": ".."})
        checkpoint = {"task": "test", "stage": "analysis", "summary": "Controller verified"}
        a.dispatch("checkpoint_save", checkpoint)
        self.assertEqual(b.dispatch("checkpoint_get", {"task": "test"})["summary"], checkpoint["summary"])
        with self.assertRaises(ValueError):
            b.dispatch("checkpoint_save", checkpoint)
        hosts.main(["workspace", "--target", a.host, "--revoke"])
        with self.assertRaises(ValueError):
            find(a)
        self.assertTrue(find(b))

    def test_catalog_uses_only_host_profiles(self):
        cfg = self.root / "models.json"
        cfg.write_text(json.dumps([{"id": "example/fast", "stages": ["analysis"]}]))
        hosts.main(["workspace", "--target", "opencode", "--catalog", str(cfg)])
        hosts.main(["workspace", "--target", "gemini-cli"])
        oc = Workspace(self.project, "opencode")
        gem = Workspace(self.project, "gemini-cli")
        catalog = oc.dispatch("model_catalog", {})["models"]
        self.assertEqual(classify_and_route("inspect", lambda task: "analysis", catalog)["model"], "example/fast")
        self.assertEqual(gem.dispatch("recommend_route", {"stage": "analysis"})["mode"], "host-default")

    def test_mcp_initialization_discovery_and_invalid_args_all_hosts(self):
        for host in MATRIX["hosts"]:
            with self.subTest(host=host):
                hosts.main(["workspace", "--target", host, "--vault", str(self.vault)])
                messages = [
                    {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                    {"method": "notifications/initialized"},
                    {"id": 2, "method": "tools/list"},
                    {"id": 3, "method": "tools/call", "params": {
                        "name": "knowledge_search", "arguments": {"query": "Saturn"}}},
                    {"id": 4, "method": "tools/call", "params": {"name": "shell_exec"}},
                ]
                stream = io.BytesIO(b"".join(json.dumps({"jsonrpc": "2.0", **m}).encode() + b"\n"
                                            for m in messages))
                out = io.StringIO()
                serve(Workspace(self.project, host), stream, out)
                hello, listing, call, error = map(json.loads, out.getvalue().splitlines())
                self.assertEqual(hello["result"]["protocolVersion"], "2025-06-18")
                self.assertIn("knowledge_search", {t["name"] for t in listing["result"]["tools"]})
                self.assertIn("Saturn", call["result"]["content"][0]["text"])
                self.assertTrue(error["result"]["isError"])

    def test_packaged_host_assets_and_safe_opencode_roundtrip(self):
        claude = hosts.bundle("claude-code")
        plugin = claude / "plugins/autorouter"
        meta = json.loads((plugin / ".claude-plugin/plugin.json").read_text())
        self.assertEqual(meta["name"], "autorouter")
        mcp = json.loads((plugin / ".mcp.json").read_text())["mcpServers"]
        self.assertEqual(next(iter(mcp.values()))["args"][-1], "claude-code")
        hooks = json.loads((plugin / "hooks/hooks.json").read_text())["hooks"]
        self.assertIn("SessionStart", hooks)
        self.assertTrue((plugin / "skills/project-context/SKILL.md").is_file())
        gem = hosts.bundle("gemini-cli")
        self.assertTrue((gem / "gemini-extension.json").is_file())
        with patch("autorouter.hosts.shutil.which", return_value="/bin/opencode"), patch(
            "autorouter.hosts.subprocess.check_output", return_value="1.19.6"
        ):
            hosts.main(["install", "--target", "opencode"])
            dest = self.project / ".opencode/plugins/autorouter.js"
            self.assertEqual(dest.read_bytes(), (ASSETS / "opencode.js").read_bytes())
            dest.write_text("user owned")
            with self.assertRaises(SystemExit):
                hosts.main(["uninstall", "--target", "opencode"])
            dest.write_bytes((ASSETS / "opencode.js").read_bytes())
            hosts.main(["uninstall", "--target", "opencode"])
            self.assertFalse(dest.exists())

    def test_real_stdio_process_without_codex(self):
        result = subprocess.run(
            [sys.executable, "-m", "autorouter.native", "serve", "--target", "cline"],
            input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n',
            text=True, capture_output=True, check=True, timeout=5,
        )
        self.assertEqual(json.loads(result.stdout)["result"]["serverInfo"]["name"], "autorouter")
