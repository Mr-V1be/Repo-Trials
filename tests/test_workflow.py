from __future__ import annotations

import hashlib
import json
import os
import runpy
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from repotrials.config import initialize_project, load_config
from repotrials.models import Run
from repotrials.workflow import RepoTrialsWorkflow, WorkflowError, _junit_failure_kind


def git(root: Path, *arguments: str) -> str:
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
    return completed.stdout.strip()


class WorkflowTests(unittest.TestCase):
    def test_local_junit_policy_allows_only_optional_skips_and_xfails(self) -> None:
        expected = {"suite::required"}
        healthy = {
            "suite::required": "passed",
            "suite::optional-skip": "skipped",
            "suite::optional-xfail": "xfailed",
        }
        self.assertIsNone(_junit_failure_kind(healthy, expected, command_ok=True))

        for status in ("skipped", "xfailed"):
            with self.subTest(required_status=status):
                outcomes = dict(healthy)
                outcomes["suite::required"] = status
                self.assertEqual(
                    _junit_failure_kind(outcomes, expected, command_ok=True),
                    "tests_failed",
                )
        for status in ("failed", "error"):
            with self.subTest(optional_status=status):
                outcomes = dict(healthy)
                outcomes["suite::optional-skip"] = status
                self.assertEqual(
                    _junit_failure_kind(outcomes, expected, command_ok=True),
                    "tests_failed",
                )

    def test_report_and_compare_use_task_level_pass_at_k_cohorts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "target"
            root.mkdir()
            git(root, "init", "--initial-branch=main")
            git(root, "config", "user.name", "Fixture")
            git(root, "config", "user.email", "fixture@example.invalid")
            (root / "README.txt").write_text("fixture\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-m", "Initial fixture")
            initialize_project(root)

            profile = {
                "schema_version": "repotrials.execution-profile/v1",
                "backend": "test",
                "isolation": "fixture",
            }

            def profile_digest(value: dict[str, str]) -> str:
                payload = json.dumps(
                    value,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                return hashlib.sha256(payload).hexdigest()

            def digest(value: str) -> str:
                if len(value) == 64 and all(character in "0123456789abcdef" for character in value):
                    return value
                return hashlib.sha256(value.encode()).hexdigest()

            def make_run(
                *,
                run_id: str,
                task_id: str,
                group: str,
                attempt: int,
                passed: bool,
                task_digest: str,
                execution_profile: dict[str, str] = profile,
                agent: str = "shared-agent",
                task_contract_sha256: str | None = None,
                integrity_passed: bool = True,
            ) -> Run:
                status = "passed" if passed else "failed"
                contract_digest = (
                    digest(task_contract_sha256)
                    if task_contract_sha256 is not None
                    else digest(f"contract:{task_digest}")
                )
                return Run(
                    id=run_id,
                    task_id=task_id,
                    agent=agent,
                    status=status,
                    passed=passed,
                    exit_code=0,
                    metadata={
                        "run_group": group,
                        "attempt": attempt,
                        "integrity_passed": integrity_passed,
                        "f2p": {"hidden": status},
                        "p2p": {"existing": "passed"},
                        "task_digest": digest(task_digest),
                        "task_contract_sha256": contract_digest,
                        "execution_profile": execution_profile,
                        "execution_profile_sha256": profile_digest(execution_profile),
                    },
                )

            def write_group_manifest(
                group_runs: tuple[Run, ...] | list[Run],
                *,
                status: str = "complete",
                attempts: int | None = None,
            ) -> Path:
                self.assertTrue(group_runs)
                group = str(group_runs[0].metadata["run_group"])
                task_ids = list(dict.fromkeys(run.task_id for run in group_runs))
                attempt_count = attempts or max(int(run.metadata["attempt"]) for run in group_runs)
                payload: dict[str, object] = {
                    "schema_version": "repotrials.run-group/v1",
                    "run_group": group,
                    "status": status,
                    "task_ids": task_ids,
                    "task_digests": {
                        task_id: next(
                            str(run.metadata["task_digest"])
                            for run in group_runs
                            if run.task_id == task_id
                        )
                        for task_id in task_ids
                    },
                    "task_contract_digests": {
                        task_id: next(
                            str(run.metadata["task_contract_sha256"])
                            for run in group_runs
                            if run.task_id == task_id
                        )
                        for task_id in task_ids
                    },
                    "attempts": attempt_count,
                    "expected_trial_count": len(task_ids) * attempt_count,
                    "agent": group_runs[0].agent,
                    "model": group_runs[0].model or None,
                    "created_at": "2026-01-01T00:00:00Z",
                }
                if status == "complete":
                    payload.update(
                        {
                            "run_ids": [run.id for run in group_runs],
                            "completed_at": "2026-01-01T00:00:01Z",
                        }
                    )
                path = root / ".repotrials" / "runs" / group / "group.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
                return path

            with RepoTrialsWorkflow(load_config(root)) as workflow:
                baseline = (
                    make_run(
                        run_id="base-a-1",
                        task_id="task-a",
                        group="baseline-group",
                        attempt=1,
                        passed=False,
                        task_digest="digest-a",
                    ),
                    make_run(
                        run_id="base-a-2",
                        task_id="task-a",
                        group="baseline-group",
                        attempt=2,
                        passed=True,
                        task_digest="digest-a",
                    ),
                    make_run(
                        run_id="base-b-1",
                        task_id="task-b",
                        group="baseline-group",
                        attempt=1,
                        passed=False,
                        task_digest="digest-b",
                    ),
                    make_run(
                        run_id="base-b-2",
                        task_id="task-b",
                        group="baseline-group",
                        attempt=2,
                        passed=False,
                        task_digest="digest-b",
                    ),
                )
                candidate = (
                    make_run(
                        run_id="candidate-a-1",
                        task_id="task-a",
                        group="candidate-group",
                        attempt=1,
                        passed=False,
                        task_digest="digest-a",
                    ),
                    make_run(
                        run_id="candidate-a-2",
                        task_id="task-a",
                        group="candidate-group",
                        attempt=2,
                        passed=False,
                        task_digest="digest-a",
                    ),
                    make_run(
                        run_id="candidate-b-1",
                        task_id="task-b",
                        group="candidate-group",
                        attempt=1,
                        passed=False,
                        task_digest="digest-b",
                    ),
                    make_run(
                        run_id="candidate-b-2",
                        task_id="task-b",
                        group="candidate-group",
                        attempt=2,
                        passed=True,
                        task_digest="digest-b",
                    ),
                )
                for run in (*baseline, *candidate):
                    workflow.store.save_run(run)
                write_group_manifest(baseline)
                write_group_manifest(candidate)

                report = workflow.report(("baseline-group",), Path(".repotrials/task-level-report"))
                self.assertEqual(report["tasks"], 2)
                self.assertEqual(report["trials"], 4)
                self.assertEqual(report["resolved"], 1)
                self.assertEqual(report["resolve_rate"], 0.5)
                self.assertEqual(report["aggregation"]["k"], 2)
                self.assertEqual(report["confidence_interval"]["unit"], "task")
                report_payload = json.loads(Path(report["json"]).read_text(encoding="utf-8"))
                self.assertEqual(
                    set(report_payload["metadata"]["task_contract_digests"]),
                    {"task-a", "task-b"},
                )

                report_from_one_run = workflow.report(
                    (baseline[0].id,), Path(".repotrials/expanded-run-report")
                )
                self.assertEqual(report_from_one_run["tasks"], 2)
                self.assertEqual(report_from_one_run["trials"], 4)

                comparison = workflow.compare("baseline-group", "candidate-group")
                self.assertEqual(comparison["paired_tasks"], 2)
                self.assertEqual(comparison["trials_per_group"], 4)
                self.assertEqual(comparison["aggregation"]["k"], 2)
                self.assertEqual(comparison["baseline_rate"], 0.5)
                self.assertEqual(comparison["candidate_rate"], 0.5)
                self.assertEqual(comparison["wins"], 1)
                self.assertEqual(comparison["losses"], 1)
                self.assertEqual(comparison["ties"], 0)

                with self.assertRaisesRegex(WorkflowError, "exactly one.*run_group"):
                    workflow.compare("shared-agent", "candidate-group")

                for attempt in (1, 2):
                    workflow.store.save_run(
                        make_run(
                            run_id=f"digest-bad-{attempt}",
                            task_id="task-a",
                            group="digest-bad",
                            attempt=attempt,
                            passed=False,
                            task_digest="different-a",
                            agent="digest-agent",
                        )
                    )
                    workflow.store.save_run(
                        make_run(
                            run_id=f"digest-bad-b-{attempt}",
                            task_id="task-b",
                            group="digest-bad",
                            attempt=attempt,
                            passed=False,
                            task_digest="digest-b",
                            agent="digest-agent",
                        )
                    )
                write_group_manifest(
                    [
                        run
                        for run in workflow.store.list_runs()
                        if run.metadata.get("run_group") == "digest-bad"
                    ]
                )
                with self.assertRaisesRegex(WorkflowError, "identical task digests"):
                    workflow.compare("baseline-group", "digest-bad")

                for task_id, task_digest in (("task-a", "digest-a"), ("task-b", "digest-b")):
                    for attempt in (1, 2):
                        workflow.store.save_run(
                            make_run(
                                run_id=f"contract-bad-{task_id}-{attempt}",
                                task_id=task_id,
                                group="contract-bad",
                                attempt=attempt,
                                passed=False,
                                task_digest=task_digest,
                                task_contract_sha256=(
                                    "different-contract" if task_id == "task-a" else None
                                ),
                                agent="contract-agent",
                            )
                        )
                write_group_manifest(
                    [
                        run
                        for run in workflow.store.list_runs()
                        if run.metadata.get("run_group") == "contract-bad"
                    ]
                )
                with self.assertRaisesRegex(WorkflowError, "identical task contracts"):
                    workflow.compare("baseline-group", "contract-bad")

                for attempt in (1, 2):
                    workflow.store.save_run(
                        make_run(
                            run_id=f"taskset-bad-{attempt}",
                            task_id="task-a",
                            group="taskset-bad",
                            attempt=attempt,
                            passed=False,
                            task_digest="digest-a",
                            agent="taskset-agent",
                        )
                    )
                write_group_manifest(
                    [
                        run
                        for run in workflow.store.list_runs()
                        if run.metadata.get("run_group") == "taskset-bad"
                    ]
                )
                with self.assertRaisesRegex(WorkflowError, "identical task sets"):
                    workflow.compare("baseline-group", "taskset-bad")

                for task_id, task_digest in (("task-a", "digest-a"), ("task-b", "digest-b")):
                    workflow.store.save_run(
                        make_run(
                            run_id=f"attempt-bad-{task_id}",
                            task_id=task_id,
                            group="attempt-bad",
                            attempt=1,
                            passed=False,
                            task_digest=task_digest,
                            agent="attempt-agent",
                        )
                    )
                write_group_manifest(
                    [
                        run
                        for run in workflow.store.list_runs()
                        if run.metadata.get("run_group") == "attempt-bad"
                    ]
                )
                with self.assertRaisesRegex(WorkflowError, "identical attempt sets"):
                    workflow.compare("baseline-group", "attempt-bad")

                changed_profile = {**profile, "isolation": "different"}
                for task_id, task_digest in (("task-a", "digest-a"), ("task-b", "digest-b")):
                    for attempt in (1, 2):
                        workflow.store.save_run(
                            make_run(
                                run_id=f"profile-bad-{task_id}-{attempt}",
                                task_id=task_id,
                                group="profile-bad",
                                attempt=attempt,
                                passed=False,
                                task_digest=task_digest,
                                execution_profile=changed_profile,
                                agent="profile-agent",
                            )
                        )
                write_group_manifest(
                    [
                        run
                        for run in workflow.store.list_runs()
                        if run.metadata.get("run_group") == "profile-bad"
                    ]
                )
                with self.assertRaisesRegex(WorkflowError, "execution profiles"):
                    workflow.compare("baseline-group", "profile-bad")

                for task_id, task_digest in (("task-a", "digest-a"), ("task-b", "digest-b")):
                    for attempt in (1, 2):
                        workflow.store.save_run(
                            make_run(
                                run_id=f"integrity-bad-{task_id}-{attempt}",
                                task_id=task_id,
                                group="integrity-bad",
                                attempt=attempt,
                                passed=task_id == "task-a",
                                task_digest=task_digest,
                                integrity_passed=False,
                                agent="integrity-agent",
                            )
                        )
                write_group_manifest(
                    [
                        run
                        for run in workflow.store.list_runs()
                        if run.metadata.get("run_group") == "integrity-bad"
                    ]
                )
                integrity_comparison = workflow.compare("baseline-group", "integrity-bad")
                self.assertEqual(integrity_comparison["candidate_rate"], 0.0)
                self.assertEqual(integrity_comparison["losses"], 1)

                partial = make_run(
                    run_id="partial-a-1",
                    task_id="task-a",
                    group="partial-group",
                    attempt=1,
                    passed=False,
                    task_digest="digest-a",
                    agent="partial-agent",
                )
                workflow.store.save_run(partial)
                write_group_manifest([partial], status="running", attempts=2)
                with self.assertRaisesRegex(WorkflowError, "incomplete"):
                    workflow.report(("partial-group",), root / ".repotrials/partial-report")

                missing = make_run(
                    run_id="missing-manifest-a-1",
                    task_id="task-a",
                    group="missing-manifest-group",
                    attempt=1,
                    passed=False,
                    task_digest="digest-a",
                    agent="missing-agent",
                )
                workflow.store.save_run(missing)
                with self.assertRaisesRegex(WorkflowError, "missing its group manifest"):
                    workflow.report(
                        ("missing-manifest-group",),
                        root / ".repotrials/missing-report",
                    )

    def test_full_local_pipeline_and_harbor_export(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "target"
            root.mkdir()
            git(root, "init", "--initial-branch=main")
            git(root, "config", "user.name", "Fixture")
            git(root, "config", "user.email", "fixture@example.invalid")
            git(root, "config", "core.autocrlf", "false")
            (root / "tests").mkdir()
            (root / "calc.py").write_text(
                "def divide(left, right):\n    return left / right\n", encoding="utf-8"
            )
            (root / "build_snapshot.py").write_text(
                "import os\n"
                "from pathlib import Path\n"
                "source = Path('calc.py')\n"
                "Path('built_calc.py').write_bytes(source.read_bytes())\n"
                "source.chmod(0o755)\n"
                "if os.name != 'nt':\n"
                "    link = Path('.repotrials-setup-link')\n"
                "    if not link.exists():\n"
                "        link.symlink_to('calc.py')\n",
                encoding="utf-8",
            )
            (root / "tests/test_calc.py").write_text(
                "import pytest\n\n"
                "from built_calc import divide\n\n"
                "def test_division():\n    assert divide(6, 3) == 2\n\n"
                "@pytest.mark.skip(reason='optional dependency is unavailable')\n"
                "def test_optional_skip():\n    assert False\n\n"
                "@pytest.mark.xfail(reason='optional behavior is not implemented')\n"
                "def test_optional_xfail():\n    assert False\n",
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
                handle.write("\ndef test_division_by_zero():\n    assert divide(1, 0) is None\n")
            git(root, "add", ".")
            git(root, "commit", "-m", "Fix division by zero with regression test")

            initialize_project(root)
            config_path = root / "repotrials.toml"
            config_text = config_path.read_text(encoding="utf-8")
            config_path.write_text(
                config_text.replace("setup = []", 'setup = ["python build_snapshot.py"]')
                .replace("repeats = 3", "repeats = 1")
                .replace("attempts = 3", "attempts = 1"),
                encoding="utf-8",
            )
            with RepoTrialsWorkflow(load_config(root)) as workflow:
                mined = workflow.mine(limit=10)
                self.assertEqual(mined["stored"], 1)
                validated = workflow.validate_candidates(
                    repeats=1,
                    accept=True,
                    allow_unsafe_local=True,
                )
                self.assertEqual(len(validated), 1)
                self.assertTrue(validated[0]["valid"], validated[0]["reasons"])
                self.assertTrue(validated[0]["fail_to_pass"])
                self.assertTrue(validated[0]["pass_to_pass"])

                agent_script = Path(raw) / "agent.py"
                agent_script.write_text(
                    "from pathlib import Path\n"
                    "path = Path('calc.py')\n"
                    'path.write_text("def divide(left, right):\\n    if right == 0:\\n'
                    "        return None\\n    return left / right\\n\", encoding='utf-8')\n",
                    encoding="utf-8",
                )
                run = workflow.run_agent(
                    agent_command=f'python "{agent_script.as_posix()}"',
                    name="fixture-agent",
                    attempts=1,
                    allow_unsafe_local=True,
                )
                self.assertEqual(run["resolved"], 1)

                project_root = Path(__file__).resolve().parents[1]
                task_id = validated[0]["task_id"]
                public_manifest = root / ".repotrials/tasks" / task_id / "public.json"
                private_manifest = root / ".repotrials/private" / f"{task_id}.json"
                group_dir = root / ".repotrials/runs" / run["run_group"]
                run_manifest = group_dir / f"{run['run_ids'][0]}.json"
                group_manifest = group_dir / "group.json"
                for schema_name, manifest_path in (
                    ("task-public-v1.schema.json", public_manifest),
                    ("task-private-v1.schema.json", private_manifest),
                    ("result-v1.schema.json", run_manifest),
                    ("run-group-v1.schema.json", group_manifest),
                ):
                    schema = json.loads((project_root / "schemas" / schema_name).read_text())
                    payload = json.loads(manifest_path.read_text())
                    Draft202012Validator(schema).validate(payload)
                portable_run = json.loads(run_manifest.read_text(encoding="utf-8"))
                self.assertEqual(portable_run["run_group"], run["run_group"])
                self.assertEqual(portable_run["attempt"], 1)
                portable_group = json.loads(group_manifest.read_text(encoding="utf-8"))
                self.assertEqual(portable_group["status"], "complete")
                self.assertEqual(portable_group["run_ids"], run["run_ids"])
                self.assertEqual(portable_group["expected_trial_count"], 1)

                report = workflow.report((run["run_group"],), Path(".repotrials/report"))
                self.assertTrue(Path(report["json"]).is_file())
                self.assertTrue(Path(report["html"]).is_file())
                self.assertEqual(report["resolve_rate"], 1.0)

                exported = workflow.export_harbor(
                    Path(".repotrials/exports/harbor"),
                    agent_image="python:3.12-slim",
                    verifier_image="python:3.12-slim",
                )
                self.assertEqual(exported["count"], 1)
                task_dir = Path(exported["tasks"][0])
                self.assertTrue((task_dir / "tests/hidden-tests.patch").is_file())
                self.assertNotIn(
                    "hidden-tests.patch", (task_dir / "environment/Dockerfile").read_text()
                )
                verifier_dockerfile = (task_dir / "tests/Dockerfile").read_text()
                self.assertIn("find /workspace/repo -mindepth 1", verifier_dockerfile)
                self.assertEqual(verifier_dockerfile.count("tar -xf"), 2)
                disabled_junit = Path(raw) / "disabled.xml"
                disabled_junit.write_text(
                    '<testsuite><testcase classname="x" name="required" '
                    'status="disabled"/></testsuite>',
                    encoding="utf-8",
                )
                grader_namespace = runpy.run_path(
                    str(task_dir / "tests/grader.py"),
                    run_name="repotrials_grader_parity_test",
                )
                self.assertEqual(
                    grader_namespace["outcomes"](disabled_junit),
                    {"x::required": "skipped"},
                )
                xfail_junit = Path(raw) / "xfail.xml"
                xfail_junit.write_text(
                    '<testsuite><testcase classname="x" name="optional">'
                    '<skipped type="pytest.xfail"/></testcase></testsuite>',
                    encoding="utf-8",
                )
                self.assertEqual(
                    grader_namespace["outcomes"](xfail_junit),
                    {"x::optional": "xfailed"},
                )
                harbor_policy = grader_namespace["validate_outcomes"]
                harbor_policy(
                    {
                        "x::required": "passed",
                        "x::optional-skip": "skipped",
                        "x::optional-xfail": "xfailed",
                    },
                    {"x::required"},
                    0,
                )
                for status in ("skipped", "xfailed"):
                    with (
                        self.subTest(harbor_required_status=status),
                        self.assertRaisesRegex(RuntimeError, "expected tests failed"),
                    ):
                        harbor_policy({"x::required": status}, {"x::required"}, 0)
                for status in ("failed", "error"):
                    with (
                        self.subTest(harbor_optional_status=status),
                        self.assertRaisesRegex(RuntimeError, "failed or errored"),
                    ):
                        harbor_policy(
                            {"x::required": "passed", "x::optional": status},
                            {"x::required"},
                            0,
                        )

                def verifier_workspace(name: str) -> Path:
                    workspace = Path(raw) / name
                    workspace.mkdir()
                    with tarfile.open(task_dir / "tests/.repotrials-base.tar") as archive:
                        archive.extractall(workspace, filter="data")
                    return workspace

                def submission_patch(name: str, changes: dict[str, str | None]) -> Path:
                    agent_workspace = verifier_workspace(name + "-agent")
                    git(agent_workspace, "init", "--initial-branch=trial")
                    git(agent_workspace, "config", "user.name", "Fixture")
                    git(agent_workspace, "config", "user.email", "fixture@example.invalid")
                    git(agent_workspace, "add", "--all")
                    git(agent_workspace, "commit", "-m", "Sealed baseline")
                    for relative, content in changes.items():
                        path = agent_workspace / relative
                        if content is None:
                            path.unlink()
                        else:
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_text(content, encoding="utf-8")
                    git(agent_workspace, "add", "--all")
                    completed = subprocess.run(
                        (
                            "git",
                            "diff",
                            "--cached",
                            "--binary",
                            "--full-index",
                            "HEAD",
                            "--",
                        ),
                        cwd=agent_workspace,
                        capture_output=True,
                        check=True,
                    )
                    patch = Path(raw) / f"{name}.patch"
                    patch.write_bytes(completed.stdout)
                    return patch

                def grade(workspace: Path, patch: Path) -> subprocess.CompletedProcess[str]:
                    environment = dict(os.environ)
                    environment.update(
                        {
                            "REPOTRIALS_VERIFIER_WORKSPACE": str(workspace),
                            "REPOTRIALS_VERIFIER_TESTS": str(task_dir / "tests"),
                            "REPOTRIALS_VERIFIER_LOGS": str(Path(raw) / "harbor-logs"),
                            "REPOTRIALS_VERIFIER_PATCH": str(patch),
                            "REPOTRIALS_VERIFIER_DISABLE_PRIVDROP": "1",
                        }
                    )
                    return subprocess.run(
                        (sys.executable, str(task_dir / "tests/grader.py")),
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=60,
                        check=False,
                    )

                no_op_patch = Path(raw) / "no-op.patch"
                no_op_patch.write_bytes(b"")
                no_op = grade(verifier_workspace("harbor-noop"), no_op_patch)
                self.assertNotEqual(no_op.returncode, 0)

                fixed_content = (
                    "def divide(left, right):\n"
                    "    if right == 0:\n"
                    "        return None\n"
                    "    return left / right\n"
                )
                fixed_patch = submission_patch("fixed", {"calc.py": fixed_content})
                grader = grade(verifier_workspace("harbor-fixed"), fixed_patch)
                self.assertEqual(grader.returncode, 0, grader.stderr)

                deleted_workspace = verifier_workspace("harbor-deleted")
                deleted = grade(
                    deleted_workspace,
                    submission_patch("deleted", {"calc.py": None}),
                )
                self.assertNotEqual(deleted.returncode, 0)
                self.assertFalse((deleted_workspace / "calc.py").exists())

                outside = grade(
                    verifier_workspace("harbor-outside"),
                    submission_patch("outside", {"rogue.py": "SECRET = True\n"}),
                )
                self.assertNotEqual(outside.returncode, 0)
                self.assertIn("outside its allowlist", outside.stderr)


if __name__ == "__main__":
    unittest.main()
