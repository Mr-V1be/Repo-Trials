"""Workspace construction and submission handling for local trials.

This module provides isolation *mechanics*, not a hardened sandbox.  The local
backend executes repository and agent code with the current user's authority;
production evaluations should use the exported Harbor task in a VM/container.
"""

from __future__ import annotations

import hashlib
import io
import os
import shlex
import shutil
import stat
import subprocess
import tarfile
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from .execution import Command, CommandBackend, CommandResult


class SandboxError(RuntimeError):
    """Raised when an archive, patch, or synthetic workspace is unsafe."""


_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 100_000
_MAX_ARCHIVE_EXPANDED_BYTES = 1024 * 1024 * 1024


def safe_extract_tar(archive: bytes, destination: str | os.PathLike[str]) -> Path:
    """Extract a Git archive while preventing traversal and special files.

    Git repositories may contain symlinks, but creating them is not reliably
    permissionless on Windows.  RepoTrials therefore rejects link entries in
    the local reference implementation.  Container profiles can opt into a
    more capable extractor later without weakening this boundary.
    """

    if len(archive) > _MAX_ARCHIVE_BYTES:
        raise SandboxError(f"archive exceeds {_MAX_ARCHIVE_BYTES} bytes")
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as bundle:
        members = bundle.getmembers()
        if not members:
            raise SandboxError("archive is empty")
        if len(members) > _MAX_ARCHIVE_MEMBERS:
            raise SandboxError(f"archive has more than {_MAX_ARCHIVE_MEMBERS} members")
        expanded = 0
        portable_paths: set[str] = set()
        for member in members:
            relative = PurePosixPath(member.name)
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in ("", ".", "..") for part in relative.parts)
            ):
                raise SandboxError(f"unsafe archive path: {member.name!r}")
            if member.issym() or member.islnk():
                raise SandboxError(f"archive links are not supported: {member.name!r}")
            if member.isdev() or member.isfifo():
                raise SandboxError(f"archive special file rejected: {member.name!r}")
            if not (member.isfile() or member.isdir()):
                raise SandboxError(f"unsupported archive member: {member.name!r}")
            if member.mode & 0o7000:
                raise SandboxError(f"archive special permission bits rejected: {member.name!r}")
            portable = unicodedata.normalize("NFC", relative.as_posix()).casefold()
            if portable in portable_paths:
                raise SandboxError(f"archive has a portable path collision: {member.name!r}")
            portable_paths.add(portable)
            if member.isfile():
                expanded += member.size
                if expanded > _MAX_ARCHIVE_EXPANDED_BYTES:
                    raise SandboxError(
                        f"archive expands beyond {_MAX_ARCHIVE_EXPANDED_BYTES} bytes"
                    )
            target = root.joinpath(*relative.parts)
            if not _is_within(target, root):
                raise SandboxError(f"archive path escapes destination: {member.name!r}")

        for member in members:
            relative = PurePosixPath(member.name)
            target = root.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise SandboxError(f"unsupported archive member: {member.name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise SandboxError(f"cannot read archive member: {member.name!r}")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            # Preserve only executable bits; never reproduce setuid/setgid bits.
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    return root


def initialize_synthetic_git(workspace: str | os.PathLike[str]) -> str:
    """Create a one-commit repository with no remote or historical objects."""

    root = Path(workspace).resolve()
    _git(root, "init", "--quiet", "--initial-branch=trial")
    _git(root, "config", "user.name", "RepoTrials")
    _git(root, "config", "user.email", "trial@invalid.local")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "core.filemode", "false")
    _git(root, "add", "--all")
    _git(
        root,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "--no-verify",
        "-m",
        "RepoTrials sealed baseline",
    )
    sha = _git(root, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    # Defensive check: callers rely on this repository containing exactly one
    # reachable commit and no configured network location.
    count = _git(root, "rev-list", "--count", "--all").stdout.decode("ascii").strip()
    if count != "1":
        raise SandboxError("synthetic repository contains unexpected history")
    if _git(root, "remote", check=False).stdout.strip():
        raise SandboxError("synthetic repository unexpectedly contains a remote")
    return sha


def collect_submission_patch(workspace: str | os.PathLike[str]) -> bytes:
    """Return tracked and untracked agent changes as one binary Git patch."""

    root = Path(workspace).resolve()
    _prepare_submission_index(root)
    result = _git(
        root,
        "-c",
        "core.quotePath=false",
        "diff",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "HEAD",
        "--",
    )
    return result.stdout


def collect_submission_paths(workspace: str | os.PathLike[str]) -> tuple[str, ...]:
    """Return verbatim changed Git paths and reject unsafe live node types."""

    root = Path(workspace).resolve()
    _prepare_submission_index(root)
    result = _git(
        root,
        "-c",
        "core.quotePath=false",
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        "HEAD",
        "--",
    )
    encoded_paths = result.stdout.split(b"\x00")
    if encoded_paths and encoded_paths[-1] == b"":
        encoded_paths.pop()
    try:
        paths = tuple(path.decode("utf-8", errors="strict") for path in encoded_paths)
    except UnicodeDecodeError as exc:
        raise SandboxError("submission contains a non-UTF-8 Git path") from exc
    if any(not path or "\x00" in path for path in paths):
        raise SandboxError("submission contains an empty or NUL Git path")
    for path in paths:
        _require_regular_submission_node(root, path)
    return tuple(sorted(set(paths)))


def _prepare_submission_index(root: Path) -> None:
    for generated in (".repotrials-junit.xml", ".coverage"):
        (root / generated).unlink(missing_ok=True)
    # Intent-to-add makes untracked regular files visible to `git diff` without
    # staging their content in the final submission.
    _git(root, "add", "--intent-to-add", "--all")


def _require_regular_submission_node(root: Path, relative: str) -> None:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise SandboxError(f"submission contains an unsafe Git path: {relative!r}")
    current = root
    for index, part in enumerate(pure.parts):
        current /= part
        if not os.path.lexists(current):
            return
        mode = os.lstat(current).st_mode
        if stat.S_ISLNK(mode):
            raise SandboxError(f"submission path is or traverses a symlink: {relative!r}")
        if index < len(pure.parts) - 1:
            if not stat.S_ISDIR(mode):
                raise SandboxError(f"submission path traverses a non-directory: {relative!r}")
        elif not stat.S_ISREG(mode):
            raise SandboxError(f"submission path is not a regular file: {relative!r}")


def apply_patch(workspace: str | os.PathLike[str], patch: bytes) -> None:
    """Apply a binary Git patch to a workspace or raise ``SandboxError``."""

    if not patch:
        return
    result = subprocess.run(
        ("git", "apply", "--whitespace=nowarn", "--recount", "-"),
        cwd=Path(workspace),
        input=patch,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SandboxError(f"submission patch cannot be applied: {detail}")


def expand_command(
    command: Command,
    *,
    workspace: str | os.PathLike[str],
    instruction_path: str | os.PathLike[str] | None = None,
) -> tuple[str, ...]:
    """Expand documented placeholders without evaluating a shell."""

    if isinstance(command, str):
        arguments = shlex.split(command, posix=True)
    else:
        arguments = [os.fspath(value) for value in command]
    replacements = {
        "workspace": os.fspath(Path(workspace).resolve()),
        "instruction": os.fspath(Path(instruction_path).resolve()) if instruction_path else "",
    }
    try:
        return tuple(argument.format_map(replacements) for argument in arguments)
    except KeyError as exc:
        raise ValueError(f"unknown command placeholder: {exc.args[0]}") from exc


def run_agent_command(
    backend: CommandBackend,
    command: Command,
    *,
    workspace: str | os.PathLike[str],
    instruction: str,
    instruction_path: str | os.PathLike[str],
    timeout: float,
    environment: Mapping[str, str] | None = None,
) -> CommandResult:
    env = {
        "REPOTRIALS_INSTRUCTION": instruction,
        "REPOTRIALS_INSTRUCTION_PATH": os.fspath(Path(instruction_path).resolve()),
        "REPOTRIALS_WORKSPACE": os.fspath(Path(workspace).resolve()),
    }
    if environment:
        env.update(environment)
    return backend.run(
        expand_command(command, workspace=workspace, instruction_path=instruction_path),
        cwd=workspace,
        env=env,
        timeout=timeout,
    )


def command_digest(command: str | Sequence[str]) -> str:
    if isinstance(command, str):
        payload = command
    else:
        payload = "\x00".join(os.fspath(item) for item in command)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _git(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    null_device = "NUL" if os.name == "nt" else "/dev/null"
    result = subprocess.run(
        ("git", "-c", f"core.hooksPath={null_device}", *arguments),
        cwd=root,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SandboxError(f"git {' '.join(arguments)} failed: {detail}")
    return result


__all__ = [
    "SandboxError",
    "apply_patch",
    "collect_submission_patch",
    "collect_submission_paths",
    "command_digest",
    "expand_command",
    "initialize_synthetic_git",
    "run_agent_command",
    "safe_extract_tar",
]
