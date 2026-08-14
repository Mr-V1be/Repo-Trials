"""Safe, dependency-free Git access for mining historical tasks."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    """Raised when a Git command cannot be completed."""

    def __init__(
        self,
        args: Sequence[str],
        cwd: Path,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.args_run = tuple(args)
        self.cwd = cwd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        detail = stderr.strip() or stdout.strip() or "no diagnostic output"
        if len(detail) > 2_000:
            detail = detail[:2_000] + "…"
        super().__init__(f"git failed ({returncode}) in {cwd}: {detail}")


@dataclass(frozen=True, slots=True)
class Commit:
    """Metadata for one Git commit."""

    sha: str
    parents: tuple[str, ...]
    author: str
    authored_at: str
    title: str
    message: str

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


@dataclass(frozen=True, slots=True)
class FileChange:
    """One path changed between two trees."""

    status: str
    path: str
    old_path: str | None = None
    additions: int = 0
    deletions: int = 0
    binary: bool = False


def _environment(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        }
    )
    if overrides:
        environment.update(overrides)
    return environment


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


class GitRepository:
    """A Git working tree accessed exclusively through argument-vector calls.

    No method invokes a shell, interpolates a revision into a command string, or
    enables credential prompts.  Revisions are resolved to immutable commit
    object IDs before they are used by diff/archive methods.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        candidate = Path(path).expanduser().resolve()
        if not candidate.is_dir():
            raise ValueError(f"repository path is not a directory: {candidate}")
        self.path = candidate
        # Fail eagerly and keep the canonical work-tree root when possible.
        top = self.run("rev-parse", "--show-toplevel").strip()
        if top:
            self.path = Path(top).resolve()

    @classmethod
    def discover(cls, path: str | os.PathLike[str] = ".") -> GitRepository:
        """Discover the repository containing *path*."""

        return cls(path)

    def _command(self, arguments: Sequence[str]) -> list[str]:
        return [
            "git",
            "-C",
            str(self.path),
            "-c",
            "core.quotePath=false",
            "-c",
            f"safe.directory={self.path}",
            "--no-pager",
            *arguments,
        ]

    def _run_bytes(
        self,
        arguments: Sequence[str],
        *,
        input_data: bytes | None = None,
        check: bool = True,
        timeout: float = 60.0,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command = self._command(arguments)
        try:
            result = subprocess.run(
                command,
                input=input_data,
                capture_output=True,
                check=False,
                timeout=timeout,
                env=_environment(env),
                shell=False,
            )
        except FileNotFoundError as exc:
            raise GitError(arguments, self.path, 127, stderr="git executable not found") from exc
        except subprocess.TimeoutExpired as exc:
            stdout = _decode(exc.stdout or b"")
            stderr = _decode(exc.stderr or b"")
            raise GitError(arguments, self.path, 124, stdout, stderr or "timed out") from exc
        if check and result.returncode != 0:
            raise GitError(
                arguments,
                self.path,
                result.returncode,
                _decode(result.stdout),
                _decode(result.stderr),
            )
        return result

    def run(
        self,
        *arguments: str,
        check: bool = True,
        timeout: float = 60.0,
        env: Mapping[str, str] | None = None,
    ) -> str:
        """Run a Git subcommand and return decoded stdout."""

        result = self._run_bytes(arguments, check=check, timeout=timeout, env=env)
        return _decode(result.stdout)

    def rev_parse(self, revision: str = "HEAD") -> str:
        """Resolve *revision* to a verified commit object ID."""

        if not revision or "\x00" in revision:
            raise ValueError("revision cannot be empty or contain NUL")
        output = self.run(
            "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"
        ).strip()
        if not output or any(char not in "0123456789abcdefABCDEF" for char in output):
            raise GitError(("rev-parse", revision), self.path, 1, output, "invalid object id")
        return output.lower()

    def commit(self, revision: str = "HEAD") -> Commit:
        """Read commit metadata without relying on locale-sensitive output."""

        sha = self.rev_parse(revision)
        payload = self.run(
            "show",
            "-s",
            "--no-show-signature",
            "--format=%H%x00%P%x00%an%x00%aI%x00%s%x00%B",
            sha,
        )
        parts = payload.split("\x00", 5)
        if len(parts) != 6:
            raise GitError(("show", sha), self.path, 1, payload, "malformed metadata")
        actual_sha, parents, author, authored_at, title, message = parts
        return Commit(
            sha=actual_sha.strip().lower(),
            parents=tuple(item.lower() for item in parents.split() if item),
            author=author.strip(),
            authored_at=authored_at.strip(),
            title=title.strip(),
            message=message.strip(),
        )

    def iter_commits(
        self,
        ref: str = "HEAD",
        *,
        since: str | None = None,
        max_count: int | None = None,
        first_parent: bool = True,
    ) -> Iterable[Commit]:
        """Yield newest-first commits reachable from *ref*.

        First-parent traversal is the default because it represents the sequence
        of states users actually saw on the main branch, including squash and
        merge commits without separately evaluating every feature-branch commit.
        """

        if max_count is not None and max_count <= 0:
            return
        resolved = self.rev_parse(ref)
        arguments = ["log", "--format=%H", "--no-decorate"]
        if first_parent:
            arguments.append("--first-parent")
        if since:
            if "\x00" in since:
                raise ValueError("since cannot contain NUL")
            arguments.append(f"--since={since}")
        if max_count is not None:
            arguments.append(f"--max-count={max_count}")
        arguments.extend(("--", resolved))
        # `git log -- <sha>` treats the SHA as a path.  The revision must precede
        # `--`; it is already a verified hexadecimal ID and cannot be an option.
        arguments[-2:] = (resolved, "--")
        for sha in self.run(*arguments).splitlines():
            if sha:
                yield self.commit(sha.strip())

    def changed_files(self, commit: str, parent: str | None = None) -> tuple[FileChange, ...]:
        """Return path and line statistics for ``parent..commit``.

        For merge commits callers normally pass the first parent.  Rename and
        copy records retain the original path while classification uses the new
        path.
        """

        commit_sha = self.rev_parse(commit)
        if parent is None:
            info = self.commit(commit_sha)
            if not info.parents:
                return ()
            parent_sha = info.parents[0]
        else:
            parent_sha = self.rev_parse(parent)

        name_output = self._run_bytes(
            (
                "diff",
                "--name-status",
                "-z",
                "--find-renames",
                "--find-copies",
                parent_sha,
                commit_sha,
                "--",
            )
        ).stdout
        stats_output = self._run_bytes(
            ("diff", "--numstat", "-z", parent_sha, commit_sha, "--")
        ).stdout
        stats = self._parse_numstat(stats_output)
        return self._parse_name_status(name_output, stats)

    @staticmethod
    def _parse_numstat(payload: bytes) -> dict[str, tuple[int, int, bool]]:
        tokens = payload.split(b"\x00")
        result: dict[str, tuple[int, int, bool]] = {}
        index = 0
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if not token:
                continue
            parts = token.split(b"\t", 2)
            if len(parts) != 3:
                continue
            added, deleted, encoded_path = parts
            if not encoded_path:
                # With -z Git emits rename paths as: header\0old\0new\0.
                if index + 1 >= len(tokens):
                    break
                index += 1  # old path
                encoded_path = tokens[index]
                index += 1
            path = _decode(encoded_path)
            binary = added == b"-" or deleted == b"-"
            result[path] = (
                0 if binary else int(added),
                0 if binary else int(deleted),
                binary,
            )
        return result

    @staticmethod
    def _parse_name_status(
        payload: bytes, stats: Mapping[str, tuple[int, int, bool]]
    ) -> tuple[FileChange, ...]:
        tokens = payload.split(b"\x00")
        changes: list[FileChange] = []
        index = 0
        while index < len(tokens):
            encoded_status = tokens[index]
            index += 1
            if not encoded_status:
                continue
            inline_path: bytes | None = None
            if b"\t" in encoded_status:
                encoded_status, inline_path = encoded_status.split(b"\t", 1)
            status = _decode(encoded_status)
            if inline_path is None:
                if index >= len(tokens):
                    break
                encoded_path = tokens[index]
                index += 1
            else:
                encoded_path = inline_path
            old_path: str | None = None
            if status.startswith(("R", "C")):
                old_path = _decode(encoded_path)
                if index >= len(tokens):
                    break
                encoded_path = tokens[index]
                index += 1
            path = _decode(encoded_path)
            additions, deletions, binary = stats.get(path, (0, 0, False))
            changes.append(
                FileChange(
                    status=status,
                    path=path,
                    old_path=old_path,
                    additions=additions,
                    deletions=deletions,
                    binary=binary,
                )
            )
        return tuple(changes)

    def diff(
        self,
        parent: str,
        commit: str,
        *,
        paths: Sequence[str] | None = None,
    ) -> bytes:
        """Return a binary-safe, full-index patch between two commits."""

        parent_sha = self.rev_parse(parent)
        commit_sha = self.rev_parse(commit)
        arguments = [
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            parent_sha,
            commit_sha,
            "--",
        ]
        if paths:
            for path in paths:
                if "\x00" in path:
                    raise ValueError("Git paths cannot contain NUL")
            arguments.extend(paths)
        return self._run_bytes(arguments).stdout

    def archive(self, revision: str = "HEAD") -> bytes:
        """Return a tar archive of a commit, intentionally excluding ``.git``."""

        sha = self.rev_parse(revision)
        return self._run_bytes(("archive", "--format=tar", sha), timeout=300).stdout


# A concise alias reads naturally in callers while retaining the explicit class
# name in documentation.
Repository = GitRepository


__all__ = ["Commit", "FileChange", "GitError", "GitRepository", "Repository"]
