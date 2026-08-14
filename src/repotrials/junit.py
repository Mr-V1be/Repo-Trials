"""Small, dependency-free parser for common JUnit XML variants."""

from __future__ import annotations

import math
import os
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PASSING_TEST_STATUSES = frozenset({"passed", "xpassed"})
SUITE_HEALTHY_TEST_STATUSES = PASSING_TEST_STATUSES | frozenset({"skipped", "xfailed"})
MAX_JUNIT_BYTES = 16 * 1024 * 1024
MAX_JUNIT_CASES = 100_000


class JUnitParseError(ValueError):
    pass


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass(frozen=True, slots=True)
class TestOutcome:
    test_id: str
    status: str
    duration_seconds: float | None = None
    message: str = ""

    @property
    def passed(self) -> bool:
        return self.status in PASSING_TEST_STATUSES

    def to_dict(self) -> dict[str, object]:
        return {
            "test_id": self.test_id,
            "status": self.status,
            "duration_seconds": self.duration_seconds,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class JUnitReport:
    outcomes: tuple[TestOutcome, ...]

    @property
    def collected_count(self) -> int:
        return len(self.outcomes)

    @property
    def statuses(self) -> Mapping[str, str]:
        return {outcome.test_id: outcome.status for outcome in self.outcomes}

    @property
    def all_passed(self) -> bool:
        return bool(self.outcomes) and all(outcome.passed for outcome in self.outcomes)


def _case_identifier(case: ET.Element, suite_name: str) -> str:
    name = case.attrib.get("name", "").strip()
    if not name:
        raise JUnitParseError("JUnit testcase is missing a name")
    classname = case.attrib.get("classname", "").strip()
    file_name = case.attrib.get("file", "").strip().replace("\\", "/")
    prefix = classname or file_name or suite_name
    return f"{prefix}::{name}" if prefix else name


def _case_status(case: ET.Element) -> tuple[str, str]:
    skipped: tuple[str, str] | None = None
    for child in case:
        tag = _local_name(child.tag)
        if tag == "failure":
            return "failed", child.attrib.get("message", "") or (child.text or "").strip()
        if tag == "error":
            return "error", child.attrib.get("message", "") or (child.text or "").strip()
        if tag == "skipped":
            kind = child.attrib.get("type", "").lower()
            status = "xfailed" if "xfail" in kind else "skipped"
            skipped = status, child.attrib.get("message", "") or (child.text or "").strip()
    if skipped is not None:
        return skipped
    status = case.attrib.get("status", "").strip().lower()
    if status in {"notrun", "disabled", "skipped"}:
        return "skipped", ""
    return "passed", ""


def parse_junit_xml(source: str | bytes | os.PathLike[str]) -> JUnitReport:
    """Parse pytest, unittest, Surefire, and similar JUnit XML reports."""

    try:
        if isinstance(source, bytes):
            payload = source
        elif isinstance(source, os.PathLike):
            path = Path(source)
            if path.stat().st_size > MAX_JUNIT_BYTES:
                raise JUnitParseError(f"JUnit XML exceeds {MAX_JUNIT_BYTES} bytes")
            payload = path.read_bytes()
        elif "<" in source and ("\n" in source or source.lstrip().startswith("<")):
            payload = source.encode("utf-8")
        else:
            path = Path(source)
            if path.stat().st_size > MAX_JUNIT_BYTES:
                raise JUnitParseError(f"JUnit XML exceeds {MAX_JUNIT_BYTES} bytes")
            payload = path.read_bytes()
        if len(payload) > MAX_JUNIT_BYTES:
            raise JUnitParseError(f"JUnit XML exceeds {MAX_JUNIT_BYTES} bytes")
        if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
            raise JUnitParseError("JUnit XML declarations and entities are not supported")
        root = ET.fromstring(payload)
    except (ET.ParseError, OSError) as exc:
        raise JUnitParseError(f"cannot parse JUnit XML: {exc}") from exc

    outcomes: list[TestOutcome] = []
    identifiers: dict[str, int] = {}
    for suite in root.iter():
        if _local_name(suite.tag) != "testsuite":
            continue
        suite_name = suite.attrib.get("name", "").strip()
        for case in suite:
            if _local_name(case.tag) != "testcase":
                continue
            base_identifier = _case_identifier(case, suite_name)
            duplicate_number = identifiers.get(base_identifier, 0) + 1
            identifiers[base_identifier] = duplicate_number
            identifier = (
                base_identifier
                if duplicate_number == 1
                else f"{base_identifier}#{duplicate_number}"
            )
            status, message = _case_status(case)
            duration: float | None = None
            raw_duration = case.attrib.get("time")
            if raw_duration:
                try:
                    duration = float(raw_duration)
                except ValueError:
                    duration = None
                if duration is not None and (not math.isfinite(duration) or duration < 0):
                    duration = None
            outcomes.append(TestOutcome(identifier, status, duration, message))
            if len(outcomes) > MAX_JUNIT_CASES:
                raise JUnitParseError(f"JUnit XML has more than {MAX_JUNIT_CASES} testcases")

    # A single testsuite root is included by root.iter().  A bare testcase root
    # is uncommon but invalid for our benchmark contract, as it lacks suite
    # collection semantics.
    outcomes.sort(key=lambda item: (item.test_id.casefold(), item.test_id))
    return JUnitReport(tuple(outcomes))


__all__ = [
    "MAX_JUNIT_BYTES",
    "MAX_JUNIT_CASES",
    "PASSING_TEST_STATUSES",
    "SUITE_HEALTHY_TEST_STATUSES",
    "JUnitParseError",
    "JUnitReport",
    "TestOutcome",
    "parse_junit_xml",
]
