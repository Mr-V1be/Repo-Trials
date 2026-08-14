"""Export private RepoTrials tasks to Harbor's separate-verifier format."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import re
import shlex
import shutil
import tarfile
import tempfile
import tomllib
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

FileContent = str | bytes | os.PathLike[str]
_SAFE_NAME = re.compile(r"[^a-z0-9._-]+")
_BARE_TOML_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_CONTAINER_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")
BASE_ARCHIVE_NAME = ".repotrials-base.tar"
AGENT_PATCH_PATH = "/tmp/agent.patch"
_BASE_ARCHIVE_NAME = BASE_ARCHIVE_NAME
_BASE_SHA_PATH = "/opt/repotrials-base-sha"
_SETUP_SCRIPT_NAME = ".repotrials-setup.py"
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 100_000
_MAX_ARCHIVE_EXPANDED_BYTES = 1024 * 1024 * 1024
_PYTEST_VERSION = "9.1.1"
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class HarborExportError(RuntimeError):
    pass


def _single_line(value: str, field_name: str) -> str:
    if not value or any(char in value for char in "\r\n\x00"):
        raise ValueError(f"{field_name} must be a non-empty single-line string")
    return value


def _slug(value: str) -> str:
    slug = _SAFE_NAME.sub("-", value.lower()).strip("-._")
    if not slug:
        raise ValueError("task id does not contain a registry-safe character")
    return slug[:120]


def _safe_relative(path: str) -> PurePosixPath:
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        pure.is_absolute()
        or not pure.parts
        or normalized != pure.as_posix()
        or any(part in ("", ".", "..") for part in pure.parts)
    ):
        raise ValueError(f"unsafe exported file path: {path!r}")
    for part in pure.parts:
        if (
            part != unicodedata.normalize("NFC", part)
            or part.endswith((" ", "."))
            or ":" in part
            or any(ord(char) < 32 for char in part)
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED
        ):
            raise ValueError(f"non-portable exported file path: {path!r}")
    return pure


def _file_bytes(content: FileContent) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, os.PathLike):
        return Path(content).read_bytes()
    return content.encode("utf-8")


def _tree_digest(root: Path) -> str:
    """Hash canonical relative paths, node kinds, and regular-file bytes."""

    if root.is_symlink() or not root.is_dir():
        raise HarborExportError(f"Harbor task destination is not a directory: {root}")
    digest = hashlib.sha256()
    entries = sorted(root.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
    for path in entries:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            raise HarborExportError(f"Harbor task tree contains a symlink: {path}")
        if path.is_dir():
            digest.update(b"D" + len(relative).to_bytes(8, "big") + relative)
            continue
        if not path.is_file():
            raise HarborExportError(f"Harbor task tree contains a special file: {path}")
        size = path.stat().st_size
        digest.update(b"F" + len(relative).to_bytes(8, "big") + relative + size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _validate_base_archive(data: bytes) -> None:
    """Reject tar members that could escape the image work directory."""

    if len(data) > _MAX_ARCHIVE_BYTES:
        raise ValueError(f"base_archive exceeds {_MAX_ARCHIVE_BYTES} bytes")

    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            members = archive.getmembers()
    except (tarfile.TarError, OSError) as exc:
        raise ValueError("base_archive must be a readable tar archive") from exc
    if not members:
        raise ValueError("base_archive cannot be empty")
    if len(members) > _MAX_ARCHIVE_MEMBERS:
        raise ValueError(f"base_archive has more than {_MAX_ARCHIVE_MEMBERS} members")
    expanded = 0
    for member in members:
        name = PurePosixPath(member.name.replace("\\", "/"))
        if name.is_absolute() or any(part in ("", ".", "..") for part in name.parts):
            raise ValueError(f"unsafe base archive member: {member.name!r}")
        if any(part.lower() in {".git", ".repotrials"} for part in name.parts):
            raise ValueError(f"private or historical metadata in base archive: {member.name!r}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unsupported archive member in base archive: {member.name!r}")
        if member.issym() or member.islnk():
            raise ValueError(f"archive links are not supported: {member.name!r}")
        if member.mode & 0o7000:
            raise ValueError(f"special permission bits in base archive: {member.name!r}")
        if member.isfile():
            expanded += member.size
            if expanded > _MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError(f"base_archive expands beyond {_MAX_ARCHIVE_EXPANDED_BYTES} bytes")


def _freeze_files(
    files: Mapping[str, FileContent],
    *,
    reserved: tuple[str, ...],
) -> Mapping[str, bytes]:
    frozen: dict[str, bytes] = {}
    seen: set[str] = set()
    reserved_keys = {name.casefold() for name in reserved}
    for raw_name, content in files.items():
        name = _safe_relative(raw_name).as_posix()
        key = unicodedata.normalize("NFC", name).casefold()
        if key in reserved_keys:
            raise ValueError(f"exported files cannot replace generated path: {raw_name!r}")
        if key in seen:
            raise ValueError(f"case-insensitive exported path collision: {raw_name!r}")
        seen.add(key)
        frozen[name] = _file_bytes(content)
    return MappingProxyType(frozen)


def _toml_string(value: str) -> str:
    # JSON string escaping is compatible with TOML basic strings for the subset
    # used here (including Unicode and control-character escapes).
    return json.dumps(value, ensure_ascii=False)


def _toml_key(value: str) -> str:
    return value if _BARE_TOML_KEY.fullmatch(value) else _toml_string(value)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite metadata floats are not supported")
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if value is None:
        return _toml_string("null")
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, (str, int, float, bool)) for item in value
    ):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    # Preserve structured metadata without inventing nested TOML tables.
    return _toml_string(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _render_setup_runner(commands: tuple[str, ...]) -> bytes:
    """Render the no-shell argv runner shared by build and verification phases."""

    payload = json.dumps(commands, ensure_ascii=True, separators=(",", ":"))
    source = f"""#!/usr/bin/env python3
