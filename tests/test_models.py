from __future__ import annotations

import json
import unittest

from repotrials.models import Candidate, Run, Task, Validation


class ModelTests(unittest.TestCase):
    def test_candidate_round_trip_normalises_sequences(self) -> None:
        candidate = Candidate(
            id="candidate-1",
            commit_sha="a" * 40,
            parent_sha="b" * 40,
            title="Fix race",
            source_files=["src/session.py"],  # type: ignore[arg-type]
            test_files=["tests/test_session.py"],  # type: ignore[arg-type]
            metadata={"labels": ["bug"], "number": 42},
        )

        self.assertEqual(candidate.changed_files, ("src/session.py", "tests/test_session.py"))
        payload = candidate.to_json()
        self.assertNotIn('": ', payload)
        restored = Candidate.from_json(payload)
        self.assertEqual(restored, candidate)
        self.assertIsInstance(restored.source_files, tuple)
        self.assertEqual(json.loads(payload)["test_files"], ["tests/test_session.py"])

    def test_all_domain_models_round_trip(self) -> None:
        task = Task(
            id="task-1",
            candidate_id="candidate-1",
            base_sha="b" * 40,
            fix_sha="a" * 40,
            instruction="Prevent duplicate refreshes.",
            gold_patch_oid="c" * 64,
            test_patch_oid="d" * 64,
            source_files=("src/session.py",),
            test_files=("tests/test_session.py",),
        )
        validation = Validation(
            task_id=task.id,
            status="passed",
            passed=True,
            stable=True,
            attempts=3,
            baseline_exit_codes=(0, 0, 0),
            red_exit_codes=(1, 1, 1),
            gold_exit_codes=(0, 0, 0),
            noop_exit_codes=(1, 1, 1),
            diagnostics=("oracle verified",),
        )
        run = Run(
            id="run-1",
            task_id=task.id,
            agent="codex",
            model="example-model",
            status="passed",
            passed=True,
            exit_code=0,
            duration_seconds=2.5,
            cost_usd=0.02,
            input_tokens=100,
            output_tokens=50,
        )

        for model in (task, validation, run):
            self.assertEqual(type(model).from_json(model.to_json()), model)
        self.assertEqual(task.source_patch_oid, task.gold_patch_oid)
        self.assertEqual(task.hidden_test_patch_oid, task.test_patch_oid)
        self.assertTrue(validation.successful)

    def test_unknown_or_invalid_data_is_rejected(self) -> None:
        base = {
            "id": "candidate-1",
            "commit_sha": "a",
            "parent_sha": "b",
            "title": "Fix",
        }
        with self.assertRaisesRegex(ValueError, "unknown Candidate"):
            Candidate.from_dict({**base, "typo": True})
        with self.assertRaisesRegex(TypeError, "source_files"):
            Candidate.from_dict({**base, "source_files": "src/a.py"})
        with self.assertRaises(ValueError):
            Candidate(id="", commit_sha="a", parent_sha="b", title="Fix")
        with self.assertRaises(ValueError):
            Run(id="r", task_id="t", agent="a", duration_seconds=-1)
        with self.assertRaisesRegex(ValueError, "JSON must contain an object"):
            Task.from_json("[]")


if __name__ == "__main__":
    unittest.main()
