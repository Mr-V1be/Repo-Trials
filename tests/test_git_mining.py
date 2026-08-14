from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from repotrials.git import GitError, GitRepository
from repotrials.mining import (
    FileKind,
    Miner,
    MiningConfig,
    classify_path,
    classify_paths,
    classify_paths_with_globs,
)


@unittest.skipUnless(shutil.which("git"), "Git is required")
class GitMiningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self._git("init", "-b", "main")
        self._git("config", "user.name", "RepoTrials Test")
        self._git("config", "user.email", "repotrials@example.invalid")
        self._git("config", "core.autocrlf", "false")

        self._write("src/calculator.py", "def add(a, b):\n    return a + b\n")
        self._git("add", ".")
        self._git("commit", "-m", "Initial source")

        self._write(
            "src/calculator.py",
            "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n",
        )
        self._write(
            "tests/test_calculator.py",
            "from src.calculator import sub\n\ndef test_sub():\n    assert sub(3, 1) == 2\n",
        )
        self._git("add", ".")
        self._git("commit", "-m", "Fix subtraction regression", "-m", "Issue #12")
        self.normal_fix = self._git("rev-parse", "HEAD").strip()

        self._write("docs/notes.md", "A documentation-only commit.\n")
        self._git("add", ".")
        self._git("commit", "-m", "Update docs")

        self._git("checkout", "-b", "fix/multiply")
        self._write(
            "src/calculator.py",
            "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n\ndef mul(a, b):\n    return a * b\n",
        )
        self._write(
            "tests/test_calculator.py",
            "from src.calculator import mul, sub\n\ndef test_sub():\n    assert sub(3, 1) == 2\n\ndef test_mul():\n    assert mul(3, 2) == 6\n",
        )
        self._git("add", ".")
        self._git("commit", "-m", "Fix multiplication")
        self._git("checkout", "main")
        self._write("CHANGELOG.md", "Unreleased\n")
        self._git("add", ".")
        self._git("commit", "-m", "Prepare release")
        self.merge_first_parent = self._git("rev-parse", "HEAD").strip()
        self._git("merge", "--no-ff", "fix/multiply", "-m", "Merge multiplication fix")
        self.merge_sha = self._git("rev-parse", "HEAD").strip()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout

    def _write(self, relative: str, content: str) -> None:
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8", newline="\n")

    def test_repository_reads_commits_diff_stats_and_archive(self) -> None:
        repository = GitRepository(self.root / "src")
        self.assertEqual(repository.path, self.root.resolve())
        merge = repository.commit("HEAD")
        self.assertTrue(merge.is_merge)
        self.assertEqual(merge.sha, self.merge_sha)
        self.assertEqual(merge.parents[0], self.merge_first_parent)

        changes = repository.changed_files(merge.sha, merge.parents[0])
        paths = {change.path for change in changes}
        self.assertEqual(paths, {"src/calculator.py", "tests/test_calculator.py"})
        self.assertGreater(sum(change.additions for change in changes), 0)
        self.assertIn(b"def mul", repository.diff(merge.parents[0], merge.sha))
        archive = repository.archive(merge.parents[0])
        self.assertGreater(len(archive), 100)
        self.assertNotIn(b".git/", archive)

        mainline = list(repository.iter_commits(first_parent=True))
        self.assertEqual(mainline[0].sha, self.merge_sha)
        self.assertNotIn(
            self._git("rev-parse", "fix/multiply").strip(),
            {commit.sha for commit in mainline},
        )
        with self.assertRaises(GitError):
            repository.rev_parse("does-not-exist")

    def test_miner_handles_single_parent_and_merge_first_parent(self) -> None:
        candidates = Miner(self.root).mine()
        self.assertEqual(len(candidates), 2)
        by_sha = {candidate.commit_sha: candidate for candidate in candidates}

        single = by_sha[self.normal_fix]
        self.assertFalse(single.is_merge)
        self.assertEqual(single.source_files, ("src/calculator.py",))
        self.assertEqual(single.test_files, ("tests/test_calculator.py",))
        self.assertGreater(single.additions, 0)

        merge = by_sha[self.merge_sha]
        self.assertTrue(merge.is_merge)
        self.assertEqual(merge.parent_sha, self.merge_first_parent)
        self.assertEqual(merge.metadata["parents"][0], self.merge_first_parent)
        self.assertEqual(Miner(self.root).mine(limit=1), [merge])

        no_merges = Miner(self.root, MiningConfig(include_merges=False)).mine()
        self.assertEqual([item.commit_sha for item in no_merges], [self.normal_fix])

    def test_path_classification_is_conservative(self) -> None:
        self.assertEqual(classify_path("src/core.py"), FileKind.SOURCE)
        self.assertEqual(classify_path("tests/fixtures/input.json"), FileKind.TEST)
        self.assertEqual(classify_path("web/button.spec.tsx"), FileKind.TEST)
        self.assertEqual(classify_path("docs/example.py"), FileKind.IGNORED)
        self.assertEqual(classify_path("package-lock.json"), FileKind.IGNORED)
        classified = classify_paths(["src/a.py", "src/a.py", "tests/test_a.py", "README.md"])
        self.assertEqual(classified.source, ("src/a.py",))
        self.assertEqual(classified.test, ("tests/test_a.py",))
        self.assertEqual(classified.ignored, ("README.md",))

    def test_repository_globs_override_builtin_language_classification(self) -> None:
        classified = classify_paths_with_globs(
            ["lib/widget.custom", "checks/widget.case", "notes/widget.custom"],
            source_globs=("lib/**",),
            test_globs=("checks/**",),
            ignored_globs=("notes/**",),
        )
        self.assertEqual(classified.source, ("lib/widget.custom",))
        self.assertEqual(classified.test, ("checks/widget.case",))
        self.assertEqual(classified.ignored, ("notes/widget.custom",))

    def test_repository_globs_preserve_dot_directories_and_segment_boundaries(self) -> None:
        classified = classify_paths_with_globs(
            [".hidden/pkg/core.py", "nested/.hidden/core.py", "src/pkg/core.py"],
            source_globs=(".hidden/**/*.py", "src/**/*.py"),
            test_globs=(),
            ignored_globs=(),
        )
        self.assertEqual(
            classified.source,
            (".hidden/pkg/core.py", "src/pkg/core.py"),
        )
        self.assertEqual(classified.ignored, ("nested/.hidden/core.py",))

    def test_miner_conservatively_rejects_rename_and_copy_records(self) -> None:
        self._git("mv", "src/calculator.py", "src/arithmetic.py")
        self._git("mv", "tests/test_calculator.py", "tests/test_arithmetic.py")
        self._git("commit", "-m", "Fix calculator naming")
        renamed_sha = self._git("rev-parse", "HEAD").strip()

        repository = GitRepository(self.root)
        commit = repository.commit(renamed_sha)
        changes = repository.changed_files(commit.sha, commit.parents[0])
        self.assertTrue(any(change.status.startswith("R") for change in changes))
        self.assertNotIn(renamed_sha, {item.commit_sha for item in Miner(self.root).mine()})


if __name__ == "__main__":
    unittest.main()