import json
import os
import shlex
import signal
import subprocess
import sys

COMMANDS = json.loads({payload!r})


def terminate(process):
    if hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()


def main():
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else None
    if timeout is not None and timeout <= 0:
        raise SystemExit("setup timeout must be positive")
    for raw_command in COMMANDS:
        arguments = shlex.split(raw_command, posix=True)
        try:
            process = subprocess.Popen(arguments, shell=False, start_new_session=True)
        except OSError as exc:
            print("setup command could not start: " + str(exc), file=sys.stderr)
            return 127
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate(process)
            process.wait()
            print("setup command timed out", file=sys.stderr)
            return 124
        if returncode:
            return returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
    return source.encode("utf-8")


DEFAULT_TEST_SCRIPT = """#!/bin/sh
set +e
python /tests/grader.py
status=$?
mkdir -p /logs/verifier
if [ "$status" -eq 0 ]; then
  printf '1\n' > /logs/verifier/reward.txt
else
  printf '0\n' > /logs/verifier/reward.txt
fi
exit 0
"""


@dataclass(frozen=True, slots=True)
class HarborTaskSpec:
    task_id: str
    instruction: str
    agent_base_image: str
    verifier_base_image: str
    verifier_files: Mapping[str, FileContent]
    base_archive: FileContent | None = None
    setup_commands: tuple[str, ...] = ()
    test_script: FileContent | None = None
    description: str = "Private coding-agent evaluation generated by RepoTrials"
    keywords: tuple[str, ...] = ("software-engineering", "bug-fix", "private-eval")
    metadata: Mapping[str, Any] = field(default_factory=dict)
    submission_paths: tuple[str, ...] = ()
    max_patch_bytes: int = 1_000_000
    collect_timeout_sec: float = 60.0
    artifact_paths: tuple[str, ...] = field(default=(AGENT_PATCH_PATH,), init=False)
    agent_timeout_sec: float = 1800.0
    verifier_timeout_sec: float = 600.0
    setup_timeout_sec: float = 600.0
    build_timeout_sec: float = 600.0
    cpus: int = 2
    memory_mb: int = 4096
    storage_mb: int | None = None
    network_mode: str = "no-network"
    workdir: str = "/workspace/repo"
    agent_user: str | int | None = None
    verifier_user: str | int | None = None
    environment_files: Mapping[str, FileContent] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _single_line(self.task_id, "task_id")
        _single_line(self.agent_base_image, "agent_base_image")
        _single_line(self.verifier_base_image, "verifier_base_image")
        if not self.instruction.strip():
            raise ValueError("instruction cannot be empty")
        if self.network_mode not in {"no-network", "allowlist", "public"}:
            raise ValueError("unsupported network mode")
        if self.network_mode == "allowlist":
            # No allowed-host field is exposed here intentionally: an empty
            # Harbor allowlist denies all egress and is the safe default.
            pass
        if not _CONTAINER_PATH.fullmatch(self.workdir) or ".." in PurePosixPath(self.workdir).parts:
            raise ValueError("workdir must be a canonical absolute Linux container path")
        if (
            isinstance(self.cpus, bool)
            or not isinstance(self.cpus, int)
            or self.cpus < 1
            or isinstance(self.memory_mb, bool)
            or not isinstance(self.memory_mb, int)
            or self.memory_mb < 1
        ):
            raise ValueError("resource limits must be positive")
        if self.storage_mb is not None and (
            isinstance(self.storage_mb, bool)
            or not isinstance(self.storage_mb, int)
            or self.storage_mb < 1
        ):
            raise ValueError("storage_mb must be positive")
        for label, value in (
            ("agent_timeout_sec", self.agent_timeout_sec),
            ("verifier_timeout_sec", self.verifier_timeout_sec),
            ("setup_timeout_sec", self.setup_timeout_sec),
            ("build_timeout_sec", self.build_timeout_sec),
            ("collect_timeout_sec", self.collect_timeout_sec),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"{label} must be finite and positive")
        if (
            isinstance(self.max_patch_bytes, bool)
            or not isinstance(self.max_patch_bytes, int)
            or not 1 <= self.max_patch_bytes <= _MAX_ARCHIVE_BYTES
        ):
            raise ValueError(f"max_patch_bytes must be between 1 and {_MAX_ARCHIVE_BYTES}")
        if isinstance(self.submission_paths, str) or not self.submission_paths:
            raise ValueError("submission_paths must contain at least one repository path")
        normalized_submission_paths: list[str] = []
        submission_keys: set[str] = set()
        for raw_path in self.submission_paths:
            if not isinstance(raw_path, str):
                raise ValueError("submission_paths must contain strings")
            relative = _safe_relative(raw_path)
            if any(part.casefold() in {".git", ".repotrials"} for part in relative.parts):
                raise ValueError("submission_paths cannot include evaluator metadata")
            path = relative.as_posix()
            key = unicodedata.normalize("NFC", path).casefold()
            if key in submission_keys:
                raise ValueError("submission_paths must be portable and unique")
            submission_keys.add(key)
            normalized_submission_paths.append(path)
        object.__setattr__(self, "submission_paths", tuple(normalized_submission_paths))
        if any(
            not command.strip() or any(char in command for char in "\r\n\x00")
            for command in self.setup_commands
        ):
            raise ValueError("setup_commands must contain non-empty single-line commands")
        object.__setattr__(
            self,
            "verifier_files",
            _freeze_files(
                self.verifier_files,
                reserved=("Dockerfile", "test.sh", _BASE_ARCHIVE_NAME, _SETUP_SCRIPT_NAME),
            ),
        )
        object.__setattr__(
            self,
            "environment_files",
            _freeze_files(
                self.environment_files,
                reserved=("Dockerfile", _BASE_ARCHIVE_NAME, _SETUP_SCRIPT_NAME),
            ),
        )
        if self.test_script is None and "grader.py" not in self.verifier_files:
            raise ValueError("default test script requires verifier_files['grader.py']")
        if self.test_script is not None:
            object.__setattr__(self, "test_script", _file_bytes(self.test_script))
        if self.base_archive is not None:
            archive = _file_bytes(self.base_archive)
            _validate_base_archive(archive)
            object.__setattr__(self, "base_archive", archive)

    @property
    def slug(self) -> str:
        return _slug(self.task_id)

    @property
    def collect_command(self) -> str:
        """Return the bounded atomic patch collector executed by Harbor."""

        temporary_path = AGENT_PATCH_PATH + ".tmp"
        limiter = (
            "import sys;"
            f"d=sys.stdin.buffer.read({self.max_patch_bytes + 1});"
            f"len(d)<={self.max_patch_bytes} or sys.exit(65);"
            "sys.stdout.buffer.write(d)"
        )
        return " ".join(
            (
                "set -euo pipefail;",
                "umask 077;",
                f"rm -f -- {shlex.quote(AGENT_PATCH_PATH)} {shlex.quote(temporary_path)};",
                f"cd -- {shlex.quote(self.workdir)};",
                f"base=$(cat -- {shlex.quote(_BASE_SHA_PATH)});",
                'git cat-file -e "$base^{commit}";',
                "rm -f -- .repotrials-junit.xml .coverage;",
                "git add -A -- .;",
                "git --literal-pathspecs diff --cached --binary --full-index "
                '--no-color --no-ext-diff "$base" -- . | ',
                f"python -c {shlex.quote(limiter)} > {shlex.quote(temporary_path)};",
                f"mv -f -- {shlex.quote(temporary_path)} {shlex.quote(AGENT_PATCH_PATH)}",
            )
        )


