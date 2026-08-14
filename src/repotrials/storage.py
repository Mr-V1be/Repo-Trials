"""Content-addressed artifacts and transactional SQLite state."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any, TypeVar

from .models import Candidate, JsonModel, Run, Task, Validation
from .state_paths import (
    StatePathError,
    absolute_path,
    ensure_managed_directory,
    ensure_root_directory,
    managed_path,
    prepare_managed_file,
    validate_directory_creation,
    validate_managed_file,
    validate_root_directory,
)


class StorageError(RuntimeError):
    """Raised for corrupt objects or invalid persisted model data."""


_OID = re.compile(r"^[0-9a-f]{64}$")


class ObjectStore:
    """An immutable SHA-256 content-addressed object store.

    Objects are written to a temporary file in the destination directory and
    atomically renamed, making concurrent writers of identical data safe.
    Reads re-hash content so disk corruption is never reported as a valid patch.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = absolute_path(root)
        try:
            if not validate_root_directory(self.root):
                validate_directory_creation(self.root)
            ensure_root_directory(self.root)
        except StatePathError as exc:
            raise StorageError(f"unsafe object store root: {exc}") from exc

    @staticmethod
    def _validate_oid(oid: str) -> str:
        if not isinstance(oid, str) or not _OID.fullmatch(oid):
            raise ValueError("object id must be a lowercase SHA-256 digest")
        return oid

    def path_for(self, oid: str) -> Path:
        """Return the fan-out path for a validated object ID."""

        oid = self._validate_oid(oid)
        try:
            return managed_path(self.root, Path(oid[:2]) / oid[2:], expected="file")
        except StatePathError as exc:
            raise StorageError(f"unsafe object path for {oid}: {exc}") from exc

    def put(self, data: bytes | bytearray | memoryview | str) -> str:
        """Store bytes (or UTF-8 text) and return their SHA-256 object ID."""

        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        oid = hashlib.sha256(payload).hexdigest()
        destination = self.path_for(oid)
        try:
            ensure_managed_directory(self.root, oid[:2])
            destination = prepare_managed_file(self.root, Path(oid[:2]) / oid[2:])
        except StatePathError as exc:
            raise StorageError(f"unsafe object path for {oid}: {exc}") from exc
        if destination.exists():
            if self.get(oid) != payload:
                raise StorageError(f"hash collision or corrupt object: {oid}")
            return oid

        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=".tmp-", delete=False
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            # If another process won the race, replacement is still safe because
            # both paths have the same content-derived name.
            os.replace(temporary_name, destination)
            temporary_name = None
        finally:
            if temporary_name is not None:
                with suppress(FileNotFoundError):
                    Path(temporary_name).unlink()
        return oid

    def put_file(self, path: str | os.PathLike[str]) -> str:
        """Store a file and return its content ID."""

        return self.put(Path(path).read_bytes())

    def get(self, oid: str) -> bytes:
        """Read and integrity-check an object."""

        path = self.path_for(oid)
        try:
            payload = path.read_bytes()
        except FileNotFoundError as exc:
            raise KeyError(oid) from exc
        actual = hashlib.sha256(payload).hexdigest()
        if actual != oid:
            raise StorageError(f"object failed integrity check: expected {oid}, got {actual}")
        return payload

    def has(self, oid: str) -> bool:
        """Return whether a valid object exists; corrupt data returns ``False``."""

        try:
            self.get(oid)
        except (KeyError, StorageError):
            return False
        return True


ModelT = TypeVar("ModelT", bound=JsonModel)


