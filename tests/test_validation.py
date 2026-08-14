from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from repotrials.junit import JUnitParseError, parse_junit_xml
from repotrials.validation import (
    ValidationPhase,
    ValidationPlan,
    ValidationRunner,
    check_patch_integrity,
    extract_patch_paths,
)
from repotrials.vault import ContentAddressedVault, VaultIntegrityError


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@unittest.skipUnless(shutil.which("git"), "git is required for patch validation")
class ValidationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary.name) / "repo"
        self.repository.mkdir()
        (self.repository / "calc.py").write_text(
            "def add(left, right):\n    return left - right\n", encoding="utf-8"
        )
        (self.repository / "test_existing.py").write_text(
            "import unittest\n"
            "from calc import add\n\n"
            "class ExistingTest(unittest.TestCase):\n"
            "    def test_identity(self):\n"
            "        self.assertEqual(add(2, 0), 2)\n",
            encoding="utf-8",
        )
        (self.repository / "test_runner.py").write_text(
            "import pathlib, sys, xml.etree.ElementTree as ET\n"
            "from calc import add\n"
            "assert pathlib.Path('setup.marker').is_file()\n"
            "suite = ET.Element('testsuite', name='repotrials')\n"
            "failures = 0\n"
            "def record(name, passed):\n"
            "    global failures\n"
            "    case = ET.SubElement(suite, 'testcase', classname='calc', name=name)\n"
            "    if not passed:\n"
            "        ET.SubElement(case, 'failure', message='assertion failed')\n"
            "        failures += 1\n"
            "record('test_identity', add(2, 0) == 2)\n"
            "if pathlib.Path('test_hidden.py').is_file():\n"
            "    record('test_adds', add(2, 3) == 5)\n"
            "optional_skip = ET.SubElement(suite, 'testcase', "
            "classname='calc', name='test_optional_skip')\n"
            "ET.SubElement(optional_skip, 'skipped', type='pytest.skip')\n"
            "optional_xfail = ET.SubElement(suite, 'testcase', "
            "classname='calc', name='test_optional_xfail')\n"
            "ET.SubElement(optional_xfail, 'skipped', type='pytest.xfail')\n"
            "ET.ElementTree(suite).write(sys.argv[1], encoding='utf-8', xml_declaration=True)\n"
            "raise SystemExit(1 if failures else 0)\n",
            encoding="utf-8",
        )
        _git(self.repository, "init", "-q")
        _git(self.repository, "config", "user.email", "tests@example.invalid")
        _git(self.repository, "config", "user.name", "RepoTrials tests")
        _git(self.repository, "add", ".")
        _git(self.repository, "commit", "-qm", "buggy base")

        hidden = self.repository / "test_hidden.py"
        hidden.write_text(
            "import unittest\n"
            "from calc import add\n\n"
            "class HiddenTest(unittest.TestCase):\n"
            "    def test_adds(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n",
            encoding="utf-8",
        )
        _git(self.repository, "add", "-N", "test_hidden.py")
        self.test_patch = _git(self.repository, "diff", "--binary", "--", "test_hidden.py").stdout
        _git(self.repository, "reset", "-q")
        hidden.unlink()

        (self.repository / "calc.py").write_text(
            "def add(left, right):\n    return left + right\n", encoding="utf-8"
        )
        self.gold_patch = _git(self.repository, "diff", "--binary", "--", "calc.py").stdout
        _git(self.repository, "restore", "calc.py")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def plan(self, **changes: object) -> ValidationPlan:
        values: dict[str, object] = {
            "base_dir": self.repository,
            "setup_commands": (
                (
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('setup.marker').write_text('ok')",
                ),
            ),
            "test_command": (sys.executable, "test_runner.py", "{junit}"),
            "test_patch": self.test_patch,
            "gold_patch": self.gold_patch,
            "repetitions": 2,
            "timeout": 30.0,
        }
        values.update(changes)
        return ValidationPlan(**values)  # type: ignore[arg-type]

    def test_repeated_base_red_gold_validation(self) -> None:
        report = ValidationRunner().validate(self.plan())

        self.assertTrue(report.valid, report.reasons)
        self.assertEqual(len(report.runs), 6)
        self.assertTrue(report.base_passed)
        self.assertTrue(report.red_failed)
        self.assertTrue(report.gold_passed)
        self.assertTrue(report.phase_stable(ValidationPhase.RED))
        self.assertEqual(report.fail_to_pass, ("calc::test_adds",))
        self.assertEqual(report.pass_to_pass, ("calc::test_identity",))
        for phase in (ValidationPhase.BASE, ValidationPhase.GOLD):
            for run in report.runs_for(phase):
                statuses = {item.test_id: item.status for item in run.test_outcomes}
                self.assertEqual(statuses["calc::test_optional_skip"], "skipped")
                self.assertEqual(statuses["calc::test_optional_xfail"], "xfailed")
                self.assertTrue(run.passed)
        for run in report.runs:
            self.assertEqual(len(run.setup_results), 1)
            self.assertTrue(run.setup_results[0].ok)
            self.assertIsNotNone(run.test_result)
            self.assertIn(".repotrials-junit.xml", run.test_result.command)
            self.assertNotIn("{junit}", run.test_result.command)
            self.assertGreaterEqual(run.collected_count, 1)

    def test_setup_failure_has_specific_reason(self) -> None:
        plan = self.plan(
            setup_commands=((sys.executable, "-c", "raise SystemExit(7)"),),
            repetitions=1,
        )
        report = ValidationRunner().validate(plan)

        self.assertFalse(report.valid)
        self.assertIn("setup_failed", report.reasons)
        self.assertIn("base_setup_failed", report.reasons)
        self.assertTrue(all(run.error == "setup_failed" for run in report.runs))

    def test_setup_cannot_mutate_the_phase_snapshot(self) -> None:
        plan = self.plan(
            setup_commands=((sys.executable, "-c", "open('calc.py', 'w').write('tampered')"),),
            repetitions=1,
        )
        report = ValidationRunner().validate(plan)

        self.assertFalse(report.valid)
        self.assertIn("setup_mutated_workspace", report.reasons)
        self.assertTrue(all(run.error == "setup_mutated_workspace" for run in report.runs))

    def test_setup_cannot_create_a_source_path_added_by_the_gold_patch(self) -> None:
        generated = self.repository / "generated_feature.py"
        generated.write_text("def value():\n    return 1\n", encoding="utf-8")
        _git(self.repository, "add", "-N", "generated_feature.py")
        gold_patch = _git(
            self.repository,
            "diff",
            "--binary",
            "--",
            "generated_feature.py",
        ).stdout
        _git(self.repository, "reset", "-q")
        generated.unlink()
        runner = self.repository / "generated_runner.py"
        runner.write_text(
            "import pathlib, sys, xml.etree.ElementTree as ET\n"
            "suite = ET.Element('testsuite', name='repotrials')\n"
            "failures = 0\n"
            "def record(name, passed):\n"
            "    global failures\n"
            "    case = ET.SubElement(suite, 'testcase', classname='generated', name=name)\n"
            "    if not passed:\n"
            "        ET.SubElement(case, 'failure')\n"
            "        failures += 1\n"
            "record('test_existing', True)\n"
            "if pathlib.Path('test_hidden.py').is_file():\n"
            "    from generated_feature import value\n"
            "    record('test_generated', value() == 1)\n"
            "ET.ElementTree(suite).write(sys.argv[1], encoding='utf-8')\n"
            "raise SystemExit(1 if failures else 0)\n",
            encoding="utf-8",
        )
        setup = (
            sys.executable,
            "-c",
            "from pathlib import Path; "
            "Path('setup.marker').write_text('ok'); "
            "p = Path('generated_feature.py'); "
            "p.exists() or p.write_text('def value():\\n    return 0\\n')",
        )

        report = ValidationRunner().validate(
            self.plan(
                gold_patch=gold_patch,
                test_command=(sys.executable, "generated_runner.py", "{junit}"),
                setup_commands=(setup,),
                repetitions=1,
            )
        )

        self.assertFalse(report.valid)
        diagnostic = "setup_created_submission_path: generated_feature.py"
        self.assertIn(diagnostic, report.reasons)
        self.assertEqual(
            {run.error for run in report.runs_for(ValidationPhase.BASE)},
            {diagnostic},
        )
        self.assertEqual(
            {run.error for run in report.runs_for(ValidationPhase.RED)},
            {diagnostic},
        )

    def test_protected_path_integrity(self) -> None:
        report = check_patch_integrity(self.test_patch, ("test*.py", "tests/**"))

        self.assertFalse(report.ok)
        self.assertIn("test_hidden.py", report.paths)
        self.assertEqual(report.violations[0].reason, "protected path")
        self.assertEqual(extract_patch_paths(self.gold_patch), ("calc.py",))

    def test_integrity_globs_cover_root_and_dot_directories(self) -> None:
        for path in ("conftest.py", ".github/workflows/pwn.yml", "pytest/__main__.py"):
            patch = (
                f"diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+x\n"
            )
            with self.subTest(path=path):
                report = check_patch_integrity(
                    patch,
                    ("**/conftest.py", ".github/**", "pytest/**"),
                )
                self.assertFalse(report.ok)
                self.assertEqual(report.violations[0].reason, "protected path")

    def test_hidden_patch_must_stay_in_test_allowlist(self) -> None:
        report = check_patch_integrity(
            self.gold_patch,
            (),
            allowed_paths=("tests/**", "**/test_*.py"),
        )

        self.assertFalse(report.ok)
        self.assertEqual(report.violations[0].reason, "path is outside the allowlist")

    def test_submission_allowlist_treats_git_metacharacters_literally(self) -> None:
        def patch(path: str) -> str:
            return (
                f"diff --git a/{path} b/{path}\n"
                f"--- a/{path}\n"
                f"+++ b/{path}\n"
                "@@ -1 +1 @@\n-old\n+new\n"
            )

        for literal, other in (("src/*.py", "src/evil.py"), ("src/[old].py", "src/o.py")):
            with self.subTest(literal=literal):
                rejected = check_patch_integrity(patch(other), (), exact_allowed_paths=(literal,))
                accepted = check_patch_integrity(patch(literal), (), exact_allowed_paths=(literal,))
                self.assertFalse(rejected.ok)
                self.assertEqual(rejected.violations[0].reason, "path is outside the allowlist")
                self.assertTrue(accepted.ok)

        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            check_patch_integrity(
                self.gold_patch,
                (),
                allowed_paths=("*.py",),
                exact_allowed_paths=("calc.py",),
            )

    def test_exit_zero_cannot_hide_a_failed_junit_case(self) -> None:
        runner = self.repository / "lying_runner.py"
        runner.write_text(
            "from pathlib import Path\n"
            "Path(__import__('sys').argv[1]).write_text(\"<testsuite><testcase "
            "classname='calc' name='test_identity'><failure/></testcase></testsuite>\")\n",
            encoding="utf-8",
        )
        report = ValidationRunner().validate(
            self.plan(
                test_command=(sys.executable, "lying_runner.py", "{junit}"),
                setup_commands=(),
                repetitions=1,
            )
        )

        self.assertFalse(report.valid)
        self.assertIn("base_failed", report.reasons)
        self.assertIn("gold_failed", report.reasons)


