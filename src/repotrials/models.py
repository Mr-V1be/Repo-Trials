"""Serializable domain models used throughout RepoTrials.

The project deliberately keeps these models dependency free.  They are small,
stable records that can be written to SQLite, embedded in run manifests, or
passed to third-party runners without teaching those components about an ORM.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from typing import Any, ClassVar, Self, cast


def utc_now() -> str:
    """Return a compact, timezone-aware ISO-8601 timestamp."""

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    """Return *value* using only JSON container types.

    ``dataclasses.asdict`` intentionally preserves tuples.  JSON can encode
    tuples, but normalising them to lists makes ``to_dict`` itself suitable for
    APIs and deterministic fixtures as well.
    """

    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


class JsonModel:
    """Mixin implementing strict and deterministic JSON serialisation."""

    _tuple_fields: ClassVar[frozenset[str]] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        """Serialise the model to a detached JSON-compatible dictionary."""

        result: dict[str, Any] = {}
        for item in fields(cast(Any, self)):
            result[item.name] = _json_safe(getattr(self, item.name))
        return result

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialise the model to UTF-8 friendly deterministic JSON."""

        separators = None if indent is not None else (",", ":")
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Construct a model from a mapping.

        Unknown keys are rejected so corrupt manifests and spelling mistakes do
        not silently alter benchmark semantics.  Tuple-backed fields accept
        both JSON lists and Python tuples.
        """

        if not isinstance(data, Mapping):
            raise TypeError(f"{cls.__name__}.from_dict expects a mapping")
        allowed = {item.name for item in fields(cast(Any, cls)) if item.init}
        unknown = set(data) - allowed
        if unknown:
            names = ", ".join(sorted(str(name) for name in unknown))
            raise ValueError(f"unknown {cls.__name__} field(s): {names}")

        values = dict(data)
        for name in cls._tuple_fields:
            if name in values:
                raw = values[name]
                if isinstance(raw, str) or not isinstance(raw, (tuple, list)):
                    raise TypeError(f"{cls.__name__}.{name} must be a list or tuple")
                values[name] = tuple(raw)
        if "metadata" in values:
            if not isinstance(values["metadata"], Mapping):
                raise TypeError(f"{cls.__name__}.metadata must be a mapping")
            values["metadata"] = dict(values["metadata"])
        try:
            return cls(**values)
        except TypeError as exc:
            raise ValueError(f"invalid {cls.__name__}: {exc}") from exc

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> Self:
        """Construct a model from a JSON object."""

        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid {cls.__name__} JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{cls.__name__} JSON must contain an object")
        return cls.from_dict(data)

    def _normalise(self) -> None:
        """Normalise mutable constructor inputs on frozen dataclasses."""

        for name in self._tuple_fields:
            value = getattr(self, name)
            if isinstance(value, str):
                raise TypeError(f"{type(self).__name__}.{name} cannot be a string")
            object.__setattr__(self, name, tuple(value))
        metadata = getattr(self, "metadata", None)
        if metadata is not None:
            if not isinstance(metadata, Mapping):
                raise TypeError(f"{type(self).__name__}.metadata must be a mapping")
            object.__setattr__(self, "metadata", dict(metadata))


@dataclass(frozen=True, slots=True)
class Candidate(JsonModel):
    """A historical commit that may be compiled into an evaluation task."""

    id: str
    commit_sha: str
    parent_sha: str
    title: str
    repository: str = ""
    message: str = ""
    author: str = ""
    authored_at: str = ""
    is_merge: bool = False
    source_files: tuple[str, ...] = ()
    test_files: tuple[str, ...] = ()
    ignored_files: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    additions: int = 0
    deletions: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    _tuple_fields: ClassVar[frozenset[str]] = frozenset(
        {"source_files", "test_files", "ignored_files", "changed_files"}
    )

    def __post_init__(self) -> None:
        self._normalise()
        for name in ("id", "commit_sha", "parent_sha", "title"):
            if not getattr(self, name):
                raise ValueError(f"Candidate.{name} cannot be empty")
        if self.additions < 0 or self.deletions < 0:
            raise ValueError("Candidate line counts cannot be negative")
        if not self.changed_files:
            combined = dict.fromkeys((*self.source_files, *self.test_files, *self.ignored_files))
            object.__setattr__(self, "changed_files", tuple(combined))


@dataclass(frozen=True, slots=True)
class Task(JsonModel):
    """A reproducible agent task derived from a :class:`Candidate`."""

    id: str
    candidate_id: str
    base_sha: str
    fix_sha: str
    instruction: str
    repository: str = ""
    gold_patch_oid: str = ""
    test_patch_oid: str = ""
    source_files: tuple[str, ...] = ()
    test_files: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    _tuple_fields: ClassVar[frozenset[str]] = frozenset({"source_files", "test_files"})

    def __post_init__(self) -> None:
        self._normalise()
        for name in ("id", "candidate_id", "base_sha", "fix_sha", "instruction"):
            if not getattr(self, name):
                raise ValueError(f"Task.{name} cannot be empty")

    @property
    def source_patch_oid(self) -> str:
        """Backward-friendly name for the human (gold) source patch."""

        return self.gold_patch_oid

    @property
    def hidden_test_patch_oid(self) -> str:
        """Explicit name for the verifier-only test patch."""

        return self.test_patch_oid


@dataclass(frozen=True, slots=True)
class Validation(JsonModel):
    """Result of proving a task's BASE/RED/GOLD/NOOP invariants."""

    task_id: str
    status: str = "pending"
    passed: bool = False
    stable: bool = False
    attempts: int = 0
    baseline_exit_codes: tuple[int, ...] = ()
    red_exit_codes: tuple[int, ...] = ()
    gold_exit_codes: tuple[int, ...] = ()
    noop_exit_codes: tuple[int, ...] = ()
    duration_seconds: float = 0.0
    diagnostics: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    _tuple_fields: ClassVar[frozenset[str]] = frozenset(
        {
            "baseline_exit_codes",
            "red_exit_codes",
            "gold_exit_codes",
            "noop_exit_codes",
            "diagnostics",
        }
    )

    def __post_init__(self) -> None:
        self._normalise()
        if not self.task_id:
            raise ValueError("Validation.task_id cannot be empty")
        if self.attempts < 0 or self.duration_seconds < 0:
            raise ValueError("Validation attempts and duration cannot be negative")

    @property
    def successful(self) -> bool:
        """Alias used by reporting code."""

        return self.passed


@dataclass(frozen=True, slots=True)
class Run(JsonModel):
    """One agent attempt against one task."""

    id: str
    task_id: str
    agent: str
    model: str = ""
    status: str = "pending"
    passed: bool = False
    exit_code: int | None = None
    duration_seconds: float = 0.0
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    patch_oid: str = ""
    created_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._normalise()
        for name in ("id", "task_id", "agent"):
            if not getattr(self, name):
                raise ValueError(f"Run.{name} cannot be empty")
        if self.duration_seconds < 0:
            raise ValueError("Run.duration_seconds cannot be negative")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise ValueError("Run.cost_usd cannot be negative")
        for name in ("input_tokens", "output_tokens"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"Run.{name} cannot be negative")


__all__ = ["Candidate", "JsonModel", "Run", "Task", "Validation", "utc_now"]