class HarborExporter:
    """Materialize Harbor tasks below one private output directory."""

    def __init__(self, destination_root: str | os.PathLike[str]):
        self.destination_root = Path(destination_root)

    def export(self, spec: HarborTaskSpec) -> Path:
        root = self.destination_root
        root.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            root.chmod(0o700)
        destination = root / spec.slug
        temporary = Path(tempfile.mkdtemp(prefix=f".{spec.slug}.", dir=root))
        try:
            self._populate(temporary, spec)
            if destination.exists() or destination.is_symlink():
                if _tree_digest(temporary) != _tree_digest(destination):
                    raise FileExistsError(
                        f"existing Harbor task differs from immutable task {spec.task_id}: "
                        f"{destination}"
                    )
                shutil.rmtree(temporary)
                return destination
            try:
                temporary.replace(destination)
            except OSError:
                # A concurrent identical export is harmless; every other race
                # fails closed and leaves the established tree untouched.
                if not destination.exists() or _tree_digest(temporary) != _tree_digest(destination):
                    raise
                shutil.rmtree(temporary)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return destination

    # Alias selected for callers that prefer a more explicit verb.
    export_task = export

    def _populate(self, directory: Path, spec: HarborTaskSpec) -> None:
        environment = directory / "environment"
        tests = directory / "tests"
        environment.mkdir()
        tests.mkdir()

        (directory / "instruction.md").write_text(
            spec.instruction.rstrip() + "\n", encoding="utf-8", newline="\n"
        )
        (directory / "task.toml").write_text(self._task_toml(spec), encoding="utf-8", newline="\n")
        (environment / "Dockerfile").write_text(
            self._agent_dockerfile(spec), encoding="utf-8", newline="\n"
        )
        (tests / "Dockerfile").write_text(
            self._verifier_dockerfile(spec), encoding="utf-8", newline="\n"
        )
        script = _file_bytes(spec.test_script or DEFAULT_TEST_SCRIPT)
        (tests / "test.sh").write_bytes(script.rstrip(b"\r\n") + b"\n")

        if spec.base_archive is not None:
            archive = _file_bytes(spec.base_archive)
            (environment / _BASE_ARCHIVE_NAME).write_bytes(archive)
            (tests / _BASE_ARCHIVE_NAME).write_bytes(archive)
        if spec.setup_commands:
            setup_script = _render_setup_runner(spec.setup_commands)
            (environment / _SETUP_SCRIPT_NAME).write_bytes(setup_script)
            (tests / _SETUP_SCRIPT_NAME).write_bytes(setup_script)

        self._write_files(environment, spec.environment_files)
        self._write_files(tests, spec.verifier_files)

    @staticmethod
    def _write_files(root: Path, files: Mapping[str, FileContent]) -> None:
        for relative_name, content in sorted(files.items()):
            relative = _safe_relative(relative_name)
            destination = root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(_file_bytes(content))

    @staticmethod
    def _agent_dockerfile(spec: HarborTaskSpec) -> str:
        lines = [
            f"FROM {spec.agent_base_image}",
            "USER root",
            "RUN if command -v git >/dev/null 2>&1 && command -v bash >/dev/null 2>&1; "
            "then :; elif command -v apt-get >/dev/null 2>&1; then apt-get update "
            "&& apt-get install -y --no-install-recommends git bash "
            "&& rm -rf /var/lib/apt/lists/*; else echo 'RepoTrials requires Git and Bash' "
            ">&2; exit 1; fi",
            f'RUN python -c "import pytest" 2>/dev/null || '
            f"python -m pip install --no-cache-dir pytest=={_PYTEST_VERSION}",
            f"WORKDIR {spec.workdir}",
        ]
        if spec.base_archive is not None:
            lines.extend(
                (
                    f"COPY {_BASE_ARCHIVE_NAME} /tmp/{_BASE_ARCHIVE_NAME}",
                    f"RUN tar -xf /tmp/{_BASE_ARCHIVE_NAME} -C {spec.workdir} "
                    f"&& rm -f /tmp/{_BASE_ARCHIVE_NAME}",
                )
            )
        if spec.setup_commands:
            lines.extend(
                (
                    f"COPY {_SETUP_SCRIPT_NAME} /tmp/{_SETUP_SCRIPT_NAME}",
                    f"RUN python /tmp/{_SETUP_SCRIPT_NAME} {spec.setup_timeout_sec} "
                    f"&& rm -f /tmp/{_SETUP_SCRIPT_NAME}",
                )
            )
        if spec.base_archive is not None:
            lines.append(
                "RUN git init --quiet --initial-branch=trial "
                "&& git config user.name RepoTrials "
                "&& git config user.email trial@invalid.local "
                "&& git config commit.gpgsign false "
                "&& git add --all "
                "&& git commit --quiet --no-verify -m 'RepoTrials sealed baseline' "
                f"&& git rev-parse HEAD > {_BASE_SHA_PATH} "
                f"&& chmod 0444 {_BASE_SHA_PATH}"
            )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _verifier_dockerfile(spec: HarborTaskSpec) -> str:
        lines = [
            f"FROM {spec.verifier_base_image}",
            "USER root",
            "RUN if command -v git >/dev/null 2>&1 && command -v bash >/dev/null 2>&1; "
            "then :; elif command -v apt-get >/dev/null 2>&1; then apt-get update "
            "&& apt-get install -y --no-install-recommends git bash "
            "&& rm -rf /var/lib/apt/lists/*; else echo 'RepoTrials requires Git and Bash' "
            ">&2; exit 1; fi",
            f'RUN python -c "import pytest" 2>/dev/null || '
            f"python -m pip install --no-cache-dir pytest=={_PYTEST_VERSION}",
            f"WORKDIR {spec.workdir}",
        ]
        if spec.base_archive is not None:
            remove_archive = "" if spec.setup_commands else f" && rm -f /tmp/{_BASE_ARCHIVE_NAME}"
            lines.extend(
                (
                    f"COPY {_BASE_ARCHIVE_NAME} /tmp/{_BASE_ARCHIVE_NAME}",
                    f"RUN tar -xf /tmp/{_BASE_ARCHIVE_NAME} -C {spec.workdir}{remove_archive}",
                )
            )
        if spec.setup_commands:
            lines.extend(
                (
                    f"COPY {_SETUP_SCRIPT_NAME} /tmp/{_SETUP_SCRIPT_NAME}",
                    f"RUN python /tmp/{_SETUP_SCRIPT_NAME} {spec.setup_timeout_sec} "
                    f"&& rm -f /tmp/{_SETUP_SCRIPT_NAME}",
                )
            )
            if spec.base_archive is not None:
                lines.append(
                    f"RUN find {spec.workdir} -mindepth 1 -maxdepth 1 "
                    "-exec rm -rf -- {} + "
                    f"&& tar -xf /tmp/{_BASE_ARCHIVE_NAME} -C {spec.workdir} "
                    f"&& rm -f /tmp/{_BASE_ARCHIVE_NAME}"
                )
        parents = sorted({PurePosixPath(path).parent.as_posix() for path in spec.artifact_paths})
        lines.extend(
            (
                "RUN mkdir -p " + " ".join(parents),
                "COPY . /tests",
                "RUN chmod 0555 /tests/test.sh",
            )
        )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _task_toml(spec: HarborTaskSpec) -> str:
        lines = [
            'schema_version = "1.3"',
            "artifacts = " + _toml_value(list(spec.artifact_paths)),
            "",
            "[task]",
            f"name = {_toml_string('repotrials/' + spec.slug)}",
            f"description = {_toml_string(spec.description)}",
            "keywords = " + _toml_value(list(spec.keywords)),
            "",
            "[metadata]",
            f"repotrials_task_id = {_toml_string(spec.task_id)}",
        ]
        for key, value in sorted(spec.metadata.items()):
            if key == "repotrials_task_id":
                continue
            lines.append(f"{_toml_key(str(key))} = {_toml_value(value)}")
        lines.extend(
            [
                "",
                "[agent]",
                f"timeout_sec = {spec.agent_timeout_sec}",
            ]
        )
        if spec.agent_user is not None:
            lines.append(f"user = {_toml_value(spec.agent_user)}")
        lines.extend(
            [
                "",
                "[environment]",
                f"network_mode = {_toml_string(spec.network_mode)}",
                f"build_timeout_sec = {spec.build_timeout_sec}",
                f"cpus = {spec.cpus}",
                f"memory_mb = {spec.memory_mb}",
            ]
        )
        if spec.storage_mb is not None:
            lines.append(f"storage_mb = {spec.storage_mb}")
        lines.extend(
            [
                "",
                "[verifier]",
                'environment_mode = "separate"',
                'network_mode = "no-network"',
                f"timeout_sec = {spec.verifier_timeout_sec}",
            ]
        )
        if spec.verifier_user is not None:
            lines.append(f"user = {_toml_value(spec.verifier_user)}")
        lines.extend(
            [
                "",
                "[[verifier.collect]]",
                'service = "main"',
                f"command = {_toml_string(spec.collect_command)}",
                f"timeout_sec = {spec.collect_timeout_sec}",
                "",
                "[verifier.environment]",
                'network_mode = "no-network"',
                f"build_timeout_sec = {spec.build_timeout_sec}",
                f"cpus = {spec.cpus}",
                f"memory_mb = {spec.memory_mb}",
            ]
        )
        if spec.storage_mb is not None:
            lines.append(f"storage_mb = {spec.storage_mb}")
        return "\n".join(lines) + "\n"


