"""Configuration loading, validation, and project initialization.

The core intentionally uses the standard library only.  This keeps ``uvx
repotrials`` and isolated CI runners fast, while language profiles can add
their own optional dependencies later.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .git import GitError, GitRepository
from .state_paths import (
    StatePathError,
    ensure_managed_directory,
    ensure_root_directory,
    managed_path,
    prepare_managed_file,
    validate_directory_creation,
    validate_managed_file,
    validate_root_directory,
)

CONFIG_NAME = "repotrials.toml"
STATE_DIR_NAME = ".repotrials"

_STATE_DIRECTORIES = (
    "objects",
    "objects/sha256",
    "private",
    "tasks",
    "runs",
    "exports",
    "reports",
    "cache",
    "tmp",
)
_STATE_FILES = (
    "README.txt",
    "state.sqlite3",
    "state.sqlite3-journal",
    "state.sqlite3-shm",
    "state.sqlite3-wal",
)


class ConfigError(ValueError):
    """Raised when a RepoTrials configuration is invalid."""


@dataclasses.dataclass(frozen=True, slots=True)
class RepositorySettings:
    default_branch: str = "main"
    exposure: str = "private_unpublished"


@dataclasses.dataclass(frozen=True, slots=True)
class TestSettings:
    setup: tuple[str, ...] = ()
    command: str = "python -m pytest -q --junitxml={junit}"
    source_globs: tuple[str, ...] = (
        "src/**/*.py",
        "*.py",
    )
    test_globs: tuple[str, ...] = (
        "tests/**/*.py",
        "test/**/*.py",
        "**/test_*.py",
        "**/*_test.py",
    )
    ignored_globs: tuple[str, ...] = (
        "docs/**",
        "**/*.md",
        "**/*.rst",
        "**/__pycache__/**",
        "**/*.lock",
    )
    protected_paths: tuple[str, ...] = (
        ".git/**",
        ".repotrials/**",
        ".github/**",
        "tests/**",
        "test/**",
        "test_*.py",
        "*_test.py",
        "conftest.py",
        "**/conftest.py",
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
        "pyproject.toml",
        "uv.lock",
    )


@dataclasses.dataclass(frozen=True, slots=True)
class MiningSettings:
    max_files: int = 8
    max_changed_lines: int = 400
    require_test_changes: bool = True
    include_merges: bool = True
    keyword_pattern: str = r"(?i)\b(fix|bug|regression|crash|incorrect|race|null|nil)\b"


@dataclasses.dataclass(frozen=True, slots=True)
class ValidationSettings:
    backend: str = "local"
    repeats: int = 3
    timeout_seconds: int = 600
    require_pass_to_pass: bool = False
    docker_image: str = "python:3.12-slim"


@dataclasses.dataclass(frozen=True, slots=True)
class ExecutionSettings:
    backend: str = "local"
    network: str = "provider-only"
    timeout_seconds: int = 1200
    attempts: int = 3
    cpus: float = 2.0
    memory_mb: int = 4096


@dataclasses.dataclass(frozen=True, slots=True)
class RepoTrialsConfig:
    root: Path
    repository: RepositorySettings = dataclasses.field(default_factory=RepositorySettings)
    test: TestSettings = dataclasses.field(default_factory=TestSettings)
    mining: MiningSettings = dataclasses.field(default_factory=MiningSettings)
    validation: ValidationSettings = dataclasses.field(default_factory=ValidationSettings)
    execution: ExecutionSettings = dataclasses.field(default_factory=ExecutionSettings)

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_NAME

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR_NAME

    @property
    def database_path(self) -> Path:
        return self.state_dir / "state.sqlite3"

    def to_safe_dict(self) -> dict[str, Any]:
        """Return serializable settings without host-specific private paths."""

        payload = dataclasses.asdict(self)
        payload["root"] = "."
        return payload


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise ConfigError(f"[{name}] must be a TOML table")
    return value


def _tuple_of_strings(
    section: Mapping[str, Any], key: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    value = section.get(key, default)
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise ConfigError(f"{key} must be an array of strings")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ConfigError(f"{key} must contain only non-empty strings")
    return tuple(value)


def _known_values(section: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(section) - allowed
    if unknown:
        values = ", ".join(sorted(unknown))
        raise ConfigError(f"unknown option(s) in [{name}]: {values}")


def _string(section: Mapping[str, Any], key: str, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def _boolean(section: Mapping[str, Any], key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false")
    return value


def _integer(section: Mapping[str, Any], key: str, default: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer")
    return value


def _number(section: Mapping[str, Any], key: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{key} must be a number")
    return float(value)


def load_config(start: str | os.PathLike[str] = ".") -> RepoTrialsConfig:
    """Load ``repotrials.toml`` from *start* or one of its parents."""

    start_path = Path(start).expanduser().resolve()
    if start_path.is_file():
        config_path = start_path
        root = config_path.parent
    else:
        found = _find_upwards(start_path, CONFIG_NAME)
        if found is None:
            raise ConfigError(
                f"{CONFIG_NAME} was not found from {start_path}; run `repotrials init` first"
            )
        config_path = found
        root = config_path.parent

    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {config_path}: {exc}") from exc

    repo = _section(data, "repository")
    test = _section(data, "test")
    mining = _section(data, "mining")
    validation = _section(data, "validation")
    execution = _section(data, "execution")

    _known_values(repo, {"default_branch", "exposure"}, "repository")
    _known_values(
        test,
        {"setup", "command", "source_globs", "test_globs", "ignored_globs", "protected_paths"},
        "test",
    )
    _known_values(
        mining,
        {
            "max_files",
            "max_changed_lines",
            "require_test_changes",
            "include_merges",
            "keyword_pattern",
        },
        "mining",
    )
    _known_values(
        validation,
        {
            "backend",
            "repeats",
            "timeout_seconds",
            "require_pass_to_pass",
            "docker_image",
        },
        "validation",
    )
    _known_values(
        execution,
        {"backend", "network", "timeout_seconds", "attempts", "cpus", "memory_mb"},
        "execution",
    )

    test_defaults = TestSettings()
    mining_defaults = MiningSettings()
    config = RepoTrialsConfig(
        root=root,
        repository=RepositorySettings(
            default_branch=_string(repo, "default_branch", "main"),
            exposure=_string(repo, "exposure", "private_unpublished"),
        ),
        test=TestSettings(
            setup=_tuple_of_strings(test, "setup", ()),
            command=_string(test, "command", test_defaults.command),
            source_globs=_tuple_of_strings(test, "source_globs", test_defaults.source_globs),
            test_globs=_tuple_of_strings(test, "test_globs", test_defaults.test_globs),
            ignored_globs=_tuple_of_strings(test, "ignored_globs", test_defaults.ignored_globs),
            protected_paths=_tuple_of_strings(
                test, "protected_paths", test_defaults.protected_paths
            ),
        ),
        mining=MiningSettings(
            max_files=_integer(mining, "max_files", 8),
            max_changed_lines=_integer(mining, "max_changed_lines", 400),
            require_test_changes=_boolean(mining, "require_test_changes", True),
            include_merges=_boolean(mining, "include_merges", True),
            keyword_pattern=_string(mining, "keyword_pattern", mining_defaults.keyword_pattern),
        ),
        validation=ValidationSettings(
            backend=_string(validation, "backend", "local"),
            repeats=_integer(validation, "repeats", 3),
            timeout_seconds=_integer(validation, "timeout_seconds", 600),
            require_pass_to_pass=_boolean(validation, "require_pass_to_pass", False),
            docker_image=_string(validation, "docker_image", "python:3.12-slim"),
        ),
        execution=ExecutionSettings(
            backend=_string(execution, "backend", "local"),
            network=_string(execution, "network", "provider-only"),
            timeout_seconds=_integer(execution, "timeout_seconds", 1200),
            attempts=_integer(execution, "attempts", 3),
            cpus=_number(execution, "cpus", 2.0),
            memory_mb=_integer(execution, "memory_mb", 4096),
        ),
    )
    validate_config(config)
    return config


def validate_config(config: RepoTrialsConfig) -> None:
    """Validate values that TOML's type system cannot express."""

    if config.repository.exposure not in {
        "private_unpublished",
        "public_post_model_release",
        "public_pre_model_release",
        "unknown",
    }:
        raise ConfigError("repository.exposure has an unsupported value")
    if config.validation.backend not in {"local", "docker"}:
        raise ConfigError("validation.backend must be 'local' or 'docker'")
    if config.execution.backend not in {"local", "harbor"}:
        raise ConfigError("execution.backend must be 'local' or 'harbor'")
    if config.execution.network not in {"none", "provider-only", "public"}:
        raise ConfigError("execution.network must be none, provider-only, or public")
    if config.execution.cpus <= 0:
        raise ConfigError("execution.cpus must be positive")
    for label, value in {
        "mining.max_files": config.mining.max_files,
        "mining.max_changed_lines": config.mining.max_changed_lines,
        "validation.repeats": config.validation.repeats,
        "validation.timeout_seconds": config.validation.timeout_seconds,
        "execution.timeout_seconds": config.execution.timeout_seconds,
        "execution.attempts": config.execution.attempts,
        "execution.memory_mb": config.execution.memory_mb,
    }.items():
        if value < 1:
            raise ConfigError(f"{label} must be at least 1")
    if "{junit}" not in config.test.command:
        raise ConfigError("test.command must contain the {junit} placeholder")
    if any("\n" in command or "\r" in command for command in config.test.setup):
        raise ConfigError("test.setup commands must be single-line strings")
    try:
        re.compile(config.mining.keyword_pattern)
    except re.error as exc:
        raise ConfigError(f"mining.keyword_pattern is invalid: {exc}") from exc


