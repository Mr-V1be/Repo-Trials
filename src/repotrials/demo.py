"""Self-contained demonstration of the complete RepoTrials pipeline.

The demo builds a throwaway two-commit Git repository, mines it, validates the
mined candidate, then scores one deliberately broken agent and one working
agent against the same sealed task.  Every product operation goes through the
installed command line interface, so the demo doubles as a smoke test of the
public interface.  The generated directory is never removed unless the caller
asks for it, which keeps the report available for inspection afterwards.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import console

UVX_COMMAND = (
    "uvx --with pytest --from git+https://github.com/PozziTiv4ik/Repo-Trials repotrials demo"
)


class DemoError(RuntimeError):
    """Raised when the bundled demo cannot complete."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class DemoResult:
    """Paths and scores produced by one complete demo run."""

    output: Path
    repository: Path
    report_html: Path
    report_json: Path
    harbor_export: Path
    baseline_name: str
    baseline_run_group: str
    baseline_resolved: int
    baseline_trials: int
    candidate_name: str
    candidate_run_group: str
    candidate_resolved: int
    candidate_trials: int
    delta_pp: float
    kept: bool = True

    def payload(self) -> dict[str, Any]:
        """Return a machine-readable summary of the run."""

        return {
            "output": str(self.output),
            "repository": str(self.repository),
            "report_html": str(self.report_html),
            "report_json": str(self.report_json),
            "harbor_export": str(self.harbor_export),
            "runs": [
                {
                    "name": self.baseline_name,
                    "run_group": self.baseline_run_group,
                    "resolved": self.baseline_resolved,
                    "trials": self.baseline_trials,
                },
                {
                    "name": self.candidate_name,
                    "run_group": self.candidate_run_group,
                    "resolved": self.candidate_resolved,
                    "trials": self.candidate_trials,
                },
            ],
            "delta_pp": self.delta_pp,
            "kept": self.kept,
        }


def fixture_test_interpreter() -> str:
    """Resolve the interpreter that the generated fixture config will invoke.

    The generated ``repotrials.toml`` runs ``python -m pytest``, and commands
    are executed without a shell, so the runner is whichever ``python`` the
    process environment resolves first -- not necessarily ``sys.executable``.
    """

    return shutil.which("python") or shutil.which("python3") or sys.executable


