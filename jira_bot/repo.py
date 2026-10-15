"""Clone or update a GitHub repo and check out a ticket branch."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class GitError(RuntimeError):
    """git command failed."""


def checkout_repo(repo_slug: str, issue_key: str, workspace_dir: Path) -> Path:
    """Clone (or pull) a repo and create a feature branch named after the ticket."""
    repo_name = repo_slug.rsplit("/", 1)[-1]
    repo_path = workspace_dir / repo_name
    clone_url = github_clone_url(repo_slug)

    if repo_path.exists():
        logger.info("Repository already exists at %s, pulling latest...", repo_path)
        try:
            _git(repo_path, ["checkout", "main"])
        except GitError:
            _git(repo_path, ["checkout", "master"])
        _git(repo_path, ["pull"])
    else:
        logger.info("Cloning %s into %s...", clone_url, repo_path)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        _git(workspace_dir, ["clone", clone_url])

    branch_name = issue_key.lower()
    if _branch_exists(repo_path, branch_name):
        _git(repo_path, ["checkout", branch_name])
        logger.info("Switched to existing branch %s", branch_name)
    else:
        _git(repo_path, ["checkout", "-b", branch_name])
        logger.info("Created and switched to branch %s", branch_name)

    return repo_path


def default_workspace() -> Path:
    return Path.home() / "jira-repos"


def github_clone_url(repo_slug: str) -> str:
    """HTTPS by default; set JIRA_GIT_SSH=1 on a machine that has deploy keys."""
    flag = os.getenv("JIRA_GIT_SSH", "").lower()
    if flag in {"1", "true", "yes"}:
        return f"git@github.com:{repo_slug}.git"
    return f"https://github.com/{repo_slug}.git"


def default_branch(repo_path: Path) -> str:
    """Return the remote's default branch, e.g. 'main'."""
    ref = _git(repo_path, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    return ref.removeprefix("origin/")


def has_changes(repo_path: Path) -> bool:
    return bool(_git(repo_path, ["status", "--porcelain"]))


def commit_all(repo_path: Path, message: str) -> str:
    """Stage everything and commit. Returns the new commit sha.

    JIRA_BOT_GIT_NAME and JIRA_BOT_GIT_EMAIL override the committer, which
    helps when the service account has no git identity configured.
    """
    identity: list[str] = []
    if name := os.getenv("JIRA_BOT_GIT_NAME"):
        identity += ["-c", f"user.name={name}"]
    if email := os.getenv("JIRA_BOT_GIT_EMAIL"):
        identity += ["-c", f"user.email={email}"]
    _git(repo_path, ["add", "--all"])
    _git(repo_path, [*identity, "commit", "--file", "-"], stdin=message)
    return _git(repo_path, ["rev-parse", "HEAD"])


def discard_changes(repo_path: Path) -> None:
    """Drop uncommitted edits and untracked files (ignored files are kept)."""
    _git(repo_path, ["reset", "--hard", "HEAD"])
    _git(repo_path, ["clean", "-fd"])


def push_branch(repo_path: Path, branch_name: str) -> None:
    _git(repo_path, ["push", "--set-upstream", "origin", branch_name])


def _branch_exists(repo_path: Path, branch_name: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", branch_name],
        cwd=repo_path,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _git(cwd: Path, args: list[str], stdin: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()
