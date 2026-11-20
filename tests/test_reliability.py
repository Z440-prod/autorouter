import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autorouter import evaluation, updater


class ReliabilityTests(unittest.TestCase):
    def test_evaluation_promotes_and_rolls_back_verified_defaults(self):
        with tempfile.TemporaryDirectory() as root:
            state_path = Path(root) / "models.json"
            state = evaluation.read(state_path)
            members = {
                "gpt-6.2-sol": ("sol", (6, 2), "low"),
                "gpt-6.3-sol": ("sol", (6, 3), "medium"),
            }
            evaluation.record(state, "gpt-6.2-sol", members, {"passed": True}, 1)
            evaluation.record(state, "gpt-6.3-sol", members, {"passed": True}, 2)
            self.assertEqual(state["families"]["sol"]["current"], "gpt-6.3-sol")
            evaluation.write(state_path, state)
            state = evaluation.read(state_path)
            self.assertEqual(state["checks"]["gpt-6.3-sol"]["effort"], "medium")
            evaluation.record(state, "gpt-6.3-sol", members, {"passed": False}, 3)
            self.assertEqual(state["families"]["sol"]["current"], "gpt-6.2-sol")

    def test_evaluation_tracks_unfamiliar_ids_and_recovers_an_older_verified_model(self):
        models = [{"model": model, "inputModalities": ["text"],
                   "supportedReasoningEfforts": [{"reasoningEffort": "low"}]}
                  for model in ("gpt-6-sol", "gpt-6.1-sol", "gpt-6.2-sol", "new-family-model")]
        members = evaluation.available(models)
        state = {"version": 1, "families": {}, "checks": {}}
        for model in members:
            evaluation.record(state, model, members, {"passed": True}, 1)
        self.assertTrue(state["checks"]["new-family-model"]["passed"])
        self.assertEqual(list(state["families"]), ["sol"])
        members.pop("gpt-6.1-sol")
        evaluation.record(state, "gpt-6.2-sol", members, {"passed": False}, 2)
        self.assertEqual(state["families"]["sol"]["current"], "gpt-6-sol")

    def test_activation_checks_gates_and_hash_and_restores_previous_package(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            active = root / "active"
            active.mkdir()
            original = active / "original"
            original.write_text("previous")
            current = active / "codex"
            current.symlink_to(original)
            staged = root / "stage"
            (staged / "bin").mkdir(parents=True)
            candidate = staged / "bin/codex"
            candidate.write_text("candidate")
            sidecars = {}
            for name in ("codex-code-mode-host", "codex-exec-server"):
                path = candidate.parent / name
                path.write_text(name)
                sidecars[name] = updater.digest(path)
            record = staged / "autorouter-verified.json"
            record.write_text(json.dumps({"gates": [], "sha256": updater.digest(candidate)}))
            with patch.object(updater, "busy", return_value=False):
                with self.assertRaises(RuntimeError):
                    updater.activate(active, candidate)
                record.write_text(json.dumps({
                    "gates": updater.GATES, "sha256": updater.digest(candidate), "sidecars": sidecars,
                }))
                self.assertEqual(Path(updater.activate(active, candidate)), candidate)
                self.assertEqual(current.resolve(), candidate)
                self.assertEqual(Path(updater.activate(active, candidate)), candidate)
                self.assertEqual(Path(updater.activate(active, None, rollback=True)), original)
                self.assertEqual(current.resolve(), original)
            with patch.object(updater, "busy", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "session is active"):
                    updater.activate(active, candidate)


if __name__ == "__main__":
    unittest.main()
