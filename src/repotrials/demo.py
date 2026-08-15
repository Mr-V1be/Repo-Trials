"""Dependency-free end-to-end demo for the public RepoTrials workflow."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


class DemoError(RuntimeError):
    """Raised when the self-contained demo cannot complete."""


def _run(
    *arguments: str,
    cwd: Path | None = None,
    capture: bool = False,
    verbose: bool = True,
) -> str:
    command = [*arguments]
    if verbose:
        print("$ " + shlex.join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=capture or not verbose,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        if completed.stdout:
            print(completed.stdout, file=sys.stderr)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        raise DemoError(f"demo command exited with {completed.returncode}: {shlex.join(command)}")
    return completed.stdout if capture or not verbose else ""


def _create_history(root: Path, *, verbose: bool) -> None:
    root.mkdir(parents=True)
    _run("git", "init", "--initial-branch=main", cwd=root, verbose=verbose)
    _run("git", "config", "user.name", "RepoTrials Demo", cwd=root, verbose=verbose)
    _run("git", "config", "user.email", "demo@invalid.local", cwd=root, verbose=verbose)
    _run("git", "config", "core.autocrlf", "false", cwd=root, verbose=verbose)
    (root / "tests").mkdir()
    (root / "cart.py").write_text(
        "def total(prices):\n    return sum(prices)\n",
        encoding="utf-8",
    )
    (root / "tests/test_cart.py").write_text(
        "from cart import total\n\ndef test_total():\n    assert total([2, 3]) == 5\n",
        encoding="utf-8",
    )
    (root / "tests/run_tests.py").write_text(_TEST_RUNNER, encoding="utf-8")
    _run("git", "add", ".", cwd=root, verbose=verbose)
    _run("git", "commit", "-m", "Add cart totals", cwd=root, verbose=verbose)

    (root / "cart.py").write_text(
        "def total(prices):\n"
        "    if not prices:\n"
        "        return 0\n"
        "    return round(sum(prices), 2)\n",
        encoding="utf-8",
    )
    with (root / "tests/test_cart.py").open("a", encoding="utf-8") as handle:
        handle.write("\ndef test_total_rounds_currency():\n    assert total([0.1, 0.2]) == 0.3\n")
    _run("git", "add", ".", cwd=root, verbose=verbose)
    _run(
        "git",
        "commit",
        "-m",
        "Fix floating point cart total with regression test",
        cwd=root,
        verbose=verbose,
    )


def run_demo(output: Path | None = None, *, verbose: bool = True) -> dict[str, Any]:
    """Run the complete product loop and return paths plus the measured result."""

    destination = (
        output.expanduser().resolve()
        if output is not None
        else Path(tempfile.mkdtemp(prefix="repotrials-demo-"))
    )
    if destination.exists():
        if not destination.is_dir():
            raise DemoError(f"output path must be a directory: {destination}")
        if any(destination.iterdir()):
            raise DemoError(f"output directory must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    repository = destination / "demo-repository"
    _create_history(repository, verbose=verbose)

    cli = (sys.executable, "-m", "repotrials", "--root", str(repository))
    _run(*cli, "init", verbose=verbose)
    config = repository / "repotrials.toml"
    default_command = 'command = "python -m pytest -q --junitxml={junit}"'
    raw_config = config.read_text(encoding="utf-8")
    if default_command not in raw_config:
        raise DemoError("generated configuration does not contain the expected test command")
    configured = (
        raw_config.replace(default_command, 'command = "python tests/run_tests.py {junit}"')
        .replace("repeats = 3", "repeats = 1")
        .replace("attempts = 3", "attempts = 1")
    )
    config.write_text(configured, encoding="utf-8")
    _run(*cli, "doctor", verbose=verbose)
    _run(*cli, "mine", "--limit", "20", verbose=verbose)
    _run(*cli, "candidates", verbose=verbose)
    _run(
        *cli,
        "validate",
        "--repeats",
        "1",
        "--accept",
        "--unsafe-local",
        verbose=verbose,
    )

    noop = destination / "noop_agent.py"
    noop.write_text("# Deliberately leave the repository unchanged.\n", encoding="utf-8")
    fixer = destination / "fix_agent.py"
    fixer.write_text(
        "from pathlib import Path\n"
        "Path('cart.py').write_text(\"def total(prices):\\n"
        "    if not prices:\\n        return 0\\n"
        "    return round(sum(prices), 2)\\n\", encoding='utf-8')\n",
        encoding="utf-8",
    )
    noop_run = json.loads(
        _run(
            *cli,
            "--json",
            "run",
            "--agent-command",
            f'"{Path(sys.executable).as_posix()}" "{noop.as_posix()}"',
            "--name",
            "noop-agent",
            "--attempts",
            "1",
            "--unsafe-local",
            capture=True,
            verbose=verbose,
        )
    )
    fix_run = json.loads(
        _run(
            *cli,
            "--json",
            "run",
            "--agent-command",
            f'"{Path(sys.executable).as_posix()}" "{fixer.as_posix()}"',
            "--name",
            "fix-agent",
            "--attempts",
            "1",
            "--unsafe-local",
            capture=True,
            verbose=verbose,
        )
    )
    comparison = json.loads(
        _run(
            *cli,
            "--json",
            "compare",
            str(noop_run["run_group"]),
            str(fix_run["run_group"]),
            capture=True,
            verbose=verbose,
        )
    )
    if comparison["delta_pp"] <= 0:
        raise DemoError("demo comparison did not detect the improvement")

    report_dir = repository / ".repotrials" / "reports" / "demo"
    _run(
        *cli,
        "report",
        str(fix_run["run_group"]),
        "--output",
        str(report_dir),
        verbose=verbose,
    )
    export_dir = repository / ".repotrials" / "exports" / "harbor"
    _run(*cli, "export-harbor", "--output", str(export_dir), verbose=verbose)
    return {
        "output": str(destination),
        "repository": str(repository),
        "report": str(report_dir / "report.html"),
        "harbor_export": str(export_dir),
        "noop_resolved": int(noop_run["resolved"]),
        "fix_resolved": int(fix_run["resolved"]),
        "trials": int(fix_run["trials"]),
        "delta_pp": float(comparison["delta_pp"]),
    }


def render_summary(result: dict[str, Any]) -> str:
    """Render a compact, copyable conclusion for the demo."""

    return (
        f"noop-agent  {result['noop_resolved']}/{result['trials']} trials resolved\n"
        f"fix-agent   {result['fix_resolved']}/{result['trials']} trials resolved\n"
        f"delta       {result['delta_pp']:+.0f} percentage points\n\n"
        f"Demo complete: {result['output']}\n"
        f"Open report:    {result['report']}"
    )


_TEST_RUNNER = """from __future__ import annotations

import importlib.util
import sys
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
suite = ET.Element("testsuite", name="repotrials-demo")
failures = 0
tests = 0
for path in sorted(Path("tests").glob("test_*.py")):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in sorted(value for value in vars(module) if value.startswith("test_")):
        tests += 1
        case = ET.SubElement(suite, "testcase", classname=path.stem, name=name)
        try:
            getattr(module, name)()
        except Exception:
            failures += 1
            failure = ET.SubElement(case, "failure", message="test failed")
            failure.text = traceback.format_exc()
suite.set("tests", str(tests))
suite.set("failures", str(failures))
ET.ElementTree(suite).write(sys.argv[1], encoding="utf-8", xml_declaration=True)
raise SystemExit(1 if failures else 0)
"""


__all__ = ["DemoError", "render_summary", "run_demo"]
