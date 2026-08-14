from __future__ import annotations

import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from repotrials.sandbox import (
    SandboxError,
    collect_submission_patch,
    collect_submission_paths,
    initialize_synthetic_git,
    safe_extract_tar,
)
from repotrials.validation import check_patch_integrity


class SandboxTests(unittest.TestCase):
    def test_rejects_archive_traversal(self) -> None:
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            info = tarfile.TarInfo("../escape.txt")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        with tempfile.TemporaryDirectory() as raw, self.assertRaises(SandboxError):
            safe_extract_tar(stream.getvalue(), raw)

    def test_synthetic_repo_contains_only_baseline_and_collects_untracked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            initialize_synthetic_git(root)
            (root / "app.py").write_text("value = 2\n", encoding="utf-8")
            (root / "new.py").write_text("created = True\n", encoding="utf-8")
            patch = collect_submission_patch(root).decode("utf-8")
            self.assertIn("value = 2", patch)
            self.assertIn("new.py", patch)
            count = subprocess.check_output(("git", "rev-list", "--count", "--all"), cwd=root)
            self.assertEqual(count.strip(), b"1")

    def test_submission_patch_uses_literal_utf8_git_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "caf303251.py").write_text("allowed = True\n", encoding="utf-8")
            initialize_synthetic_git(root)
            (root / "café.py").write_text("outside = True\n", encoding="utf-8")

            patch = collect_submission_patch(root)
            changed_paths = collect_submission_paths(root)
            outside = check_patch_integrity(
                patch,
                (),
                exact_allowed_paths=("caf303251.py",),
                observed_paths=changed_paths,
            )
            legitimate = check_patch_integrity(
                patch,
                (),
                exact_allowed_paths=("café.py",),
                observed_paths=changed_paths,
            )

        self.assertIn("café.py", patch.decode("utf-8"))
        self.assertFalse(outside.ok)
        self.assertEqual(outside.violations[0].path, "café.py")
        self.assertTrue(legitimate.ok)

    @unittest.skipIf(os.name == "nt", "Windows forbids tab characters in file names")
    def test_nul_delimited_paths_prevent_c_quoted_allowlist_confusion(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "footbar.py").write_text("allowed = True\n", encoding="utf-8")
            initialize_synthetic_git(root)
            (root / "foo\tbar.py").write_text("outside = True\n", encoding="utf-8")

            changed_paths = collect_submission_paths(root)
            patch = collect_submission_patch(root)
            report = check_patch_integrity(
                patch,
                (),
                exact_allowed_paths=("footbar.py",),
                observed_paths=changed_paths,
            )

        self.assertEqual(changed_paths, ("foo\tbar.py",))
        self.assertFalse(report.ok)
        self.assertEqual(report.violations[0].path, "foo\tbar.py")


if __name__ == "__main__":
    unittest.main()