class VaultTests(unittest.TestCase):
    def test_content_addressing_deduplicates_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = ContentAddressedVault(Path(temp) / "private")
            first = vault.put_text("secret test patch")
            second = vault.put_text("secret test patch")
            manifest = vault.put_json({"gold": first.uri, "tests": ["one"]})

            self.assertEqual(first.digest, second.digest)
            self.assertEqual(vault.get_text(first), "secret test patch")
            self.assertEqual(vault.get_json(manifest)["gold"], first.uri)
            self.assertTrue(vault.verify(first))
            self.assertEqual(len(tuple(vault.iter_references())), 2)

            object_path = vault.object_path(first)
            object_path.write_bytes(b"tampered")
            self.assertFalse(vault.verify(first))
            with self.assertRaises(VaultIntegrityError):
                vault.get_bytes(first)

    def test_canonical_json_has_stable_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = ContentAddressedVault(temp)
            left = vault.put_json({"b": 2, "a": 1})
            right = vault.put_json(json.loads('{"a": 1, "b": 2}'))
            self.assertEqual(left.digest, right.digest)


class JUnitParserTests(unittest.TestCase):
    def test_parses_nested_suites_and_failure_status(self) -> None:
        report = parse_junit_xml(
            b"""<?xml version='1.0'?>
            <testsuites><testsuite name='unit'>
              <testcase classname='pkg.Test' name='green' time='0.1'/>
              <testcase classname='pkg.Test' name='red'><failure message='boom'/></testcase>
            </testsuite></testsuites>"""
        )

        self.assertEqual(report.collected_count, 2)
        self.assertEqual(report.statuses["pkg.Test::green"], "passed")
        self.assertEqual(report.statuses["pkg.Test::red"], "failed")
        self.assertFalse(report.all_passed)

    def test_failure_precedes_skip_and_duplicates_are_explicit(self) -> None:
        report = parse_junit_xml(
            b"""<testsuite name='suite'>
            <testcase classname='pkg.Test' name='same'><skipped/><failure/></testcase>
            <testcase classname='pkg.Test' name='same'/>
            </testsuite>"""
        )

        self.assertEqual(report.statuses["pkg.Test::same"], "failed")
        self.assertEqual(report.statuses["pkg.Test::same#2"], "passed")

    def test_rejects_entity_declarations(self) -> None:
        with self.assertRaises(JUnitParseError):
            parse_junit_xml(
                b"<!DOCTYPE testsuite [<!ENTITY x 'value'>]>"
                b"<testsuite><testcase name='x'/></testsuite>"
            )