def validate_harbor_task(path: str | os.PathLike[str]) -> tuple[str, ...]:
    """Return structural errors without invoking Harbor or Docker."""

    root = Path(path)
    required = (
        "task.toml",
        "instruction.md",
        "environment/Dockerfile",
        "tests/Dockerfile",
        "tests/test.sh",
    )
    errors = [f"missing {name}" for name in required if not (root / name).is_file()]
    task_file = root / "task.toml"
    if task_file.is_file():
        try:
            with task_file.open("rb") as handle:
                manifest = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            errors.append(f"invalid task.toml: {exc}")
        else:
            schema_version = manifest.get("schema_version")
            verifier = manifest.get("verifier")
            artifacts = manifest.get("artifacts")
            if schema_version != "1.3":
                errors.append("task schema is not Harbor 1.3")
            if not isinstance(verifier, Mapping) or verifier.get("environment_mode") != "separate":
                errors.append("verifier is not separate")
            if not isinstance(verifier, Mapping) or verifier.get("network_mode") != "no-network":
                errors.append("verifier network is not disabled")
            verifier_environment = (
                verifier.get("environment") if isinstance(verifier, Mapping) else None
            )
            if (
                not isinstance(verifier_environment, Mapping)
                or verifier_environment.get("network_mode") != "no-network"
            ):
                errors.append("separate verifier environment network is not disabled")
            if artifacts != [AGENT_PATCH_PATH]:
                errors.append("agent artifact is not the canonical submission patch")
            collect = verifier.get("collect") if isinstance(verifier, Mapping) else None
            if (
                not isinstance(collect, list)
                or len(collect) != 1
                or not isinstance(collect[0], Mapping)
                or collect[0].get("service") != "main"
                or not isinstance(collect[0].get("command"), str)
                or not collect[0]["command"].strip()
            ):
                errors.append("bounded main-service patch collect hook is missing")
    verifier_dockerfile = root / "tests" / "Dockerfile"
    if verifier_dockerfile.is_file():
        dockerfile = verifier_dockerfile.read_text(encoding="utf-8", errors="replace")
        if "COPY . /tests" not in dockerfile:
            errors.append("verifier image does not own /tests")
        if "RUN mkdir -p " not in dockerfile:
            errors.append("verifier image does not create artifact parents")
    return tuple(errors)


def export_harbor_task(spec: HarborTaskSpec, destination_root: str | os.PathLike[str]) -> Path:
    return HarborExporter(destination_root).export(spec)


__all__ = [
    "AGENT_PATCH_PATH",
    "BASE_ARCHIVE_NAME",
    "DEFAULT_TEST_SCRIPT",
    "FileContent",
    "HarborExportError",
    "HarborExporter",
    "HarborTaskSpec",
    "export_harbor_task",
    "validate_harbor_task",
]
