"""Dependency-free command line interface for RepoTrials."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__, console
from .config import ConfigError, doctor, initialize_project, load_config

if TYPE_CHECKING:
    from .workflow import RepoTrialsWorkflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repotrials",
        description="Turn Git history into private, reproducible coding-agent evaluations.",
    )
    parser.add_argument("--version", action="version", version=f"RepoTrials {__version__}")
    parser.add_argument(
        "--root",
        default=".",
        help="target repository or path below it (default: current directory)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="initialize RepoTrials in a Git repository")
    init_parser.add_argument("path", nargs="?", default=None)
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(handler=_cmd_init)

    doctor_parser = subparsers.add_parser(
        "doctor", help="check local prerequisites and isolation tools"
    )
    doctor_parser.set_defaults(handler=_cmd_doctor)

    mine_parser = subparsers.add_parser("mine", help="discover test-backed historical fixes")
    mine_parser.add_argument("--ref", default="HEAD")
    mine_parser.add_argument(
        "--since", default=None, help="Git-compatible date, for example 12.months.ago"
    )
    mine_parser.add_argument("--limit", type=positive_int, default=None)
    mine_parser.add_argument(
        "--github", action="store_true", help="enrich candidates using GitHub metadata"
    )
    mine_parser.set_defaults(handler=_cmd_mine)

    candidates_parser = subparsers.add_parser("candidates", help="list mined candidates")
    candidates_parser.add_argument("--limit", type=positive_int, default=100)
    candidates_parser.set_defaults(handler=_cmd_candidates)

    validate_parser = subparsers.add_parser("validate", help="run repeatable BASE/RED/GOLD checks")
    validate_parser.add_argument("candidate_ids", nargs="*")
    validate_parser.add_argument("--repeats", type=positive_int, default=None)
    validate_parser.add_argument("--backend", choices=("local", "docker"), default=None)
    validate_parser.add_argument(
        "--accept", action="store_true", help="accept valid tasks as auto tier"
    )
    validate_parser.add_argument(
        "--unsafe-local",
        action="store_true",
        help="acknowledge that local validation executes repository code on the host",
    )
    validate_parser.set_defaults(handler=_cmd_validate)

    review_parser = subparsers.add_parser(
        "review", help="inspect or change accepted task quality tiers"
    )
    review_group = review_parser.add_mutually_exclusive_group()
    review_group.add_argument("--verify", nargs="+", metavar="TASK_ID")
    review_group.add_argument("--reject", nargs="+", metavar="TASK_ID")
    review_group.add_argument("--accept-all-auto", action="store_true")
    review_parser.set_defaults(handler=_cmd_review)

    export_parser = subparsers.add_parser(
        "export-harbor", help="export accepted tasks with a separate Harbor verifier"
    )
    export_parser.add_argument(
        "--output", default=".repotrials/exports/harbor", help="private output directory"
    )
    export_parser.add_argument("--task", action="append", dest="task_ids")
    export_parser.set_defaults(handler=_cmd_export_harbor)

    run_parser = subparsers.add_parser(
        "run", help="run a command-based agent against accepted tasks"
    )
    run_parser.add_argument("--agent-command", required=True)
    run_parser.add_argument("--name", required=True)
    run_parser.add_argument("--model", default="")
    run_parser.add_argument("--attempts", type=positive_int, default=None)
    run_parser.add_argument("--task", action="append", dest="task_ids")
    run_parser.add_argument(
        "--cost-usd",
        type=nonnegative_float,
        default=None,
        help="constant USD cost recorded on each task attempt (not a run-group total)",
    )
    run_parser.add_argument(
        "--unsafe-local",
        action="store_true",
        help="acknowledge that the local agent command is not sandboxed",
    )
    run_parser.set_defaults(handler=_cmd_run)

    report_parser = subparsers.add_parser(
        "report", help="write JSON and self-contained HTML reports"
    )
    report_parser.add_argument("run_ids", nargs="*")
    report_parser.add_argument("--output", default=".repotrials/reports/latest")
    report_parser.add_argument("--open", action="store_true", dest="open_report")
    report_parser.set_defaults(handler=_cmd_report)

    compare_parser = subparsers.add_parser("compare", help="compare two named runs on paired tasks")
    compare_parser.add_argument("baseline")
    compare_parser.add_argument("candidate")
    compare_parser.add_argument("--fail-on-regression", type=percentage_points, default=None)
    compare_parser.add_argument("--output", default=None)
    compare_parser.set_defaults(handler=_cmd_compare)

    vault_parser = subparsers.add_parser("vault", help="inspect the private artifact vault")
    vault_subparsers = vault_parser.add_subparsers(dest="vault_command", required=True)
    vault_verify = vault_subparsers.add_parser("verify", help="hash-check every stored object")
    vault_verify.set_defaults(handler=_cmd_vault_verify)

    return parser


def positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def nonnegative_float(raw: str) -> float:
    value = float(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return value


def percentage_points(raw: str) -> float:
    value = raw.strip().lower()
    if value.endswith("pp"):
        value = value[:-2]
    parsed = float(value)
    if not 0 <= parsed <= 100:
        raise argparse.ArgumentTypeError("must be between 0 and 100 percentage points")
    return parsed


def _workflow(args: argparse.Namespace) -> RepoTrialsWorkflow:
    from .workflow import RepoTrialsWorkflow

    return RepoTrialsWorkflow(load_config(args.root))


def _emit(args: argparse.Namespace, payload: Any, *, message: str | None = None) -> None:
    if args.json:
        console.print_json(payload)
    elif message:
        console.success(message)


def _cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.path or args.root)
    config_path = initialize_project(root, force=args.force)
    payload = {"config": str(config_path), "state": str(config_path.parent / ".repotrials")}
    _emit(args, payload, message=f"initialized {config_path}")
    if not args.json:
        console.info("Review repotrials.toml, then run `repotrials doctor`.")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.root)
    checks = doctor(config)
    payload = [dict(name=name, ok=ok, detail=detail) for name, ok, detail in checks]
    if args.json:
        console.print_json(payload)
    else:
        console.title("RepoTrials doctor")
        for name, ok, detail in checks:
            (console.success if ok else console.failure)(f"{name}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def _cmd_mine(args: argparse.Namespace) -> int:
    with _workflow(args) as workflow:
        result = workflow.mine(ref=args.ref, since=args.since, limit=args.limit, github=args.github)
    _emit(
        args,
        result,
        message=f"stored {result['stored']} candidate(s), including {result['new']} new",
    )
    return 0


def _cmd_candidates(args: argparse.Namespace) -> int:
    with _workflow(args) as workflow:
        candidates = workflow.list_candidates(limit=args.limit)
    if args.json:
        console.print_json(candidates)
    else:
        console.title(f"Candidates ({len(candidates)})")
        console.table(
            ("ID", "commit", "source", "tests", "lines", "title"),
            (
                (
                    item["id"],
                    item["commit_sha"][:9],
                    len(item["source_files"]),
                    len(item["test_files"]),
                    item["additions"] + item["deletions"],
                    item["title"][:58],
                )
                for item in candidates
            ),
        )
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    with _workflow(args) as workflow:
        outcomes = workflow.validate_candidates(
            args.candidate_ids or None,
            repeats=args.repeats,
            backend_name=args.backend,
            accept=args.accept,
            allow_unsafe_local=args.unsafe_local,
        )
    if args.json:
        console.print_json(outcomes)
    else:
        for item in outcomes:
            action = console.success if item["valid"] else console.warning
            detail = "valid" if item["valid"] else ", ".join(item["reasons"])
            action(f"{item['candidate_id']}: {detail}")
    return 0 if all(item["valid"] for item in outcomes) else 1


def _cmd_review(args: argparse.Namespace) -> int:
    with _workflow(args) as workflow:
        result: dict[str, int] | list[dict[str, Any]]
        if args.verify:
            result = workflow.set_task_tier(args.verify, "verified")
        elif args.reject:
            result = workflow.set_task_tier(args.reject, "rejected")
        elif args.accept_all_auto:
            result = workflow.accept_all_auto()
        else:
            result = workflow.list_tasks()
    if args.json:
        console.print_json(result)
    elif isinstance(result, list):
        console.title(f"Tasks ({len(result)})")
        console.table(
            ("ID", "tier", "candidate", "instruction"),
            (
                (item["id"], item["tier"], item["candidate_id"], item["instruction"][:70])
                for item in result
            ),
        )
    else:
        console.success(f"updated {result['updated']} task(s)")
    return 0


def _cmd_export_harbor(args: argparse.Namespace) -> int:
    with _workflow(args) as workflow:
        result = workflow.export_harbor(
            Path(args.output),
            task_ids=args.task_ids,
        )
    _emit(args, result, message=f"exported {result['count']} Harbor task(s) to {result['output']}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    with _workflow(args) as workflow:
        result = workflow.run_agent(
            agent_command=args.agent_command,
            name=args.name,
            model=args.model,
            attempts=args.attempts,
            task_ids=args.task_ids,
            cost_usd=args.cost_usd,
            allow_unsafe_local=args.unsafe_local,
        )
    _emit(
        args,
        result,
        message=f"run {result['run_group']}: {result['resolved']}/{result['trials']} trials resolved",
    )
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    with _workflow(args) as workflow:
        result = workflow.report(
            args.run_ids or None, Path(args.output), open_report=args.open_report
        )
    _emit(args, result, message=f"report written to {result['html']}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    with _workflow(args) as workflow:
        result = workflow.compare(
            args.baseline,
            args.candidate,
            fail_on_regression=args.fail_on_regression,
            output=Path(args.output) if args.output else None,
        )
    if args.json:
        console.print_json(result)
    else:
        console.title(f"{args.baseline} → {args.candidate}")
        console.table(
            ("baseline", "candidate", "delta", "paired tasks"),
            (
                (
                    f"{result['baseline_rate']:.1%}",
                    f"{result['candidate_rate']:.1%}",
                    f"{result['delta_pp']:+.1f} pp",
                    result["paired_tasks"],
                ),
            ),
        )
        if result["regression"]:
            console.failure("candidate crossed the configured regression threshold")
    return 1 if result["regression"] else 0


def _cmd_vault_verify(args: argparse.Namespace) -> int:
    with _workflow(args) as workflow:
        result = workflow.verify_vault()
    if args.json:
        console.print_json(result)
    else:
        for reference, ok in result["objects"].items():
            (console.success if ok else console.failure)(reference)
        console.info(f"verified {result['verified']}/{result['total']} object(s)")
    return 0 if result["verified"] == result["total"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (ConfigError, FileNotFoundError, ValueError, RuntimeError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        else:
            console.failure(str(exc))
        return 1
    except KeyboardInterrupt:
        console.failure("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
