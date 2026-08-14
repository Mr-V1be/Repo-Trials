"""Mine leak-resistant benchmark candidates from first-parent Git history."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import cache
from pathlib import PurePosixPath

from .git import Commit, FileChange, GitRepository
from .models import Candidate


class FileKind(StrEnum):
    """The three deliberately coarse mining path classes."""

    SOURCE = "source"
    TEST = "test"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class ClassifiedPaths:
    """A deterministic partition of changed paths."""

    source: tuple[str, ...] = ()
    test: tuple[str, ...] = ()
    ignored: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MiningConfig:
    """Conservative filters for high-signal, test-backed bug-fix commits."""

    max_files: int = 20
    max_changed_lines: int = 1_000
    max_commits: int = 1_000
    require_source: bool = True
    require_tests: bool = True
    reject_binary: bool = True
    include_single_parent: bool = True
    include_merges: bool = True
    first_parent_history: bool = True
    source_globs: tuple[str, ...] = ()
    test_globs: tuple[str, ...] = ()
    ignored_globs: tuple[str, ...] = ()
    keyword_pattern: str | None = None

    def __post_init__(self) -> None:
        for name in ("max_files", "max_changed_lines", "max_commits"):
            if getattr(self, name) <= 0:
                raise ValueError(f"MiningConfig.{name} must be positive")
        if self.keyword_pattern is not None:
            re.compile(self.keyword_pattern)


_SOURCE_EXTENSIONS = frozenset(
    {
        ".c",
        ".cc",
        ".clj",
        ".cljs",
        ".cpp",
        ".cs",
        ".dart",
        ".ex",
        ".exs",
        ".fs",
        ".fsx",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".lua",
        ".m",
        ".mm",
        ".php",
        ".pl",
        ".py",
        ".rb",
        ".rs",
        ".scala",
        ".sh",
        ".sol",
        ".swift",
        ".ts",
        ".tsx",
        ".vue",
        ".zig",
    }
)
_TEST_SEGMENTS = frozenset({"test", "tests", "testing", "__tests__", "spec", "specs"})
_IGNORED_SEGMENTS = frozenset(
    {
        ".git",
        ".github",
        ".idea",
        ".venv",
        ".vscode",
        "assets",
        "build",
        "coverage",
        "dist",
        "doc",
        "docs",
        "examples",
        "fixtures",
        "generated",
        "migrations",
        "node_modules",
        "snapshots",
        "target",
        "vendor",
        "venv",
    }
)
_IGNORED_SUFFIXES = (
    ".lock",
    ".md",
    ".rst",
    ".txt",
    ".min.js",
    ".map",
    ".snap",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
)


def _normalise_path(path: str) -> PurePosixPath:
    return PurePosixPath(path.replace("\\", "/"))


def classify_path(path: str) -> FileKind:
    """Classify a repository-relative path as source, test, or ignored.

    Test detection runs before ignored-directory detection, so a fixture under
    ``tests/fixtures`` remains verifier material rather than disappearing from
    the test patch.
    """

    if not path or "\x00" in path:
        return FileKind.IGNORED
    normal = _normalise_path(path)
    parts = tuple(part.casefold() for part in normal.parts)
    name = normal.name.casefold()
    suffix = normal.suffix.casefold()

    looks_like_test = bool(set(parts[:-1]) & _TEST_SEGMENTS) or (
        name.startswith("test_")
        or "_test." in name
        or ".test." in name
        or ".spec." in name
        or name.endswith("tests.py")
    )
    if looks_like_test:
        return FileKind.TEST
    if set(parts) & _IGNORED_SEGMENTS or name.endswith(_IGNORED_SUFFIXES):
        return FileKind.IGNORED
    if suffix in _SOURCE_EXTENSIONS:
        return FileKind.SOURCE
    return FileKind.IGNORED


def classify_paths(paths: Iterable[str]) -> ClassifiedPaths:
    """Partition paths with stable order and no duplicates."""

    buckets: dict[FileKind, dict[str, None]] = {
        FileKind.SOURCE: {},
        FileKind.TEST: {},
        FileKind.IGNORED: {},
    }
    for path in paths:
        buckets[classify_path(path)].setdefault(path, None)
    return ClassifiedPaths(
        source=tuple(buckets[FileKind.SOURCE]),
        test=tuple(buckets[FileKind.TEST]),
        ignored=tuple(buckets[FileKind.IGNORED]),
    )


def classify_paths_with_globs(
    paths: Iterable[str],
    *,
    source_globs: Iterable[str],
    test_globs: Iterable[str],
    ignored_globs: Iterable[str],
) -> ClassifiedPaths:
    """Partition paths using repository-specific patterns.

    Test patterns intentionally win over ignored patterns so fixtures below a
    test tree remain part of the private verifier bundle.
    """

    buckets: dict[FileKind, dict[str, None]] = {
        FileKind.SOURCE: {},
        FileKind.TEST: {},
        FileKind.IGNORED: {},
    }
    source = tuple(source_globs)
    tests = tuple(test_globs)
    ignored = tuple(ignored_globs)
    for path in paths:
        if _glob_matches(path, tests):
            kind = FileKind.TEST
        elif _glob_matches(path, ignored):
            kind = FileKind.IGNORED
        elif _glob_matches(path, source):
            kind = FileKind.SOURCE
        else:
            kind = FileKind.IGNORED
        buckets[kind].setdefault(path, None)
    return ClassifiedPaths(
        source=tuple(buckets[FileKind.SOURCE]),
        test=tuple(buckets[FileKind.TEST]),
        ignored=tuple(buckets[FileKind.IGNORED]),
    )


def _glob_matches(path: str, patterns: Iterable[str]) -> bool:
    candidate = unicodedata.normalize("NFC", path.replace("\\", "/")).casefold()
    path_parts = tuple(PurePosixPath(candidate).parts)
    for raw_pattern in patterns:
        pattern = raw_pattern.replace("\\", "/")
        if pattern.startswith("./"):
            pattern = pattern[2:]
        pattern = unicodedata.normalize("NFC", pattern).casefold()
        pure = PurePosixPath(pattern)
        if not pattern or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            continue
        if _match_glob_parts(path_parts, tuple(pure.parts)):
            return True
    return False


def _match_glob_parts(path_parts: tuple[str, ...], pattern_parts: tuple[str, ...]) -> bool:
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


class Miner:
    """Extract test-backed candidates from a repository's visible history."""

    def __init__(
        self,
        repository: str | os.PathLike[str] | GitRepository,
        config: MiningConfig | None = None,
    ) -> None:
        self.repository = (
            repository if isinstance(repository, GitRepository) else GitRepository(repository)
        )
        self.config = config or MiningConfig()

    def mine(
        self,
        ref: str = "HEAD",
        *,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[Candidate]:
        """Return newest-first candidates.

        A single-parent commit is diffed against that parent.  A merge commit is
        diffed against its first parent, which reconstructs exactly what landing
        the merge introduced to the mainline tree.
        """

        if limit is not None and limit <= 0:
            return []
        candidates: list[Candidate] = []
        commits = self.repository.iter_commits(
            ref,
            since=since,
            max_count=self.config.max_commits,
            first_parent=self.config.first_parent_history,
        )
        for commit in commits:
            if not commit.parents:
                continue
            if commit.is_merge and not self.config.include_merges:
                continue
            if not commit.is_merge and not self.config.include_single_parent:
                continue

            parent = commit.parents[0]
            changes = self.repository.changed_files(commit.sha, parent)
            candidate = self._candidate(commit, parent, changes)
            if candidate is None:
                continue
            if self.config.keyword_pattern is not None:
                metadata = dict(candidate.metadata)
                metadata["keyword_match"] = bool(
                    re.search(
                        self.config.keyword_pattern,
                        f"{candidate.title}\n{candidate.message}",
                    )
                )
                candidate = replace(candidate, metadata=metadata)
            candidates.append(candidate)
            if limit is not None and len(candidates) >= limit:
                break
        return candidates

    def _candidate(
        self, commit: Commit, parent: str, changes: tuple[FileChange, ...]
    ) -> Candidate | None:
        if not changes or len(changes) > self.config.max_files:
            return None
        additions = sum(change.additions for change in changes)
        deletions = sum(change.deletions for change in changes)
        if additions + deletions > self.config.max_changed_lines:
            return None
        if self.config.reject_binary and any(change.binary for change in changes):
            return None
        # Git's rename/copy record has two paths while Candidate exposes one.
        # Until the task schema can preserve both sides, rejecting it avoids
        # silently producing an incomplete source allowlist or oracle patch.
        if any(change.status.startswith(("R", "C")) for change in changes):
            return None

        # Deleted tests cannot serve as a hidden regression oracle.  Keep them
        # listed as ignored while requiring at least one added/modified test.
        eligible_paths = [change.path for change in changes if not change.status.startswith("D")]
        if self.config.source_globs or self.config.test_globs or self.config.ignored_globs:
            classified = classify_paths_with_globs(
                eligible_paths,
                source_globs=self.config.source_globs,
                test_globs=self.config.test_globs,
                ignored_globs=self.config.ignored_globs,
            )
            all_classified = classify_paths_with_globs(
                (change.path for change in changes),
                source_globs=self.config.source_globs,
                test_globs=self.config.test_globs,
                ignored_globs=self.config.ignored_globs,
            )
        else:
            classified = classify_paths(eligible_paths)
            all_classified = classify_paths(change.path for change in changes)
        if self.config.require_source and not classified.source:
            return None
        if self.config.require_tests and not classified.test:
            return None

        digest = hashlib.sha256(f"{parent}\x00{commit.sha}".encode("ascii")).hexdigest()[:20]
        statuses = {change.path: change.status for change in changes}
        return Candidate(
            id=f"candidate-{digest}",
            repository=str(self.repository.path),
            commit_sha=commit.sha,
            parent_sha=parent,
            title=commit.title,
            message=commit.message,
            author=commit.author,
            authored_at=commit.authored_at,
            is_merge=commit.is_merge,
            source_files=classified.source,
            test_files=classified.test,
            ignored_files=all_classified.ignored,
            changed_files=tuple(change.path for change in changes),
            additions=additions,
            deletions=deletions,
            metadata={
                "parents": list(commit.parents),
                "file_status": statuses,
                "first_parent_diff": True,
            },
        )


CandidateMiner = Miner


__all__ = [
    "CandidateMiner",
    "ClassifiedPaths",
    "FileKind",
    "Miner",
    "MiningConfig",
    "classify_path",
    "classify_paths",
    "classify_paths_with_globs",
]
