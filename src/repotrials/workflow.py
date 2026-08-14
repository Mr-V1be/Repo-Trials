"""High-level RepoTrials workflow used by the CLI.

The module deliberately keeps policy decisions in one place: mining produces
candidates, validation proves executable red/green behavior, review promotes a
task, and execution never receives private oracle artifacts.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import platform
import shlex
import shutil
import tempfile
import time
import uuid
import webbrowser
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .config import RepoTrialsConfig, ensure_project_state
from .execution import (
    CommandBackend,
    CommandResult,
    DockerCommandBackend,
    LocalCommandBackend,
)
from .git import GitRepository
from .github import GitHubClient, GitHubError, discover_github_slug
from .harbor import (
    AGENT_PATCH_PATH,
    HarborExporter,
    HarborTaskSpec,
    validate_harbor_task,
)
from .junit import (
    PASSING_TEST_STATUSES,
    SUITE_HEALTHY_TEST_STATUSES,
    JUnitParseError,
    parse_junit_xml,
)
from .mining import Miner, MiningConfig
from .models import Candidate, Run, Task, Validation, utc_now
from .prompts import added_lines_from_patch, build_prompt
from .reporting import write_report_bundle
from .sandbox import (
    SandboxError,
    apply_patch,
    collect_submission_patch,
    collect_submission_paths,
    command_digest,
    initialize_synthetic_git,
    run_agent_command,
    safe_extract_tar,
)
from .scoring import TrialScore, aggregate_scores, score_trial
from .state_paths import (
    StatePathError,
    absolute_path,
    ensure_managed_directory,
    managed_component,
    managed_path,
    prepare_managed_file,
    prepare_managed_files,
)
from .storage import StateStore
from .validation import (
    ValidationPhase,
    ValidationPlan,
    ValidationReport,
    ValidationRunner,
    check_patch_integrity,
    find_setup_mutation,
    snapshot_workspace,
)
from .vault import ContentAddressedVault


class WorkflowError(RuntimeError):
    """Raised for user-facing workflow failures."""


def _junit_failure_kind(
    outcomes: Mapping[str, str],
    expected: Iterable[str],
    *,
    command_ok: bool,
) -> str | None:
    required = set(expected)
    if required - set(outcomes):
        return "expected_tests_missing"
    if any(outcomes[name] not in PASSING_TEST_STATUSES for name in required):
        return "tests_failed"
    if any(status not in SUITE_HEALTHY_TEST_STATUSES for status in outcomes.values()):
        return "tests_failed"
    if not command_ok:
        return "tests_failed"
    return None


@dataclasses.dataclass(frozen=True, slots=True)
class TaskContract:
    """Immutable execution and verification inputs bound to a task ID.

    Accepted tasks must never inherit later edits to ``repotrials.toml``.  This
    record freezes every setting used to validate, run, or export a task; its
    canonical SHA-256 digest is part of the task identity.
    """

    schema_version: str
    profile: str
    instruction_sha256: str
    test_command: str
    setup_commands: tuple[str, ...]
    source_globs: tuple[str, ...]
    test_globs: tuple[str, ...]
    ignored_globs: tuple[str, ...]
    protected_paths: tuple[str, ...]
    require_pass_to_pass: bool
    validation_backend: str
    validation_image: str
    validation_repetitions: int
    validation_timeout_seconds: int
    max_changed_files: int
    max_patch_bytes: int
    execution_backend: str
    execution_network: str
    execution_timeout_seconds: int
    execution_attempts: int
    execution_cpus: float
    execution_memory_mb: int
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject malformed contracts even when constructed outside deserialization."""

        self._validate()

    @classmethod
    def from_config(
        cls,
        config: RepoTrialsConfig,
        *,
        instruction: str,
        validation_backend: str,
        repetitions: int,
        fail_to_pass: Sequence[str] = (),
        pass_to_pass: Sequence[str] = (),
    ) -> TaskContract:
        """Freeze the effective configuration used by one validation."""

        return cls(
            schema_version="repotrials.contract/v1",
            profile="python-pytest/v1",
            instruction_sha256=hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
            test_command=config.test.command,
            setup_commands=tuple(config.test.setup),
            source_globs=tuple(config.test.source_globs),
            test_globs=tuple(config.test.test_globs),
            ignored_globs=tuple(config.test.ignored_globs),
            protected_paths=tuple(config.test.protected_paths),
            require_pass_to_pass=config.validation.require_pass_to_pass,
            validation_backend=validation_backend,
            validation_image=config.validation.docker_image,
            validation_repetitions=repetitions,
            validation_timeout_seconds=config.validation.timeout_seconds,
            max_changed_files=config.mining.max_files,
            max_patch_bytes=_max_patch_bytes(config),
            execution_backend=config.execution.backend,
            execution_network=config.execution.network,
            execution_timeout_seconds=config.execution.timeout_seconds,
            execution_attempts=config.execution.attempts,
            execution_cpus=config.execution.cpus,
            execution_memory_mb=config.execution.memory_mb,
            fail_to_pass=tuple(fail_to_pass),
            pass_to_pass=tuple(pass_to_pass),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TaskContract:
        """Parse and strictly validate a stored canonical contract."""

        if not isinstance(value, Mapping):
            raise ValueError("task contract must be an object")
        allowed = {field.name for field in dataclasses.fields(cls)}
        unknown = set(value) - allowed
        missing = allowed - set(value)
        if unknown or missing:
            detail: list[str] = []
            if missing:
                detail.append("missing " + ", ".join(sorted(missing)))
            if unknown:
                detail.append("unknown " + ", ".join(sorted(str(item) for item in unknown)))
            raise ValueError("invalid task contract fields: " + "; ".join(detail))

        tuple_fields = {
            "setup_commands",
            "source_globs",
            "test_globs",
            "ignored_globs",
            "protected_paths",
            "fail_to_pass",
            "pass_to_pass",
        }
        data = dict(value)
        for name in tuple_fields:
            raw = data[name]
            if isinstance(raw, str) or not isinstance(raw, list | tuple):
                raise ValueError(f"task contract {name} must be an array")
            if not all(isinstance(item, str) and item for item in raw):
                raise ValueError(f"task contract {name} must contain non-empty strings")
            data[name] = tuple(raw)
        try:
            contract = cls(**data)
        except TypeError as exc:
            raise ValueError(f"invalid task contract: {exc}") from exc
        return contract

    def _validate(self) -> None:
        if self.schema_version != "repotrials.contract/v1":
            raise ValueError(f"unsupported task contract: {self.schema_version!r}")
        if self.profile != "python-pytest/v1":
            raise ValueError(f"unsupported task profile: {self.profile!r}")
        if (
            not isinstance(self.instruction_sha256, str)
            or len(self.instruction_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.instruction_sha256)
        ):
            raise ValueError("task contract instruction_sha256 is invalid")
        for name in (
            "setup_commands",
            "source_globs",
            "test_globs",
            "ignored_globs",
            "protected_paths",
            "fail_to_pass",
            "pass_to_pass",
        ):
            current = getattr(self, name)
            if not isinstance(current, tuple) or not all(
                isinstance(item, str) and item for item in current
            ):
                raise ValueError(f"task contract {name} must contain non-empty strings")
        for name in (
            "test_command",
            "validation_backend",
            "validation_image",
            "execution_backend",
            "execution_network",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"task contract {name} must be a non-empty string")
        if not isinstance(self.require_pass_to_pass, bool):
            raise ValueError("task contract require_pass_to_pass must be boolean")
        for name in (
            "validation_repetitions",
            "validation_timeout_seconds",
            "max_changed_files",
            "max_patch_bytes",
            "execution_timeout_seconds",
            "execution_attempts",
            "execution_memory_mb",
        ):
            current = getattr(self, name)
            if isinstance(current, bool) or not isinstance(current, int) or current < 1:
                raise ValueError(f"task contract {name} must be a positive integer")
        if (
            isinstance(self.execution_cpus, bool)
            or not isinstance(self.execution_cpus, int | float)
            or not math.isfinite(float(self.execution_cpus))
            or self.execution_cpus <= 0
        ):
            raise ValueError("task contract execution_cpus must be finite and positive")
        if self.validation_backend not in {"local", "docker"}:
            raise ValueError("task contract validation_backend is unsupported")
        if self.execution_backend not in {"local", "harbor"}:
            raise ValueError("task contract execution_backend is unsupported")
        if self.execution_network not in {"none", "provider-only", "public"}:
            raise ValueError("task contract execution_network is unsupported")

    def to_dict(self) -> dict[str, Any]:
        self._validate()
        value = dataclasses.asdict(self)
        for name in (
            "setup_commands",
            "source_globs",
            "test_globs",
            "ignored_globs",
            "protected_paths",
            "fail_to_pass",
            "pass_to_pass",
        ):
            value[name] = list(value[name])
        return value

    @property
    def digest(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class RepoTrialsWorkflow:
    """Facade that coordinates mining, storage, validation, and trials."""

    def __init__(self, config: RepoTrialsConfig) -> None:
        self.config = config
        self.repository = GitRepository.discover(config.root)
        try:
            state_dir = ensure_project_state(config.root)
        except StatePathError as exc:
            raise WorkflowError(f"unsafe private state path: {exc}") from exc
        self.store = StateStore(state_dir)
        self.vault = ContentAddressedVault(state_dir)

    def close(self) -> None:
        """Flush and close local state, which is required before Windows cleanup."""

        self.store.close()

    def __enter__(self) -> RepoTrialsWorkflow:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def mine(
        self,
        *,
        ref: str = "HEAD",
        since: str | None = None,
        limit: int | None = None,
        github: bool = False,
    ) -> dict[str, Any]:
        mining = self.config.mining
        miner = Miner(
            self.repository,
            MiningConfig(
                max_files=mining.max_files,
                max_changed_lines=mining.max_changed_lines,
                max_commits=max(1_000, limit or 0),
                require_source=True,
                require_tests=mining.require_test_changes,
                include_merges=mining.include_merges,
                source_globs=self.config.test.source_globs,
                test_globs=self.config.test.test_globs,
                ignored_globs=self.config.test.ignored_globs,
                keyword_pattern=mining.keyword_pattern,
            ),
        )
        candidates = miner.mine(ref, since=since, limit=limit)
        github_errors: list[str] = []
        if github and candidates:
            candidates, github_errors = self._enrich_with_github(candidates)

        existing = {item.id for item in self.store.list_candidates()}
        for candidate in candidates:
            self.store.save_candidate(candidate)
        return {
            "stored": len(candidates),
            "new": sum(candidate.id not in existing for candidate in candidates),
            "github_errors": github_errors,
            "repository": str(self.repository.path),
            "ref": self.repository.rev_parse(ref),
        }

    def list_candidates(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.store.list_candidates()[:limit]]

    def validate_candidates(
        self,
        candidate_ids: Sequence[str] | None = None,
        *,
        repeats: int | None = None,
        backend_name: str | None = None,
        accept: bool = False,
        allow_unsafe_local: bool = False,
    ) -> list[dict[str, Any]]:
        effective_backend_name = backend_name or self.config.validation.backend
        candidates = self._select_candidates(candidate_ids)
        if not candidates:
            raise WorkflowError("no candidates selected; run `repotrials mine` first")
        repetition_count = repeats if repeats is not None else self.config.validation.repeats
        if repetition_count < 1:
            raise WorkflowError("validation repetitions must be at least one")
        if effective_backend_name == "local" and not allow_unsafe_local:
            raise WorkflowError(
                "local validation executes historical repository setup and tests with host "
                "permissions; pass the explicit unsafe-local opt-in or use `--backend docker`"
            )
        backend = self._validation_backend(effective_backend_name)
        outcomes: list[dict[str, Any]] = []

        for candidate in candidates:
            started = time.monotonic()
            gold_patch = self.repository.diff(
                candidate.parent_sha,
                candidate.commit_sha,
                paths=candidate.source_files,
            )
            test_patch = self.repository.diff(
                candidate.parent_sha,
                candidate.commit_sha,
                paths=candidate.test_files,
            )
            assessment = build_prompt(
                issue_title=str(candidate.metadata.get("issue_title", "")),
                issue_body=str(candidate.metadata.get("issue_body", "")),
                pr_title=str(candidate.metadata.get("pr_title", "")),
                pr_body=str(candidate.metadata.get("pr_body", "")),
                commit_title=candidate.title,
                commit_body=candidate.message,
                added_code_lines=added_lines_from_patch(
                    gold_patch.decode("utf-8", errors="replace")
                ),
            )
            archive = self.repository.archive(candidate.parent_sha)
            gold_oid = self.store.put_object(gold_patch)
            test_oid = self.store.put_object(test_patch)
            archive_oid = self.store.put_object(archive)
            validation_contract = TaskContract.from_config(
                self.config,
                instruction=assessment.text,
                validation_backend=effective_backend_name,
                repetitions=repetition_count,
            )

            with tempfile.TemporaryDirectory(prefix="repotrials-base-") as raw:
                base_dir = safe_extract_tar(archive, Path(raw) / "repo")
                plan = ValidationPlan(
                    base_dir=base_dir,
                    test_command=validation_contract.test_command,
                    test_patch=test_patch,
                    gold_patch=gold_patch,
                    setup_commands=validation_contract.setup_commands,
                    repetitions=validation_contract.validation_repetitions,
                    protected_paths=validation_contract.protected_paths,
                    test_path_patterns=validation_contract.test_globs,
                    timeout=float(validation_contract.validation_timeout_seconds),
                    require_pass_to_pass=validation_contract.require_pass_to_pass,
                    max_changed_files=validation_contract.max_changed_files,
                    max_patch_bytes=validation_contract.max_patch_bytes,
                )
                report = ValidationRunner(backend).validate(plan)

            contract = dataclasses.replace(
                validation_contract,
                fail_to_pass=tuple(report.fail_to_pass),
                pass_to_pass=tuple(report.pass_to_pass),
            )
            task_id = _task_id(
                candidate,
                gold_oid,
                test_oid,
                archive_oid,
                contract.digest,
            )
            tier = "auto" if report.valid else "rejected"
            accepted = bool(accept and report.valid and assessment.risk != "high")
            task = Task(
                id=task_id,
                candidate_id=candidate.id,
                base_sha=candidate.parent_sha,
                fix_sha=candidate.commit_sha,
                instruction=assessment.text,
                repository=candidate.repository,
                gold_patch_oid=gold_oid,
                test_patch_oid=test_oid,
                source_files=candidate.source_files,
                test_files=candidate.test_files,
                metadata={
                    "schema_version": "repotrials.task/v1",
                    "base_archive_oid": archive_oid,
                    "contract": contract.to_dict(),
                    "contract_digest": contract.digest,
                    "tier": tier,
                    "accepted": accepted,
                    "exposure": self.config.repository.exposure,
                    "prompt_source": assessment.source,
                    "prompt_risk": assessment.risk,
                    "prompt_findings": list(assessment.findings),
                    "fail_to_pass": list(report.fail_to_pass),
                    "pass_to_pass": list(report.pass_to_pass),
                    "reconstruction_method": (
                        "merge_first_parent" if candidate.is_merge else "single_parent"
                    ),
                },
            )
            validation = self._validation_model(
                task,
                report,
                repetition_count,
                time.monotonic() - started,
            )
            self._preflight_task_state(task.id)
            self._task_public_paths(prepare_index=False)
            self.store.save_task(task)
            self.store.save_validation(validation)
            self._supersede_candidate_tasks(candidate.id, task.id)
            if report.valid and accepted:
                self._materialize_task(task, validation, candidate)
            else:
                self._dematerialize_task(task.id, rebuild_index=True)
            outcomes.append(
                {
                    "candidate_id": candidate.id,
                    "task_id": task.id,
                    "valid": report.valid,
                    "accepted": accepted,
                    "tier": tier,
                    "reasons": list(report.reasons),
                    "fail_to_pass": list(report.fail_to_pass),
                    "pass_to_pass": list(report.pass_to_pass),
                    "prompt_risk": assessment.risk,
                }
            )
        return outcomes

    def list_tasks(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for task in self.store.list_tasks():
            item = task.to_dict()
            item["tier"] = str(task.metadata.get("tier", "pending"))
            item["accepted"] = bool(task.metadata.get("accepted", False))
            validation = self.store.get_validation(task.id)
            item["validation"] = validation.to_dict() if validation else None
            result.append(item)
        return result

    def set_task_tier(self, task_ids: Sequence[str], tier: str) -> dict[str, int]:
        if tier not in {"auto", "verified", "rejected"}:
            raise ValueError("tier must be auto, verified, or rejected")
        _reject_duplicate_selectors(task_ids, label="task")
        updated = 0
        for task_id in task_ids:
            task = self.store.get_task(task_id)
            if task is None:
                raise WorkflowError(f"unknown task: {task_id}")
            validation = self.store.get_validation(task_id)
            if tier != "rejected" and (validation is None or not validation.passed):
                raise WorkflowError(f"task {task_id} has not passed automatic validation")
            if tier != "rejected":
                self._require_current_task_revision(task)
                _contract_from_task(task)
            metadata = dict(task.metadata)
            metadata["tier"] = tier
            metadata["accepted"] = tier != "rejected"
            metadata["reviewed_at"] = utc_now()
            updated_task = dataclasses.replace(task, metadata=metadata)
            self._preflight_task_state(task.id)
            self._task_public_paths(prepare_index=False)
            self.store.save_task(updated_task)
            if tier == "rejected":
                self._dematerialize_task(task.id, rebuild_index=True)
            elif validation is not None:
                candidate = self.store.get_candidate(task.candidate_id)
                if candidate is None:
                    raise WorkflowError(
                        f"task {task.id} has no candidate provenance; re-run mining"
                    )
                self._materialize_task(updated_task, validation, candidate)
            updated += 1
        return {"updated": updated}

    def accept_all_auto(self) -> dict[str, int]:
        ids = [
            task.id
            for task in self.store.list_tasks()
            if task.metadata.get("tier") == "auto"
            and not task.metadata.get("accepted")
            and (validation := self.store.get_validation(task.id)) is not None
            and validation.passed
            and task.metadata.get("prompt_risk") != "high"
        ]
        if not ids:
            return {"updated": 0}
        return self.set_task_tier(ids, "auto")

    def export_harbor(
        self,
        output: Path,
        *,
        task_ids: Sequence[str] | None = None,
        agent_image: str | None = None,
        verifier_image: str | None = None,
    ) -> dict[str, Any]:
        tasks = self._accepted_tasks(task_ids)
        destination = output if output.is_absolute() else self.config.root / output
        destination = self._prepare_output_directory(destination)
        exporter = HarborExporter(destination)
        exported: list[str] = []

        for task in tasks:
            contract = _contract_from_task(task)
            image = contract.validation_image
            if agent_image is not None and agent_image != image:
                raise WorkflowError(
                    f"task {task.id} is frozen to image {image!r}; "
                    "change config and revalidate before overriding it"
                )
            if verifier_image is not None and verifier_image != image:
                raise WorkflowError(
                    f"task {task.id} is frozen to verifier image {image!r}; "
                    "change config and revalidate before overriding it"
                )
            verify_image = image
            network = {
                "none": "no-network",
                "provider-only": "allowlist",
                "public": "public",
            }[contract.execution_network]
            archive = self._task_object(task, "base_archive_oid")
            hidden_patch = self.store.get_object(task.test_patch_oid)
            grader = _render_harbor_grader(
                test_command=contract.test_command,
                setup_commands=contract.setup_commands,
                hidden_patch_name="hidden-tests.patch",
                expected_f2p=contract.fail_to_pass,
                expected_p2p=contract.pass_to_pass,
                protected_paths=contract.protected_paths,
                submission_paths=task.source_files,
                max_changed_files=contract.max_changed_files,
                max_patch_bytes=contract.max_patch_bytes,
                timeout_seconds=contract.validation_timeout_seconds,
            )
            spec = HarborTaskSpec(
                task_id=task.id,
                instruction=task.instruction,
                agent_base_image=image,
                verifier_base_image=verify_image,
                base_archive=archive,
                setup_commands=contract.setup_commands,
                verifier_files={
                    "grader.py": grader,
                    "hidden-tests.patch": hidden_patch,
                },
                metadata={
                    "repotrials_version": __version__,
                    "task_digest": _task_digest(task),
                    "contract_digest": contract.digest,
                    "quality_tier": task.metadata.get("tier", "auto"),
                    "exposure": task.metadata.get("exposure", "unknown"),
                },
                submission_paths=task.source_files,
                max_patch_bytes=contract.max_patch_bytes,
                collect_timeout_sec=float(min(60, contract.validation_timeout_seconds)),
                agent_timeout_sec=float(contract.execution_timeout_seconds),
                verifier_timeout_sec=float(
                    (len(contract.setup_commands) + 1) * contract.validation_timeout_seconds + 60
                ),
                setup_timeout_sec=float(contract.validation_timeout_seconds),
                build_timeout_sec=float(
                    max(
                        600,
                        len(contract.setup_commands) * contract.validation_timeout_seconds + 300,
                    )
                ),
                cpus=max(1, math.ceil(contract.execution_cpus)),
                memory_mb=contract.execution_memory_mb,
                network_mode=network,
            )
            path = exporter.export(spec)
            errors = validate_harbor_task(path)
            if errors:
                raise WorkflowError(f"invalid Harbor export {path}: {', '.join(errors)}")
            exported.append(str(path))
        return {"count": len(exported), "output": str(destination), "tasks": exported}

    def run_agent(
        self,
        *,
        agent_command: str,
        name: str,
        model: str = "",
        attempts: int | None = None,
        task_ids: Sequence[str] | None = None,
        cost_usd: float | None = None,
        allow_unsafe_local: bool = False,
    ) -> dict[str, Any]:
        if not name.strip() or any(character in name for character in "\r\n\x00"):
            raise WorkflowError("agent name must be a non-empty single-line string")
        if not agent_command.strip() or "\x00" in agent_command:
            raise WorkflowError("agent command must be a non-empty non-NUL string")
        tasks = self._accepted_tasks(task_ids)
        contracts = [_contract_from_task(task) for task in tasks]
        unsupported = [
            task.id
            for task, contract in zip(tasks, contracts, strict=True)
            if contract.execution_backend != "local"
        ]
        if unsupported:
            raise WorkflowError(
                "task(s) are frozen for Harbor execution: "
                + ", ".join(unsupported)
                + "; use `repotrials export-harbor`"
            )
        if not allow_unsafe_local:
            raise WorkflowError(
                "local agent execution is not sandboxed and can access host files, "
                "credentials, and network; pass the explicit unsafe-local opt-in or "
                "use `repotrials export-harbor`"
            )
        frozen_attempts = {contract.execution_attempts for contract in contracts}
        if len(frozen_attempts) != 1:
            raise WorkflowError("selected tasks have incompatible frozen attempt budgets")
        attempt_count = attempts if attempts is not None else next(iter(frozen_attempts))
        if attempt_count != next(iter(frozen_attempts)):
            raise WorkflowError(
                f"selected tasks are frozen to {next(iter(frozen_attempts))} attempt(s); "
                "change config and revalidate before overriding the budget"
            )
        if cost_usd is not None and (not math.isfinite(cost_usd) or cost_usd < 0):
            raise WorkflowError("cost_usd must be finite and non-negative")
        run_group = _run_group(name)
        created_at = utc_now()
        group_manifest: dict[str, Any] = {
            "schema_version": "repotrials.run-group/v1",
            "run_group": run_group,
            "status": "running",
            "task_ids": [task.id for task in tasks],
            "task_digests": {task.id: _task_digest(task) for task in tasks},
            "task_contract_digests": {
                task.id: contract.digest for task, contract in zip(tasks, contracts, strict=True)
            },
            "attempts": attempt_count,
            "expected_trial_count": len(tasks) * attempt_count,
            "agent": name,
            "model": model or None,
            "created_at": created_at,
        }
        group_manifest_path = _run_group_manifest_path(
            self.config.state_dir, run_group, prepare=True
        )
        _write_json(group_manifest_path, group_manifest)
        runs: list[Run] = []
        for task in tasks:
            for attempt in range(1, attempt_count + 1):
                run = self._run_one(
                    task,
                    agent_command=agent_command,
                    name=name,
                    model=model,
                    attempt=attempt,
                    run_group=run_group,
                    cost_usd=cost_usd,
                )
                self.store.save_run(run)
                self._materialize_run(run)
                runs.append(run)
        _write_json(
            group_manifest_path,
            {
                **group_manifest,
                "status": "complete",
                "run_ids": [run.id for run in runs],
                "completed_at": utc_now(),
            },
        )
        summary = aggregate_scores(
            (self._score_run(run) for run in runs),
            bootstrap_samples=2_000,
            seed=0,
        )
        resolved_trials = sum(run.passed for run in runs)
        return {
            "run_group": run_group,
            "trials": len(runs),
            # Keep the CLI's resolved/trials pair attempt-level while exposing
            # the canonical task-level empirical pass@k explicitly.
            "resolved": resolved_trials,
            "resolved_trials": resolved_trials,
            "resolved_tasks": summary.resolved_tasks,
            "task_count": summary.total_tasks,
            "attempts": attempt_count,
            "trial_resolve_rate": resolved_trials / len(runs),
            "resolve_rate": summary.resolve_rate,
            "aggregation": {
                "method": summary.aggregation_method,
                "rule": summary.task_resolved_rule,
                "k": summary.k,
            },
            "run_ids": [run.id for run in runs],
        }

    def report(
        self,
        run_ids: Sequence[str] | None,
        output: Path,
        *,
        open_report: bool = False,
    ) -> dict[str, Any]:
        runs = self._select_runs(run_ids)
        if not runs:
            raise WorkflowError("no matching runs; execute `repotrials run` first")
        cohort = self._validated_run_cohort(runs, label="report")
        scores = [self._score_run(run) for run in runs]
        summary = aggregate_scores(scores, bootstrap_samples=2_000, seed=0)
        destination = output if output.is_absolute() else self.config.root / output
        destination = self._prepare_output_directory(
            destination,
            expected_files=("report.json", "report.html"),
        )
        bundle = write_report_bundle(
            destination,
            summary,
            scores,
            {
                "repotrials_version": __version__,
                "repository": str(self.repository.path),
                "generated_at": utc_now(),
                "run_group": cohort["run_group"],
                "task_digests": cohort["task_digests"],
                "task_contract_digests": cohort["task_contract_digests"],
                "execution_profile": cohort["execution_profile"],
                "execution_profile_sha256": cohort["execution_profile_sha256"],
                "aggregation": {
                    "method": summary.aggregation_method,
                    "rule": summary.task_resolved_rule,
                    "k": summary.k,
                    "unit": "task",
                },
            },
            title="RepoTrials — coding-agent evaluation",
        )
        if open_report:
            webbrowser.open(bundle.html_path.resolve().as_uri())
        return {
            "json": str(bundle.json_path),
            "html": str(bundle.html_path),
            "tasks": summary.total_tasks,
            "trials": summary.trial_count,
            "resolved": summary.resolved_tasks,
            "aggregation": {
                "method": summary.aggregation_method,
                "rule": summary.task_resolved_rule,
                "k": summary.k,
            },
            "resolve_rate": summary.resolve_rate,
            "confidence_interval": summary.confidence_interval.to_dict(),
        }

    def compare(
        self,
        baseline: str,
        candidate: str,
        *,
        fail_on_regression: float | None = None,
        output: Path | None = None,
    ) -> dict[str, Any]:
        baseline_runs = self._select_runs((baseline,))
        candidate_runs = self._select_runs((candidate,))
        if not baseline_runs:
            raise WorkflowError(f"no runs match baseline {baseline!r}")
        if not candidate_runs:
            raise WorkflowError(f"no runs match candidate {candidate!r}")
        baseline_cohort = self._validated_run_cohort(baseline_runs, label="baseline")
        candidate_cohort = self._validated_run_cohort(candidate_runs, label="candidate")
        baseline_by_task = baseline_cohort["runs_by_task"]
        candidate_by_task = candidate_cohort["runs_by_task"]
        baseline_tasks = set(baseline_by_task)
        candidate_tasks = set(candidate_by_task)
        if baseline_tasks != candidate_tasks:
            missing_from_candidate = sorted(baseline_tasks - candidate_tasks)
            missing_from_baseline = sorted(candidate_tasks - baseline_tasks)
            raise WorkflowError(
                "comparison requires identical task sets; "
                f"candidate missing={missing_from_candidate}, "
                f"baseline missing={missing_from_baseline}"
            )
        if baseline_cohort["task_digests"] != candidate_cohort["task_digests"]:
            mismatched = sorted(
                task_id
                for task_id in baseline_tasks
                if baseline_cohort["task_digests"].get(task_id)
                != candidate_cohort["task_digests"].get(task_id)
            )
            raise WorkflowError(
                "comparison requires identical task digests; mismatched=" + ", ".join(mismatched)
            )
        if baseline_cohort["task_contract_digests"] != candidate_cohort["task_contract_digests"]:
            mismatched_contracts = sorted(
                task_id
                for task_id in baseline_tasks
                if baseline_cohort["task_contract_digests"].get(task_id)
                != candidate_cohort["task_contract_digests"].get(task_id)
            )
            raise WorkflowError(
                "comparison requires identical task contracts; mismatched="
                + ", ".join(mismatched_contracts)
            )
        if baseline_cohort["attempts"] != candidate_cohort["attempts"]:
            raise WorkflowError("comparison requires identical attempt sets for every task")
        if (
            baseline_cohort["execution_profile_sha256"]
            != candidate_cohort["execution_profile_sha256"]
        ):
            raise WorkflowError("comparison requires compatible execution profiles")

        task_ids = sorted(baseline_tasks)
        baseline_values = {
            task_id: any(self._score_run(run).resolved for run in baseline_by_task[task_id])
            for task_id in task_ids
        }
        candidate_values = {
            task_id: any(self._score_run(run).resolved for run in candidate_by_task[task_id])
            for task_id in task_ids
        }
        baseline_rate = sum(baseline_values.values()) / len(task_ids)
        candidate_rate = sum(candidate_values.values()) / len(task_ids)
        delta_pp = (candidate_rate - baseline_rate) * 100.0
        threshold = fail_on_regression
        regression = threshold is not None and delta_pp < -threshold
        payload = {
            "schema_version": "repotrials.comparison/v1",
            "baseline": baseline_cohort["run_group"],
            "candidate": candidate_cohort["run_group"],
            "paired_tasks": len(task_ids),
            "trials_per_group": len(baseline_runs),
            "aggregation": {
                "method": "pass@k",
                "rule": "any_attempt_resolved",
                "k": baseline_cohort["k"],
                "unit": "task",
            },
            "execution_profile_sha256": baseline_cohort["execution_profile_sha256"],
            "baseline_rate": baseline_rate,
            "candidate_rate": candidate_rate,
            "delta_pp": delta_pp,
            "wins": sum(
                (not baseline_values[task_id]) and candidate_values[task_id] for task_id in task_ids
            ),
            "losses": sum(
                baseline_values[task_id] and (not candidate_values[task_id]) for task_id in task_ids
            ),
            "ties": sum(
                baseline_values[task_id] == candidate_values[task_id] for task_id in task_ids
            ),
            "threshold_pp": threshold,
            "regression": regression,
        }
        if output is not None:
            path = output if output.is_absolute() else self.config.root / output
            path = self._prepare_output_file(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            payload["output"] = str(path)
        return payload

    def verify_vault(self) -> dict[str, Any]:
        objects = dict(self.vault.verify_all())
        return {
            "objects": objects,
            "total": len(objects),
            "verified": sum(objects.values()),
        }

    def _prepare_output_directory(
        self,
        path: Path,
        *,
        expected_files: Sequence[str] = (),
    ) -> Path:
        """Preflight user output paths that fall below the private state root."""

        candidate = absolute_path(path)
        state_root = absolute_path(self.config.state_dir)
        try:
            relative = candidate.relative_to(state_root)
        except ValueError:
            return candidate
        try:
            destination = ensure_managed_directory(state_root, relative)
            prepare_managed_files(
                state_root,
                (relative / filename for filename in expected_files),
            )
        except StatePathError as exc:
            raise WorkflowError(f"unsafe private output path: {exc}") from exc
        return destination

    def _prepare_output_file(self, path: Path) -> Path:
        """Preflight one user output file when it falls below private state."""

        candidate = absolute_path(path)
        state_root = absolute_path(self.config.state_dir)
        try:
            relative = candidate.relative_to(state_root)
        except ValueError:
            return candidate
        try:
            return prepare_managed_file(state_root, relative)
        except StatePathError as exc:
            raise WorkflowError(f"unsafe private output path: {exc}") from exc

    def _run_one(
        self,
        task: Task,
        *,
        agent_command: str,
        name: str,
        model: str,
        attempt: int,
        run_group: str,
        cost_usd: float | None,
    ) -> Run:
        started = time.monotonic()
        contract = _contract_from_task(task)
        archive = self._task_object(task, "base_archive_oid")
        hidden_patch = self.store.get_object(task.test_patch_oid)
        run_id = f"run-{uuid.uuid4().hex[:20]}"
        agent_stdout_oid = ""
        agent_stderr_oid = ""
        verifier_stdout_oid = ""
        verifier_stderr_oid = ""
        patch = b""
        failure_kind: str | None = None
        integrity_ok = False
        verifier_exit: int | None = None
        outcomes: dict[str, str] = {}
        agent_exit: int | None = None

        try:
            with tempfile.TemporaryDirectory(prefix=f"repotrials-{task.id}-") as raw:
                temporary = Path(raw)
                workspace = safe_extract_tar(archive, temporary / "agent" / "repo")
                instruction_path = temporary / "instruction.md"
                instruction_path.write_text(task.instruction.rstrip() + "\n", encoding="utf-8")
                backend = LocalCommandBackend()

                # Give the agent the frozen dependency/bootstrap environment and
                # commit its derived files into the synthetic baseline so they do
                # not leak into the submission patch.  Verification independently
                # rebuilds after applying the candidate and hidden-test patches.
                setup_failure = self._run_setup(backend, workspace, contract)
                if setup_failure is not None:
                    failure_kind = "setup_failed"
                    agent_exit = setup_failure.returncode
                    agent_stdout_oid = self.store.put_object(setup_failure.stdout)
                    agent_stderr_oid = self.store.put_object(setup_failure.stderr)
                else:
                    initialize_synthetic_git(workspace)
                    sealed_git = temporary / "evaluator" / "sealed-git"
                    sealed_git.parent.mkdir()
                    shutil.copytree(workspace / ".git", sealed_git)
                    agent_result = run_agent_command(
                        backend,
                        agent_command,
                        workspace=workspace,
                        instruction=task.instruction,
                        instruction_path=instruction_path,
                        timeout=float(contract.execution_timeout_seconds),
                        environment={"REPOTRIALS_TASK_ID": task.id},
                    )
                    agent_exit = agent_result.returncode
                    agent_stdout_oid = self.store.put_object(agent_result.stdout)
                    agent_stderr_oid = self.store.put_object(agent_result.stderr)
                    capture_error: str | None = None
                    try:
                        _restore_synthetic_git(workspace, sealed_git)
                        changed_paths = collect_submission_paths(workspace)
                        patch = collect_submission_patch(workspace)
                        integrity = check_patch_integrity(
                            patch,
                            contract.protected_paths,
                            exact_allowed_paths=task.source_files,
                            observed_paths=changed_paths,
                            max_files=contract.max_changed_files,
                            max_patch_bytes=contract.max_patch_bytes,
                        )
                        integrity_ok = integrity.ok
                    except (OSError, SandboxError) as exc:
                        capture_error = str(exc)
                        verifier_stderr_oid = self.store.put_object(capture_error)
                        integrity_ok = False
                    if agent_result.timed_out:
                        failure_kind = "agent_timeout"
                    elif not agent_result.ok:
                        failure_kind = "agent_exit"
                    elif capture_error is not None:
                        failure_kind = "submission_capture"
                    elif not integrity_ok:
                        failure_kind = "integrity"
                    else:
                        verification = self._verify_submission(
                            task, contract, archive, patch, hidden_patch
                        )
                        verifier_exit = verification["returncode"]
                        verifier_stdout_oid = self.store.put_object(verification["stdout"])
                        verifier_stderr_oid = self.store.put_object(verification["stderr"])
                        outcomes = verification["outcomes"]
                        if not verification["resolved"]:
                            failure_kind = verification["failure_kind"]
        except (OSError, SandboxError) as exc:
            failure_kind = "infrastructure"
            verifier_stderr_oid = self.store.put_object(str(exc))

        expected_f2p = contract.fail_to_pass
        expected_p2p = contract.pass_to_pass
        f2p = {test_id: outcomes.get(test_id, "missing") for test_id in expected_f2p}
        p2p = {test_id: outcomes.get(test_id, "missing") for test_id in expected_p2p}
        resolved = (
            failure_kind is None
            and agent_exit == 0
            and verifier_exit == 0
            and integrity_ok
            and all(status in PASSING_TEST_STATUSES for status in f2p.values())
            and all(status in PASSING_TEST_STATUSES for status in p2p.values())
        )
        patch_oid = self.store.put_object(patch)
        duration = time.monotonic() - started
        execution_profile: dict[str, Any] = {
            "schema_version": "repotrials.execution-profile/v1",
            "configured_backend": contract.execution_backend,
            "effective_backend": "local-command",
            "isolation": "none",
            "configured_network": contract.execution_network,
            "effective_network": "host",
            "agent_timeout_seconds": contract.execution_timeout_seconds,
            "verifier_timeout_seconds": contract.validation_timeout_seconds,
            "test_command_sha256": hashlib.sha256(
                contract.test_command.encode("utf-8")
            ).hexdigest(),
            "setup_commands_sha256": hashlib.sha256(
                json.dumps(
                    list(contract.setup_commands),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "protected_paths_sha256": hashlib.sha256(
                json.dumps(
                    list(contract.protected_paths),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "repotrials_version": __version__,
        }
        execution_profile_sha256 = hashlib.sha256(
            json.dumps(
                execution_profile,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return Run(
            id=run_id,
            task_id=task.id,
            agent=name,
            model=model,
            status="passed" if resolved else failure_kind or "failed",
            passed=resolved,
            exit_code=agent_exit,
            duration_seconds=duration,
            cost_usd=cost_usd,
            patch_oid=patch_oid,
            metadata={
                "schema_version": "repotrials.run/v1",
                "run_group": run_group,
                "attempt": attempt,
                "failure_kind": failure_kind,
                "integrity_passed": integrity_ok,
                "verifier_exit_code": verifier_exit,
                "f2p": f2p,
                "p2p": p2p,
                "agent_command_sha256": command_digest(agent_command),
                "instruction_sha256": hashlib.sha256(task.instruction.encode()).hexdigest(),
                "task_digest": _task_digest(task),
                "task_contract_sha256": contract.digest,
                "execution_profile": execution_profile,
                "execution_profile_sha256": execution_profile_sha256,
                "agent_stdout_oid": agent_stdout_oid,
                "agent_stderr_oid": agent_stderr_oid,
                "verifier_stdout_oid": verifier_stdout_oid,
                "verifier_stderr_oid": verifier_stderr_oid,
                "repotrials_version": __version__,
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
        )

    def _verify_submission(
        self,
        task: Task,
        contract: TaskContract,
        archive: bytes,
        patch: bytes,
        hidden_patch: bytes,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="repotrials-verifier-") as raw:
            workspace = safe_extract_tar(archive, Path(raw) / "repo")
            backend = LocalCommandBackend()
            try:
                apply_patch(workspace, patch)
                apply_patch(workspace, hidden_patch)
            except SandboxError as exc:
                return {
                    "resolved": False,
                    "failure_kind": "patch_apply",
                    "returncode": 1,
                    "stdout": "",
                    "stderr": str(exc),
                    "outcomes": {},
                }
            before_setup = snapshot_workspace(workspace)
            setup_failure = self._run_setup(backend, workspace, contract)
            if setup_failure is not None:
                return {
                    "resolved": False,
                    "failure_kind": "verifier_setup",
                    "returncode": setup_failure.returncode,
                    "stdout": setup_failure.stdout,
                    "stderr": setup_failure.stderr,
                    "outcomes": {},
                }
            setup_mutation = find_setup_mutation(
                workspace,
                before_setup,
                contract.protected_paths,
            )
            if setup_mutation is not None:
                return {
                    "resolved": False,
                    "failure_kind": "verifier_setup_mutation",
                    "returncode": 1,
                    "stdout": "",
                    "stderr": (
                        "setup changed an existing or protected workspace path: " + setup_mutation
                    ),
                    "outcomes": {},
                }
            command = _expand_junit_command(contract.test_command, ".repotrials-junit.xml")
            result = backend.run(
                command,
                cwd=workspace,
                timeout=float(contract.validation_timeout_seconds),
            )
            junit_path = workspace / ".repotrials-junit.xml"
            outcomes: dict[str, str] = {}
            failure_kind: str | None = None
            if not junit_path.is_file():
                failure_kind = "junit_missing"
            else:
                try:
                    report = parse_junit_xml(junit_path)
                    outcomes = {item.test_id: item.status for item in report.outcomes}
                except JUnitParseError:
                    failure_kind = "junit_parse"
            expected = set(contract.fail_to_pass) | set(contract.pass_to_pass)
            suite_failure = _junit_failure_kind(outcomes, expected, command_ok=result.ok)
            if suite_failure is not None:
                failure_kind = suite_failure
            resolved = failure_kind is None and result.ok
            return {
                "resolved": resolved,
                "failure_kind": failure_kind,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "outcomes": outcomes,
            }

    def _run_setup(
        self,
        backend: LocalCommandBackend,
        workspace: Path,
        contract: TaskContract,
    ) -> CommandResult | None:
        for command in contract.setup_commands:
            result = backend.run(
                command,
                cwd=workspace,
                timeout=float(contract.validation_timeout_seconds),
            )
            if not result.ok:
                return result
        return None

    def _score_run(self, run: Run) -> TrialScore:
        raw_f2p = run.metadata.get("f2p")
        raw_p2p = run.metadata.get("p2p")
        f2p = raw_f2p if isinstance(raw_f2p, Mapping) and raw_f2p else {"verifier": run.passed}
        p2p = raw_p2p if isinstance(raw_p2p, Mapping) and raw_p2p else {"suite": run.passed}
        score = score_trial(
            run.task_id,
            f2p,
            p2p,
            integrity_passed=bool(run.metadata.get("integrity_passed", False)),
            metadata={
                "run_id": run.id,
                "task_id": run.task_id,
                "agent": run.agent,
                "model": run.model,
                "duration_seconds": run.duration_seconds,
                "cost_usd": run.cost_usd,
                "run_group": run.metadata.get("run_group"),
                "attempt": run.metadata.get("attempt", 1),
                "task_digest": run.metadata.get("task_digest", ""),
                "task_contract_sha256": run.metadata.get("task_contract_sha256", ""),
                "execution_profile_sha256": run.metadata.get("execution_profile_sha256", ""),
            },
        )
        # An agent timeout/exit is never rescued by stale or malformed outcome
        # metadata, even if all serialized test values happen to be passing.
        if score.resolved and not run.passed:
            return dataclasses.replace(
                score,
                resolved=False,
                failure_kind=str(run.metadata.get("failure_kind") or run.status),
            )
        return score

    def _validated_run_cohort(self, runs: Sequence[Run], *, label: str) -> dict[str, Any]:
        """Validate that selected attempts form one comparable run cohort."""

        groups = {str(run.metadata.get("run_group", "")) for run in runs}
        if "" in groups or len(groups) != 1:
            raise WorkflowError(
                f"{label} selector must resolve to exactly one non-empty run_group; "
                f"found {sorted(groups)!r}"
            )
        run_group = next(iter(groups))
        manifest = self._load_complete_run_group_manifest(run_group, label=label)

        runs_by_task: dict[str, list[Run]] = {}
        attempts: dict[str, set[int]] = {}
        task_digests: dict[str, str] = {}
        task_contract_digests: dict[str, str] = {}
        profile_payloads: dict[str, Mapping[str, Any]] = {}
        seen: set[tuple[str, int]] = set()
        for run in runs:
            raw_attempt = run.metadata.get("attempt")
            if not isinstance(raw_attempt, int) or isinstance(raw_attempt, bool) or raw_attempt < 1:
                raise WorkflowError(f"{label} run {run.id} has an invalid attempt number")
            key = (run.task_id, raw_attempt)
            if key in seen:
                raise WorkflowError(
                    f"{label} contains duplicate task attempt {run.task_id}#{raw_attempt}"
                )
            seen.add(key)
            runs_by_task.setdefault(run.task_id, []).append(run)
            attempts.setdefault(run.task_id, set()).add(raw_attempt)

            digest = run.metadata.get("task_digest")
            if not isinstance(digest, str) or not digest:
                raise WorkflowError(f"{label} run {run.id} is missing task_digest")
            previous_digest = task_digests.setdefault(run.task_id, digest)
            if previous_digest != digest:
                raise WorkflowError(f"{label} contains multiple task digests for {run.task_id}")

            contract_digest = run.metadata.get("task_contract_sha256")
            if not isinstance(contract_digest, str) or not contract_digest:
                raise WorkflowError(f"{label} run {run.id} is missing task_contract_sha256")
            previous_contract_digest = task_contract_digests.setdefault(
                run.task_id, contract_digest
            )
            if previous_contract_digest != contract_digest:
                raise WorkflowError(f"{label} contains multiple task contracts for {run.task_id}")

            raw_profile = run.metadata.get("execution_profile")
            if not isinstance(raw_profile, Mapping):
                raise WorkflowError(f"{label} run {run.id} is missing execution_profile")
            profile_json = json.dumps(
                raw_profile,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            computed_profile_digest = hashlib.sha256(profile_json.encode("utf-8")).hexdigest()
            if run.metadata.get("execution_profile_sha256") != computed_profile_digest:
                raise WorkflowError(f"{label} run {run.id} has an invalid execution profile digest")
            profile_payloads.setdefault(profile_json, raw_profile)

        if len(profile_payloads) != 1:
            raise WorkflowError(f"{label} mixes incompatible execution profiles")
        normalized_attempts = {
            task_id: tuple(sorted(values)) for task_id, values in attempts.items()
        }
        attempt_shapes = set(normalized_attempts.values())
        if len(attempt_shapes) != 1:
            raise WorkflowError(f"{label} has different attempt sets across tasks")
        attempt_shape = next(iter(attempt_shapes))
        if attempt_shape != tuple(range(1, len(attempt_shape) + 1)):
            raise WorkflowError(
                f"{label} attempts must be contiguous and start at one; got {attempt_shape!r}"
            )
        profile_json, execution_profile = next(iter(profile_payloads.items()))
        ordered_task_ids = list(dict.fromkeys(run.task_id for run in runs))
        run_ids = [run.id for run in runs]
        if manifest["task_ids"] != ordered_task_ids:
            raise WorkflowError(f"{label} run group manifest does not match its exact task order")
        if manifest["task_digests"] != dict(sorted(task_digests.items())):
            raise WorkflowError(f"{label} run group manifest has mismatched task digests")
        if manifest["task_contract_digests"] != dict(sorted(task_contract_digests.items())):
            raise WorkflowError(f"{label} run group manifest has mismatched task contracts")
        if manifest["attempts"] != len(attempt_shape):
            raise WorkflowError(f"{label} run group manifest has a mismatched attempt budget")
        if manifest["expected_trial_count"] != len(runs):
            raise WorkflowError(f"{label} run group manifest has a mismatched trial count")
        if manifest["run_ids"] != run_ids:
            raise WorkflowError(f"{label} run group manifest does not match its exact run order")
        if any(run.agent != manifest["agent"] for run in runs):
            raise WorkflowError(f"{label} run group manifest has a mismatched agent")
        expected_model = manifest["model"] or ""
        if any(run.model != expected_model for run in runs):
            raise WorkflowError(f"{label} run group manifest has a mismatched model")
        return {
            "run_group": run_group,
            "runs_by_task": runs_by_task,
            "attempts": normalized_attempts,
            "k": len(attempt_shape),
            "task_digests": dict(sorted(task_digests.items())),
            "task_contract_digests": dict(sorted(task_contract_digests.items())),
            "execution_profile": dict(execution_profile),
            "execution_profile_sha256": hashlib.sha256(profile_json.encode("utf-8")).hexdigest(),
        }

    def _load_complete_run_group_manifest(self, run_group: str, *, label: str) -> dict[str, Any]:
        """Load and strictly validate the completion record for one run group."""

        path = _run_group_manifest_path(self.config.state_dir, run_group)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkflowError(
                f"{label} run group {run_group!r} is missing its group manifest"
            ) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkflowError(
                f"{label} run group {run_group!r} has an unreadable group manifest: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise WorkflowError(f"{label} run group manifest must be a JSON object")

        common = {
            "schema_version",
            "run_group",
            "status",
            "task_ids",
            "task_digests",
            "task_contract_digests",
            "attempts",
            "expected_trial_count",
            "agent",
            "model",
            "created_at",
        }
        complete = {"run_ids", "completed_at"}
        missing = common - set(raw)
        unknown = set(raw) - common - complete
        if missing or unknown:
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(sorted(missing)))
            if unknown:
                details.append("unknown " + ", ".join(sorted(unknown)))
            raise WorkflowError(f"{label} run group manifest is invalid: {'; '.join(details)}")
        if raw.get("schema_version") != "repotrials.run-group/v1":
            raise WorkflowError(f"{label} run group manifest has an unsupported schema version")
        if raw.get("run_group") != run_group:
            raise WorkflowError(f"{label} run group manifest has a mismatched identity")
        status = raw.get("status")
        if status == "running":
            if complete & set(raw):
                raise WorkflowError(f"{label} running group manifest contains completion fields")
            raise WorkflowError(f"{label} run group {run_group!r} is incomplete")
        if status != "complete":
            raise WorkflowError(f"{label} run group manifest has an invalid status")
        missing_complete = complete - set(raw)
        if missing_complete:
            raise WorkflowError(
                f"{label} complete run group manifest is missing "
                + ", ".join(sorted(missing_complete))
            )

        task_ids = raw.get("task_ids")
        if (
            not isinstance(task_ids, list)
            or not task_ids
            or not all(isinstance(task_id, str) and task_id for task_id in task_ids)
            or len(set(task_ids)) != len(task_ids)
        ):
            raise WorkflowError(f"{label} run group manifest has invalid task_ids")
        task_id_set = set(task_ids)
        for field in ("task_digests", "task_contract_digests"):
            digests = raw.get(field)
            if not isinstance(digests, dict) or set(digests) != task_id_set:
                raise WorkflowError(f"{label} run group manifest has invalid {field}")
            if not all(isinstance(value, str) and _is_sha256(value) for value in digests.values()):
                raise WorkflowError(f"{label} run group manifest has invalid {field}")
            raw[field] = dict(sorted(digests.items()))
        attempts = raw.get("attempts")
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
            raise WorkflowError(f"{label} run group manifest has invalid attempts")
        expected_trial_count = raw.get("expected_trial_count")
        if (
            isinstance(expected_trial_count, bool)
            or not isinstance(expected_trial_count, int)
            or expected_trial_count != len(task_ids) * attempts
        ):
            raise WorkflowError(f"{label} run group manifest has invalid expected_trial_count")
        if not isinstance(raw.get("agent"), str) or not raw["agent"]:
            raise WorkflowError(f"{label} run group manifest has an invalid agent")
        if raw.get("model") is not None and not isinstance(raw.get("model"), str):
            raise WorkflowError(f"{label} run group manifest has an invalid model")
        for field in ("created_at", "completed_at"):
            if not isinstance(raw.get(field), str) or not raw[field]:
                raise WorkflowError(f"{label} run group manifest has an invalid {field}")
        run_ids = raw.get("run_ids")
        if (
            not isinstance(run_ids, list)
            or len(run_ids) != expected_trial_count
            or not all(isinstance(run_id, str) and run_id for run_id in run_ids)
            or len(set(run_ids)) != len(run_ids)
        ):
            raise WorkflowError(f"{label} run group manifest has invalid run_ids")
        return raw

    def _select_candidates(self, ids: Sequence[str] | None) -> list[Candidate]:
        if ids is None:
            return self.store.list_candidates()
        _reject_duplicate_selectors(ids, label="candidate")
        result: list[Candidate] = []
        for candidate_id in ids:
            candidate = self.store.get_candidate(candidate_id)
            if candidate is None:
                raise WorkflowError(f"unknown candidate: {candidate_id}")
            result.append(candidate)
        return result

    def _accepted_tasks(self, ids: Sequence[str] | None) -> list[Task]:
        if ids is None:
            tasks = [task for task in self.store.list_tasks() if task.metadata.get("accepted")]
        else:
            _reject_duplicate_selectors(ids, label="task")
            tasks = []
            for task_id in ids:
                task = self.store.get_task(task_id)
                if task is None:
                    raise WorkflowError(f"unknown task: {task_id}")
                tasks.append(task)
        rejected = [task.id for task in tasks if not task.metadata.get("accepted")]
        if rejected:
            raise WorkflowError(f"task(s) require review before use: {', '.join(rejected)}")
        if not tasks:
            raise WorkflowError("no accepted tasks; run `repotrials validate --accept` or `review`")
        for task in tasks:
            self._require_current_task_revision(task)
            _contract_from_task(task)
            validation = self.store.get_validation(task.id)
            if validation is None or not validation.passed:
                raise WorkflowError(
                    f"task {task.id} is accepted without a passing validation; revalidate it"
                )
        return tasks

    def _require_current_task_revision(self, task: Task) -> None:
        """Reject superseded or ambiguous revisions of one historical candidate."""

        superseded_by = task.metadata.get("superseded_by")
        if superseded_by:
            raise WorkflowError(
                f"task {task.id} was superseded by {superseded_by} and cannot be accepted"
            )
        revisions = self.store.list_tasks(task.candidate_id)
        current = [item for item in revisions if not item.metadata.get("superseded_by")]
        if not current or current[-1].id != task.id:
            latest = current[-1].id if current else "unknown"
            raise WorkflowError(
                f"task {task.id} is not the latest revision for candidate "
                f"{task.candidate_id}; current task is {latest}"
            )

    def _select_runs(self, selectors: Sequence[str] | None) -> list[Run]:
        runs = self.store.list_runs()
        if not selectors:
            return runs
        wanted = set(selectors)
        matched = [
            run
            for run in runs
            if {
                run.id,
                run.agent,
                str(run.metadata.get("run_group", "")),
            }
            & wanted
        ]
        matched_groups = {str(run.metadata.get("run_group", "")) for run in matched}
        if not matched_groups or "" in matched_groups:
            return matched
        # A run ID is an alias for its complete immutable cohort.  Expanding
        # here prevents a single attempt from being reported as pass@1 when
        # the run group was actually executed with a larger attempt budget.
        return [run for run in runs if str(run.metadata.get("run_group", "")) in matched_groups]

    def _validation_backend(self, name: str) -> CommandBackend:
        if name == "local":
            return LocalCommandBackend()
        if name == "docker":
            backend = DockerCommandBackend(
                self.config.validation.docker_image,
                platform_name="linux/amd64",
                network="none",
                memory=f"{self.config.execution.memory_mb}m",
                cpus=self.config.execution.cpus,
            )
            if not backend.available:
                raise WorkflowError("Docker validation requested, but `docker` was not found")
            return backend
        raise ValueError(f"unknown validation backend: {name}")

    def _validation_model(
        self,
        task: Task,
        report: ValidationReport,
        attempts: int,
        duration: float,
    ) -> Validation:
        def codes(phase: ValidationPhase) -> tuple[int, ...]:
            return tuple(
                run.test_result.returncode if run.test_result is not None else 125
                for run in report.runs_for(phase)
            )

        stable = all(report.phase_stable(phase) for phase in ValidationPhase)
        red = codes(ValidationPhase.RED)
        return Validation(
            task_id=task.id,
            status="validated" if report.valid else "rejected",
            passed=report.valid,
            stable=stable,
            attempts=attempts,
            baseline_exit_codes=codes(ValidationPhase.BASE),
            red_exit_codes=red,
            gold_exit_codes=codes(ValidationPhase.GOLD),
            noop_exit_codes=red,
            duration_seconds=duration,
            diagnostics=report.reasons,
            metadata={
                "fail_to_pass": list(report.fail_to_pass),
                "pass_to_pass": list(report.pass_to_pass),
                "integrity_paths": list(report.integrity.paths),
            },
        )

    def _task_object(self, task: Task, metadata_key: str) -> bytes:
        oid = task.metadata.get(metadata_key)
        if not isinstance(oid, str) or not oid:
            raise WorkflowError(f"task {task.id} is missing {metadata_key}")
        return self.store.get_object(oid)

    def _supersede_candidate_tasks(self, candidate_id: str, current_task_id: str) -> None:
        """Retire older task revisions for one historical candidate."""

        changed = False
        for previous in self.store.list_tasks(candidate_id):
            if previous.id == current_task_id:
                continue
            metadata = dict(previous.metadata)
            metadata.update(
                {
                    "tier": "rejected",
                    "accepted": False,
                    "superseded_by": current_task_id,
                    "reviewed_at": utc_now(),
                }
            )
            self.store.save_task(dataclasses.replace(previous, metadata=metadata))
            self._dematerialize_task(previous.id, rebuild_index=False)
            changed = True
        if changed:
            self._write_task_index()

    def _dematerialize_task(self, task_id: str, *, rebuild_index: bool) -> None:
        """Remove manifests for a rejected/superseded task without touching its audit rows."""

        self._task_public_paths(prepare_index=False)
        _identifier, task_dir, private_path = self._preflight_task_state(task_id)
        if task_dir.exists():
            shutil.rmtree(task_dir)
        if private_path.exists():
            private_path.unlink()
        if rebuild_index:
            self._write_task_index()

    def _materialize_task(self, task: Task, validation: Validation, candidate: Candidate) -> None:
        """Write auditable public/private manifests outside the SQLite cache."""

        tier = str(task.metadata.get("tier", "auto"))
        if tier not in {"auto", "verified"} or not task.metadata.get("accepted"):
            self._dematerialize_task(task.id, rebuild_index=True)
            return
        contract = _contract_from_task(task)
        title, _, body = task.instruction.partition("\n")
        body = body.strip() or title
        archive_oid = str(task.metadata["base_archive_oid"])
        public = {
            "schema_version": "repotrials.task/v1",
            "task_id": task.id,
            "contract_sha256": contract.digest,
            "prompt": {
                "title": title.strip(),
                "body": body,
                "visibility": "issue_only",
            },
            "base": {
                "archive_sha256": archive_oid,
                "image": contract.validation_image,
                "platform": "linux/amd64",
            },
            "profile": contract.profile,
            "policy": {
                "network": contract.execution_network,
                "protected_paths": list(contract.protected_paths),
                "submission_paths": list(task.source_files),
                "timeout_seconds": contract.execution_timeout_seconds,
            },
            "quality": {
                "tier": tier,
                "exposure": task.metadata.get("exposure", "unknown"),
                "environment_fidelity": "inferred",
            },
        }
        private = {
            "schema_version": "repotrials.oracle/v1",
            "task_id": task.id,
            "provenance": {
                "repository": candidate.repository,
                "base_commit": task.base_sha,
                "fixed_commit": task.fix_sha,
                "reconstruction_method": task.metadata.get(
                    "reconstruction_method", "single_parent"
                ),
                "issue": candidate.metadata.get("issue"),
                "pull_request": candidate.metadata.get("pull_request"),
            },
            "artifacts": {
                "base_archive": f"sha256:{archive_oid}",
                "hidden_test_patch": f"sha256:{task.test_patch_oid}",
                "gold_patch": f"sha256:{task.gold_patch_oid}",
            },
            "expected": {
                "fail_to_pass": list(contract.fail_to_pass),
                "pass_to_pass": list(contract.pass_to_pass),
            },
            "validation": {
                "repetitions": contract.validation_repetitions,
                "base_stable": validation.stable,
                "red_stable": validation.stable,
                "gold_stable": validation.stable,
                "gold_resolved": validation.passed,
                "noop_resolved": False,
                "contract_digest": contract.digest,
                "contract": contract.to_dict(),
            },
        }
        self._task_public_paths(prepare_index=False)
        self._preflight_task_state(task.id)
        try:
            identifier = managed_component(task.id, label="task id")
            public_path, instruction_path, private_path = prepare_managed_files(
                self.config.state_dir,
                (
                    Path("tasks") / identifier / "public.json",
                    Path("tasks") / identifier / "instruction.md",
                    Path("private") / f"{identifier}.json",
                ),
            )
        except StatePathError as exc:
            raise WorkflowError(f"unsafe task state path: {exc}") from exc
        _write_json(public_path, public)
        _write_text(instruction_path, task.instruction.rstrip() + "\n")
        _write_json(private_path, private, private=True)
        self._write_task_index()

    def _preflight_task_state(self, task_id: str) -> tuple[str, Path, Path]:
        """Validate all paths owned by one materialized task without creating them."""

        suffix = task_id.removeprefix("rt_")
        if (
            not task_id.startswith("rt_")
            or not 8 <= len(suffix) <= 32
            or not suffix.isalnum()
            or suffix != suffix.lower()
        ):
            raise WorkflowError(f"unsafe task id in local state: {task_id!r}")
        try:
            identifier = managed_component(task_id, label="task id")
            task_dir = managed_path(
                self.config.state_dir,
                Path("tasks") / identifier,
                expected="directory",
            )
            for filename in ("public.json", "instruction.md"):
                managed_path(
                    self.config.state_dir,
                    Path("tasks") / identifier / filename,
                    expected="file",
                )
            private_path = managed_path(
                self.config.state_dir,
                Path("private") / f"{identifier}.json",
                expected="file",
            )
        except StatePathError as exc:
            raise WorkflowError(f"unsafe task state path: {exc}") from exc
        return identifier, task_dir, private_path

    def _task_public_paths(self, *, prepare_index: bool) -> tuple[list[Path], Path]:
        """Validate the public task tree and return its manifests and index path."""

        try:
            tasks_dir = managed_path(self.config.state_dir, "tasks", expected="directory")
            children = sorted(tasks_dir.iterdir(), key=lambda item: item.name)
            public_paths: list[Path] = []
            for child in children:
                if child.name == "index.json":
                    managed_path(self.config.state_dir, "tasks/index.json", expected="file")
                    continue
                identifier = managed_component(child.name, label="materialized task id")
                managed_path(
                    self.config.state_dir,
                    Path("tasks") / identifier,
                    expected="directory",
                )
                public_path = managed_path(
                    self.config.state_dir,
                    Path("tasks") / identifier / "public.json",
                    expected="file",
                )
                if public_path.exists():
                    public_paths.append(public_path)
            index_path = (
                prepare_managed_file(self.config.state_dir, "tasks/index.json")
                if prepare_index
                else managed_path(self.config.state_dir, "tasks/index.json", expected="file")
            )
        except StatePathError as exc:
            raise WorkflowError(f"unsafe task index path: {exc}") from exc
        return public_paths, index_path

    def _write_task_index(self) -> None:
        entries: list[dict[str, str]] = []
        public_paths, index_path = self._task_public_paths(prepare_index=True)
        for public_path in public_paths:
            payload = public_path.read_bytes()
            entries.append(
                {
                    "task_id": public_path.parent.name,
                    "manifest": public_path.relative_to(self.config.state_dir).as_posix(),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        _write_json(
            index_path,
            {"schema_version": "repotrials.suite/v1", "tasks": entries},
        )

    def _materialize_run(self, run: Run) -> None:
        group = str(run.metadata.get("run_group", "ungrouped"))
        payload = {
            "schema_version": "repotrials.run/v1",
            "run_id": run.id,
            "run_group": group,
            "attempt": run.metadata.get("attempt"),
            "task_id": run.task_id,
            "system": {
                "name": run.agent,
                "model": run.model or None,
                "agent_command_sha256": run.metadata.get("agent_command_sha256", ""),
                "repotrials_version": __version__,
                "task_digest": run.metadata.get("task_digest", ""),
                "task_contract_sha256": run.metadata.get("task_contract_sha256", ""),
                "execution_profile_sha256": run.metadata.get("execution_profile_sha256", ""),
            },
            "submission": {"patch_sha256": run.patch_oid},
            "result": {
                "resolved": run.passed,
                "failure_kind": run.metadata.get("failure_kind"),
                "wall_seconds": run.duration_seconds,
                "cost_usd": run.cost_usd,
                "f2p": run.metadata.get("f2p", {}),
                "p2p": run.metadata.get("p2p", {}),
                "integrity_passed": run.metadata.get("integrity_passed", False),
            },
        }
        try:
            group_id = managed_component(group, label="run group")
            run_id = managed_component(run.id, label="run id")
            path = prepare_managed_file(
                self.config.state_dir,
                Path("runs") / group_id / f"{run_id}.json",
            )
        except StatePathError as exc:
            raise WorkflowError(f"unsafe run state path: {exc}") from exc
        _write_json(path, payload)

    def _enrich_with_github(
        self, candidates: Sequence[Candidate]
    ) -> tuple[list[Candidate], list[str]]:
        slug = discover_github_slug(self.repository.path)
        if slug is None:
            raise WorkflowError("origin is not a recognized GitHub repository")
        client = GitHubClient()
        enriched: list[Candidate] = []
        errors: list[str] = []
        for candidate in candidates:
            try:
                pull = client.pull_for_commit(slug, candidate.commit_sha)
            except GitHubError as exc:
                errors.append(f"{candidate.commit_sha[:9]}: {exc}")
                enriched.append(candidate)
                continue
            if pull is None:
                enriched.append(candidate)
                continue
            metadata = dict(candidate.metadata)
            metadata.update(
                {
                    "github_repository": slug,
                    "pull_request": pull.number,
                    "pr_title": pull.title,
                    "pr_body": pull.body,
                    "pr_url": pull.html_url,
                    "pr_base_sha": pull.base_sha,
                    "pr_head_sha": pull.head_sha,
                    "labels": list(pull.labels),
                }
            )
            enriched.append(dataclasses.replace(candidate, metadata=metadata))
        return enriched, errors


def _max_patch_bytes(config: RepoTrialsConfig) -> int:
    """Derive and freeze a generous byte ceiling from the configured line budget."""

    return max(1_000_000, config.mining.max_changed_lines * 5_000)


def _task_id(
    candidate: Candidate,
    gold_oid: str,
    test_oid: str,
    archive_oid: str,
    contract_digest: str,
) -> str:
    return _task_id_from_values(
        candidate_id=candidate.id,
        base_sha=candidate.parent_sha,
        fix_sha=candidate.commit_sha,
        gold_oid=gold_oid,
        test_oid=test_oid,
        archive_oid=archive_oid,
        contract_digest=contract_digest,
    )


def _task_id_from_values(
    *,
    candidate_id: str,
    base_sha: str,
    fix_sha: str,
    gold_oid: str,
    test_oid: str,
    archive_oid: str,
    contract_digest: str,
) -> str:
    payload = json.dumps(
        {
            "schema_version": "repotrials.task-identity/v1",
            "candidate_id": candidate_id,
            "base_sha": base_sha,
            "fix_sha": fix_sha,
            "gold_patch_oid": gold_oid,
            "test_patch_oid": test_oid,
            "base_archive_oid": archive_oid,
            "contract_digest": contract_digest,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "rt_" + hashlib.sha256(payload).hexdigest()[:20]


def _contract_from_task(task: Task) -> TaskContract:
    """Load a task's frozen contract or require an explicit revalidation."""

    raw = task.metadata.get("contract")
    stored_digest = task.metadata.get("contract_digest")
    if not isinstance(raw, Mapping) or not isinstance(stored_digest, str):
        raise WorkflowError(f"task {task.id} predates frozen contracts; revalidate it before use")
    try:
        contract = TaskContract.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise WorkflowError(
            f"task {task.id} has an invalid frozen contract; revalidate it: {exc}"
        ) from exc
    if stored_digest != contract.digest:
        raise WorkflowError(f"task {task.id} frozen contract digest does not match; revalidate it")
    instruction_digest = hashlib.sha256(task.instruction.encode("utf-8")).hexdigest()
    if instruction_digest != contract.instruction_sha256:
        raise WorkflowError(
            f"task {task.id} instruction differs from its frozen contract; revalidate it"
        )
    archive_oid = task.metadata.get("base_archive_oid")
    if not isinstance(archive_oid, str) or not archive_oid:
        raise WorkflowError(f"task {task.id} is missing base_archive_oid; revalidate it")
    expected_id = _task_id_from_values(
        candidate_id=task.candidate_id,
        base_sha=task.base_sha,
        fix_sha=task.fix_sha,
        gold_oid=task.gold_patch_oid,
        test_oid=task.test_patch_oid,
        archive_oid=archive_oid,
        contract_digest=contract.digest,
    )
    if task.id != expected_id:
        raise WorkflowError(
            f"task {task.id} identity does not bind its frozen contract; revalidate it"
        )
    return contract


def _task_digest(task: Task) -> str:
    """Hash only portable, immutable task semantics used during execution."""

    contract = _contract_from_task(task)
    archive_oid = task.metadata.get("base_archive_oid")
    payload = {
        "schema_version": "repotrials.task-digest/v1",
        "task_id": task.id,
        "candidate_id": task.candidate_id,
        "base_sha": task.base_sha,
        "fix_sha": task.fix_sha,
        "instruction_sha256": contract.instruction_sha256,
        "gold_patch_oid": task.gold_patch_oid,
        "test_patch_oid": task.test_patch_oid,
        "base_archive_oid": archive_oid,
        "source_files": list(task.source_files),
        "test_files": list(task.test_files),
        "fail_to_pass": list(contract.fail_to_pass),
        "pass_to_pass": list(contract.pass_to_pass),
        "contract_digest": contract.digest,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _restore_synthetic_git(workspace: Path, sealed_git: Path) -> None:
    """Atomically quarantine agent-controlled Git metadata and restore the baseline."""

    if workspace.is_symlink() or not workspace.is_dir():
        raise SandboxError("agent workspace was deleted or replaced")
    if sealed_git.is_symlink() or not sealed_git.is_dir():
        raise SandboxError("evaluator Git backup is missing or invalid")
    git_dir = workspace / ".git"
    if os.path.lexists(git_dir):
        quarantine = sealed_git.parent / f"agent-git-{uuid.uuid4().hex}"
        os.replace(git_dir, quarantine)
    shutil.copytree(sealed_git, git_dir)


def _run_group(name: str) -> str:
    safe = "".join(character.lower() if character.isalnum() else "-" for character in name)
    safe = "-".join(filter(None, safe.split("-")))[:32] or "agent"
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"{safe}-{timestamp}-{uuid.uuid4().hex[:6]}"


def _run_group_manifest_path(
    state_dir: Path,
    run_group: str,
    *,
    prepare: bool = False,
) -> Path:
    """Resolve a group manifest without allowing metadata to escape the runs root."""

    try:
        identifier = managed_component(run_group, label="run group")
        relative = Path("runs") / identifier / "group.json"
        if prepare:
            return prepare_managed_file(state_dir, relative)
        return managed_path(state_dir, relative, expected="file")
    except StatePathError as exc:
        raise WorkflowError(f"unsafe run group path: {exc}") from exc


def _reject_duplicate_selectors(selectors: Sequence[str], *, label: str) -> None:
    """Reject repeated selectors before any state-changing workflow work begins."""

    seen: set[str] = set()
    duplicates: list[str] = []
    for selector in selectors:
        if selector in seen and selector not in duplicates:
            duplicates.append(selector)
        seen.add(selector)
    if duplicates:
        raise WorkflowError(f"duplicate {label} selector(s): {', '.join(duplicates)}")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _paired_key(run: Run) -> tuple[str, int]:
    attempt = run.metadata.get("attempt", 1)
    return run.task_id, int(attempt) if isinstance(attempt, int | str) else 1


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return ()


def _expand_junit_command(command: str, junit_path: str) -> tuple[str, ...]:
    arguments = shlex.split(command, posix=True)
    return tuple(argument.replace("{junit}", junit_path) for argument in arguments)


def _render_harbor_grader(
    *,
    test_command: str,
    setup_commands: Sequence[str],
    hidden_patch_name: str,
    expected_f2p: Sequence[str],
    expected_p2p: Sequence[str],
    protected_paths: Sequence[str],
    submission_paths: Sequence[str],
    max_changed_files: int,
    max_patch_bytes: int,
    timeout_seconds: int,
) -> str:
    """Render a standalone verifier with no RepoTrials package dependency."""

    settings = json.dumps(
        {
            "test_command": test_command,
            "setup_commands": list(setup_commands),
            "setup_script": ".repotrials-setup.py",
            "hidden_patch": hidden_patch_name,
            "submission_patch": AGENT_PATCH_PATH,
            "submission_paths": list(submission_paths),
            "f2p": list(expected_f2p),
            "p2p": list(expected_p2p),
            "protected": list(protected_paths),
            "max_changed_files": max_changed_files,
            "max_patch_bytes": max_patch_bytes,
            "timeout_seconds": timeout_seconds,
        },
        sort_keys=True,
    )
    return f"""#!/usr/bin/env python3
import fnmatch
import functools
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET

SETTINGS = json.loads({settings!r})
WORKSPACE = Path(os.environ.get("REPOTRIALS_VERIFIER_WORKSPACE", "/workspace/repo"))
TESTS = Path(os.environ.get("REPOTRIALS_VERIFIER_TESTS", "/tests"))
LOGS = Path(os.environ.get("REPOTRIALS_VERIFIER_LOGS", "/logs"))
SUBMISSION_PATCH = Path(
    os.environ.get("REPOTRIALS_VERIFIER_PATCH", SETTINGS["submission_patch"])
)

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def match_path(value, pattern):
    normalized = pattern.replace("\\\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = unicodedata.normalize("NFC", normalized).casefold()
    candidate = unicodedata.normalize("NFC", value).casefold()
    path_parts = tuple(part for part in candidate.split("/") if part)
    pattern_parts = tuple(part for part in normalized.split("/") if part)

    @functools.lru_cache(maxsize=None)
    def walk(path_index, pattern_index):
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        part = pattern_parts[pattern_index]
        if part == "**":
            return walk(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and walk(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], part)
            and walk(path_index + 1, pattern_index + 1)
        )

    return walk(0, 0)

def protected(relative):
    value = relative.as_posix()
    return any(match_path(value, pattern) for pattern in SETTINGS["protected"])

def snapshot():
    result = {{}}
    for path in WORKSPACE.rglob("*"):
        relative = path.relative_to(WORKSPACE).as_posix()
        if path.is_symlink():
            raise RuntimeError("submission created a symlink: " + relative)
        if path.is_file():
            result[relative] = (digest(path), path.stat().st_mode & 0o7777)
        elif not path.is_dir():
            raise RuntimeError("submission created a special file: " + relative)
    return result

def setup_snapshot():
    result = {{}}
    for path in WORKSPACE.rglob("*"):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(WORKSPACE).as_posix()
            result[relative] = digest(path)
    return result

def apply_submission():
    if SUBMISSION_PATCH.is_symlink() or not SUBMISSION_PATCH.is_file():
        raise RuntimeError("agent submission patch is missing or not a regular file")
    patch_bytes = SUBMISSION_PATCH.stat().st_size
    if patch_bytes > SETTINGS["max_patch_bytes"]:
        raise RuntimeError("agent submission patch exceeds its frozen byte limit")

    before = snapshot()
    if patch_bytes:
        applied = subprocess.run(
            [
                "git",
                "apply",
                "--whitespace=nowarn",
                "--recount",
                "--",
                str(SUBMISSION_PATCH),
            ],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
        )
        if applied.returncode:
            raise RuntimeError(
                "agent submission patch could not be applied: " + applied.stderr[:2000]
            )
    after = snapshot()
    changed = sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )
    if len(changed) > SETTINGS["max_changed_files"]:
        raise RuntimeError("agent submission changes too many files")
    allowed = set(SETTINGS["submission_paths"])
    outside = [path for path in changed if path not in allowed]
    if outside:
        raise RuntimeError("agent submission changed a path outside its allowlist: " + outside[0])
    protected_changes = [path for path in changed if protected(Path(path))]
    if protected_changes:
        raise RuntimeError("agent submission changed a protected path: " + protected_changes[0])
    return changed

def run_setup():
    if not SETTINGS["setup_commands"]:
        return
    before = setup_snapshot()
    setup_runner = TESTS / SETTINGS["setup_script"]
    if setup_runner.is_symlink() or not setup_runner.is_file():
        raise RuntimeError("frozen setup runner is missing or not a regular file")
    process = subprocess.run(
        [os.sys.executable, str(setup_runner), str(SETTINGS["timeout_seconds"])],
        cwd=WORKSPACE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if process.returncode == 124:
        raise RuntimeError("setup command timed out")
    if process.returncode:
        raise RuntimeError("setup command failed with exit code " + str(process.returncode))
    after = setup_snapshot()
    for path in sorted(before):
        if after.get(path) != before[path]:
            raise RuntimeError("setup changed an existing workspace path: " + path)
    for path in sorted(set(after) - set(before)):
        if protected(Path(path)):
            raise RuntimeError("setup created a protected workspace path: " + path)

def outcomes(path):
    payload = Path(path).read_bytes()
    if len(payload) > 16 * 1024 * 1024:
        raise RuntimeError("JUnit XML exceeds 16 MiB")
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise RuntimeError("JUnit declarations and entities are rejected")
    result = {{}}
    identifiers = {{}}
    root = ET.fromstring(payload)
    for suite in root.iter():
        if suite.tag.rsplit("}}", 1)[-1] != "testsuite":
            continue
        suite_name = suite.attrib.get("name", "").strip()
        for case in suite:
            if case.tag.rsplit("}}", 1)[-1] != "testcase":
                continue
            name = case.attrib.get("name", "").strip()
            if not name:
                raise RuntimeError("JUnit testcase has no name")
            classname = case.attrib.get("classname", "").strip()
            file_name = case.attrib.get("file", "").strip().replace("\\\\", "/")
            prefix = classname or file_name or suite_name
            base_id = prefix + "::" + name if prefix else name
            duplicate = identifiers.get(base_id, 0) + 1
            identifiers[base_id] = duplicate
            test_id = base_id if duplicate == 1 else base_id + "#" + str(duplicate)
            child_tags = {{child.tag.rsplit("}}", 1)[-1] for child in case}}
            skipped_child = next(
                (
                    child
                    for child in case
                    if child.tag.rsplit("}}", 1)[-1] == "skipped"
                ),
                None,
            )
            declared_status = case.attrib.get("status", "").strip().lower()
            if "failure" in child_tags:
                status = "failed"
            elif "error" in child_tags:
                status = "error"
            elif skipped_child is not None:
                kind = skipped_child.attrib.get("type", "").lower()
                status = "xfailed" if "xfail" in kind else "skipped"
            elif declared_status in ("notrun", "disabled", "skipped"):
                status = "skipped"
            else:
                status = "passed"
            result[test_id] = status
            if len(result) > 100000:
                raise RuntimeError("JUnit XML has too many testcases")
    return result

def drop_privileges():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        os.setgroups([])
        os.setgid(65534)
        os.setuid(65534)

def run_tests(command):
    for private_root in (TESTS, LOGS):
        if private_root.exists():
            private_root.chmod(0o700)
    should_drop = (
        hasattr(os, "geteuid")
        and os.geteuid() == 0
        and os.environ.get("REPOTRIALS_VERIFIER_DISABLE_PRIVDROP") != "1"
    )
    preexec = drop_privileges if should_drop else None
    process = subprocess.Popen(
        command,
        cwd=WORKSPACE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        preexec_fn=preexec,
    )
    try:
        return process.wait(timeout=SETTINGS["timeout_seconds"])
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("test command timed out") from exc
    finally:
        if hasattr(os, "killpg"):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

def validate_outcomes(observed, expected, returncode):
    if expected - set(observed):
        raise RuntimeError("expected tests were not collected")
    if any(observed[name] not in ("passed", "xpassed") for name in expected):
        raise RuntimeError("one or more expected tests failed")
    if any(
        status not in ("passed", "xpassed", "skipped", "xfailed")
        for status in observed.values()
    ):
        raise RuntimeError("the collected test suite contains a failed or errored case")
    if returncode:
        raise RuntimeError("test suite failed")

def main():
    apply_submission()
    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "--recount", str(TESTS / SETTINGS["hidden_patch"])],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
    )
    if applied.returncode:
        raise RuntimeError("hidden tests could not be applied: " + applied.stderr)
    run_setup()
    descriptor, junit = tempfile.mkstemp(prefix="repotrials-junit-", suffix=".xml")
    os.close(descriptor)
    Path(junit).unlink(missing_ok=True)
    command = [item.replace("{{junit}}", junit) for item in shlex.split(SETTINGS["test_command"])]
    returncode = run_tests(command)
    if not Path(junit).is_file():
        raise RuntimeError("test command did not write JUnit XML")
    observed = outcomes(junit)
    expected = set(SETTINGS["f2p"]) | set(SETTINGS["p2p"])
    validate_outcomes(observed, expected, returncode)
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"RepoTrials verifier: {{exc}}", file=os.sys.stderr)
        raise SystemExit(1)
"""


def _write_json(path: Path, value: Any, *, private: bool = False) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _write_text(path, payload, private=private)


def _write_text(path: Path, value: str, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        if private:
            temporary.chmod(0o600)
        os.replace(temporary, path)
        if private:
            path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["RepoTrialsWorkflow", "TaskContract", "WorkflowError"]
