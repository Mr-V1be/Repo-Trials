from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from repotrials.config import DEFAULT_CONFIG, ConfigError, doctor, initialize_project, load_config


def _directory_symlink(test: unittest.TestCase, target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        test.skipTest(f"directory symlinks are unavailable: {exc}")


class ConfigTests(unittest.TestCase):
    def test_initialize_and_load_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(
                ("git", "init", "--quiet", "--initial-branch=main"),
                cwd=root,
                check=True,
            )
            path = initialize_project(root)
            self.assertTrue(path.is_file())
            config = load_config(root)
            self.assertEqual(config.root, root.resolve())
            self.assertEqual(config.validation.repeats, 3)
            self.assertIn("tests/**", config.test.protected_paths)
            self.assertTrue((root / ".repotrials/private").is_dir())
            self.assertIn("/.repotrials/", (root / ".git/info/exclude").read_text())

    def test_doctor_treats_harbor_as_optional_for_export_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(
                ("git", "init", "--quiet", "--initial-branch=main"),
                cwd=root,
                check=True,
            )
            initialize_project(root)
            config = load_config(root)
            config = replace(config, execution=replace(config.execution, backend="harbor"))
            real_which = shutil.which

            with mock.patch(
                "repotrials.config.shutil.which",
                side_effect=lambda name: None if name == "harbor" else real_which(name),
            ):
                checks = {name: (ok, detail) for name, ok, detail in doctor(config)}

            self.assertTrue(checks["harbor"][0])
            self.assertIn("export does not require it", checks["harbor"][1])

    def test_existing_valid_config_is_preserved_and_init_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(
                ("git", "init", "--quiet", "--initial-branch=main"),
                cwd=root,
                check=True,
            )
            config = root / "repotrials.toml"
            content = DEFAULT_CONFIG + "\n# tracked project customization\n"
            config.write_text(content, encoding="utf-8")

            self.assertTrue(initialize_project(root).samefile(config))
            self.assertTrue(initialize_project(root).samefile(config))
            self.assertEqual(config.read_text(encoding="utf-8"), content)
            exclude = root / ".git/info/exclude"
            self.assertEqual(exclude.read_text(encoding="utf-8").count("/.repotrials/"), 1)

    def test_existing_invalid_config_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(
                ("git", "init", "--quiet", "--initial-branch=main"),
                cwd=root,
                check=True,
            )
            config = root / "repotrials.toml"
            config.write_text("[mining]\nmagic = true\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "unknown option"):
                initialize_project(root)
            self.assertEqual(config.read_text(encoding="utf-8"), "[mining]\nmagic = true\n")
            self.assertFalse((root / ".repotrials").exists())

            initialize_project(root, force=True)
            self.assertEqual(config.read_text(encoding="utf-8"), DEFAULT_CONFIG)

    def test_refuses_non_repository_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            requested = Path(raw) / "not-created"
            with self.assertRaisesRegex(ConfigError, "Git working tree"):
                initialize_project(requested)
            self.assertFalse(requested.exists())

    def test_rejects_unknown_options(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "repotrials.toml").write_text("[mining]\nmagic = true\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "unknown option"):
                load_config(root)

    def test_rejects_weakly_typed_or_incomplete_contracts(self) -> None:
        cases = (
            ("[validation]\nrepeats = '3'\n", "must be an integer"),
            ("[mining]\nrequire_test_changes = 'false'\n", "must be true or false"),
            ("[test]\ncommand = 'pytest -q'\n", "must contain the {junit}"),
            ("[mining]\nkeyword_pattern = '['\n", "keyword_pattern is invalid"),
            ("[execution]\ncpus = 0\n", "cpus must be positive"),
        )
        for content, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                (root / "repotrials.toml").write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ConfigError, message):
                    load_config(root)

    def test_fresh_clone_keeps_tracked_config_and_excludes_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            source = base / "source"
            clone = base / "clone"
            source.mkdir()
            subprocess.run(
                ("git", "init", "--quiet", "--initial-branch=main"), cwd=source, check=True
            )
            subprocess.run(
                ("git", "config", "user.email", "tests@example.invalid"),
                cwd=source,
                check=True,
            )
            subprocess.run(
                ("git", "config", "user.name", "RepoTrials tests"), cwd=source, check=True
            )
            tracked = DEFAULT_CONFIG + "\n# committed benchmark profile\n"
            (source / "repotrials.toml").write_text(tracked, encoding="utf-8")
            subprocess.run(("git", "add", "-f", "repotrials.toml"), cwd=source, check=True)
            subprocess.run(
                ("git", "commit", "-qm", "add RepoTrials config"), cwd=source, check=True
            )
            subprocess.run(("git", "clone", "--quiet", str(source), str(clone)), check=True)

            self.assertFalse((clone / ".repotrials").exists())
            cloned_config = (clone / "repotrials.toml").read_bytes()
            initialize_project(clone)

            self.assertEqual((clone / "repotrials.toml").read_bytes(), cloned_config)
            self.assertIn("/.repotrials/", (clone / ".git/info/exclude").read_text())
            status = subprocess.run(
                ("git", "status", "--porcelain"),
                cwd=clone,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(status.stdout, "")

    def test_rejects_top_level_state_symlink_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            sink = Path(raw) / "sink"
            root.mkdir()
            sink.mkdir()
            marker = sink / "keep.txt"
            marker.write_text("unchanged", encoding="utf-8")
            subprocess.run(
                ("git", "init", "--quiet", "--initial-branch=main"), cwd=root, check=True
            )
            _directory_symlink(self, sink, root / ".repotrials")

            with self.assertRaisesRegex(ConfigError, "symlink|reparse"):
                initialize_project(root)

            self.assertFalse((root / "repotrials.toml").exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual({item.name for item in sink.iterdir()}, {"keep.txt"})

    def test_rejects_nested_state_symlink_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "repo"
            sink = Path(raw) / "sink"
            state = root / ".repotrials"
            root.mkdir()
            sink.mkdir()
            state.mkdir()
            marker = sink / "keep.txt"
            marker.write_text("unchanged", encoding="utf-8")
            subprocess.run(
                ("git", "init", "--quiet", "--initial-branch=main"), cwd=root, check=True
            )
            _directory_symlink(self, sink, state / "private")

            with self.assertRaisesRegex(ConfigError, "symlink|reparse"):
                initialize_project(root)

            self.assertFalse((root / "repotrials.toml").exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual({item.name for item in sink.iterdir()}, {"keep.txt"})
            self.assertEqual({item.name for item in state.iterdir()}, {"private"})

    def test_rejects_wrong_state_component_type_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = root / ".repotrials"
            state.mkdir()
            (state / "runs").write_text("not a directory", encoding="utf-8")
            subprocess.run(
                ("git", "init", "--quiet", "--initial-branch=main"), cwd=root, check=True
            )

            with self.assertRaisesRegex(ConfigError, "must be a directory"):
                initialize_project(root)

            self.assertFalse((root / "repotrials.toml").exists())
            self.assertEqual({item.name for item in state.iterdir()}, {"runs"})


if __name__ == "__main__":
    unittest.main()