def pytest_importable(executable: str) -> bool:
    """Report whether ``executable`` can import pytest."""

    if Path(executable) == Path(sys.executable):
        return importlib.util.find_spec("pytest") is not None
    try:
        completed = subprocess.run(
            (executable, "-c", "import pytest"),
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0


def ensure_pytest(executable: str | None = None) -> str:
    """Return the fixture test interpreter, or raise an actionable error."""

    resolved = executable or fixture_test_interpreter()
    if pytest_importable(resolved):
        return resolved
    raise DemoError(
        f"pytest is not importable by {resolved}, and the demo runs the generated "
        "fixture repository's tests with `python -m pytest`. "
        f"Install it with `{resolved} -m pip install pytest`, or run the demo without "
        f"installing anything: {UVX_COMMAND}"
    )


def open_command(path: Path) -> str:
    """Return the platform-appropriate command that opens ``path``."""

    if sys.platform == "darwin":
        return f"open {shlex.quote(str(path))}"
    if sys.platform == "win32":
        return f'start "" "{path}"'
    return f"xdg-open {shlex.quote(str(path))}"


def run(
    *arguments: str,
    cwd: Path | None = None,
    capture: bool = False,
    stream: bool = True,
) -> str:
    """Run one demo step, echoing the command so the transcript is auditable."""

    command = [*arguments]
    print("$ " + " ".join(command), file=sys.stdout if stream else sys.stderr, flush=True)
    collect = capture or not stream
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=collect,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        if collect:
            print(completed.stdout, file=sys.stdout if stream else sys.stderr)
            print(completed.stderr, file=sys.stderr)
        raise DemoError(
            f"demo step failed with status {completed.returncode}: {' '.join(command)}",
            exit_code=completed.returncode,
        )
    if capture:
        return completed.stdout
    return ""


def create_history(root: Path, *, stream: bool = True) -> None:
    """Create the two-commit fixture repository the demo mines."""

    root.mkdir(parents=True)
    run("git", "init", "--initial-branch=main", cwd=root, stream=stream)
    run("git", "config", "user.name", "RepoTrials Demo", cwd=root, stream=stream)
    run("git", "config", "user.email", "demo@invalid.local", cwd=root, stream=stream)
    run("git", "config", "core.autocrlf", "false", cwd=root, stream=stream)
    (root / "tests").mkdir()
    (root / "cart.py").write_text("def total(prices):\n    return sum(prices)\n", encoding="utf-8")
    (root / "tests/test_cart.py").write_text(
        "from cart import total\n\ndef test_total():\n    assert total([2, 3]) == 5\n",
        encoding="utf-8",
    )
    run("git", "add", ".", cwd=root, stream=stream)
    run("git", "commit", "-m", "Add cart totals", cwd=root, stream=stream)

    (root / "cart.py").write_text(
        "def total(prices):\n"
        "    if not prices:\n"
        "        return 0\n"
        "    return round(sum(prices), 2)\n",
        encoding="utf-8",
    )
    with (root / "tests/test_cart.py").open("a", encoding="utf-8") as handle:
        handle.write("\ndef test_total_rounds_currency():\n    assert total([0.1, 0.2]) == 0.3\n")
    run("git", "add", ".", cwd=root, stream=stream)
    run(
        "git",
        "commit",
        "-m",
        "Fix floating point cart total with regression test",
        cwd=root,
        stream=stream,
    )


def prepare_output(output: Path | None) -> Path:
    """Resolve and create the demo directory, refusing to reuse a populated one."""

    if output is None:
        return Path(tempfile.mkdtemp(prefix="repotrials-demo-"))
    destination = output.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise DemoError(f"output directory must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def run_demo(output: Path | None = None, *, keep: bool = True, stream: bool = True) -> DemoResult:
    """Build the fixture repository and drive the full pipeline over it.

    ``keep`` retains the generated directory, which is what makes the report
    inspectable; pass ``keep=False`` to delete it once the run has finished.
    ``stream`` writes the command transcript and the human summary to stdout;
    disable it when the caller intends to emit JSON on stdout instead.
    """

    ensure_pytest()
    destination = prepare_output(output)
    try:
        return _execute(destination, stream=stream, keep=keep)
    finally:
        if not keep:
            shutil.rmtree(destination, ignore_errors=True)


def _execute(output: Path, *, stream: bool, keep: bool) -> DemoResult:
    repository = output / "demo-repository"
    create_history(repository, stream=stream)

    cli = (sys.executable, "-m", "repotrials", "--root", str(repository))
    run(*cli, "init", stream=stream)
    config = repository / "repotrials.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace("repeats = 3", "repeats = 1")
        .replace("attempts = 3", "attempts = 1"),
        encoding="utf-8",
    )
    run(*cli, "doctor", stream=stream)
    run(*cli, "mine", "--limit", "20", stream=stream)
    run(*cli, "candidates", stream=stream)
    run(*cli, "validate", "--repeats", "1", "--accept", "--unsafe-local", stream=stream)

    noop = output / "noop_agent.py"
    noop.write_text("# Deliberately leave the repository unchanged.\n", encoding="utf-8")
    fixer = output / "fix_agent.py"
    fixer.write_text(
        "from pathlib import Path\n"
        "Path('cart.py').write_text(\"def total(prices):\\n"
        "    if not prices:\\n        return 0\\n"
        "    return round(sum(prices), 2)\\n\", encoding='utf-8')\n",
        encoding="utf-8",
    )
    noop_run = _agent_run(cli, noop, "noop-agent", stream=stream)
    fix_run = _agent_run(cli, fixer, "fix-agent", stream=stream)
    comparison: dict[str, Any] = _json(
        run(
            *cli,
            "--json",
            "compare",
            str(noop_run["run_group"]),
            str(fix_run["run_group"]),
            capture=True,
            stream=stream,
        )
    )
    delta_pp = float(comparison["delta_pp"])
    if delta_pp <= 0:
        raise DemoError("demo comparison did not detect the improvement")
    if stream:
        print(
            f"noop-agent  {noop_run['resolved']}/{noop_run['trials']} trials resolved\n"
            f"fix-agent   {fix_run['resolved']}/{fix_run['trials']} trials resolved\n"
            f"delta       {delta_pp:+.0f} percentage points"
        )
    report_dir = repository / ".repotrials" / "reports" / "demo"
    run(*cli, "report", str(fix_run["run_group"]), "--output", str(report_dir), stream=stream)
    harbor_export = repository / ".repotrials" / "exports" / "harbor"
    run(*cli, "export-harbor", "--output", str(harbor_export), stream=stream)

    result = DemoResult(
        output=output,
        repository=repository,
        report_html=report_dir / "report.html",
        report_json=report_dir / "report.json",
        harbor_export=harbor_export,
        baseline_name="noop-agent",
        baseline_run_group=str(noop_run["run_group"]),
        baseline_resolved=int(noop_run["resolved"]),
        baseline_trials=int(noop_run["trials"]),
        candidate_name="fix-agent",
        candidate_run_group=str(fix_run["run_group"]),
        candidate_resolved=int(fix_run["resolved"]),
        candidate_trials=int(fix_run["trials"]),
        delta_pp=delta_pp,
        kept=keep,
    )
    if stream:
        render_banner(result)
    return result


def _agent_run(cli: tuple[str, ...], agent: Path, name: str, *, stream: bool) -> dict[str, Any]:
    command = f'"{Path(sys.executable).as_posix()}" "{agent.as_posix()}"'
    return _json(
        run(
            *cli,
            "--json",
            "run",
            "--agent-command",
            command,
            "--name",
            name,
            "--attempts",
            "1",
            "--unsafe-local",
            capture=True,
            stream=stream,
        )
    )


def _json(payload: str) -> dict[str, Any]:
    decoded: dict[str, Any] = json.loads(payload)
    return decoded


def render_banner(result: DemoResult) -> None:
    """Print where the demo landed and the exact command that opens the report."""

    print()
    print(f"Demo complete: {result.output}")
    print(f"Open report:   {result.report_html}")
    print(f"               {open_command(result.report_html)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="repotrials-demo",
        description="Build a throwaway repository and run the full RepoTrials pipeline.",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        result = run_demo(args.output, stream=not args.json)
    except DemoError as exc:
        console.failure(str(exc))
        return exc.exit_code
    if args.json:
        console.print_json(result.payload())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
