from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path

from repotrials.harbor import HarborExporter, HarborTaskSpec, validate_harbor_task


def _tar_bytes(name: str = "app.py", content: bytes = b"print('base')\n") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class HarborExporterTests(unittest.TestCase):
    def test_exports_standalone_separate_verifier_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec = HarborTaskSpec(
                task_id="Fix/Add Regression #1",
                instruction="Make add() return the mathematical sum.",
                agent_base_image="python:3.12-slim@sha256:abc",
                verifier_base_image="python:3.12-slim@sha256:def",
                base_archive=_tar_bytes(),
                setup_commands=("python -m pip --version",),
                submission_paths=("app.py",),
                verifier_files={
                    "grader.py": "raise SystemExit(0)\n",
                    "private/hidden.patch": b"secret hidden test",
                },
                metadata={"candidate": "private", "repeats": 3},
            )

            output = HarborExporter(temp).export(spec)

            self.assertEqual(validate_harbor_task(output), ())
            self.assertTrue((output / "environment/.repotrials-base.tar").is_file())
            self.assertTrue((output / "tests/.repotrials-base.tar").is_file())
            self.assertTrue((output / "environment/.repotrials-setup.py").is_file())
            self.assertTrue((output / "tests/.repotrials-setup.py").is_file())
            self.assertTrue((output / "tests/private/hidden.patch").is_file())
            self.assertFalse((output / "environment/private/hidden.patch").exists())
            self.assertFalse((output / "solution").exists())

            agent_dockerfile = (output / "environment/Dockerfile").read_text(encoding="utf-8")
            verifier_dockerfile = (output / "tests/Dockerfile").read_text(encoding="utf-8")
            for dockerfile in (agent_dockerfile, verifier_dockerfile):
                self.assertIn("tar -xf", dockerfile)
                self.assertIn("python /tmp/.repotrials-setup.py", dockerfile)
                self.assertIn("command -v bash", dockerfile)
                self.assertIn("git bash", dockerfile)
            self.assertIn("COPY . /tests", verifier_dockerfile)
            self.assertIn("git rev-parse HEAD > /opt/repotrials-base-sha", agent_dockerfile)
            self.assertNotIn("find /workspace/repo -mindepth 1", agent_dockerfile)
            self.assertIn("find /workspace/repo -mindepth 1", verifier_dockerfile)
            self.assertEqual(agent_dockerfile.count("tar -xf"), 1)
            self.assertEqual(verifier_dockerfile.count("tar -xf"), 2)

            with (output / "task.toml").open("rb") as handle:
                manifest = tomllib.load(handle)
            self.assertEqual(manifest["verifier"]["environment_mode"], "separate")
            self.assertEqual(manifest["environment"]["network_mode"], "no-network")
            self.assertEqual(manifest["environment"]["build_timeout_sec"], 600.0)
            self.assertEqual(manifest["schema_version"], "1.3")
            self.assertEqual(manifest["artifacts"], ["/tmp/agent.patch"])
            self.assertEqual(manifest["verifier"]["collect"][0]["service"], "main")
            collect = manifest["verifier"]["collect"][0]["command"]
            self.assertIn("base=$(cat -- /opt/repotrials-base-sha)", collect)
            self.assertIn("git add -A -- .", collect)
            self.assertIn("git --literal-pathspecs diff --cached --binary", collect)
            self.assertIn('"$base" -- .', collect)
            self.assertNotIn("--no-ext-diff HEAD", collect)
            self.assertIn("mv -f -- /tmp/agent.patch.tmp /tmp/agent.patch", collect)

            setup_runner = (output / "tests/.repotrials-setup.py").read_text(encoding="utf-8")
            self.assertIn("shlex.split(raw_command, posix=True)", setup_runner)
            self.assertIn("shell=False", setup_runner)
            self.assertNotIn("#!/bin/sh", setup_runner)

    def test_setup_runner_uses_portable_argv_without_a_shell(self) -> None:
        executable = Path(sys.executable).as_posix()
        command = (
            f'"{executable}" -c "import sys; '
            "sys.exit(0 if sys.argv[1:] == ['&&', 'sentinel'] else 9)\" && sentinel"
        )
        with tempfile.TemporaryDirectory() as temp:
            spec = HarborTaskSpec(
                task_id="argv-setup",
                instruction="Do work",
                agent_base_image="python:3.12",
                verifier_base_image="python:3.12",
                setup_commands=(command,),
                submission_paths=("app.py",),
                verifier_files={"grader.py": "raise SystemExit(0)"},
            )
            output = HarborExporter(temp).export(spec)
            runner = output / "tests/.repotrials-setup.py"

            completed = subprocess.run(
                (sys.executable, runner),
                cwd=temp,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_rejects_verifier_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            HarborTaskSpec(
                task_id="task",
                instruction="Do work",
                agent_base_image="python:3.12",
                verifier_base_image="python:3.12",
                submission_paths=("app.py",),
                verifier_files={"grader.py": "", "../hidden": "secret"},
            )

    def test_rejects_tar_path_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe base archive member"):
            HarborTaskSpec(
                task_id="task",
                instruction="Do work",
                agent_base_image="python:3.12",
                verifier_base_image="python:3.12",
                submission_paths=("app.py",),
                verifier_files={"grader.py": "raise SystemExit(0)"},
                base_archive=_tar_bytes("../escape.py"),
            )

    def test_rejects_git_history_in_base_archive(self) -> None:
        with self.assertRaisesRegex(ValueError, "historical metadata"):
            HarborTaskSpec(
                task_id="task",
                instruction="Do work",
                agent_base_image="python:3.12",
                verifier_base_image="python:3.12",
                submission_paths=("app.py",),
                verifier_files={"grader.py": "raise SystemExit(0)"},
                base_archive=_tar_bytes(".git/objects/secret"),
            )

    def test_existing_destination_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec = HarborTaskSpec(
                task_id="same-task",
                instruction="Do work",
                agent_base_image="python:3.12",
                verifier_base_image="python:3.12",
                submission_paths=("app.py",),
                verifier_files={"grader.py": "raise SystemExit(0)"},
            )
            exporter = HarborExporter(Path(temp))
            first = exporter.export(spec)
            self.assertEqual(exporter.export(spec), first)
            (first / "instruction.md").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "differs from immutable task"):
                exporter.export(spec)
            self.assertEqual((first / "instruction.md").read_text(encoding="utf-8"), "tampered\n")

    def test_generated_files_cannot_be_overwritten_by_aliases(self) -> None:
        for field, files in (
            ("verifier_files", {"grader.py": "", "./test.sh": "PWNED"}),
            ("environment_files", {"./Dockerfile": "PWNED"}),
        ):
            values = {
                "task_id": "reserved-alias",
                "instruction": "Do work",
                "agent_base_image": "python:3.12",
                "verifier_base_image": "python:3.12",
                "submission_paths": ("app.py",),
                "verifier_files": {"grader.py": "raise SystemExit(0)"},
                field: files,
            }
            with self.subTest(field=field), self.assertRaises(ValueError):
                HarborTaskSpec(**values)  # type: ignore[arg-type]

    def test_verifier_network_is_always_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec = HarborTaskSpec(
                task_id="network-policy",
                instruction="Do work",
                agent_base_image="python:3.12",
                verifier_base_image="python:3.12",
                submission_paths=("app.py",),
                verifier_files={"grader.py": "raise SystemExit(0)"},
                network_mode="public",
            )
            output = HarborExporter(temp).export(spec)
            with (output / "task.toml").open("rb") as handle:
                manifest = tomllib.load(handle)

            self.assertEqual(manifest["environment"]["network_mode"], "public")
            self.assertEqual(manifest["verifier"]["network_mode"], "no-network")
            self.assertEqual(manifest["verifier"]["environment"]["network_mode"], "no-network")
            self.assertEqual(manifest["verifier"]["environment"]["build_timeout_sec"], 600.0)

    def test_rejects_non_finite_or_boolean_limits(self) -> None:
        base: dict[str, object] = {
            "task_id": "invalid-limits",
            "instruction": "Do work",
            "agent_base_image": "python:3.12",
            "verifier_base_image": "python:3.12",
            "submission_paths": ("app.py",),
            "verifier_files": {"grader.py": "raise SystemExit(0)"},
        }
        for field, value in (("verifier_timeout_sec", float("nan")), ("cpus", True)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                HarborTaskSpec(**base, **{field: value})  # type: ignore[arg-type]
