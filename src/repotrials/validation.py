"""Repeatable BASE/RED/GOLD validation for mined repository tasks."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import shlex
import shutil
import tempfile
import unicodedata
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from pathlib import Path, PurePosixPath

from .execution import (
    Command,
    CommandBackend,
    CommandResult,
    DockerCommandBackend,
    LocalCommandBackend,
)
from .junit import (
    PASSING_TEST_STATUSES,
    SUITE_HEALTHY_TEST_STATUSES,
    JUnitParseError,
    TestOutcome,
    parse_junit_xml,
)

PatchSource = str | bytes | os.PathLike[str]

DEFAULT_PROTECTED_PATHS: tuple[str, ...] = (
    ".git/**",
    ".repotrials/**",
    "tests/**",
    "test/**",
    "test_*.py",
    "*_test.py",
    "conftest.py",
    "**/conftest.py",
    ".github/**",
    "pytest.py",
    "pytest/**",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "setup.py",
    ".coveragerc",
    "sitecustomize.py",
    "**/sitecustomize.py",
    "usercustomize.py",
    "**/usercustomize.py",
)

DEFAULT_TEST_PATHS: tuple[str, ...] = (
    "tests/**",
    "test/**",
    "test_*.py",
    "*_test.py",
    "**/test_*.py",
    "**/*_test.py",
    "conftest.py",
    "**/conftest.py",
)


class ValidationPhase(StrEnum):
    BASE = "BASE"
    RED = "RED"
    GOLD = "GOLD"


@dataclass(frozen=True, slots=True)
class IntegrityViolation:
    path: str
    reason: str
    pattern: str | None = None


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    paths: tuple[str, ...]
    violations: tuple[IntegrityViolation, ...]
    patch_bytes: int

    @property
    def ok(self) -> bool:
        return not self.violations

    def require_ok(self) -> IntegrityReport:
        if not self.ok:
            raise ProtectedPathError(self)
        return self


class ProtectedPathError(ValueError):
    def __init__(self, report: IntegrityReport):
        self.report = report
        details = ", ".join(f"{item.path}: {item.reason}" for item in report.violations)
        super().__init__(f"patch violates integrity policy ({details})")


def read_patch(source: PatchSource | None) -> bytes:
    """Read a patch from bytes, a path, or a literal patch string."""

    if source is None:
        return b""
    if isinstance(source, bytes):
        return source
    if isinstance(source, os.PathLike):
        return Path(source).read_bytes()
    if "\n" not in source and "\r" not in source:
        try:
            candidate = Path(source)
            if candidate.is_file():
                return candidate.read_bytes()
        except OSError:
            pass
    return source.encode("utf-8")


def _expand_junit(command: Command, junit_path: str) -> Command:
    """Expand the public ``{junit}`` placeholder inside the fresh workspace."""

    if isinstance(command, str):
        return command.replace("{junit}", junit_path)
    return tuple(os.fspath(argument).replace("{junit}", junit_path) for argument in command)


def _header_path(raw: str) -> str | None:
    raw = raw.strip()
    if not raw:
        return None
    # Unified diff headers may append a timestamp after a tab.
    raw = raw.split("\t", 1)[0]
    try:
        parsed = shlex.split(raw, posix=True)
        if parsed:
            raw = parsed[0]
    except ValueError:
        raw = raw.strip('"')
    if raw == "/dev/null":
        return None
    if raw.startswith("a/") or raw.startswith("b/"):
        raw = raw[2:]
    return raw.replace("\\", "/")


def extract_patch_paths(patch: PatchSource | None) -> tuple[str, ...]:
    """Extract every old/new path mentioned by a unified Git patch."""

    text = read_patch(patch).decode("utf-8", errors="replace")
    paths: set[str] = set()
    for line in text.splitlines():
        candidates: list[str] = []
        if line.startswith("diff --git "):
            try:
                parts = shlex.split(line, posix=True)
            except ValueError:
                parts = line.split()
            if len(parts) >= 4:
                candidates.extend((parts[2], parts[3]))
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            candidates.append(line.split(" ", 2)[2])
        elif line.startswith("--- ") or line.startswith("+++ "):
            candidates.append(line[4:])
        for candidate in candidates:
            normalized = _header_path(candidate)
            if normalized:
                paths.add(normalized)
    return tuple(sorted(paths))


def _unsafe_path(path: str) -> str | None:
    if not path or "\x00" in path:
        return "empty or NUL-containing path"
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in path):
        return "control-character path"
    if len(path) >= 2 and path[0].isalpha() and path[1] == ":":
        return "Windows drive-qualified path"
    pure = PurePosixPath(path)
    if pure.is_absolute():
        return "absolute path"
    if any(part in ("", ".", "..") for part in pure.parts):
        return "path traversal or non-canonical path"
    if any(part.lower() == ".git" for part in pure.parts):
        return "Git metadata path"
    return None


def _matches(path: str, pattern: str) -> bool:
    """Match Git paths with segment-aware, zero-directory ``**`` semantics."""

    normalized = pattern.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = unicodedata.normalize("NFC", normalized).casefold()
    candidate = unicodedata.normalize("NFC", path.replace("\\", "/")).casefold()
    if not normalized or _unsafe_path(normalized):
        return False

    path_parts = tuple(PurePosixPath(candidate).parts)
    pattern_parts = tuple(PurePosixPath(normalized).parts)

    @cache
    def match(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        part = pattern_parts[pattern_index]
        if part == "**":
            return match(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and match(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], part)
            and match(path_index + 1, pattern_index + 1)
        )

    return match(0, 0)


def check_patch_integrity(
    patch: PatchSource | None,
    protected_paths: Iterable[str] = DEFAULT_PROTECTED_PATHS,
    *,
    allowed_paths: Iterable[str] | None = None,
    exact_allowed_paths: Iterable[str] | None = None,
    observed_paths: Iterable[str] | None = None,
    max_files: int = 100,
    max_patch_bytes: int = 1_000_000,
) -> IntegrityReport:
    """Check traversal, size, and protected-path constraints for a patch."""

    if allowed_paths is not None and exact_allowed_paths is not None:
        raise ValueError("glob and exact patch allowlists are mutually exclusive")

    payload = read_patch(patch)
    paths = (
        tuple(sorted(set(observed_paths)))
        if observed_paths is not None
        else extract_patch_paths(payload)
    )
    violations: list[IntegrityViolation] = []
    if len(payload) > max_patch_bytes:
        violations.append(IntegrityViolation("<patch>", f"patch exceeds {max_patch_bytes} bytes"))
    if len(paths) > max_files:
        violations.append(
            IntegrityViolation("<patch>", f"patch changes more than {max_files} files")
        )
    patterns = tuple(protected_paths)
    allow_patterns = tuple(allowed_paths) if allowed_paths is not None else None
    exact_allowlist = set(exact_allowed_paths) if exact_allowed_paths is not None else None
    for path in paths:
        unsafe_reason = _unsafe_path(path)
        if unsafe_reason:
            violations.append(IntegrityViolation(path, unsafe_reason))
            continue
        outside_exact = exact_allowlist is not None and path not in exact_allowlist
        outside_patterns = allow_patterns is not None and not any(
            _matches(path, pattern) for pattern in allow_patterns
        )
        if outside_exact or outside_patterns:
            violations.append(IntegrityViolation(path, "path is outside the allowlist"))
            continue
        for pattern in patterns:
            if _matches(path, pattern):
                violations.append(IntegrityViolation(path, "protected path", pattern=pattern))
                break
    return IntegrityReport(paths, tuple(violations), len(payload))


@dataclass(frozen=True, slots=True)
class ValidationPlan:
    """Inputs required to validate one historical task candidate."""

    base_dir: Path
    test_command: Command
    test_patch: PatchSource
    gold_patch: PatchSource
    setup_commands: tuple[Command, ...] = ()
    repetitions: int = 3
    protected_paths: tuple[str, ...] = DEFAULT_PROTECTED_PATHS
    test_path_patterns: tuple[str, ...] = DEFAULT_TEST_PATHS
    timeout: float | None = 600.0
    test_environment: Mapping[str, str] = field(default_factory=dict)
    junit_path: str = ".repotrials-junit.xml"
    require_pass_to_pass: bool = False
    max_changed_files: int = 100
    max_patch_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_dir", Path(self.base_dir))
        if self.repetitions < 1:
            raise ValueError("repetitions must be at least one")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.max_changed_files < 1 or self.max_patch_bytes < 1:
            raise ValueError("patch limits must be positive")
        junit = PurePosixPath(self.junit_path.replace("\\", "/"))
        if (
            junit.is_absolute()
            or not junit.parts
            or any(part in ("", ".", "..") for part in junit.parts)
        ):
            raise ValueError("junit_path must be a safe workspace-relative path")
        command_values = (
            (self.test_command,)
            if isinstance(self.test_command, str)
            else tuple(os.fspath(item) for item in self.test_command)
        )
        if not any("{junit}" in item for item in command_values):
            raise ValueError("test_command must contain the {junit} placeholder")


@dataclass(frozen=True, slots=True)
class PhaseRun:
    phase: ValidationPhase
    repetition: int
    setup_results: tuple[CommandResult, ...]
    patch_results: tuple[CommandResult, ...]
    test_result: CommandResult | None
    test_outcomes: tuple[TestOutcome, ...]
    error: str | None = None

    @property
    def patches_applied(self) -> bool:
        return bool(self.patch_results) or self.phase is ValidationPhase.BASE

    @property
    def passed(self) -> bool:
        return (
            self.error is None
            and self.test_result is not None
            and self.test_result.ok
            and bool(self.test_outcomes)
            and all(item.status in SUITE_HEALTHY_TEST_STATUSES for item in self.test_outcomes)
        )

    @property
    def timed_out(self) -> bool:
        return bool(self.test_result and self.test_result.timed_out)

    @property
    def collected_count(self) -> int:
        return len(self.test_outcomes)

    @property
    def outcome_signature(self) -> tuple[tuple[str, str], ...]:
        return tuple((item.test_id, item.status) for item in self.test_outcomes)


@dataclass(frozen=True, slots=True)
class ValidationReport:
    runs: tuple[PhaseRun, ...]
    integrity: IntegrityReport
    reasons: tuple[str, ...]
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.reasons

    def runs_for(self, phase: ValidationPhase) -> tuple[PhaseRun, ...]:
        return tuple(run for run in self.runs if run.phase is phase)

    def phase_stable(self, phase: ValidationPhase) -> bool:
        runs = self.runs_for(phase)
        if not runs or any(not run.test_outcomes for run in runs):
            return False
        signatures = {(run.passed, run.outcome_signature) for run in runs}
        return len(signatures) == 1

    @property
    def base_passed(self) -> bool:
        runs = self.runs_for(ValidationPhase.BASE)
        return bool(runs) and all(run.passed for run in runs)

    @property
    def red_failed(self) -> bool:
        runs = self.runs_for(ValidationPhase.RED)
        return bool(runs) and all(not run.passed and run.error is None for run in runs)

    @property
    def gold_passed(self) -> bool:
        runs = self.runs_for(ValidationPhase.GOLD)
        return bool(runs) and all(run.passed for run in runs)


class ValidationRunner:
    """Run each validation state in a fresh copy of the base snapshot."""

    def __init__(self, backend: CommandBackend | None = None):
        self.backend: CommandBackend = backend or LocalCommandBackend()

    def validate(self, plan: ValidationPlan) -> ValidationReport:
        if not plan.base_dir.is_dir():
            raise FileNotFoundError(plan.base_dir)

        gold_integrity = check_patch_integrity(
            plan.gold_patch,
            plan.protected_paths,
            max_files=plan.max_changed_files,
            max_patch_bytes=plan.max_patch_bytes,
        )
        hidden_integrity = check_patch_integrity(
            plan.test_patch,
            (),
            allowed_paths=plan.test_path_patterns,
            max_files=plan.max_changed_files,
            max_patch_bytes=plan.max_patch_bytes,
        )
        reasons: list[str] = []
        if not gold_integrity.ok:
            reasons.append("gold_patch_integrity_failed")
        if not hidden_integrity.ok:
            reasons.append("test_patch_integrity_failed")
        if not read_patch(plan.test_patch):
            reasons.append("test_patch_empty")
        if not read_patch(plan.gold_patch):
            reasons.append("gold_patch_empty")
        if reasons:
            combined = IntegrityReport(
                tuple(sorted(set(gold_integrity.paths + hidden_integrity.paths))),
                gold_integrity.violations + hidden_integrity.violations,
                gold_integrity.patch_bytes + hidden_integrity.patch_bytes,
            )
            return ValidationReport((), combined, tuple(dict.fromkeys(reasons)))

        runs: list[PhaseRun] = []
        setup_sensitive_paths = tuple(
            path for path in gold_integrity.paths if not _path_present(plan.base_dir, path)
        )
        for phase in ValidationPhase:
            for repetition in range(1, plan.repetitions + 1):
                runs.append(self._run_phase(plan, phase, repetition, setup_sensitive_paths))

        for phase in ValidationPhase:
            phase_runs = [run for run in runs if run.phase is phase]
            if not phase_runs or len({run.passed for run in phase_runs}) != 1:
                reasons.append(f"{phase.value.lower()}_unstable")
            collected_sets = {
                tuple(outcome.test_id for outcome in run.test_outcomes) for run in phase_runs
            }
            if len(collected_sets) != 1 or not next(iter(collected_sets), ()):
                reasons.append(f"{phase.value.lower()}_collected_tests_unstable")
            outcome_signatures = {run.outcome_signature for run in phase_runs}
            if len(outcome_signatures) != 1:
                reasons.append(f"{phase.value.lower()}_test_outcomes_unstable")
            if any(run.error for run in phase_runs):
                reasons.append(f"{phase.value.lower()}_execution_error")
            if any(run.error == "setup_failed" for run in phase_runs):
                reasons.append(f"{phase.value.lower()}_setup_failed")
            if any(run.error == "setup_mutated_workspace" for run in phase_runs):
                reasons.append(f"{phase.value.lower()}_setup_mutated_workspace")
            reasons.extend(
                sorted(
                    {
                        run.error
                        for run in phase_runs
                        if run.error and run.error.startswith("setup_created_submission_path: ")
                    }
                )
            )
            if any(run.timed_out for run in phase_runs):
                reasons.append(f"{phase.value.lower()}_timeout")

        if not all(run.passed for run in runs if run.phase is ValidationPhase.BASE):
            reasons.append("base_failed")
        if not all(not run.passed for run in runs if run.phase is ValidationPhase.RED):
            reasons.append("red_did_not_fail")
        if not all(run.passed for run in runs if run.phase is ValidationPhase.GOLD):
            reasons.append("gold_failed")
        if any(run.error == "setup_failed" for run in runs):
            reasons.append("setup_failed")
        if any(run.error == "setup_mutated_workspace" for run in runs):
            reasons.append("setup_mutated_workspace")

        base_statuses = self._stable_statuses(runs, ValidationPhase.BASE)
        red_statuses = self._stable_statuses(runs, ValidationPhase.RED)
        gold_statuses = self._stable_statuses(runs, ValidationPhase.GOLD)
        if red_statuses and gold_statuses and set(red_statuses) != set(gold_statuses):
            reasons.append("red_gold_collected_tests_mismatch")
        fail_to_pass = tuple(
            sorted(
                test_id
                for test_id, status in red_statuses.items()
                if status not in PASSING_TEST_STATUSES
                and gold_statuses.get(test_id) in PASSING_TEST_STATUSES
            )
        )
        pass_to_pass = tuple(
            sorted(
                test_id
                for test_id, status in base_statuses.items()
                if status in PASSING_TEST_STATUSES
                and gold_statuses.get(test_id) in PASSING_TEST_STATUSES
            )
        )
        if not fail_to_pass:
            reasons.append("no_fail_to_pass")
        if plan.require_pass_to_pass and not pass_to_pass:
            reasons.append("no_pass_to_pass")

        return ValidationReport(
            tuple(runs),
            gold_integrity,
            tuple(dict.fromkeys(reasons)),
            fail_to_pass,
            pass_to_pass,
        )

    @staticmethod
    def _stable_statuses(runs: Sequence[PhaseRun], phase: ValidationPhase) -> dict[str, str]:
        selected = [run for run in runs if run.phase is phase]
        if not selected or any(not run.test_outcomes for run in selected):
            return {}
        signatures = {run.outcome_signature for run in selected}
        return dict(next(iter(signatures))) if len(signatures) == 1 else {}

    def _run_phase(
        self,
        plan: ValidationPlan,
        phase: ValidationPhase,
        repetition: int,
        setup_sensitive_paths: Sequence[str],
    ) -> PhaseRun:
        with tempfile.TemporaryDirectory(prefix=f"repotrials-{phase.value.lower()}-") as temp:
            workspace = Path(temp) / "repo"
            shutil.copytree(plan.base_dir, workspace, symlinks=True)
            if isinstance(self.backend, DockerCommandBackend):
                with self.backend.session(workspace) as backend:
                    return self._execute_phase(
                        plan,
                        phase,
                        repetition,
                        workspace,
                        backend,
                        setup_sensitive_paths,
                    )
            return self._execute_phase(
                plan,
                phase,
                repetition,
                workspace,
                self.backend,
                setup_sensitive_paths,
            )

    def _execute_phase(
        self,
        plan: ValidationPlan,
        phase: ValidationPhase,
        repetition: int,
        workspace: Path,
        backend: CommandBackend,
        setup_sensitive_paths: Sequence[str],
    ) -> PhaseRun:
        patches: list[bytes] = []
        if phase in (ValidationPhase.RED, ValidationPhase.GOLD):
            patches.append(read_patch(plan.test_patch))
        if phase is ValidationPhase.GOLD:
            patches.append(read_patch(plan.gold_patch))

        setup_results: list[CommandResult] = []
        patch_results: list[CommandResult] = []
        for patch in patches:
            result = self._apply_patch(backend, workspace, patch, plan.timeout)
            patch_results.append(result)
            if not result.ok:
                return PhaseRun(
                    phase,
                    repetition,
                    tuple(setup_results),
                    tuple(patch_results),
                    None,
                    (),
                    error="patch_apply_failed",
                )

        # Dependency setup happens after the phase patches so non-editable
        # builds install the exact BASE/RED/GOLD state being measured.  A
        # Docker phase reuses one container, so global installs persist.
        before_setup = _file_snapshot(workspace)
        for command in plan.setup_commands:
            result = backend.run(
                command,
                cwd=workspace,
                env=plan.test_environment,
                timeout=plan.timeout,
            )
            setup_results.append(result)
            if not result.ok:
                return PhaseRun(
                    phase,
                    repetition,
                    tuple(setup_results),
                    tuple(patch_results),
                    None,
                    (),
                    error="setup_failed",
                )
        mutation = _setup_mutation(workspace, before_setup, plan.protected_paths)
        if mutation is not None:
            return PhaseRun(
                phase,
                repetition,
                tuple(setup_results),
                tuple(patch_results),
                None,
                (),
                error="setup_mutated_workspace",
            )
        if phase is not ValidationPhase.GOLD:
            conflict = next(
                (path for path in setup_sensitive_paths if _path_present(workspace, path)),
                None,
            )
            if conflict is not None:
                return PhaseRun(
                    phase,
                    repetition,
                    tuple(setup_results),
                    tuple(patch_results),
                    None,
                    (),
                    error=f"setup_created_submission_path: {conflict}",
                )

        result = backend.run(
            _expand_junit(plan.test_command, plan.junit_path),
            cwd=workspace,
            env=plan.test_environment,
            timeout=plan.timeout,
        )
        junit_file = workspace.joinpath(*PurePosixPath(plan.junit_path).parts)
        if not junit_file.is_file():
            return PhaseRun(
                phase,
                repetition,
                tuple(setup_results),
                tuple(patch_results),
                result,
                (),
                error="junit_missing",
            )
        try:
            junit_report = parse_junit_xml(junit_file)
        except JUnitParseError:
            return PhaseRun(
                phase,
                repetition,
                tuple(setup_results),
                tuple(patch_results),
                result,
                (),
                error="junit_parse_failed",
            )
        if not junit_report.outcomes:
            return PhaseRun(
                phase,
                repetition,
                tuple(setup_results),
                tuple(patch_results),
                result,
                (),
                error="junit_empty",
            )
        return PhaseRun(
            phase,
            repetition,
            tuple(setup_results),
            tuple(patch_results),
            result,
            junit_report.outcomes,
        )

    @staticmethod
    def _apply_patch(
        backend: CommandBackend,
        workspace: Path,
        patch: bytes,
        timeout: float | None,
    ) -> CommandResult:
        name = f".repotrials-{uuid.uuid4().hex}.patch"
        patch_path = workspace / name
        patch_path.write_bytes(patch)
        try:
            return backend.run(
                ("git", "apply", "--whitespace=nowarn", "--recount", name),
                cwd=workspace,
                timeout=timeout,
            )
        finally:
            patch_path.unlink(missing_ok=True)


def _file_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(root).as_posix()
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _path_present(root: Path, relative: str) -> bool:
    path = root.joinpath(*PurePosixPath(relative).parts)
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _setup_mutation(
    root: Path,
    before: Mapping[str, str],
    protected_paths: Sequence[str],
) -> str | None:
    after = _file_snapshot(root)
    for path, digest in before.items():
        if after.get(path) != digest:
            return path
    for path in after.keys() - before.keys():
        if any(_matches(path, pattern) for pattern in protected_paths):
            return path
    return None


def snapshot_workspace(root: Path) -> dict[str, str]:
    """Capture regular-file contents for setup mutation enforcement."""

    return _file_snapshot(root)


def find_setup_mutation(
    root: Path,
    before: Mapping[str, str],
    protected_paths: Sequence[str],
) -> str | None:
    """Return the first existing or protected path changed by setup."""

    return _setup_mutation(root, before, protected_paths)


def validate_task(plan: ValidationPlan, backend: CommandBackend | None = None) -> ValidationReport:
    return ValidationRunner(backend).validate(plan)


__all__ = [
    "DEFAULT_PROTECTED_PATHS",
    "DEFAULT_TEST_PATHS",
    "IntegrityReport",
    "IntegrityViolation",
    "PatchSource",
    "PhaseRun",
    "ProtectedPathError",
    "ValidationPhase",
    "ValidationPlan",
    "ValidationReport",
    "ValidationRunner",
    "check_patch_integrity",
    "extract_patch_paths",
    "find_setup_mutation",
    "read_patch",
    "snapshot_workspace",
    "validate_task",
]
