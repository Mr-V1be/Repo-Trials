"""Command execution backends used by RepoTrials.

The module intentionally depends only on the Python standard library.  Commands
are always passed as argument vectors and are never evaluated by a shell.  This
is important because repository metadata and task instructions are untrusted
inputs in a benchmark miner.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

Command = Sequence[str | os.PathLike[str]] | str


def _command_tuple(command: Command) -> tuple[str, ...]:
    if isinstance(command, str):
        # RepoTrials configuration uses one portable, POSIX-like quoting
        # grammar on every host. subprocess receives argv directly; no shell is
        # involved, so retaining Windows quote characters would be incorrect.
        values = shlex.split(command, posix=True)
    else:
        values = [os.fspath(value) for value in command]
    if not values or any("\x00" in value for value in values):
        raise ValueError("command must contain at least one non-NUL argument")
    return tuple(values)


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Result of one command invocation."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.returncode == 0

    def check_returncode(self) -> CommandResult:
        if not self.ok:
            raise CommandExecutionError(self)
        return self


class CommandExecutionError(RuntimeError):
    """Raised when ``check=True`` is used and a command does not succeed."""

    def __init__(self, result: CommandResult):
        self.result = result
        reason = "timed out" if result.timed_out else f"exited with status {result.returncode}"
        super().__init__(f"command {result.command!r} {reason}")


@runtime_checkable
class CommandBackend(Protocol):
    """Small execution interface shared by local and container backends."""

    def run(
        self,
        command: Command,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        input_text: str | None = None,
        check: bool = False,
    ) -> CommandResult: ...


@dataclass(slots=True)
class LocalCommandBackend:
    """Execute commands directly on the current machine without a shell."""

    inherit_environment: bool = True
    base_environment: Mapping[str, str] = field(default_factory=dict)

    def run(
        self,
        command: Command,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        input_text: str | None = None,
        check: bool = False,
    ) -> CommandResult:
        argv = _command_tuple(command)
        process_environment: dict[str, str] = dict(os.environ) if self.inherit_environment else {}
        process_environment.update(
            {str(key): str(value) for key, value in self.base_environment.items()}
        )
        if env:
            process_environment.update({str(key): str(value) for key, value in env.items()})

        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=os.fspath(cwd) if cwd is not None else None,
                env=process_environment,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                check=False,
            )
            result = CommandResult(
                command=argv,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_seconds=time.monotonic() - started,
            )
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(
                command=argv,
                returncode=124,
                stdout=_text(exc.stdout),
                stderr=_text(exc.stderr),
                duration_seconds=time.monotonic() - started,
                timed_out=True,
            )

        if check:
            result.check_returncode()
        return result


@dataclass(frozen=True, slots=True)
class DockerMount:
    source: Path
    target: str
    read_only: bool = True

    def __post_init__(self) -> None:
        if not self.target.startswith("/"):
            raise ValueError("Docker mount target must be an absolute container path")


@dataclass(slots=True)
class DockerCommandBackend:
    """Execute argv in an ephemeral Docker container.

    When ``mount_cwd`` is enabled and ``cwd`` names an existing host directory,
    that directory is mounted at ``container_workdir``.  Consequently the same
    validation pipeline can use either ``LocalCommandBackend`` or this backend.
    Docker is an optional runtime dependency; no Python Docker package is used.
    """

    image: str
    platform_name: str | None = None
    docker_executable: str = "docker"
    network: str = "none"
    user: str | None = None
    read_only_root: bool = False
    mount_cwd: bool = True
    container_workdir: str = "/workspace"
    mounts: tuple[DockerMount, ...] = ()
    memory: str | None = None
    cpus: float | None = None
    pids_limit: int = 512
    extra_run_args: tuple[str, ...] = ()
    local_backend: LocalCommandBackend = field(default_factory=LocalCommandBackend)

    def __post_init__(self) -> None:
        if not self.image or any(char in self.image for char in "\r\n\x00"):
            raise ValueError("image must be a non-empty single-line reference")
        if self.platform_name is not None and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*"
            r"(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)?",
            self.platform_name,
        ):
            raise ValueError("platform_name must use Docker's os/architecture[/variant] form")
        if not self.container_workdir.startswith("/"):
            raise ValueError("container_workdir must be absolute")
        if self.cpus is not None and self.cpus <= 0:
            raise ValueError("cpus must be positive")
        if self.pids_limit <= 0:
            raise ValueError("pids_limit must be positive")

    @property
    def available(self) -> bool:
        return shutil.which(self.docker_executable) is not None

    def session(self, cwd: str | os.PathLike[str]) -> DockerCommandSession:
        """Create one container reused by a sequence of phase commands."""

        return DockerCommandSession(self, Path(cwd).expanduser().resolve(strict=True))

    def run(
        self,
        command: Command,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        input_text: str | None = None,
        check: bool = False,
    ) -> CommandResult:
        inner_command = _command_tuple(command)
        descriptor, cid_name = tempfile.mkstemp(prefix="repotrials-docker-", suffix=".cid")
        os.close(descriptor)
        cid_path = Path(cid_name)
        cid_path.unlink(missing_ok=True)
        argv: list[str] = [
            self.docker_executable,
            "run",
            "--rm",
            "--cidfile",
            os.fspath(cid_path),
            "--network",
            self.network,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.pids_limit),
        ]
        if self.platform_name is not None:
            argv.extend(("--platform", self.platform_name))
        if input_text is not None:
            argv.append("--interactive")
        if self.read_only_root:
            argv.append("--read-only")
        if self.user:
            argv.extend(("--user", self.user))
        if self.memory:
            argv.extend(("--memory", self.memory))
        if self.cpus is not None:
            argv.extend(("--cpus", str(self.cpus)))

        for mount in self.mounts:
            source = mount.source.expanduser().resolve(strict=True)
            option = f"type=bind,source={source},target={mount.target}"
            if mount.read_only:
                option += ",readonly"
            argv.extend(("--mount", option))

        container_cwd: str | None = None
        if cwd is not None:
            candidate = Path(cwd).expanduser()
            if self.mount_cwd and candidate.exists() and candidate.is_dir():
                source = candidate.resolve(strict=True)
                argv.extend(
                    (
                        "--mount",
                        f"type=bind,source={source},target={self.container_workdir}",
                    )
                )
                container_cwd = self.container_workdir
            else:
                container_cwd = os.fspath(cwd).replace("\\", "/")
        if container_cwd:
            argv.extend(("--workdir", container_cwd))

        if env:
            for key in sorted(env):
                if not key or "=" in key or "\x00" in key:
                    raise ValueError(f"invalid environment variable name: {key!r}")
                argv.extend(("--env", f"{key}={env[key]}"))

        argv.extend(self.extra_run_args)
        argv.append(self.image)
        argv.extend(inner_command)
        try:
            result = self.local_backend.run(
                argv,
                timeout=timeout,
                input_text=input_text,
                check=False,
            )
            if result.timed_out and cid_path.is_file():
                container_id = cid_path.read_text(encoding="ascii", errors="ignore").strip()
                if re.fullmatch(r"[a-f0-9]{12,64}", container_id):
                    self.local_backend.run(
                        (self.docker_executable, "rm", "--force", container_id),
                        timeout=30,
                        check=False,
                    )
            if check:
                result.check_returncode()
            return result
        finally:
            cid_path.unlink(missing_ok=True)


@dataclass(slots=True)
class DockerCommandSession:
    """A short-lived container used for one BASE, RED, or GOLD phase."""

    backend: DockerCommandBackend
    workspace: Path
    container_id: str = ""

    def __enter__(self) -> DockerCommandSession:
        descriptor, cid_name = tempfile.mkstemp(prefix="repotrials-session-", suffix=".cid")
        os.close(descriptor)
        cid_path = Path(cid_name)
        cid_path.unlink(missing_ok=True)
        argv: list[str] = [
            self.backend.docker_executable,
            "run",
            "--detach",
            "--rm",
            "--cidfile",
            os.fspath(cid_path),
            "--network",
            self.backend.network,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.backend.pids_limit),
        ]
        if self.backend.platform_name is not None:
            argv.extend(("--platform", self.backend.platform_name))
        if self.backend.read_only_root:
            argv.append("--read-only")
        if self.backend.user:
            argv.extend(("--user", self.backend.user))
        if self.backend.memory:
            argv.extend(("--memory", self.backend.memory))
        if self.backend.cpus is not None:
            argv.extend(("--cpus", str(self.backend.cpus)))
        for mount in self.backend.mounts:
            source = mount.source.expanduser().resolve(strict=True)
            option = f"type=bind,source={source},target={mount.target}"
            if mount.read_only:
                option += ",readonly"
            argv.extend(("--mount", option))
        argv.extend(
            (
                "--mount",
                f"type=bind,source={self.workspace},target={self.backend.container_workdir}",
                "--workdir",
                self.backend.container_workdir,
                *self.backend.extra_run_args,
                self.backend.image,
                "python",
                "-c",
                "import time; time.sleep(2147483647)",
            )
        )
        try:
            result = self.backend.local_backend.run(argv, timeout=60, check=False)
            cid = ""
            if cid_path.is_file():
                cid = cid_path.read_text(encoding="ascii", errors="ignore").strip()
            if not cid:
                cid = result.stdout.strip()
            if not result.ok:
                if re.fullmatch(r"[a-f0-9]{12,64}", cid):
                    self.container_id = cid
                    self.close()
                raise CommandExecutionError(result)
            if not re.fullmatch(r"[a-f0-9]{12,64}", cid):
                raise RuntimeError("Docker did not return a valid container ID")
            self.container_id = cid
            return self
        finally:
            cid_path.unlink(missing_ok=True)

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if not self.container_id:
            return
        container_id, self.container_id = self.container_id, ""
        self.backend.local_backend.run(
            (self.backend.docker_executable, "rm", "--force", container_id),
            timeout=30,
            check=False,
        )

    def run(
        self,
        command: Command,
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        input_text: str | None = None,
        check: bool = False,
    ) -> CommandResult:
        if not self.container_id:
            raise RuntimeError("Docker command session is not running")
        inner = _command_tuple(command)
        argv: list[str] = [self.backend.docker_executable, "exec"]
        if input_text is not None:
            argv.append("--interactive")
        container_cwd = self.backend.container_workdir
        if cwd is not None:
            host_cwd = Path(cwd).expanduser().resolve(strict=True)
            try:
                relative = host_cwd.relative_to(self.workspace)
            except ValueError as exc:
                raise ValueError("session cwd must stay within the mounted workspace") from exc
            container_cwd = str(PurePosixPath(self.backend.container_workdir, *relative.parts))
        argv.extend(("--workdir", container_cwd))
        if env:
            for key in sorted(env):
                if not key or "=" in key or "\x00" in key:
                    raise ValueError(f"invalid environment variable name: {key!r}")
                argv.extend(("--env", f"{key}={env[key]}"))
        argv.append(self.container_id)
        argv.extend(inner)
        result = self.backend.local_backend.run(
            argv,
            timeout=timeout,
            input_text=input_text,
            check=False,
        )
        if result.timed_out:
            self.close()
        if check:
            result.check_returncode()
        return result


# A shorter alias is convenient for callers while keeping the explicit class
# name discoverable in generated API documentation.
DockerBackend = DockerCommandBackend


__all__ = [
    "Command",
    "CommandBackend",
    "CommandExecutionError",
    "CommandResult",
    "DockerBackend",
    "DockerCommandBackend",
    "DockerCommandSession",
    "DockerMount",
    "LocalCommandBackend",
]
