from __future__ import annotations

from pathlib import Path

import pytest

from jira_bot.repo import checkout_repo, default_workspace, github_clone_url


def test_default_workspace() -> None:
    assert default_workspace() == Path.home() / "jira-repos"


def test_github_clone_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIRA_GIT_SSH", raising=False)
    assert github_clone_url("acme/app") == "https://github.com/acme/app.git"
    monkeypatch.setenv("JIRA_GIT_SSH", "1")
    assert github_clone_url("acme/app") == "git@github.com:acme/app.git"


def test_checkout_clones_and_creates_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, list[str]]] = []

    def fake_git(cwd: Path, args: list[str]) -> str:
        calls.append((cwd, args))
        if args[:1] == ["clone"]:
            (cwd / "app").mkdir()
        return ""

    monkeypatch.setattr("jira_bot.repo._git", fake_git)
    monkeypatch.setattr("jira_bot.repo._branch_exists", lambda path, name: False)

    repo_path = checkout_repo("acme/app", "PROJ-12", tmp_path)
    assert repo_path == tmp_path / "app"
    assert any(args == ["clone", "https://github.com/acme/app.git"] for _, args in calls)
    assert any(args == ["checkout", "-b", "proj-12"] for _, args in calls)


def test_checkout_pulls_existing_and_reuses_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_path = tmp_path / "app"
    repo_path.mkdir()
    calls: list[list[str]] = []

    def fake_git(cwd: Path, args: list[str]) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr("jira_bot.repo._git", fake_git)
    monkeypatch.setattr("jira_bot.repo._branch_exists", lambda path, name: True)

    result = checkout_repo("acme/app", "PROJ-12", tmp_path)
    assert result == repo_path
    assert ["checkout", "main"] in calls
    assert ["pull"] in calls
    assert ["checkout", "proj-12"] in calls
    assert ["checkout", "-b", "proj-12"] not in calls
