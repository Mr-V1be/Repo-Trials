"""Fail-closed helpers for paths containing private RepoTrials state.

The helpers in this module deliberately use ``lstat`` instead of ``resolve``:
resolving an untrusted state path would follow a symlink before it can be
rejected.  They protect against pre-existing link/path confusion.  As with
other portable path checks, callers must still avoid sharing the repository
with an attacker that can replace directories concurrently.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

PathKind = Literal["directory", "file"]
_MANAGED_COMPONENT = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class StatePathError(RuntimeError):
    """Raised when a managed state path is a link or has the wrong type."""


def absolute_path(path: str | os.PathLike[str]) -> Path:
    """Return a lexical absolute path without following filesystem links."""

    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


def managed_component(value: str, *, label: str = "managed identifier") -> str:
    """Validate a task/run/group identity for use as exactly one path component."""

    if not isinstance(value, str) or not _MANAGED_COMPONENT.fullmatch(value):
        raise StatePathError(
            f"{label} must be 1-128 ASCII letters, digits, dots, underscores, or hyphens"
        )
    if value.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES:
        raise StatePathError(f"{label} uses a reserved filesystem name: {value!r}")
    return value


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    # ``is_symlink`` covers Unix links and Windows symbolic links.  Reject all
    # Windows reparse points as well so directory junctions cannot redirect
    # private writes outside the repository.
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _inspect(path: Path, expected: PathKind) -> bool:
    """Validate an existing path, returning ``False`` when it is absent."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StatePathError(f"cannot inspect managed path {path}: {exc}") from exc

    if _is_link_or_reparse(metadata):
        raise StatePathError(f"managed path must not be a symlink or reparse point: {path}")
    valid = (
        stat.S_ISDIR(metadata.st_mode)
        if expected == "directory"
        else stat.S_ISREG(metadata.st_mode)
    )
    if not valid:
        raise StatePathError(f"managed path must be a {expected}: {path}")
    return True


def ensure_root_directory(path: str | os.PathLike[str]) -> Path:
    """Validate or create a standalone managed directory without following links."""

    target = absolute_path(path)
    _, missing = _validated_creation_chain(target)

    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            # A concurrently-created path is accepted only after the same
            # no-link/type validation as a pre-existing path.
            pass
        except OSError as exc:
            raise StatePathError(f"cannot create managed directory {directory}: {exc}") from exc
        _inspect(directory, "directory")
    return target


def _validated_creation_chain(target: Path) -> tuple[Path, list[Path]]:
    """Return the safe existing ancestor and missing descendants for *target*."""

    missing: list[Path] = []
    current = target
    while not _inspect(current, "directory"):
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise StatePathError(f"cannot find an existing parent for managed path: {target}")
        current = parent
    return current, missing


def validate_root_directory(path: str | os.PathLike[str]) -> bool:
    """Validate an optional managed root and report whether it exists."""

    return _inspect(absolute_path(path), "directory")


def validate_directory_creation(path: str | os.PathLike[str]) -> Path:
    """Validate that a directory could be created safely, without creating it."""

    target = absolute_path(path)
    _validated_creation_chain(target)
    return target


def managed_path(
    root: str | os.PathLike[str],
    relative: str | os.PathLike[str] = ".",
    *,
    expected: PathKind | None = None,
) -> Path:
    """Resolve a lexical descendant and reject every existing unsafe component.

    Missing components are allowed.  Use :func:`ensure_managed_directory` or
    :func:`prepare_managed_file` before writing.
    """

    state_root = absolute_path(root)
    if not _inspect(state_root, "directory"):
        raise StatePathError(f"managed root does not exist: {state_root}")
    requested = Path(relative)
    if requested.is_absolute() or requested.drive:
        raise StatePathError(f"managed path must be relative to {state_root}: {requested}")
    if requested != Path(".") and any(part in {"", ".", ".."} for part in requested.parts):
        raise StatePathError(f"managed path contains a traversal component: {requested}")
    if "\x00" in os.fspath(requested):
        raise StatePathError("managed path contains a null byte")

    candidate = absolute_path(state_root / requested)
    try:
        remainder = candidate.relative_to(state_root)
    except ValueError as exc:
        raise StatePathError(f"managed path escapes {state_root}: {requested}") from exc

    current = state_root
    parts = remainder.parts
    for index, component in enumerate(parts):
        if component in {"", ".", ".."}:
            raise StatePathError(f"invalid managed path component: {component!r}")
        current /= component
        final = index == len(parts) - 1
        kind: PathKind = expected if final and expected is not None else "directory"
        exists = _inspect(current, kind)
        if not exists:
            # Descendants cannot exist when this component is genuinely absent.
            break
    return candidate


def ensure_managed_directory(
    root: str | os.PathLike[str], relative: str | os.PathLike[str] = "."
) -> Path:
    """Validate or create a directory tree beneath an existing managed root."""

    state_root = absolute_path(root)
    target = managed_path(state_root, relative, expected="directory")
    remainder = target.relative_to(state_root)
    current = state_root
    for component in remainder.parts:
        current /= component
        if not _inspect(current, "directory"):
            try:
                current.mkdir()
            except FileExistsError:
                pass
            except OSError as exc:
                raise StatePathError(f"cannot create managed directory {current}: {exc}") from exc
            _inspect(current, "directory")
    return target


def prepare_managed_file(root: str | os.PathLike[str], relative: str | os.PathLike[str]) -> Path:
    """Validate a managed file path and create only its safe parent directories."""

    state_root = absolute_path(root)
    target = managed_path(state_root, relative, expected="file")
    try:
        parent_relative = target.parent.relative_to(state_root)
    except ValueError as exc:  # Defensive; managed_path already enforces this.
        raise StatePathError(f"managed path escapes {state_root}: {relative}") from exc
    ensure_managed_directory(state_root, parent_relative)
    _inspect(target, "file")
    return target


def prepare_managed_files(
    root: str | os.PathLike[str], relatives: Iterable[str | os.PathLike[str]]
) -> tuple[Path, ...]:
    """Preflight every file target before creating any missing parent directory."""

    state_root = absolute_path(root)
    requested = tuple(relatives)
    targets = tuple(managed_path(state_root, relative, expected="file") for relative in requested)
    for relative in requested:
        prepare_managed_file(state_root, relative)
    return targets


def validate_managed_file(root: str | os.PathLike[str], relative: str | os.PathLike[str]) -> Path:
    """Validate an optional file without creating any path components."""

    return managed_path(root, relative, expected="file")


__all__ = [
    "StatePathError",
    "absolute_path",
    "ensure_managed_directory",
    "ensure_root_directory",
    "managed_component",
    "managed_path",
    "prepare_managed_file",
    "prepare_managed_files",
    "validate_directory_creation",
    "validate_managed_file",
    "validate_root_directory",
]