class StateStore:
    """Persistent RepoTrials state backed by SQLite and :class:`ObjectStore`."""

    SCHEMA_VERSION = 1

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = absolute_path(root)
        try:
            root_exists = validate_root_directory(self.root)
            if root_exists:
                managed_path(self.root, "objects", expected="directory")
                managed_path(self.root, "objects/sha256", expected="directory")
                for relative in (
                    "state.sqlite3",
                    "state.sqlite3-journal",
                    "state.sqlite3-shm",
                    "state.sqlite3-wal",
                ):
                    validate_managed_file(self.root, relative)
            else:
                validate_directory_creation(self.root)
            ensure_root_directory(self.root)
            ensure_managed_directory(self.root, "objects/sha256")
            self.database_path = prepare_managed_file(self.root, "state.sqlite3")
        except StatePathError as exc:
            raise StorageError(f"unsafe state store path: {exc}") from exc
        self.objects = ObjectStore(self.root / "objects" / "sha256")
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=30000")
        self._initialise()

    def _initialise(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS candidates (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_candidate ON tasks(candidate_id);
        CREATE TABLE IF NOT EXISTS validations (
            task_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id);
        """
        with self._lock, self._connection:
            self._connection.executescript(schema)
            row = self._connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO metadata(key, value) VALUES('schema_version', ?)",
                    (str(self.SCHEMA_VERSION),),
                )
            elif int(row["value"]) > self.SCHEMA_VERSION:
                raise StorageError(
                    f"state schema {row['value']} is newer than supported "
                    f"schema {self.SCHEMA_VERSION}"
                )

    def _ensure_open(self) -> None:
        if self._closed:
            raise StorageError("state store is closed")

    def put_object(self, data: bytes | bytearray | memoryview | str) -> str:
        """Store an immutable artifact and return its SHA-256 ID."""

        self._ensure_open()
        return self.objects.put(data)

    def get_object(self, oid: str) -> bytes:
        """Read an immutable artifact by ID."""

        self._ensure_open()
        return self.objects.get(oid)

    def has_object(self, oid: str) -> bool:
        """Check an artifact's presence and integrity."""

        self._ensure_open()
        return self.objects.has(oid)

    def _upsert(
        self,
        table: str,
        key_column: str,
        identity: str,
        payload: str,
        *,
        extra_column: str | None = None,
        extra_value: str | None = None,
    ) -> None:
        self._ensure_open()
        with self._lock, self._connection:
            if extra_column is None:
                self._connection.execute(
                    f"INSERT INTO {table}({key_column}, payload) VALUES(?, ?) "
                    f"ON CONFLICT({key_column}) DO UPDATE SET "
                    "payload=excluded.payload, updated_at=CURRENT_TIMESTAMP",
                    (identity, payload),
                )
            else:
                self._connection.execute(
                    f"INSERT INTO {table}({key_column}, {extra_column}, payload) "
                    "VALUES(?, ?, ?) "
                    f"ON CONFLICT({key_column}) DO UPDATE SET "
                    f"{extra_column}=excluded.{extra_column}, "
                    "payload=excluded.payload, updated_at=CURRENT_TIMESTAMP",
                    (identity, extra_value, payload),
                )

    def _one(
        self, table: str, key_column: str, identity: str, model: type[ModelT]
    ) -> ModelT | None:
        self._ensure_open()
        with self._lock:
            row = self._connection.execute(
                f"SELECT payload FROM {table} WHERE {key_column} = ?", (identity,)
            ).fetchone()
        if row is None:
            return None
        try:
            return model.from_json(row["payload"])
        except (TypeError, ValueError) as exc:
            raise StorageError(f"invalid {table} row {identity}: {exc}") from exc

    def _many(
        self,
        table: str,
        model: type[ModelT],
        *,
        where_column: str | None = None,
        where_value: str | None = None,
    ) -> list[ModelT]:
        self._ensure_open()
        query = f"SELECT payload FROM {table}"
        parameters: tuple[Any, ...] = ()
        if where_column is not None:
            query += f" WHERE {where_column} = ?"
            parameters = (where_value,)
        query += " ORDER BY rowid"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        try:
            return [model.from_json(row["payload"]) for row in rows]
        except (TypeError, ValueError) as exc:
            raise StorageError(f"invalid {table} row: {exc}") from exc

    def save_candidate(self, candidate: Candidate) -> None:
        """Insert or atomically replace a candidate."""

        self._upsert("candidates", "id", candidate.id, candidate.to_json())

    def get_candidate(self, candidate_id: str) -> Candidate | None:
        return self._one("candidates", "id", candidate_id, Candidate)

    def list_candidates(self) -> list[Candidate]:
        return self._many("candidates", Candidate)

    def save_task(self, task: Task) -> None:
        """Insert or atomically replace a task."""

        self._upsert(
            "tasks",
            "id",
            task.id,
            task.to_json(),
            extra_column="candidate_id",
            extra_value=task.candidate_id,
        )

    def get_task(self, task_id: str) -> Task | None:
        return self._one("tasks", "id", task_id, Task)

    def list_tasks(self, candidate_id: str | None = None) -> list[Task]:
        return self._many(
            "tasks",
            Task,
            where_column="candidate_id" if candidate_id is not None else None,
            where_value=candidate_id,
        )

    def save_validation(self, validation: Validation) -> None:
        """Store the latest validation for a task."""

        self._upsert("validations", "task_id", validation.task_id, validation.to_json())

    def get_validation(self, task_id: str) -> Validation | None:
        return self._one("validations", "task_id", task_id, Validation)

    def list_validations(self) -> list[Validation]:
        return self._many("validations", Validation)

    def save_run(self, run: Run) -> None:
        """Insert or atomically replace an agent run."""

        self._upsert(
            "runs",
            "id",
            run.id,
            run.to_json(),
            extra_column="task_id",
            extra_value=run.task_id,
        )

    def get_run(self, run_id: str) -> Run | None:
        return self._one("runs", "id", run_id, Run)

    def list_runs(self, task_id: str | None = None) -> list[Run]:
        return self._many(
            "runs",
            Run,
            where_column="task_id" if task_id is not None else None,
            where_value=task_id,
        )

    def close(self) -> None:
        """Flush and close the SQLite connection."""

        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> StateStore:
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


Storage = StateStore


__all__ = ["ObjectStore", "StateStore", "Storage", "StorageError"]
