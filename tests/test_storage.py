from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from repotrials.models import Candidate, Run, Task, Validation
from repotrials.storage import ObjectStore, StateStore, StorageError


def _symlink(
    test: unittest.TestCase, target: Path, link: Path, *, target_is_directory: bool
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        test.skipTest(f"symlinks are unavailable: {exc}")


class ObjectStoreTests(unittest.TestCase):
    def test_objects_are_content_addressed_and_integrity_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ObjectStore(Path(temporary) / "objects")
            first = store.put(b"patch data")
            second = store.put("patch data")
            self.assertEqual(first, second)
            self.assertEqual(len(first), 64)
            self.assertEqual(store.get(first), b"patch data")
            self.assertTrue(store.has(first))

            store.path_for(first).write_bytes(b"tampered")
            self.assertFalse(store.has(first))
            with self.assertRaises(StorageError):
                store.get(first)
            with self.assertRaises(ValueError):
                store.get("../state.sqlite3")

    def test_rejects_symlink_root_without_touching_sink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            sink = base / "sink"
            sink.mkdir()
            marker = sink / "keep.txt"
            marker.write_text("unchanged", encoding="utf-8")
            _symlink(self, sink, base / "objects", target_is_directory=True)

            with self.assertRaisesRegex(StorageError, "symlink|reparse"):
                ObjectStore(base / "objects")

            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual({item.name for item in sink.iterdir()}, {"keep.txt"})

    def test_rejects_symlink_fanout_before_object_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            store = ObjectStore(base / "objects")
            payload = b"private patch"
            digest = hashlib.sha256(payload).hexdigest()
            sink = base / "sink"
            sink.mkdir()
            marker = sink / "keep.txt"
            marker.write_text("unchanged", encoding="utf-8")
            _symlink(self, sink, store.root / digest[:2], target_is_directory=True)

            with self.assertRaisesRegex(StorageError, "symlink|reparse"):
                store.put(payload)

            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual({item.name for item in sink.iterdir()}, {"keep.txt"})


class StateStoreTests(unittest.TestCase):
    def _models(self) -> tuple[Candidate, Task, Validation, Run]:
        candidate = Candidate(
            id="candidate-1",
            commit_sha="a" * 40,
            parent_sha="b" * 40,
            title="Fix session",
            source_files=("src/session.py",),
            test_files=("tests/test_session.py",),
        )
        task = Task(
            id="task-1",
            candidate_id=candidate.id,
            base_sha=candidate.parent_sha,
            fix_sha=candidate.commit_sha,
            instruction="Fix the session refresh race.",
        )
        validation = Validation(
            task_id=task.id,
            status="passed",
            passed=True,
            stable=True,
            attempts=3,
        )
        run = Run(
            id="run-1",
            task_id=task.id,
            agent="example-agent",
            status="passed",
            passed=True,
        )
        return candidate, task, validation, run

    def test_crud_filtering_upsert_and_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".repotrials"
            candidate, task, validation, run = self._models()
            with StateStore(root) as store:
                oid = store.put_object(b"diff --git")
                store.save_candidate(candidate)
                store.save_task(task)
                store.save_validation(validation)
                store.save_run(run)

                self.assertEqual(store.get_candidate(candidate.id), candidate)
                self.assertEqual(store.get_task(task.id), task)
                self.assertEqual(store.get_validation(task.id), validation)
                self.assertEqual(store.get_run(run.id), run)
                self.assertEqual(store.list_tasks(candidate.id), [task])
                self.assertEqual(store.list_tasks("missing"), [])
                self.assertEqual(store.list_runs(task.id), [run])
                self.assertEqual(store.list_runs("missing"), [])

                updated = replace(candidate, title="Fix session atomically")
                store.save_candidate(updated)
                self.assertEqual(store.list_candidates(), [updated])

            with StateStore(root) as reopened:
                self.assertEqual(
                    reopened.get_candidate(candidate.id).title, "Fix session atomically"
                )  # type: ignore[union-attr]
                self.assertEqual(reopened.get_object(oid), b"diff --git")
                self.assertEqual(reopened.list_validations(), [validation])

    def test_missing_rows_and_closed_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = StateStore(temporary)
            self.assertIsNone(store.get_candidate("missing"))
            self.assertIsNone(store.get_task("missing"))
            self.assertIsNone(store.get_validation("missing"))
            self.assertIsNone(store.get_run("missing"))
            store.close()
            store.close()
            with self.assertRaises(StorageError):
                store.list_candidates()

    def test_rejects_state_root_symlink_without_touching_sink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            sink = base / "sink"
            sink.mkdir()
            marker = sink / "keep.txt"
            marker.write_text("unchanged", encoding="utf-8")
            _symlink(self, sink, base / ".repotrials", target_is_directory=True)

            with self.assertRaisesRegex(StorageError, "symlink|reparse"):
                StateStore(base / ".repotrials")

            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual({item.name for item in sink.iterdir()}, {"keep.txt"})

    def test_rejects_symlink_database_before_creating_object_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".repotrials"
            root.mkdir()
            sink = Path(temporary) / "outside.sqlite3"
            sink.write_bytes(b"unchanged")
            _symlink(self, sink, root / "state.sqlite3", target_is_directory=False)

            with self.assertRaisesRegex(StorageError, "symlink|reparse"):
                StateStore(root)

            self.assertEqual(sink.read_bytes(), b"unchanged")
            self.assertEqual({item.name for item in root.iterdir()}, {"state.sqlite3"})

    def test_rejects_database_directory_before_creating_object_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".repotrials"
            root.mkdir()
            (root / "state.sqlite3").mkdir()

            with self.assertRaisesRegex(StorageError, "must be a file"):
                StateStore(root)

            self.assertEqual({item.name for item in root.iterdir()}, {"state.sqlite3"})

    def test_rejects_symlink_object_root_before_database_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / ".repotrials"
            sink = Path(temporary) / "sink"
            root.mkdir()
            sink.mkdir()
            marker = sink / "keep.txt"
            marker.write_text("unchanged", encoding="utf-8")
            _symlink(self, sink, root / "objects", target_is_directory=True)

            with self.assertRaisesRegex(StorageError, "symlink|reparse"):
                StateStore(root)

            self.assertFalse((root / "state.sqlite3").exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual({item.name for item in sink.iterdir()}, {"keep.txt"})


if __name__ == "__main__":
    unittest.main()
