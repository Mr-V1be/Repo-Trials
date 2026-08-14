from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from repotrials.reporting import REPORT_SCHEMA, write_report_bundle
from repotrials.scoring import (
    aggregate_scores,
    bootstrap_confidence_interval,
    score_trial,
    score_trial_counts,
)


class ScoringTests(unittest.TestCase):
    def test_binary_resolution_requires_all_tests_and_integrity(self) -> None:
        resolved = score_trial(
            "green",
            {"hidden::one": "passed"},
            {"existing::one": True},
        )
        regression = score_trial(
            "regression",
            {"hidden::one": True},
            {"existing::one": False},
        )
        integrity = score_trial_counts(
            "tampered",
            f2p_passed=1,
            f2p_total=1,
            p2p_passed=2,
            p2p_total=2,
            integrity_passed=False,
        )

        self.assertTrue(resolved.resolved)
        self.assertFalse(regression.resolved)
        self.assertEqual(regression.failure_kind, "regression")
        self.assertFalse(integrity.resolved)
        self.assertEqual(integrity.failure_kind, "integrity")

    def test_empty_test_sets_are_invalid(self) -> None:
        with self.assertRaises(ValueError):
            score_trial("empty", [], [True])
        with self.assertRaises(ValueError):
            score_trial_counts(
                "empty",
                f2p_passed=0,
                f2p_total=0,
                p2p_passed=1,
                p2p_total=1,
            )

    def test_expected_failure_is_not_scored_as_a_pass(self) -> None:
        score = score_trial("xfail", {"hidden::one": "xfailed"}, [True])

        self.assertFalse(score.resolved)
        self.assertEqual(score.failure_kind, "fail_to_pass")

    def test_bootstrap_is_deterministic_and_order_independent(self) -> None:
        values = [True, False, True, True, False]
        first = bootstrap_confidence_interval(values, samples=2_000, seed=42)
        second = bootstrap_confidence_interval(reversed(values), samples=2_000, seed=42)

        self.assertEqual(first, second)
        self.assertLessEqual(first.low, 0.6)
        self.assertGreaterEqual(first.high, 0.6)

    def test_attempts_are_aggregated_as_empirical_pass_at_k_per_task(self) -> None:
        attempts = (
            score_trial("task-a", [False], [True], metadata={"attempt": 1}),
            score_trial("task-a", [True], [True], metadata={"attempt": 2}),
            score_trial("task-b", [False], [True], metadata={"attempt": 1}),
            score_trial("task-b", [False], [True], metadata={"attempt": 2}),
        )

        summary = aggregate_scores(attempts, bootstrap_samples=500, seed=11)

        self.assertEqual(summary.total_tasks, 2)
        self.assertEqual(summary.trial_count, 4)
        self.assertEqual(summary.resolved_tasks, 1)
        self.assertEqual(summary.resolve_rate, 0.5)
        self.assertEqual(summary.k, 2)
        self.assertEqual(summary.aggregation_method, "pass@k")
        self.assertEqual(summary.task_resolved_rule, "any_attempt_resolved")
        self.assertEqual(summary.confidence_interval.unit, "task")
        self.assertEqual(
            [(item.task_id, item.resolved, item.attempts) for item in summary.tasks],
            [("task-a", True, 2), ("task-b", False, 2)],
        )

    def test_pass_at_k_rejects_incomplete_attempt_shapes(self) -> None:
        attempts = (
            score_trial("task-a", [True], [True]),
            score_trial("task-a", [False], [True]),
            score_trial("task-b", [True], [True]),
        )

        with self.assertRaisesRegex(ValueError, "same number of attempts"):
            aggregate_scores(attempts)

    def test_aggregate_and_self_contained_reports(self) -> None:
        trials = (
            score_trial("a", [True], [True], metadata={"duration_seconds": 1.2}),
            score_trial("b", [False], [True]),
            score_trial("c", [True], [True]),
        )
        summary = aggregate_scores(trials, bootstrap_samples=1_000, seed=7)

        self.assertEqual(summary.total_tasks, 3)
        self.assertEqual(summary.resolved_tasks, 2)
        self.assertAlmostEqual(summary.resolve_rate, 2 / 3)

        with tempfile.TemporaryDirectory() as temp:
            bundle = write_report_bundle(
                temp,
                summary,
                metadata={"unsafe": "</script><script>alert(1)</script>"},
                title="RepoTrials <private>",
            )
            payload = json.loads(bundle.json_path.read_text(encoding="utf-8"))
            page = bundle.html_path.read_text(encoding="utf-8")

            self.assertEqual(payload["schema_version"], REPORT_SCHEMA)
            self.assertEqual(len(payload["trials"]), 3)
            self.assertEqual(payload["summary"]["aggregation"]["method"], "pass@k")
            self.assertEqual(payload["summary"]["aggregation"]["k"], 1)
            self.assertEqual(payload["summary"]["confidence_interval"]["unit"], "task")
            self.assertIn("RepoTrials &lt;private&gt;", page)
            self.assertNotIn("</script><script>alert(1)</script>", page)
            self.assertIn('type="application/json"', page)
            self.assertNotIn("http://", page)
            self.assertNotIn("https://", page)
            self.assertEqual(Path(bundle.json_path).parent, Path(temp))
