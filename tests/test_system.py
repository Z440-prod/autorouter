import io, json, tempfile, unittest
from unittest.mock import patch
from pathlib import Path
from autorouter.memory import Memory
from autorouter.engine import Engine, eligible, validate, Store
from autorouter.routing import ALIASES, TIERS, DECISION


FIXTURE_IDS = dict(zip(ALIASES, ("gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol", "gpt-6-astra")))


def models():
    return [
        {
            "id": v,
            "inputModalities": ["text", "image"],
            "supportedReasoningEfforts": [
                {"reasoningEffort": x}
                for x in ["low", "medium", "high", "xhigh", "max"]
            ],
        }
        for v in FIXTURE_IDS.values()
    ]


def decision(alias):
    return {
        "task_type": "software_engineering",
        "stage": "debugging" if alias != "luna" else "implementation",
        "complexity": "high" if alias != "luna" else "low",
        "scope": "multiple_files",
        "requires_terminal": True,
        "requires_file_editing": True,
        "requires_visual_analysis": False,
        "requires_reverse_engineering": False,
        "requires_external_tools": False,
        "preferred_model": alias,
        "reasoning_tier": "Deep",
        "estimated_risk": "medium",
        "reason": "test",
    }


def checkpoint(status):
    return {
        "status": status,
        "summary": "test stage",
        "next_stage": "verification",
        "completed_operations": ["one operation"],
        "pending_operations": [],
        "changed_files": [],
        "verified_findings": [],
    }


