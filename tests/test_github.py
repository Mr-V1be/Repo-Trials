from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from repotrials.github import (
    GitHubClient,
    GitHubError,
    discover_github_slug,
    github_slug_from_remote,
)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class GitHubTests(unittest.TestCase):
    def test_common_remotes(self) -> None:
        cases = {
            "git@github.com:owner/repo.git": "owner/repo",
            "https://github.com/owner/repo.git": "owner/repo",
            "https://github.com/owner/repo": "owner/repo",
            "ssh://git@github.com/owner/repo.git": "owner/repo",
        }
        for remote, expected in cases.items():
            with self.subTest(remote=remote):
                self.assertEqual(github_slug_from_remote(remote), expected)

    def test_non_github_remote(self) -> None:
        self.assertIsNone(github_slug_from_remote("https://gitlab.com/owner/repo.git"))

    def test_pull_metadata_and_authenticated_headers(self) -> None:
        payload = [
            {
                "number": 7,
                "title": "Older",
                "merged_at": "2024-01-01T00:00:00Z",
                "base": {"sha": "base"},
                "head": {"sha": "head"},
            },
            {
                "number": 9,
                "title": "Fix crash",
                "body": None,
                "html_url": "https://github.com/acme/widget/pull/9",
                "merged_at": "2025-01-01T00:00:00Z",
                "base": {"sha": "base2"},
                "head": {"sha": "head2"},
                "labels": [{"name": "bug"}, {"other": "ignored"}],
            },
        ]
        client = GitHubClient("secret", api_url="https://example.invalid/")
        with patch("urllib.request.urlopen", return_value=FakeResponse(payload)) as request:
            metadata = client.pull_for_commit("acme/widget", "abcdef123")

        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(metadata.number, 9)
        self.assertEqual(metadata.labels, ("bug",))
        sent = request.call_args.args[0]
        self.assertEqual(
            sent.full_url, "https://example.invalid/repos/acme/widget/commits/abcdef123/pulls"
        )
        self.assertEqual(sent.headers["Authorization"], "Bearer secret")

    def test_empty_pull_list_and_issue_validation(self) -> None:
        client = GitHubClient()
        with patch.object(client, "request", return_value=[]):
            self.assertIsNone(client.pull_for_commit("acme/widget", "abcdef1"))
        with self.assertRaises(ValueError):
            client.issue("acme/widget", 0)
        with (
            patch.object(client, "request", return_value=["unexpected"]),
            self.assertRaises(GitHubError),
        ):
            client.issue("acme/widget", 1)

    def test_rate_limit_and_transport_errors_are_actionable(self) -> None:
        headers = Message()
        headers["X-RateLimit-Remaining"] = "0"
        headers["X-RateLimit-Reset"] = "1"
        rate_limit = urllib.error.HTTPError(
            "https://api.github.com/test",
            403,
            "forbidden",
            headers,
            io.BytesIO(b'{"message":"limit"}'),
        )
        client = GitHubClient()
        with (
            patch("urllib.request.urlopen", side_effect=rate_limit),
            self.assertRaisesRegex(GitHubError, "rate limit exceeded"),
        ):
            client.request("test")
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("offline"),
            ),
            self.assertRaisesRegex(GitHubError, "request failed"),
        ):
            client.request("/test")

    def test_discovers_slug_from_a_git_remote(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(("git", "init", "-q"), cwd=root, check=True)
            subprocess.run(
                ("git", "remote", "add", "origin", "git@github.com:acme/widget.git"),
                cwd=root,
                check=True,
            )
            self.assertEqual(discover_github_slug(root), "acme/widget")


if __name__ == "__main__":
    unittest.main()
