"""Problem-statement selection, sanitization, and leakage heuristics."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_SHA = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{7,64})(?![0-9a-f])", re.IGNORECASE)
_SOLUTION_URL = re.compile(
    r"https?://(?:www\.)?github\.com/[^\s)]+/(?:pull|commit|compare)/[^\s)]+",
    re.IGNORECASE,
)
_IMPLEMENTATION_PHRASE = re.compile(
    r"(?im)^.*\b(?:fixed by|solution is|implemented by|change [`\w./-]+ to)\b.*$"
)


@dataclass(frozen=True, slots=True)
class PromptAssessment:
    text: str
    source: str
    risk: str
    findings: tuple[str, ...]


def build_prompt(
    *,
    issue_title: str = "",
    issue_body: str = "",
    pr_title: str = "",
    pr_body: str = "",
    commit_title: str = "",
    commit_body: str = "",
    added_code_lines: Iterable[str] = (),
) -> PromptAssessment:
    """Choose the strongest available historical statement and remove leaks."""

    if issue_title.strip():
        source = "issue"
        raw = _join(issue_title, issue_body)
    elif pr_title.strip():
        source = "pull_request"
        raw = _join(pr_title, pr_body)
    else:
        source = "commit"
        raw = _join(commit_title or "Historical bug fix", commit_body)

    findings: list[str] = []
    sanitized = _SOLUTION_URL.sub("[solution link removed]", raw)
    if sanitized != raw:
        findings.append("solution_url_removed")
    without_sha = _SHA.sub("[commit removed]", sanitized)
    if without_sha != sanitized:
        findings.append("commit_sha_removed")
    without_details = _IMPLEMENTATION_PHRASE.sub("", without_sha)
    if without_details != without_sha:
        findings.append("implementation_detail_removed")
    result = _normalize(without_details)

    normalized_prompt = _code_normalize(result)
    for line in added_code_lines:
        normalized_line = _code_normalize(line)
        if len(normalized_line) >= 32 and normalized_line in normalized_prompt:
            findings.append("overlap_with_gold_code")
            break

    if source == "commit":
        findings.append("weak_source_commit_message")
    if len(result) < 40:
        findings.append("underspecified_prompt")
    risk = "high" if "overlap_with_gold_code" in findings else "review" if findings else "low"
    return PromptAssessment(result, source, risk, tuple(dict.fromkeys(findings)))


def added_lines_from_patch(patch: str) -> tuple[str, ...]:
    return tuple(
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++") and line[1:].strip()
    )


def _join(title: str, body: str) -> str:
    title = title.strip()
    body = body.strip()
    return f"{title}\n\n{body}" if body else title


def _normalize(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _code_normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


__all__ = ["PromptAssessment", "added_lines_from_patch", "build_prompt"]