class Mock:
    calls = []
    aliases = ["sol", "luna", "sol", "astra"]
    count = 0
    fail = False

    def __init__(self, event, timeout=900):
        self.event = event

    def __enter__(self):
        return self

    def __exit__(self, *x):
        pass

    def models(self):
        return models()

    def call(self, method, p):
        return {"thread": {"id": "classifier" if p.get("ephemeral") else "executor"}}

    def turn(
        self, thread, model, effort, prompt, schema, disabled_plugins=None, images=None
    ):
        self.calls.append((thread, model, effort, json.loads(prompt)))
        if thread == "classifier":
            return json.dumps(
                decision(self.aliases[min(self.count, len(self.aliases) - 1)])
            )
        if self.fail:
            raise ConnectionError("interrupted after side effect")
        type(self).count += 1
        return json.dumps(
            checkpoint("complete" if self.count == len(self.aliases) else "continue")
        )


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mem = Memory(self.root / "vault", self.root / "memory.sqlite")
        Mock.calls = []
        Mock.count = 0
        Mock.fail = False
        Mock.aliases = ["sol", "luna", "sol", "astra"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_model_and_effort(self):
        for a in ALIASES:
            for t in TIERS:
                self.assertEqual(eligible(models(), a, t)[1], TIERS[t])
        with self.assertRaises(ValueError):
            eligible([], "luna", "Low")
        m = models()
        m[0]["supportedReasoningEfforts"] = [{"reasoningEffort": "medium"}]
        self.assertEqual(eligible(m, "luna", "Ultra")[1], "medium")

    def test_catalog_changes_and_future_models(self):
        previous = models()
        current = previous + [
            {**previous[2], "id": "gpt-6.2-sol"},
            {**previous[3], "id": "gpt-6.2-astra"},
        ]
        self.assertEqual(eligible(current, "sol", "Low")[0], "gpt-6.2-sol")
        self.assertEqual(eligible(current, "astra", "Low")[0], "gpt-6.2-astra")
        future = {**previous[2], "id": "gpt-6.3-sol", "supportedReasoningEfforts": []}
        self.assertEqual(eligible([*current, future], "sol", "Low")[0], "gpt-6.2-sol")
        future["supportedReasoningEfforts"] = previous[2]["supportedReasoningEfforts"]
        self.assertEqual(eligible([*current, future], "sol", "Low")[0], "gpt-6.3-sol")
        newer = {**future, "id": "gpt-6.10-sol"}
        self.assertEqual(eligible([*current, future, newer], "sol", "Low")[0], newer["id"])
        newer["hidden"] = True
        self.assertEqual(eligible([*current, future, newer], "sol", "Low")[0], future["id"])
        self.assertEqual(eligible(previous, "sol", "Low")[0], "gpt-6.1-sol")

    def test_visual_eligibility(self):
        m = models()
        m[0]["inputModalities"] = ["text"]
        with self.assertRaises(ValueError):
            eligible(m, "luna", "Low", True)

    def test_schema_rejects_invalid_output(self):
        d = decision("sol")
        d["preferred_model"] = "invented"
        with self.assertRaises(ValueError):
            validate(d, DECISION)
        d = decision("luna")
        d["requires_terminal"] = "true"
        with self.assertRaises(ValueError):
            validate(d, DECISION)

    def test_note_lifecycle_and_secrets(self):
        f = self.mem.root / "auth.md"
        f.write_text(
            "---\ntags: [code]\n---\n# authentication\nFix validate_token in src/auth.py. [[Design]] #security"
        )
        self.mem.reindex()
        self.assertEqual(self.mem.search("validate_token")[0]["path"], "auth.md")
        f.write_text("fixed refresh_token")
        self.mem.reindex()
        self.assertFalse(self.mem.search("validate_token"))
        f.rename(self.mem.root / "new.md")
        self.mem.reindex()
        self.assertEqual(self.mem.search("refresh_token")[0]["path"], "new.md")
        (self.mem.root / "secret.md").write_text("api_key: sensitive\nrefresh_token")
        self.mem.reindex()
        self.assertEqual(len(self.mem.search("refresh_token")), 1)
        (self.mem.root / "new.md").write_text("password: secret")
        self.mem.reindex()
        self.assertFalse(self.mem.search("refresh_token"))
        (self.mem.root / "new.md").unlink()
        self.mem.reindex()
        self.assertEqual(self.mem.status()["notes"], 0)

    def test_symlinks_not_indexed(self):
        outside = self.root / "private.md"
        outside.write_text("privatecontent")
        (self.mem.root / "link.md").symlink_to(outside)
        self.mem.reindex()
        self.assertFalse(self.mem.search("privatecontent"))

    def test_memory_project_scope(self):
        for folder in ("project-a", "project-b", "project-aa"):
            note = self.mem.root / folder / "note.md"
            note.parent.mkdir()
            note.write_text("shared project phrase")
        self.mem.reindex()
        self.assertEqual(
            [item["path"] for item in self.mem.search("phrase", folder="project-a")],
            ["project-a/note.md"],
        )
        with self.assertRaises(ValueError):
            self.mem.search("phrase", folder="../unrelated")

    def test_memory_mcp_batched_queries(self):
        from autorouter.mcp import main

        (self.mem.root / "note.md").write_text("bounded batch test")
        call = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "memory_search_batch", "arguments": {
                "queries": ["bounded", "batch", "bounded"],
            }},
        }
        stdout = io.StringIO()
        with patch("autorouter.mcp.paths", return_value=(self.root, None, {"vault": self.mem.root})), patch(
            "sys.stdin", io.StringIO(json.dumps(call) + "\n")
        ), patch("sys.stdout", stdout), patch("os.umask"):
            main()
        result = json.loads(stdout.getvalue())["result"]["content"][0]["text"]
        results = json.loads(result)
        self.assertEqual([record["query"] for record in results], ["bounded", "batch"])
        self.assertTrue(all(x["matches"][0]["path"] == "note.md" for x in results))

    def test_restart_persistence(self):
        (self.mem.root / "note.md").write_text("persistentknowledge")
        self.mem.reindex()
        again = Memory(self.mem.root, self.root / "memory.sqlite")
        self.assertTrue(again.search("persistentknowledge"))

    def test_handoffs_preserve_one_thread(self):
        e = Engine({"max_handoffs": 8}, self.root, self.mem, Mock)
        s = e.run("fix", self.root)
        self.assertEqual(
            [x["model"] for x in s["history"]], [FIXTURE_IDS[x] for x in Mock.aliases]
        )
        self.assertEqual(s["status"], "complete")
        calls = [c for c in Mock.calls if c[0] == "executor"]
        self.assertEqual(len(calls), 4)
        handoff = calls[-1][3]
        self.assertNotIn("checkpoints", handoff)
        self.assertEqual(handoff["original_request"], "fix")
        active = handoff["active_task_state"]
        self.assertEqual(active["checkpoint_history"], {"task": s["id"], "entries": 3})
        self.assertEqual(active["latest_summary"], "test stage")
        self.assertEqual(active["stage"], "verification")
        self.assertEqual(active["completed_operations"], ["one operation"])
        self.assertEqual(active["pending_operations"], [])
        self.assertEqual(len(Store(self.root / "tasks.sqlite").get(s["id"])["checkpoints"]), 4)
        self.assertEqual(
            Store(self.root / "tasks.sqlite").get(s["id"])["status"], "complete"
        )

    def test_pinned_model(self):
        Mock.aliases = ["astra"]
        e = Engine({}, self.root, self.mem, Mock)
        s = e.run("fix", self.root, pin="luna", tier="Low")
        self.assertEqual(s["history"][0]["model"], FIXTURE_IDS["luna"])

    def test_loop_limit(self):
        e = Engine({"max_handoffs": 1}, self.root, self.mem, Mock)
        with self.assertRaises(RuntimeError):
            e.run("fix", self.root)
        self.assertEqual(Mock.count, 1)

    def test_uncertain_not_replayed(self):
        Mock.fail = True
        e = Engine({}, self.root, self.mem, Mock)
        with self.assertRaises(ConnectionError):
            e.run("fix", self.root)
        s = e.store.list()[0]
        self.assertEqual(s["status"], "uncertain")
        before = len(Mock.calls)
        with self.assertRaises(ValueError):
            e.run(None, self.root, resume=s["id"])
        self.assertEqual(len(Mock.calls), before)


