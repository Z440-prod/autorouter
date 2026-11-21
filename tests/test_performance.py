import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from autorouter import pulse, speed
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
