from __future__ import annotations

import unittest

from repotrials.prompts import added_lines_from_patch, build_prompt


class PromptTests(unittest.TestCase):
    def test_sanitizes_solution_artifacts(self) -> None:
        assessment = build_prompt(
            pr_title="Session refresh fails intermittently",
            pr_body=(
                "See https://github.com/acme/app/pull/42.\n"
                "Fixed by changing session.py to cache the result.\n"
                "Observed when two requests arrive concurrently."
            ),
        )
        self.assertNotIn("pull/42", assessment.text)
        self.assertNotIn("Fixed by", assessment.text)
        self.assertIn("solution_url_removed", assessment.findings)

    def test_prefers_issue_and_flags_gold_overlap(self) -> None:
        assessment = build_prompt(
            issue_title="Cache problem",
            issue_body="return cached_session_when_expiry_is_equal_to_now()",
            pr_title="ignored",
            added_code_lines=["return cached_session_when_expiry_is_equal_to_now()"],
        )
        self.assertEqual(assessment.source, "issue")
        self.assertEqual(assessment.risk, "high")

    def test_extracts_only_added_patch_lines(self) -> None:
        patch = "--- a/a.py\n+++ b/a.py\n-old\n+new\n context\n"
        self.assertEqual(added_lines_from_patch(patch), ("new",))


if __name__ == "__main__":
    unittest.main()
