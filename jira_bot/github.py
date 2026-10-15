"""Minimal GitHub REST client for opening pull requests."""

from __future__ import annotations

import os
import subprocess
from typing import Any

import requests

API_URL = "https://api.github.com"


class GitHubError(RuntimeError):
    """HTTP or API failure from GitHub."""


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.getenv("GITHUB_TOKEN") or _gh_cli_token()
        if not self.token:
            raise ValueError(
                "Set GITHUB_TOKEN, or log in with `gh auth login`, to open pull requests."
            )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.session.request(method, f"{API_URL}/{path.lstrip('/')}", **kwargs)
        if not response.ok:
            raise GitHubError(f"GitHub API {response.status_code}: {response.text}")
        return response.json()

    def find_open_pull(self, repo_slug: str, branch: str) -> dict[str, Any] | None:
        owner = repo_slug.split("/", 1)[0]
        pulls = self._request(
            "GET",
            f"repos/{repo_slug}/pulls",
            params={"head": f"{owner}:{branch}", "state": "open"},
        )
        return pulls[0] if pulls else None

    def create_pull(
        self,
        repo_slug: str,
        branch: str,
        base: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"repos/{repo_slug}/pulls",
            json={"head": branch, "base": base, "title": title, "body": body, "draft": draft},
        )

    def update_pull_body(self, repo_slug: str, number: int, body: str) -> dict[str, Any]:
        return self._request("PATCH", f"repos/{repo_slug}/pulls/{number}", json={"body": body})


def _gh_cli_token() -> str:
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
