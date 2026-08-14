from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from repotrials.execution import (
    CommandExecutionError,
    CommandResult,
    DockerCommandBackend,
    DockerMount,
    LocalCommandBackend,
)


class RecordingBackend:
    def __init__(self) -> None:
        self.command: tuple[str, ...] = ()
        self.timeout: float | None = None
        self.input_text: str | None = None

    def run(
        self,
        command: object,
        *,
        cwd: object = None,
        env: object = None,
        timeout: float | None = None,
        input_text: str | None = None,
        check: bool = False,
    ) -> CommandResult:
        del cwd, env, check
        self.command = tuple(str(item) for item in command)  # type: ignore[union-attr]
        self.timeout = timeout
        self.input_text = input_text
        return CommandResult(self.command, 0, "ok", "", 0.01)


class TimeoutDockerBackend(RecordingBackend):
    def __init__(self) -> None:
        super().__init__()
        self.commands: list[tuple[str, ...]] = []

    def run(self, command: object, **kwargs: object) -> CommandResult:
        argv = tuple(str(item) for item in command)  # type: ignore[union-attr]
        self.commands.append(argv)
        if len(self.commands) == 1:
            cid_path = Path(argv[argv.index("--cidfile") + 1])
            cid_path.write_text("a" * 64, encoding="ascii")
            return CommandResult(argv, 124, "", "timeout", 0.01, timed_out=True)
        return CommandResult(argv, 0, "", "", 0.01)


class SessionRecordingBackend(RecordingBackend):
    def __init__(self) -> None:
        super().__init__()
        self.commands: list[tuple[str, ...]] = []

    def run(self, command: object, **kwargs: object) -> CommandResult:
        del kwargs
        argv = tuple(str(item) for item in command)  # type: ignore[union-attr]
        self.commands.append(argv)
        stdout = "b" * 64 if argv[:3] == ("docker", "run", "--detach") else ""
        return CommandResult(argv, 0, stdout, "", 0.01)


class LocalCommandBackendTests(unittest.TestCase):
    def test_runs_without_a_shell_and_controls_environment(self) -> None:
        backend = LocalCommandBackend(inherit_environment=False, base_environment={"BASE": "yes"})
        result = backend.run(
            (
                sys.executable,
                "-c",
                "import os; print(os.environ['BASE'] + os.environ['EXTRA'])",
            ),
            env={"EXTRA": "!"},
            check=True,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.stdout.strip(), "yes!")

    def test_timeout_and_check_failure_are_structured(self) -> None:
        backend = LocalCommandBackend()
        timeout = backend.run(
            (sys.executable, "-c", "import time; time.sleep(2)"),
            timeout=0.05,
        )
        self.assertTrue(timeout.timed_out)
        self.assertEqual(timeout.returncode, 124)

        with self.assertRaises(CommandExecutionError) as raised:
            backend.run((sys.executable, "-c", "raise SystemExit(7)"), check=True)
        self.assertEqual(raised.exception.result.returncode, 7)

    def test_rejects_empty_and_nul_commands(self) -> None:
        backend = LocalCommandBackend()
        for command in ("", ("bad\x00argument",)):
            with self.subTest(command=command), self.assertRaises(ValueError):
                backend.run(command)


class DockerCommandBackendTests(unittest.TestCase):
    def test_builds_a_bounded_docker_argv(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            readonly = root / "readonly"
            readonly.mkdir()
            recorder = RecordingBackend()
            backend = DockerCommandBackend(
                "python:3.12-slim@sha256:abc",
                platform_name="linux/amd64",
                network="none",
                user="1000:1000",
                read_only_root=True,
                mounts=(DockerMount(readonly, "/readonly"),),
                memory="512m",
                cpus=1.5,
                pids_limit=64,
                local_backend=recorder,  # type: ignore[arg-type]
            )

            result = backend.run(
                ("python", "-V"),
                cwd=root,
                env={"ZED": "value"},
                timeout=12,
                input_text="input",
            )

        self.assertTrue(result.ok)
        argv = recorder.command
        self.assertEqual(argv[:3], ("docker", "run", "--rm"))
        self.assertIn("--read-only", argv)
        platform_index = argv.index("--platform")
        self.assertEqual(argv[platform_index : platform_index + 2], ("--platform", "linux/amd64"))
        self.assertIn("1000:1000", argv)
        self.assertIn("512m", argv)
        self.assertIn("1.5", argv)
        self.assertIn("ZED=value", argv)
        self.assertEqual(argv[-3:], ("python:3.12-slim@sha256:abc", "python", "-V"))
        self.assertEqual(recorder.timeout, 12)
        self.assertEqual(recorder.input_text, "input")

    def test_validates_mounts_resources_and_environment_names(self) -> None:
        with self.assertRaises(ValueError):
            DockerMount(Path.cwd(), "relative")
        with self.assertRaises(ValueError):
            DockerCommandBackend("python:3.12", cpus=0)
        for platform_name in ("", "linux", "linux/amd64/", "linux amd64"):
            with self.subTest(platform_name=platform_name), self.assertRaises(ValueError):
                DockerCommandBackend("python:3.12", platform_name=platform_name)
        with tempfile.TemporaryDirectory() as raw:
            backend = DockerCommandBackend(
                "python:3.12",
                local_backend=RecordingBackend(),  # type: ignore[arg-type]
            )
            with self.assertRaises(ValueError):
                backend.run(("true",), cwd=raw, env={"BAD=NAME": "value"})

    def test_forcibly_removes_a_container_after_cli_timeout(self) -> None:
        recorder = TimeoutDockerBackend()
        backend = DockerCommandBackend(
            "python:3.12",
            local_backend=recorder,  # type: ignore[arg-type]
        )

        result = backend.run(("python", "-V"), timeout=0.01)

        self.assertTrue(result.timed_out)
        self.assertEqual(recorder.commands[1], ("docker", "rm", "--force", "a" * 64))

    def test_session_reuses_one_container_for_phase_commands(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            recorder = SessionRecordingBackend()
            backend = DockerCommandBackend(
                "python:3.12",
                platform_name="linux/amd64",
                local_backend=recorder,  # type: ignore[arg-type]
            )
            with backend.session(raw) as session:
                result = session.run(("python", "-V"), cwd=raw, env={"PHASE": "BASE"})

        self.assertTrue(result.ok)
        self.assertEqual(recorder.commands[0][:3], ("docker", "run", "--detach"))
        platform_index = recorder.commands[0].index("--platform")
        self.assertEqual(
            recorder.commands[0][platform_index : platform_index + 2],
            ("--platform", "linux/amd64"),
        )
        self.assertEqual(recorder.commands[1][:2], ("docker", "exec"))
        self.assertIn("PHASE=BASE", recorder.commands[1])
        self.assertEqual(recorder.commands[2], ("docker", "rm", "--force", "b" * 64))


if __name__ == "__main__":
    unittest.main()
