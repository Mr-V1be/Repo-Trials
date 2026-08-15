from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repotrials import config
from repotrials.cli import build_parser
from repotrials.demo import (
    UVX_COMMAND,
    DemoError,
    DemoResult,
    create_history,
    ensure_pytest,
    fixture_test_interpreter,
    main,
    open_command,
    prepare_output,
    pytest_importable,
    render_banner,
    run,
    run_demo,
)

MISSING_INTERPRETER = "/nonexistent/repotrials-demo-python"


def _stub_interpreter(directory: Path, script: str) -> Path:
    """Write a fake `python` into ``directory`` so PATH lookups resolve to it."""

    stub = directory / ("python.exe" if sys.platform == "win32" else "python")
    stub.write_text(script, encoding="utf-8")
    stub.chmod(0o755)
    return stub


def sample_result(output: Path) -> DemoResult:
    reports = output / "demo-repository" / ".repotrials" / "reports" / "demo"
    return DemoResult(
        output=output,
        repository=output / "demo-repository",
        report_html=reports / "report.html",
        report_json=reports / "report.json",
        harbor_export=output / "demo-repository" / ".repotrials" / "exports" / "harbor",
        baseline_name="noop-agent",
        baseline_run_group="noop-agent-20260815-081633-d9b0ac",
        baseline_resolved=0,
        baseline_trials=1,
        candidate_name="fix-agent",
        candidate_run_group="fix-agent-20260815-081634-233fdf",
        candidate_resolved=1,
        candidate_trials=1,
        delta_pp=100.0,
    )


