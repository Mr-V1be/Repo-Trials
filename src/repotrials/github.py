"""Optional GitHub metadata enrichment without a mandatory SDK dependency."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class GitHubError(RuntimeError):
    """Raised for actionable GitHub API failures."""


@dataclass(frozen=True, slots=True)
class PullRequestMetadata:
    number: int
    title: str
    body: str
    html_url: str
    merged_at: str | None
    base_sha: str
    head_sha: str
    labels: tuple[str, ...]


class GitHubClient:
    """Minimal REST client used only when ``repotrials mine --github`` is set."""

    def __init__(
        self,
        token: str | None = None,
        *,
        api_url: str = "https://api.github.com",
        timeout: float = 20.0,
        user_agent: str = "repotrials/0.1",
    ) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent

    def request(self, path: str) -> Any:
        if not path.startswith("/"):
            path = "/" + path
        request = urllib.request.Request(
            self.api_url + path,
            headers=self._headers(),
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            detail = payload[:500]
            if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
                reset = exc.headers.get("X-RateLimit-Reset")
                reset_hint = ""
                if reset and reset.isdigit():
                    reset_hint = f"; resets at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(reset)))}"
                raise GitHubError(f"GitHub API rate limit exceeded{reset_hint}") from exc
            raise GitHubError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GitHubError(f"GitHub API request failed: {exc}") from exc

    def pull_for_commit(self, repository: str, commit_sha: str) -> PullRequestMetadata | None:
        repo = _validate_slug(repository)
        sha = _validate_sha(commit_sha)
        payload = self.request(f"/repos/{repo}/commits/{sha}/pulls")
        if not isinstance(payload, list):
            raise GitHubError("unexpected response for commit pull requests")
        merged = [item for item in payload if isinstance(item, Mapping) and item.get("merged_at")]
        candidates = merged or [item for item in payload if isinstance(item, Mapping)]
        if not candidates:
            return None
        # Prefer the most recently merged PR if GitHub associates a commit with
        # multiple backports or branches.
        item = sorted(candidates, key=lambda value: str(value.get("merged_at") or ""))[-1]
        return PullRequestMetadata(
            number=int(item["number"]),
            title=str(item.get("title") or ""),
            body=str(item.get("body") or ""),
            html_url=str(item.get("html_url") or ""),
            merged_at=str(item["merged_at"]) if item.get("merged_at") else None,
            base_sha=str((item.get("base") or {}).get("sha") or ""),
            head_sha=str((item.get("head") or {}).get("sha") or ""),
            labels=tuple(
                str(label.get("name"))
                for label in item.get("labels", [])
                if isinstance(label, Mapping) and label.get("name")
            ),
        )

    def issue(self, repository: str, number: int) -> Mapping[str, Any]:
        repo = _validate_slug(repository)
        if number < 1:
            raise ValueError("issue number must be positive")
        payload = self.request(f"/repos/{repo}/issues/{number}")
        if not isinstance(payload, Mapping):
            raise GitHubError("unexpected issue response")
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": self.user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def github_slug_from_remote(remote: str) -> str | None:
    """Extract ``owner/repository`` from common GitHub remote formats."""

    value = remote.strip()
    patterns = (
        r"^git@github\.com:(?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/(?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?$",
        r"^https?://github\.com/(?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.match(pattern, value, flags=re.IGNORECASE)
        if match:
            return match.group("slug").removesuffix(".git")
    return None


def discover_github_slug(repository_root: str | os.PathLike[str]) -> str | None:
    """Read ``remote.origin.url`` without invoking a shell."""

    import subprocess

    completed = subprocess.run(
        ("git", "config", "--get", "remote.origin.url"),
        cwd=Path(repository_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        return None
    return github_slug_from_remote(completed.stdout)


def _validate_slug(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError(f"invalid GitHub repository slug: {value!r}")
    return urllib.parse.quote(value, safe="/")


def _validate_sha(value: str) -> str:
    if not re.fullmatch(r"[a-fA-F0-9]{7,64}", value):
        raise ValueError("invalid Git commit SHA")
    return value


__all__ = [
    "GitHubClient",
    "GitHubError",
    "PullRequestMetadata",
    "discover_github_slug",
    "github_slug_from_remote",
]
