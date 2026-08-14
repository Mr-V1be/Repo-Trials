from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from repotrials.state_paths import (
    StatePathError,
    ensure_root_directory,
    managed_component,
    managed_path,
    prepare_managed_file,
    prepare_managed_files,
)
from repotrials.vault import ContentAddressedVault, VaultError


def _symlink(
    test: unittest.TestCase, target: Path, link: Path, *, target_is_directory: bool
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        test.skipTest(f"symlinks are unavailable: {exc}")


class ManagedPathTests(unittest.TestCase):
    def test_managed_component_accepts_generated_ids_and_rejects_path_syntax(self) -> None:
        for value in ("rt_0123456789abcdef", "run-a1b2", "agent-20260101-abc123"):
            self.assertEqual(managed_component(value), value)
        for value in ("../run", "group/run", "group\\run", ".", "CON", "name:"):
            with self.subTest(value=value), self.assertRaises(StatePathError):
                managed_component(value)

    def test_rejects_escape_and_nested_link_without_touching_sink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state = ensure_root_directory(base / ".repotrials")
            sink = base / "sink"
            sink.mkdir()
            marker = sink / "keep.txt"
            marker.write_text("unchanged", encoding="utf-8")

            with self.assertRaisesRegex(StatePathError, "traversal|escapes"):
                managed_path(state, "runs/../../sink/result.json", expected="file")
            with self.assertRaisesRegex(StatePathError, "traversal"):
                managed_path(state, "tasks/../runs/result.json", expected="file")

            _symlink(self, sink, state / "runs", target_is_directory=True)
            with self.assertRaisesRegex(StatePathError, "symlink|reparse"):
                prepare_managed_file(state, "runs/group/result.json")

            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual({item.name for item in sink.iterdir()}, {"keep.txt"})

    def test_rejects_wrong_existing_component_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = ensure_root_directory(Path(temporary) / ".repotrials")
            (state / "tasks").write_text("not a directory", encoding="utf-8")

            with self.assertRaisesRegex(StatePathError, "must be a directory"):
                prepare_managed_file(state, "tasks/example/public.json")

    def test_batch_preflight_rejects_later_target_before_creating_first_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = ensure_root_directory(Path(temporary) / ".repotrials")
            (state / "private").mkdir()
            (state / "private/rt_bad.json").mkdir()

            with self.assertRaisesRegex(StatePathError, "must be a file"):
                prepare_managed_files(
                    state,
                    (
                        "tasks/rt_good/public.json",
                        "private/rt_bad.json",
                    ),
                )

            self.assertFalse((state / "tasks").exists())


class VaultPathTests(unittest.TestCase):
    def test_put_file_is_content_addressed_and_leaves_no_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "hidden.patch"
            source.write_bytes(b"private source patch")
            vault = ContentAddressedVault(base / "private")

            first = vault.put_file(source, media_type="text/x-diff")
            second = vault.put_file(source, media_type="text/x-diff")

            self.assertEqual(first, second)
            self.assertEqual(vault.get_bytes(first), source.read_bytes())
            self.assertEqual(tuple(vault.temp_dir.iterdir()), ())

    def test_rejects_symlink_root_without_touching_sink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            sink = base / "sink"
            sink.mkdir()
            marker = sink / "keep.txt"
            marker.write_text("unchanged", encoding="utf-8")
            _symlink(self, sink, base / "private", target_is_directory=True)

            with self.assertRaisesRegex(VaultError, "symlink|reparse"):
                ContentAddressedVault(base / "private")

            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual({item.name for item in sink.iterdir()}, {"keep.txt"})

    def test_rejects_nested_temp_symlink_before_creating_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "private"
            sink = base / "sink"
            root.mkdir()
            sink.mkdir()
            marker = sink / "keep.txt"
            marker.write_text("unchanged", encoding="utf-8")
            _symlink(self, sink, root / "tmp", target_is_directory=True)

            with self.assertRaisesRegex(VaultError, "symlink|reparse"):
                ContentAddressedVault(root)

            self.assertEqual({item.name for item in root.iterdir()}, {"tmp"})
            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

    def test_rejects_symlink_object_prefix_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            vault = ContentAddressedVault(base / "private")
            payload = b"hidden test"
            digest = hashlib.sha256(payload).hexdigest()
            sink = base / "sink"
            sink.mkdir()
            marker = sink / "keep.txt"
            marker.write_text("unchanged", encoding="utf-8")
            _symlink(self, sink, vault.objects_dir / digest[:2], target_is_directory=True)

            with self.assertRaisesRegex(VaultError, "symlink|reparse"):
                vault.put_bytes(payload)

            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual({item.name for item in sink.iterdir()}, {"keep.txt"})

    def test_put_file_preflights_object_prefix_before_temporary_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            vault = ContentAddressedVault(base / "private")
            source = base / "hidden.patch"
            source.write_bytes(b"private source patch")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            sink = base / "sink"
            sink.mkdir()
            marker = sink / "keep.txt"
            marker.write_text("unchanged", encoding="utf-8")
            _symlink(self, sink, vault.objects_dir / digest[:2], target_is_directory=True)

            with self.assertRaisesRegex(VaultError, "symlink|reparse"):
                vault.put_file(source)

            self.assertEqual(tuple(vault.temp_dir.iterdir()), ())
            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual({item.name for item in sink.iterdir()}, {"keep.txt"})


if __name__ == "__main__":
    unittest.main()