if __name__ == "__main__":
    unittest.main()


class ProtocolTests(unittest.TestCase):
    def test_commentary_is_not_structured_result(self):
        from autorouter.rpc import RPC

        r = RPC.__new__(RPC)
        r.timeout = 1
        r.call = lambda *x: {"turn": {"id": "turn"}}
        events = iter(
            [
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread",
                        "item": {
                            "type": "agentMessage",
                            "phase": "commentary",
                            "text": "I will inspect",
                        },
                    },
                },
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread",
                        "item": {
                            "type": "agentMessage",
                            "phase": "final_answer",
                            "text": '{"answer":"done"}',
                        },
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread",
                        "turn": {"id": "turn", "status": "completed"},
                    },
                },
            ]
        )
        r.receive = lambda *x: next(events)
        self.assertEqual(
            json.loads(r.turn("thread", "model", "low", "prompt"))["answer"], "done"
        )

    def test_completed_checkpoint_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "tasks.sqlite")
            s = {"id": "task"}
            store.save(s, "route", {})
            store.save(
                s,
                "item/completed",
                {
                    "item": {
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": json.dumps(checkpoint("continue")),
                    }
                },
            )
            store.save(s, "turn/completed", {"turn": {"status": "completed"}})
            self.assertEqual(store.recover_checkpoint("task")["status"], "continue")

    def test_embedding_privacy_controls(self):
        from autorouter.embeddings import Embeddings, cosine

        with self.assertRaises(ValueError):
            Embeddings({"provider": "openai"})
        with self.assertRaises(ValueError):
            Embeddings({"provider": "local", "base_url": "http://example.com/v1"})
        self.assertEqual(cosine([1, 0], [1, 0]), 1)
        self.assertEqual(cosine([0, 0], [1, 0]), 0)

    def test_invalid_local_embeddings_do_not_break_fts(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = Memory(
                Path(tmp) / "vault",
                Path(tmp) / "m.sqlite",
                {
                    "enabled": True,
                    "provider": "local",
                    "base_url": "http://example.com/v1",
                },
            )
            (m.root / "test.md").write_text("searchable identifier")
            m.reindex()
            self.assertTrue(m.search("identifier"))
            self.assertTrue(m.embedding_error)


class ImageProtocolTests(unittest.TestCase):
    def test_local_image_is_sent_to_backend(self):
        from autorouter.rpc import RPC

        r = RPC.__new__(RPC)
        r.timeout = 1
        sent = []

        def call(method, params):
            sent.append(params)
            return {"turn": {"id": "turn"}}

        r.call = call
        r.receive = lambda *args: {
            "method": "turn/completed",
            "params": {
                "threadId": "thread",
                "turn": {"id": "turn", "status": "completed"},
            },
        }
        r.turn(
            "thread", "gpt-6.1-terra", "low", "inspect", images=["/tmp/screenshot.png"]
        )
        self.assertEqual(
            sent[0]["input"][1], {"type": "localImage", "path": "/tmp/screenshot.png"}
        )
