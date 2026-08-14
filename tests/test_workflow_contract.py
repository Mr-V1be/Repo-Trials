from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repotrials.config import initialize_project, load_config
from repotrials.execution import DockerCommandBackend
from repotrials.validation import IntegrityReport, ValidationReport, ValidationRunner
from repotrials.workflow import RepoTrialsWorkflow, WorkflowError, _task_digest


def _git(root: Path, *arguments: str) -> str:
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


def _directory_symlink(test: unittest.TestCase, target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        test.skipTest(f"directory symlinks are unavailable: {exc}")


class FrozenContractWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repository"
        self.root.mkdir()
        _git(self.root, "init", "--initial-branch=main")
        _git(self.root, "config", "user.name", "Fixture")
        _git(self.root, "config", "user.email", "fixture@example.invalid")
        _git(self.root, "config", "core.autocrlf", "false")
        (self.root / "tests").mkdir()
        (self.root / "calc.py").write_text(
            "def divide(left, right):\n    return left / right\n",
            encoding="utf-8",
        )
        (self.root / "tests/test_calc.py").write_text(
            "from calc import divide\n\ndef test_division():\n    assert divide(6, 3) == 2\n",
            encoding="utf-8",
        )
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-m", "Initial calculator")
        (self.root / "calc.py").write_text(
            "def divide(left, right):\n"
            "    if right == 0:\n"
            "        return None\n"
            "    return left / right\n",
            encoding="utf-8",
        )
        with (self.root / "tests/test_calc.py").open("a", encoding="utf-8") as handle:
            handle.write("\ndef test_division_by_zero():\n    assert divide(1, 0) is None\n")
        _git(
            self.root,
            "add",
            ".",
        )
        _git(
            self.root,
            "commit",
            "-m",
            "Fix division by zero regression with a focused test case",
        )

        initialize_project(self.root)
        config = load_config(self.root)
        config = dataclasses.replace(
            config,
            validation=dataclasses.replace(config.validation, repeats=1),
            execution=dataclasses.replace(config.execution, attempts=1),
        )
        self.workflow = RepoTrialsWorkflow(config)
        self.workflow.mine(limit=10)
        self.candidate_id = self.workflow.store.list_candidates()[0].id

    def tearDown(self) -> None:
        self.workflow.close()
        self.temporary.cleanup()

    def test_docker_validation_targets_frozen_linux_amd64_platform(self) -> None:
        with mock.patch.object(
            DockerCommandBackend,
            "available",
            new_callable=mock.PropertyMock,
            return_value=True,
        ):
            backend = self.workflow._validation_backend("docker")

        self.assertIsInstance(backend, DockerCommandBackend)
        self.assertEqual(backend.platform_name, "linux/amd64")

    @staticmethod
    def _valid_report() -> ValidationReport:
        return ValidationReport(
            runs=(),
            integrity=IntegrityReport(
                paths=("calc.py", "tests/test_calc.py"),
                violations=(),
                patch_bytes=200,
            ),
            reasons=(),
            fail_to_pass=("tests.test_calc::test_division_by_zero",),
            pass_to_pass=("tests.test_calc::test_division",),
        )

    @staticmethod
    def _invalid_report() -> ValidationReport:
        return ValidationReport(
            runs=(),
            integrity=IntegrityReport(paths=(), violations=(), patch_bytes=0),
            reasons=("forced_invalid",),
        )

    def _validate(self, report: ValidationReport, *, accept: bool) -> dict[str, object]:
        with mock.patch.object(ValidationRunner, "validate", return_value=report):
            outcomes = self.workflow.validate_candidates(
                (self.candidate_id,),
                repeats=1,
                accept=accept,
                allow_unsafe_local=True,
            )
        self.assertEqual(len(outcomes), 1)
        return outcomes[0]

    def _index_ids(self) -> list[str]:
        path = self.root / ".repotrials/tasks/index.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [str(item["task_id"]) for item in payload["tasks"]]

    def test_local_validation_requires_opt_in_before_private_artifacts(self) -> None:
        with self.assertRaisesRegex(WorkflowError, "local validation executes"):
            self.workflow.validate_candidates((self.candidate_id,), repeats=1)

        object_root = self.root / ".repotrials/objects/sha256"
        self.assertEqual([path for path in object_root.rglob("*") if path.is_file()], [])
        self.assertEqual(self.workflow.store.list_tasks(), [])

    def test_only_accepted_tasks_materialize_and_rejection_removes_every_manifest(self) -> None:
        outcome = self._validate(self._valid_report(), accept=False)
        task_id = str(outcome["task_id"])
        self.assertFalse((self.root / ".repotrials/tasks" / task_id).exists())
        self.assertFalse((self.root / ".repotrials/private" / f"{task_id}.json").exists())
        self.assertEqual(self._index_ids(), [])

        self.workflow.set_task_tier((task_id,), "auto")
        self.assertTrue((self.root / ".repotrials/tasks" / task_id / "public.json").is_file())
        self.assertTrue((self.root / ".repotrials/private" / f"{task_id}.json").is_file())
        self.assertEqual(self._index_ids(), [task_id])

        self.workflow.set_task_tier((task_id,), "rejected")
        self.assertFalse((self.root / ".repotrials/tasks" / task_id).exists())
        self.assertFalse((self.root / ".repotrials/private" / f"{task_id}.json").exists())
        self.assertEqual(self._index_ids(), [])
        stored = self.workflow.store.get_task(task_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertFalse(stored.metadata["accepted"])
        self.assertEqual(stored.metadata["tier"], "rejected")

    def test_duplicate_candidate_and_task_selectors_are_rejected_before_work(self) -> None:
        with self.assertRaisesRegex(WorkflowError, "duplicate candidate selector"):
            self.workflow.validate_candidates(
                (self.candidate_id, self.candidate_id), allow_unsafe_local=True
            )

        outcome = self._validate(self._valid_report(), accept=True)
        task_id = str(outcome["task_id"])
        with self.assertRaisesRegex(WorkflowError, "duplicate task selector"):
            self.workflow.export_harbor(
                self.root / "duplicate-export",
                task_ids=(task_id, task_id),
            )
        self.assertFalse((self.root / "duplicate-export").exists())

    def test_run_group_is_materialized_before_attempts_and_interruption_leaves_it_running(
        self,
    ) -> None:
        outcome = self._validate(self._valid_report(), accept=True)
        task_id = str(outcome["task_id"])
        observed: dict[str, object] = {}

        def interrupt(_task: object, **arguments: object) -> object:
            run_group = str(arguments["run_group"])
            manifest_path = self.root / ".repotrials/runs" / run_group / "group.json"
            observed.update(json.loads(manifest_path.read_text(encoding="utf-8")))
            raise RuntimeError("simulated interruption")

        with (
            mock.patch.object(self.workflow, "_run_one", side_effect=interrupt),
            self.assertRaisesRegex(RuntimeError, "simulated interruption"),
        ):
            self.workflow.run_agent(
                agent_command="unused",
                name="interrupted-agent",
                attempts=1,
                task_ids=(task_id,),
                allow_unsafe_local=True,
            )

        self.assertEqual(observed["schema_version"], "repotrials.run-group/v1")
        self.assertEqual(observed["status"], "running")
        self.assertEqual(observed["task_ids"], [task_id])
        self.assertEqual(observed["attempts"], 1)
        self.assertEqual(observed["expected_trial_count"], 1)
        self.assertNotIn("run_ids", observed)
        manifest_path = self.root / ".repotrials/runs" / str(observed["run_group"]) / "group.json"
        persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["status"], "running")
        self.assertNotIn("completed_at", persisted)

    def test_invalid_agent_identity_or_command_writes_no_run_group(self) -> None:
        outcome = self._validate(self._valid_report(), accept=True)
        task_id = str(outcome["task_id"])
        cases = (
            ({"agent_command": "python -V", "name": " \t "}, "agent name"),
            ({"agent_command": " \t ", "name": "agent"}, "agent command"),
        )
        for arguments, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(WorkflowError, message):
                self.workflow.run_agent(
                    **arguments,
                    attempts=1,
                    task_ids=(task_id,),
                    allow_unsafe_local=True,
                )

        runs_root = self.root / ".repotrials/runs"
        self.assertEqual(list(runs_root.iterdir()), [])
        self.assertEqual(self.workflow.store.list_runs(), [])

    def test_task_materialization_rejects_preseeded_symlink_before_acceptance(self) -> None:
        outcome = self._validate(self._valid_report(), accept=False)
        task_id = str(outcome["task_id"])
        sink = Path(self.temporary.name) / "task-sink"
        sink.mkdir()
        marker = sink / "keep.txt"
        marker.write_text("unchanged", encoding="utf-8")
        _directory_symlink(self, sink, self.root / ".repotrials/tasks" / task_id)

        with self.assertRaisesRegex(WorkflowError, "unsafe task (index|state) path"):
            self.workflow.set_task_tier((task_id,), "auto")

        stored = self.workflow.store.get_task(task_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertFalse(stored.metadata["accepted"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
        self.assertEqual({path.name for path in sink.iterdir()}, {"keep.txt"})

    def test_run_group_manifest_rejects_preseeded_symlink_before_execution(self) -> None:
        outcome = self._validate(self._valid_report(), accept=True)
        task_id = str(outcome["task_id"])
        sink = Path(self.temporary.name) / "run-sink"
        sink.mkdir()
        marker = sink / "keep.txt"
        marker.write_text("unchanged", encoding="utf-8")
        group = "preseeded-group"
        _directory_symlink(self, sink, self.root / ".repotrials/runs" / group)

        with (
            mock.patch("repotrials.workflow._run_group", return_value=group),
            self.assertRaisesRegex(WorkflowError, "unsafe run group path"),
        ):
            self.workflow.run_agent(
                agent_command="unused",
                name="blocked-agent",
                attempts=1,
                task_ids=(task_id,),
                allow_unsafe_local=True,
            )

        self.assertEqual(self.workflow.store.list_runs(), [])
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
        self.assertEqual({path.name for path in sink.iterdir()}, {"keep.txt"})

    def test_invalid_revalidation_dematerializes_current_and_superseded_tasks(self) -> None:
        accepted = self._validate(self._valid_report(), accept=True)
        accepted_id = str(accepted["task_id"])
        self.assertTrue((self.root / ".repotrials/tasks" / accepted_id).is_dir())

        rejected = self._validate(self._invalid_report(), accept=True)
        rejected_id = str(rejected["task_id"])
        self.assertNotEqual(accepted_id, rejected_id)
        self.assertEqual(self._index_ids(), [])
        for task_id in (accepted_id, rejected_id):
            self.assertFalse((self.root / ".repotrials/tasks" / task_id).exists())
            self.assertFalse((self.root / ".repotrials/private" / f"{task_id}.json").exists())
        old_task = self.workflow.store.get_task(accepted_id)
        self.assertIsNotNone(old_task)
        assert old_task is not None
        self.assertFalse(old_task.metadata["accepted"])
        self.assertEqual(old_task.metadata["superseded_by"], rejected_id)

    def test_prompt_and_contract_revisions_change_identity_but_review_does_not(self) -> None:
        first = self._validate(self._valid_report(), accept=True)
        first_id = str(first["task_id"])
        first_task = self.workflow.store.get_task(first_id)
        self.assertIsNotNone(first_task)
        assert first_task is not None
        digest_before_review = _task_digest(first_task)

        self.workflow.set_task_tier((first_id,), "verified")
        reviewed = self.workflow.store.get_task(first_id)
        self.assertIsNotNone(reviewed)
        assert reviewed is not None
        self.assertEqual(_task_digest(reviewed), digest_before_review)

        candidate = self.workflow.store.get_candidate(self.candidate_id)
        self.assertIsNotNone(candidate)
        assert candidate is not None
        metadata = dict(candidate.metadata)
        metadata.update(
            {
                "issue_title": "Division by zero produces an exception instead of a result",
                "issue_body": "Calling divide with a zero denominator should return None.",
            }
        )
        self.workflow.store.save_candidate(dataclasses.replace(candidate, metadata=metadata))
        prompt_revision = self._validate(self._valid_report(), accept=True)
        self.assertNotEqual(str(prompt_revision["task_id"]), first_id)
        with self.assertRaisesRegex(WorkflowError, "was superseded.*cannot be accepted"):
            self.workflow.set_task_tier((first_id,), "verified")
        accepted_ids = {
            task.id for task in self.workflow.store.list_tasks() if task.metadata.get("accepted")
        }
        self.assertEqual(accepted_ids, {str(prompt_revision["task_id"])})

        previous_id = str(prompt_revision["task_id"])
        self.workflow.config = dataclasses.replace(
            self.workflow.config,
            execution=dataclasses.replace(
                self.workflow.config.execution,
                timeout_seconds=self.workflow.config.execution.timeout_seconds + 1,
            ),
        )
        contract_revision = self._validate(self._valid_report(), accept=True)
        self.assertNotEqual(str(contract_revision["task_id"]), previous_id)

    def test_export_uses_frozen_contract_and_legacy_tasks_require_revalidation(self) -> None:
        outcome = self._validate(self._valid_report(), accept=True)
        task_id = str(outcome["task_id"])
        task = self.workflow.store.get_task(task_id)
        self.assertIsNotNone(task)
        assert task is not None
        frozen = dict(task.metadata["contract"])

        self.workflow.config = dataclasses.replace(
            self.workflow.config,
            test=dataclasses.replace(
                self.workflow.config.test,
                command="python -c 'raise SystemExit(99)' {junit}",
                setup=("changed setup",),
                protected_paths=("changed/**",),
            ),
            validation=dataclasses.replace(
                self.workflow.config.validation,
                docker_image="changed.invalid/image:latest",
                timeout_seconds=1,
            ),
            execution=dataclasses.replace(
                self.workflow.config.execution,
                network="public",
                timeout_seconds=1,
                cpus=8,
                memory_mb=128,
            ),
        )
        captured: list[object] = []

        def capture(_exporter: object, spec: object) -> Path:
            captured.append(spec)
            return self.root / "captured" / task_id

        with (
            mock.patch(
                "repotrials.workflow.HarborExporter.export",
                autospec=True,
                side_effect=capture,
            ),
            mock.patch("repotrials.workflow.validate_harbor_task", return_value=[]),
        ):
            self.workflow.export_harbor(self.root / "export", task_ids=(task_id,))

        self.assertEqual(len(captured), 1)
        spec = captured[0]
        self.assertEqual(spec.agent_base_image, frozen["validation_image"])
        self.assertEqual(spec.verifier_base_image, frozen["validation_image"])
        self.assertEqual(spec.setup_commands, tuple(frozen["setup_commands"]))
        self.assertEqual(spec.agent_timeout_sec, frozen["execution_timeout_seconds"])
        phase_timeout = frozen["validation_timeout_seconds"]
        self.assertEqual(
            spec.verifier_timeout_sec,
            (len(frozen["setup_commands"]) + 1) * phase_timeout + 60,
        )
        self.assertEqual(spec.setup_timeout_sec, phase_timeout)
        self.assertEqual(
            spec.build_timeout_sec,
            max(600, len(frozen["setup_commands"]) * phase_timeout + 300),
        )
        self.assertEqual(spec.memory_mb, frozen["execution_memory_mb"])
        self.assertEqual(spec.network_mode, "allowlist")
        self.assertEqual(spec.submission_paths, ("calc.py",))
        self.assertEqual(spec.artifact_paths, ("/tmp/agent.patch",))
        self.assertEqual(spec.max_patch_bytes, frozen["max_patch_bytes"])

        metadata = dict(task.metadata)
        metadata.pop("contract")
        metadata.pop("contract_digest")
        self.workflow.store.save_task(dataclasses.replace(task, metadata=metadata))
        with self.assertRaisesRegex(WorkflowError, "predates frozen contracts.*revalidate"):
            self.workflow.export_harbor(self.root / "legacy", task_ids=(task_id,))

    def test_agent_deleting_synthetic_git_records_a_failed_run(self) -> None:
        outcome = self._validate(self._valid_report(), accept=True)
        task_id = str(outcome["task_id"])
        original_timeout = self.workflow.config.execution.timeout_seconds
        original_command = self.workflow.config.test.command
        self.workflow.config = dataclasses.replace(
            self.workflow.config,
            test=dataclasses.replace(
                self.workflow.config.test,
                command="python -c 'raise SystemExit(99)' {junit}",
                setup=("changed setup",),
            ),
            execution=dataclasses.replace(
                self.workflow.config.execution,
                timeout_seconds=1,
            ),
        )
        script = Path(self.temporary.name) / "delete_git.py"
        script.write_text(
            "import shutil\nfrom pathlib import Path\nshutil.rmtree(Path('.git'))\n",
            encoding="utf-8",
        )

        result = self.workflow.run_agent(
            agent_command=f'python "{script.as_posix()}"',
            name="delete-git-agent",
            attempts=1,
            task_ids=(task_id,),
            allow_unsafe_local=True,
        )

        self.assertEqual(result["trials"], 1)
        self.assertEqual(result["resolved"], 0)
        runs = self.workflow.store.list_runs(task_id)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertFalse(run.passed)
        self.assertNotIn(
            run.metadata["failure_kind"],
            {"infrastructure", "submission_capture"},
        )
        profile = run.metadata["execution_profile"]
        self.assertEqual(profile["configured_backend"], "local")
        self.assertEqual(profile["effective_backend"], "local-command")
        self.assertEqual(profile["agent_timeout_seconds"], original_timeout)
        self.assertEqual(
            profile["test_command_sha256"],
            hashlib.sha256(original_command.encode("utf-8")).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