def initialize_project(root: str | os.PathLike[str], *, force: bool = False) -> Path:
    """Create config and private state directories under *root*."""

    requested = Path(root).expanduser().resolve()
    try:
        project_root = GitRepository.discover(requested).path
    except (GitError, ValueError) as exc:
        raise ConfigError(
            f"cannot initialize RepoTrials: {requested} is not inside a Git working tree"
        ) from exc
    config_path = project_root / CONFIG_NAME
    try:
        config_exists = managed_path(project_root, CONFIG_NAME, expected="file").exists()
    except StatePathError as exc:
        raise ConfigError(f"unsafe configuration path: {exc}") from exc

    # A tracked configuration is the expected state in a fresh clone.  Validate
    # and preserve it, then repair only the private local scaffolding.
    if config_exists and not force:
        load_config(config_path)

    try:
        _preflight_project_state(project_root)
    except StatePathError as exc:
        raise ConfigError(f"unsafe private state path: {exc}") from exc

    if not config_exists or force:
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8", newline="\n")

    try:
        ensure_project_state(project_root, preflight=False)
    except StatePathError as exc:
        raise ConfigError(f"unsafe private state path: {exc}") from exc
    return config_path


def ensure_project_state(project_root: str | os.PathLike[str], *, preflight: bool = True) -> Path:
    """Create private local scaffolding and Git exclusion for a configured repo.

    This function intentionally does not create or modify ``repotrials.toml``.
    Workflow entry points can call it after loading a tracked configuration so
    fresh clones acquire the same private-state protections as ``init``.
    """

    root = Path(project_root).expanduser().resolve()
    if preflight:
        _preflight_project_state(root)

    state = ensure_root_directory(root / STATE_DIR_NAME)
    for relative in _STATE_DIRECTORIES:
        ensure_managed_directory(state, relative)
    try:
        state.chmod(0o700)
        (state / "private").chmod(0o700)
    except OSError:
        pass
    marker = prepare_managed_file(state, "README.txt")
    if not marker.exists():
        marker.write_text(
            "PRIVATE: this directory may contain proprietary source patches, hidden tests, "
            "and run logs.\n",
            encoding="utf-8",
            newline="\n",
        )
    _exclude_private_state(root)
    return state


