from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from repotrials.cli import main, nonnegative_float, percentage_points, positive_int


def git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)


class CliTests(unittest.TestCase):
    def test_version(self) -> None:
        stdout = io.StringIO()
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(stdout):
            main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("RepoTrials 0.1.0", stdout.getvalue())

    def test_init_and_doctor_json(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            git(root, "init", "--quiet", "--initial-branch=main")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["--json", "init", str(root)]), 0)
            self.assertTrue((root / "repotrials.toml").is_file())
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                # Harbor is a declared production backend, so a missing binary
                # is intentionally a failed preflight.
                result = main(["--root", str(root), "--json", "doctor"])
            self.assertIn(result, (0, 1))
            self.assertIn('"name": "git"', stdout.getvalue())

    def test_init_from_subdirectory_uses_git_root_and_excludes_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "target"
            nested = root / "src" / "package"
            nested.mkdir(parents=True)
            git(root, "init", "--quiet", "--initial-branch=main")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["--json", "init", str(nested)]), 0)
            payload = json.loads(stdout.getvalue())

            self.assertEqual(Path(payload["config"]), root / "repotrials.toml")
            self.assertTrue((root / ".repotrials/private").is_dir())
            self.assertFalse((nested / "repotrials.toml").exists())
            self.assertFalse((nested / ".repotrials").exists())

            secret = root / ".repotrials" / "private" / "oracle.json"
            secret.write_text("private\n", encoding="utf-8")
            ignored = subprocess.run(
                ("git", "check-ignore", "--quiet", "--", str(secret)),
                cwd=nested,
                check=False,
            )
            self.assertEqual(ignored.returncode, 0)
            self.assertEqual(main(["--root", str(nested), "--json", "doctor"]), 0)

    def test_init_refuses_missing_path_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            requested = Path(raw) / "missing"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["--json", "init", str(requested)]), 1)
            self.assertFalse(requested.exists())
            self.assertIn("Git working tree", stderr.getvalue())

    def test_percentage_points(self) -> None:
        self.assertEqual(percentage_points("5pp"), 5.0)
        self.assertEqual(percentage_points("2.5"), 2.5)
        self.assertEqual(positive_int("1"), 1)
        self.assertEqual(nonnegative_float("0"), 0)

    def test_complete_json_cli_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "target"
            root.mkdir()
            git(root, "init", "--initial-branch=main")
            git(root, "config", "user.name", "CLI Fixture")
            git(root, "config", "user.email", "cli@example.invalid")
            git(root, "config", "core.autocrlf", "false")
            (root / "tests").mkdir()
            (root / "calc.py").write_text(
                "def divide(left, right):\n    return left / right\n", encoding="utf-8"
            )
            (root / "tests/test_calc.py").write_text(
                "from calc import divide\n\ndef test_division():\n    assert divide(6, 3) == 2\n",
                encoding="utf-8",
            )
            git(root, "add", ".")
            git(root, "commit", "-m", "Initial calculator")
            (root / "calc.py").write_text(
                "def divide(left, right):\n"
                "    if right == 0:\n"
                "        return None\n"
                "    return left / right\n",
                encoding="utf-8",
            )
            with (root / "tests/test_calc.py").open("a", encoding="utf-8") as handle:
                handle.write("\ndef test_zero():\n    assert divide(1, 0) is None\n")
            git(root, "add", ".")
            git(root, "commit", "-m", "Fix division by zero with regression test")

            def invoke(*arguments: str) -> tuple[int, str, str]:
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = main(("--root", str(root), "--json", *arguments))
                return code, stdout.getvalue(), stderr.getvalue()

            code, output, _ = invoke("init")
            self.assertEqual(code, 0)
            self.assertIn("repotrials.toml", output)
            config = root / "repotrials.toml"
            config.write_text(
                config.read_text(encoding="utf-8")
                .replace("repeats = 3", "repeats = 1")
                .replace("attempts = 3", "attempts = 1"),
                encoding="utf-8",
            )
            self.assertEqual(invoke("doctor")[0], 0)
            self.assertEqual(json.loads(invoke("mine", "--limit", "10")[1])["stored"], 1)
            candidates = json.loads(invoke("candidates", "--limit", "10")[1])
            self.assertEqual(len(candidates), 1)
            validated = json.loads(
                invoke("validate", "--repeats", "1", "--accept", "--unsafe-local")[1]
            )
            self.assertTrue(validated[0]["valid"])
            task_id = validated[0]["task_id"]
            self.assertEqual(len(json.loads(invoke("review")[1])), 1)
            self.assertEqual(json.loads(invoke("review", "--verify", task_id)[1])["updated"], 1)

            noop = Path(raw) / "noop.py"
            noop.write_text("# no changes\n", encoding="utf-8")
            fixer = Path(raw) / "fixer.py"
            fixer.write_text(
                "from pathlib import Path\n"
                "Path('calc.py').write_text(\"def divide(left, right):\\n"
                "    if right == 0:\\n        return None\\n"
                "    return left / right\\n\", encoding='utf-8')\n",
                encoding="utf-8",
            )
            noop_command = f'"{Path(sys.executable).as_posix()}" "{noop.as_posix()}"'
            fix_command = f'"{Path(sys.executable).as_posix()}" "{fixer.as_posix()}"'

            code, _, error = invoke("run", "--agent-command", noop_command, "--name", "noop")
            self.assertEqual(code, 1)
            self.assertIn("unsafe-local", error)
            noop_run = json.loads(
                invoke(
                    "run",
                    "--agent-command",
                    noop_command,
                    "--name",
                    "noop",
                    "--unsafe-local",
                )[1]
            )
            fix_run = json.loads(
                invoke(
                    "run",
                    "--agent-command",
                    fix_command,
                    "--name",
                    "fix",
                    "--unsafe-local",
                )[1]
            )
            self.assertEqual(noop_run["resolved"], 0)
            self.assertEqual(fix_run["resolved"], 1)

            comparison = json.loads(
                invoke("compare", noop_run["run_group"], fix_run["run_group"])[1]
            )
            self.assertEqual(comparison["delta_pp"], 100.0)
            report_dir = root / ".repotrials/reports/cli"
            report = json.loads(
                invoke(
                    "report",
                    fix_run["run_group"],
                    "--output",
                    str(report_dir),
                )[1]
            )
            self.assertTrue(Path(report["html"]).is_file())
            exported = json.loads(
                invoke(
                    "export-harbor",
                    "--output",
                    str(root / ".repotrials/exports/cli"),
                )[1]
            )
            self.assertEqual(exported["count"], 1)
            self.assertEqual(json.loads(invoke("vault", "verify")[1])["verified"] > 0, True)

            self.assertEqual(json.loads(invoke("review", "--reject", task_id)[1])["updated"], 1)
            self.assertEqual(invoke("export-harbor")[0], 1)


if __name__ == "__main__":
    unittest.main()