class FixtureHistoryTests(unittest.TestCase):
    """The fixture repository is the one part of the demo that is cheap to test."""

    def test_create_history_writes_two_commits(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "demo-repository"
            with contextlib.redirect_stderr(io.StringIO()):
                create_history(root, stream=False)

            self.assertTrue((root / "cart.py").is_file())
            self.assertTrue((root / "tests" / "test_cart.py").is_file())

            log = subprocess.run(
                ("git", "log", "--format=%s"),
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split("\n")
            self.assertEqual(
                [line for line in log if line],
                ["Fix floating point cart total with regression test", "Add cart totals"],
            )

            source = (root / "cart.py").read_text(encoding="utf-8")
            tests = (root / "tests" / "test_cart.py").read_text(encoding="utf-8")
            self.assertIn("round(sum(prices), 2)", source)
            self.assertIn("test_total_rounds_currency", tests)

            changed = subprocess.run(
                ("git", "show", "--name-only", "--format=", "HEAD"),
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split()
            self.assertEqual(sorted(changed), ["cart.py", "tests/test_cart.py"])

    def test_failing_step_keeps_the_child_exit_code(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(DemoError) as raised:
            run(sys.executable, "-c", "raise SystemExit(7)", stream=False)
        self.assertEqual(raised.exception.exit_code, 7)
        self.assertIn("demo step failed with status 7", str(raised.exception))

    def test_captured_step_returns_child_stdout(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            captured = run(sys.executable, "-c", "print('demo')", capture=True, stream=False)
        self.assertEqual(captured.strip(), "demo")


class PytestGuardTests(unittest.TestCase):
    def test_missing_pytest_names_the_uvx_fallback(self) -> None:
        with self.assertRaises(DemoError) as raised:
            ensure_pytest(MISSING_INTERPRETER)
        message = str(raised.exception)
        self.assertIn(MISSING_INTERPRETER, message)
        self.assertIn("pip install pytest", message)
        self.assertIn(UVX_COMMAND, message)
        self.assertIn("git+https://github.com/PozziTiv4ik/Repo-Trials", message)
        self.assertEqual(raised.exception.exit_code, 1)

    def test_current_interpreter_passes_the_guard(self) -> None:
        # The unit suite itself runs under pytest, so this must never raise.
        self.assertEqual(ensure_pytest(sys.executable), sys.executable)
        self.assertTrue(pytest_importable(sys.executable))

    def test_unusable_interpreter_is_not_importable(self) -> None:
        self.assertFalse(pytest_importable(MISSING_INTERPRETER))

    def test_guard_tracks_the_configured_fixture_test_command(self) -> None:
        # The guard resolves `python` because that is what the generated
        # fixture config invokes; changing one without the other is a bug.
        self.assertEqual(config.TestSettings().command.split()[0], "python")

    def test_fixture_interpreter_prefers_path_over_sys_executable(self) -> None:
        # Commands run without a shell, so PATH decides -- not sys.executable.
        with tempfile.TemporaryDirectory() as raw:
            stub = _stub_interpreter(Path(raw), "")
            with mock.patch.dict(os.environ, {"PATH": raw}):
                self.assertEqual(Path(fixture_test_interpreter()), stub)

    def test_guard_fires_when_the_path_interpreter_lacks_pytest(self) -> None:
        # The unactivated-venv case: sys.executable imports pytest, PATH's does not.
        with tempfile.TemporaryDirectory() as raw:
            stub = _stub_interpreter(Path(raw), "#!/bin/sh\nexit 1\n")
            with (
                mock.patch.dict(os.environ, {"PATH": raw}),
                self.assertRaises(DemoError) as raised,
            ):
                ensure_pytest()
        self.assertIn(str(stub), str(raised.exception))

    def test_run_demo_aborts_before_creating_the_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            requested = Path(raw) / "demo"
            with (
                mock.patch(
                    "repotrials.demo.fixture_test_interpreter",
                    return_value=MISSING_INTERPRETER,
                ),
                mock.patch("repotrials.demo._execute") as execute,
                self.assertRaises(DemoError),
            ):
                run_demo(requested)
            execute.assert_not_called()
            self.assertFalse(requested.exists())


class OutputDirectoryTests(unittest.TestCase):
    def test_temporary_directory_is_created_when_unspecified(self) -> None:
        destination = prepare_output(None)
        try:
            self.assertTrue(destination.is_dir())
            self.assertEqual(list(destination.iterdir()), [])
        finally:
            destination.rmdir()

    def test_requested_directory_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            requested = Path(raw) / "nested" / "demo"
            self.assertEqual(prepare_output(requested), requested.resolve())
            self.assertTrue(requested.is_dir())

    def test_populated_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            (Path(raw) / "existing.txt").write_text("keep me\n", encoding="utf-8")
            with self.assertRaises(DemoError) as raised:
                prepare_output(Path(raw))
            self.assertIn("must be empty", str(raised.exception))


class PayloadTests(unittest.TestCase):
    def test_payload_shape(self) -> None:
        result = sample_result(Path("/tmp/repotrials-demo"))
        payload = result.payload()
        self.assertEqual(
            sorted(payload),
            [
                "delta_pp",
                "harbor_export",
                "kept",
                "output",
                "report_html",
                "report_json",
                "repository",
                "runs",
            ],
        )
        self.assertTrue(payload["report_html"].endswith("report.html"))
        self.assertTrue(payload["report_json"].endswith("report.json"))
        self.assertTrue(payload["harbor_export"].endswith("harbor"))
        self.assertEqual(payload["delta_pp"], 100.0)
        self.assertTrue(payload["kept"])
        self.assertEqual(
            [(item["name"], item["run_group"]) for item in payload["runs"]],
            [
                ("noop-agent", "noop-agent-20260815-081633-d9b0ac"),
                ("fix-agent", "fix-agent-20260815-081634-233fdf"),
            ],
        )
        self.assertEqual(
            [(item["resolved"], item["trials"]) for item in payload["runs"]],
            [(0, 1), (1, 1)],
        )
        # The payload must survive a JSON round trip unchanged.
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_banner_names_the_report_and_how_to_open_it(self) -> None:
        result = sample_result(Path("/tmp/repotrials-demo"))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            render_banner(result)
        rendered = stdout.getvalue()
        self.assertIn(str(result.report_html), rendered)
        self.assertIn(open_command(result.report_html), rendered)

    def test_open_command_targets_the_report(self) -> None:
        command = open_command(Path("/tmp/repotrials-demo/report.html"))
        self.assertIn("report.html", command)
        self.assertTrue(command.split()[0] in {"open", "start", "xdg-open"})


class ParserTests(unittest.TestCase):
    def test_demo_is_registered_without_arguments(self) -> None:
        args = build_parser().parse_args(["demo"])
        self.assertEqual(args.command, "demo")
        self.assertIsNone(args.output)
        self.assertFalse(args.json)

    def test_demo_accepts_an_output_directory(self) -> None:
        args = build_parser().parse_args(["demo", "--output", "/tmp/repotrials-demo"])
        self.assertEqual(args.output, "/tmp/repotrials-demo")

    def test_demo_accepts_json_before_and_after_the_subcommand(self) -> None:
        for argv in (["--json", "demo"], ["demo", "--json"], ["--json", "demo", "--json"]):
            with self.subTest(argv=argv):
                self.assertTrue(build_parser().parse_args(argv).json)

    def test_existing_subcommands_are_unchanged(self) -> None:
        for command in ("init", "doctor", "mine", "candidates", "review", "report"):
            with self.subTest(command=command):
                self.assertEqual(build_parser().parse_args([command]).command, command)


class EntryPointTests(unittest.TestCase):
    """The demo is expensive, so only the wiring around it is exercised here."""

    def test_cli_emits_the_json_payload(self) -> None:
        from repotrials.cli import main as cli_main

        result = sample_result(Path("/tmp/repotrials-demo"))
        stdout = io.StringIO()
        with (
            mock.patch("repotrials.demo.run_demo", return_value=result) as runner,
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(cli_main(["demo", "--json"]), 0)
        runner.assert_called_once_with(None, stream=False)
        self.assertEqual(json.loads(stdout.getvalue()), result.payload())

    def test_cli_forwards_the_output_directory(self) -> None:
        from repotrials.cli import main as cli_main

        result = sample_result(Path("/tmp/repotrials-demo"))
        stdout = io.StringIO()
        with (
            mock.patch("repotrials.demo.run_demo", return_value=result) as runner,
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(cli_main(["demo", "--output", "/tmp/repotrials-demo"]), 0)
        runner.assert_called_once_with(Path("/tmp/repotrials-demo"), stream=True)
        self.assertEqual(stdout.getvalue(), "")

    def test_cli_reports_a_demo_error(self) -> None:
        from repotrials.cli import main as cli_main

        stderr = io.StringIO()
        with (
            mock.patch("repotrials.demo.run_demo", side_effect=DemoError("no pytest here")),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(cli_main(["demo"]), 1)
        self.assertIn("no pytest here", stderr.getvalue())

    def test_script_main_preserves_the_failing_exit_code(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch(
                "repotrials.demo.run_demo",
                side_effect=DemoError("validation failed", exit_code=3),
            ),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(main([]), 3)
        self.assertIn("validation failed", stderr.getvalue())

    def test_script_main_emits_json_on_request(self) -> None:
        result = sample_result(Path("/tmp/repotrials-demo"))
        stdout = io.StringIO()
        with (
            mock.patch("repotrials.demo.run_demo", return_value=result) as runner,
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(main(["--json", "--output", "/tmp/repotrials-demo"]), 0)
        runner.assert_called_once_with(Path("/tmp/repotrials-demo"), stream=False)
        self.assertEqual(json.loads(stdout.getvalue()), result.payload())

    def test_shim_delegates_to_the_package(self) -> None:
        shim = Path(__file__).resolve().parent.parent / "scripts" / "demo.py"
        source = shim.read_text(encoding="utf-8")
        self.assertIn("from repotrials.demo import main", source)
        self.assertIn("raise SystemExit(main())", source)


if __name__ == "__main__":
    unittest.main()