def _preflight_project_state(project_root: Path) -> None:
    """Reject unsafe state and exclude paths before initialization writes."""

    state = project_root / STATE_DIR_NAME
    if validate_root_directory(state):
        for relative in _STATE_DIRECTORIES:
            managed_path(state, relative, expected="directory")
        for relative in _STATE_FILES:
            validate_managed_file(state, relative)
    _validate_git_exclude_path(_git_exclude_path(project_root))


def doctor(config: RepoTrialsConfig) -> list[tuple[str, bool, str]]:
    """Return deterministic preflight checks used by CLI and tests."""

    checks: list[tuple[str, bool, str]] = []
    git = shutil.which("git")
    checks.append(("git", git is not None, git or "not found"))
    docker = shutil.which("docker")
    needed = config.validation.backend == "docker"
    checks.append(("docker", docker is not None or not needed, docker or "not found (optional)"))
    harbor = shutil.which("harbor")
    checks.append(
        (
            "harbor",
            True,
            harbor or "not found (optional; export does not require it)",
        )
    )
    checks.append(("repository", (config.root / ".git").exists(), str(config.root)))
    try:
        state_ok = validate_root_directory(config.state_dir)
        state_detail = str(config.state_dir)
    except StatePathError as exc:
        state_ok = False
        state_detail = str(exc)
    checks.append(("state", state_ok, state_detail))
    return checks


