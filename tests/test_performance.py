import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from autorouter import learning, pulse, speed
from autorouter.telemetry import fields_from
from autorouter.memory import Memory
from autorouter.state import TaskStore


class PerformanceTests(unittest.TestCase):
    def test_progress_cli_uses_local_checkpoints_and_successful_stage_history(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        store = TaskStore(root / "tasks.sqlite")
        self.addCleanup(store.db.close)
        route = {"model": "test-model", "effort": "low", "speed": "standard",
                 "decision": {"task_type": "engineering", "stage": "verification"},
                 "started_at": time.time()}
        for i in range(5):
            store.save({"id": f"done-{i}", "status": "complete",
                        "history": [dict(route, execution_ms=20_000 + i * 1000)]}, "checkpoint", {})
        store.save({"id": "failed", "status": "blocked",
                    "history": [dict(route, execution_ms=500)]}, "checkpoint", {})
        task = {"id": "active", "status": "executing", "history": [route],
                "checkpoints": [{"completed_operations": ["Edit"], "pending_operations": ["Test"]},
                                {"pending_operations": ["Test", "Package"]}]}
        store.save(task, "route", {})
        out = io.StringIO()
        with patch.dict(os.environ, {"AUTOROUTER_HOME": str(root)}), patch("sys.stdout", out), patch(
            "socket.socket", side_effect=AssertionError("Pulse must never call a provider")
        ):
            pulse.main(["--task", "active", "--json"])
        result = json.loads(out.getvalue())
        self.assertEqual(result["percentage"], 33)
        self.assertTrue(result["scope_changed"])
        self.assertEqual(result["eta_state"], "estimated")
        self.assertGreaterEqual(result["eta"]["median_seconds"], 21)
        self.assertLessEqual(len(pulse.format_snapshot(result, 20)), 20)
        self.assertIn("standard", pulse.format_snapshot(dict(result, scope_changed=False), 78))
        for status in ("blocked", "paused", "cancelled"):
            self.assertIsNone(pulse.snapshot(dict(task, status=status), [20] * 5)["eta"])
        self.assertEqual(pulse.snapshot(task, [20] * 4)["eta_state"], "calculating")
        self.assertIsNone(pulse.snapshot({"id": "unscoped"})["percentage"])

    def test_speed_uses_budget_eligibility_tool_time_and_hysteresis(self):
        forecast = {"interactive": True, "residency_allowed": True, "allow_ultrafast": True,
                    "supported": ["fast", "ultrafast"], "remaining_budget": 1,
                    "model_seconds": {"standard": 10, "fast": 7, "ultrafast": 2},
                    "premium_upper_bound": {"fast": 1, "ultrafast": 2}, "tool_seconds": 0}
        self.assertEqual(speed.recommend(forecast)["tier"], "fast")
        for overrides in ({"residency_allowed": False}, {"tool_seconds": 30},
                          {"remaining_budget": 0}, {"rate_limited": True}, {"preference": "economy"}):
            self.assertEqual(speed.recommend(dict(forecast, **overrides))["tier"], "standard")
        self.assertEqual(speed.recommend(dict(forecast, remaining_budget=2))["tier"], "ultrafast")
        self.assertEqual(speed.recommend(dict(forecast, remaining_budget=2, allow_ultrafast=False))["tier"], "fast")
        self.assertEqual(speed.recommend(dict(forecast, minimum_gain=0.4))["tier"], "standard")
        self.assertEqual(speed.recommend(dict(forecast, minimum_gain=0.4, previous="fast"))["tier"], "fast")
        self.assertEqual(speed.recommend(dict(forecast, manual="standard"))["reason"], "manual")

    def test_native_usage_reports_recorded_timing_and_routing_reuse(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        session = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        turn = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        directory = root / "sessions/2026/11/20"
        directory.mkdir(parents=True)
        rows = [
            ("turn_context", {"turn_id": turn, "model": "fixture", "effort": "low"}),
            ("response_item", {"type": "function_call", "name": "read_file"}),
            ("token_usage_record", {"turn_id": turn, "turn_token_usage": {"cached_input_tokens": 900}}),
            ("event_msg", {"type": "task_complete", "turn_id": turn, "duration_ms": 3800,
                           "time_to_first_token_ms": 920}),
        ]
        (directory / f"rollout-fixture-{session}.jsonl").write_text(
            "".join(json.dumps({"type": kind, "payload": payload}) + "\n" for kind, payload in rows)
        )
        routes = [fields_from(f"routing decision reused thread={session} turn={turn}"),
                  fields_from(f"routing applied at tool boundary turn={turn} classification_ms=1500")]
        with patch("autorouter.learning.codex_home", return_value=root), patch(
            "autorouter.learning.routing_events", return_value=routes
        ):
            metrics = learning.turn_metrics(session, turn)
        self.assertEqual(metrics["time_to_first_token_ms"], 920)
        self.assertEqual(metrics["outcome"], "completed")
        self.assertEqual(metrics["tool_calls"], 1)
        self.assertEqual(metrics["usage"]["cached_input_tokens"], 900)
        self.assertEqual(metrics["routing_performance"], {
            "applied_classifications": 1, "reused_decisions": 1,
            "classification_p50_ms": 1500, "classification_p95_ms": 1500,
        })

    def test_memory_readers_use_committed_snapshot_during_index_write(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        vault, db = root / "notes", root / "memory.db"
        writer, reader = Memory(vault, db), Memory(vault, db)
        for memory in (writer, reader):
            self.addCleanup(memory.db.close)
        first = vault / "First.md"
        first.write_text("Saturn existing")
        writer.reindex()
        writer.db.execute("UPDATE note_snapshot SET updated=0")
        writer.db.commit()

        second = vault / "Second.md"
        second.write_text("Jupiter new")
        writer.db.execute("BEGIN IMMEDIATE")
        reader.ensure_current()
        self.assertEqual(reader.search("Saturn")[0]["path"], "First.md")
        self.assertFalse(reader.search("Jupiter"))
        writer.db.rollback()
        first.rename(vault / "Renamed.md")
        writer.reindex()
        self.assertEqual(reader.search("Saturn")[0]["path"], "Renamed.md")
        self.assertEqual(reader.search("Jupiter")[0]["path"], "Second.md")
        second.write_text("Jupiter\npassword: fixture")
        self.assertFalse(reader.search("Jupiter"))
        second.unlink()
        self.assertEqual(writer.reindex()["notes"], 1)
