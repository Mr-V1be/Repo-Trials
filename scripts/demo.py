#!/usr/bin/env python3
"""Build and run a tiny real-history RepoTrials demo.

The script intentionally uses the installed CLI for every product operation,
so it doubles as a manual smoke test of the public interface.  It never deletes
the generated directory; the final report remains available for inspection.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run(*arguments: str, cwd: Path | None = None, capture: bool = False) -> str:
    command = [*arguments]
    print("$ " + " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        if capture:
            print(completed.stdout)
            print(completed.stderr, file=sys.stderr)
        raise SystemExit(completed.returncode)
    if capture:
        return completed.stdout
    return ""


def create_history(root: Path) -> None:
    root.mkdir(parents=True)
    run("git", "init", "--initial-branch=main", cwd=root)
    run("git", "config", "user.name", "RepoTrials Demo", cwd=root)
    run("git", "config", "user.email", "demo@invalid.local", cwd=root)
    run("git", "config", "core.autocrlf", "false", cwd=root)
    (root / "tests").mkdir()
    (root / "cart.py").write_text("def total(prices):\n    return sum(prices)\n", encoding="utf-8")
    (root / "tests/test_cart.py").write_text(
        "from cart import total\n\ndef test_total():\n    assert total([2, 3]) == 5\n",
        encoding="utf-8",
    )
    run("git", "add", ".", cwd=root)
    run("git", "commit", "-m", "Add cart totals", cwd=root)

    (root / "cart.py").write_text(
        "def total(prices):\n"
        "    if not prices:\n"
        "        return 0\n"
        "    return round(sum(prices), 2)\n",
        encoding="utf-8",
    )
    with (root / "tests/test_cart.py").open("a", encoding="utf-8") as handle:
        handle.write("\ndef test_total_rounds_currency():\n    assert total([0.1, 0.2]) == 0.3\n")
    run("git", "add", ".", cwd=root)
    run("git", "commit", "-m", "Fix floating point cart total with regression test", cwd=root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    output = (
        args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="repotrials-demo-"))
    )
    if output.exists() and any(output.iterdir()):
        parser.error(f"output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    repository = output / "demo-repository"
    create_history(repository)

    cli = (sys.executable, "-m", "repotrials", "--root", str(repository))
    run(*cli, "init")
    config = repository / "repotrials.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        .replace("repeats = 3", "repeats = 1")
        .replace("attempts = 3", "attempts = 1"),
        encoding="utf-8",
    )
    run(*cli, "doctor")
    run(*cli, "mine", "--limit", "20")
    run(*cli, "candidates")
    run(*cli, "validate", "--repeats", "1", "--accept", "--unsafe-local")

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
    noop_run = json.loads(
        run(
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
        )
    )
    fix_run = json.loads(
        run(
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
        )
    )
    comparison = json.loads(
        run(
            *cli,
            "--json",
            "compare",
            noop_run["run_group"],
            fix_run["run_group"],
            capture=True,
        )
    )
    if comparison["delta_pp"] <= 0:
        raise SystemExit("demo comparison did not detect the improvement")
    print(
        f"noop-agent  {noop_run['resolved']}/{noop_run['trials']} trials resolved\n"
        f"fix-agent   {fix_run['resolved']}/{fix_run['trials']} trials resolved\n"
        f"delta       {comparison['delta_pp']:+.0f} percentage points"
    )
    report_dir = repository / ".repotrials" / "reports" / "demo"
    run(*cli, "report", fix_run["run_group"], "--output", str(report_dir))
    run(
        *cli,
        "export-harbor",
        "--output",
        str(repository / ".repotrials" / "exports" / "harbor"),
    )
    print()
    print(f"Demo complete: {output}")
    print(f"Open report:    {report_dir / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