def _find_upwards(start: Path, name: str) -> Path | None:
    current = start
    while True:
        candidate = current / name
        if candidate.is_file():
            return candidate
        if current.parent == current:
            return None
        current = current.parent


def _exclude_private_state(project_root: Path) -> None:
    """Keep private state out of Git without editing the project's .gitignore."""

    exclude = _git_exclude_path(project_root)
    _validate_git_exclude_path(exclude)
    ensure_root_directory(exclude.parent)
    existing = exclude.read_text(encoding="utf-8", errors="replace") if exclude.exists() else ""
    entries = {line.strip() for line in existing.splitlines()}
    if f"/{STATE_DIR_NAME}/" in entries or f"{STATE_DIR_NAME}/" in entries:
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with exclude.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{prefix}/{STATE_DIR_NAME}/\n")


def _git_exclude_path(project_root: Path) -> Path:
    """Return Git's repository-local exclude path for normal and linked worktrees."""

    result = subprocess.run(
        ("git", "rev-parse", "--git-path", "info/exclude"),
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        detail = result.stderr.strip() or "Git did not return an exclude path"
        raise StatePathError(f"cannot locate Git info/exclude: {detail}")
    candidate = Path(result.stdout.strip())
    if not candidate.is_absolute():
        candidate = project_root / candidate
    # Lexical absolute normalization does not dereference the final path.
    return Path(os.path.abspath(candidate))


def _validate_git_exclude_path(exclude: Path) -> None:
    """Reject redirection of Git's local exclusion file before appending to it."""

    if not validate_root_directory(exclude.parent):
        validate_directory_creation(exclude.parent)
        return
    validate_managed_file(exclude.parent, exclude.name)


DEFAULT_CONFIG = r"""# RepoTrials stores benchmark oracles under .repotrials/. Never commit that directory.
[repository]
default_branch = "main"
exposure = "private_unpublished"

[test]
setup = []
command = "python -m pytest -q --junitxml={junit}"
source_globs = ["src/**/*.py", "*.py"]
test_globs = ["tests/**/*.py", "test/**/*.py", "**/test_*.py", "**/*_test.py"]
ignored_globs = ["docs/**", "**/*.md", "**/*.rst", "**/__pycache__/**", "**/*.lock"]
protected_paths = [
  ".git/**",
  ".repotrials/**",
  ".github/**",
  "tests/**",
  "test/**",
  "test_*.py",
  "*_test.py",
  "conftest.py",
  "**/conftest.py",
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
  "pyproject.toml",
  "uv.lock",
]

[mining]
max_files = 8
max_changed_lines = 400
require_test_changes = true
include_merges = true
keyword_pattern = '''(?i)\b(fix|bug|regression|crash|incorrect|race|null|nil)\b'''

[validation]
# Use "docker" for containerized validation. "local" requires CLI --unsafe-local opt-in.
backend = "local"
repeats = 3
timeout_seconds = 600
require_pass_to_pass = false
docker_image = "python:3.12-slim"

[execution]
backend = "local"
network = "provider-only"
timeout_seconds = 1200
attempts = 3
cpus = 2.0
memory_mb = 4096
"""
