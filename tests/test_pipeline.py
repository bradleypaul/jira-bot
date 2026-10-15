from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from jira_bot.client import JiraError
from jira_bot.pipeline import implement_ticket, pr_body
from jira_bot.repo import GitError
from jira_bot.sandbox import AgentConfig, SandboxError

TESTS_PASSED = {"command": "npm test", "status": "passed", "details": "12 passed"}


@pytest.fixture
def git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, MagicMock]:
    """Replace the repo helpers the pipeline calls, so no git runs."""
    mocks = {
        "checkout_repo": MagicMock(return_value=tmp_path / "app"),
        "default_branch": MagicMock(return_value="main"),
        "has_changes": MagicMock(return_value=True),
        "commit_all": MagicMock(return_value="abc123"),
        "push_branch": MagicMock(),
        "discard_changes": MagicMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(f"jira_bot.pipeline.{name}", mock)
    return mocks


def _jira() -> MagicMock:
    client = MagicMock()
    client.base_url = "https://acme.atlassian.net"
    client.get_issue_summary.return_value = {
        "key": "PROJ-7",
        "summary": "Add CSV export",
        "labels": ["bot", "repo:acme/app"],
    }
    client.get_issue_comments.return_value = []
    client.transition_by_name.return_value = "In Review"
    return client


def _github() -> MagicMock:
    github = MagicMock()
    github.find_open_pull.return_value = None
    github.create_pull.return_value = {
        "number": 42,
        "title": "PROJ-7: Add CSV export",
        "html_url": "https://github.com/acme/app/pull/42",
    }
    return github


def _runner(result: dict[str, Any]) -> MagicMock:
    return MagicMock(return_value={"cost_usd": 1.25, "num_turns": 18, **result})


def _comment_text(client: MagicMock) -> str:
    """All text the pipeline posted to Jira, flattened."""
    return json.dumps([c.args[1] for c in client.add_comment.call_args_list])


def _run(client: MagicMock, github: MagicMock, runner: MagicMock, tmp_path: Path, **kwargs: Any):
    return implement_ticket(
        client,
        "PROJ-7",
        workspace=tmp_path,
        run_root=tmp_path / "runs",
        config=AgentConfig(),
        github=github,
        runner=runner,
        **kwargs,
    )


def test_implemented_opens_draft_pr_and_reports(git, tmp_path: Path) -> None:
    client, github = _jira(), _github()
    runner = _runner(
        {"status": "implemented", "title": "Add CSV export", "summary": "Adds /export.", "tests": TESTS_PASSED}
    )

    record = _run(client, github, runner, tmp_path)

    assert record["status"] == "implemented"
    assert record["pr_url"] == "https://github.com/acme/app/pull/42"
    git["commit_all"].assert_called_once()
    assert git["commit_all"].call_args.args[1].startswith("PROJ-7: Add CSV export")
    git["push_branch"].assert_called_once_with(tmp_path / "app", "proj-7")
    create = github.create_pull.call_args
    assert create.args[0] == "acme/app"
    assert create.kwargs["branch"] == "proj-7"
    assert create.kwargs["base"] == "main"
    assert "draft" not in create.kwargs  # create_pull defaults to a draft
    client.add_remote_link.assert_called_once()
    client.transition_by_name.assert_called_once_with("PROJ-7", "review")
    assert "pull/42" in _comment_text(client)
    git["discard_changes"].assert_not_called()


def test_agent_gets_ticket_but_no_credentials(git, tmp_path: Path) -> None:
    runner = _runner({"status": "blocked", "summary": "?"})
    _run(_jira(), _github(), runner, tmp_path)

    config, repo_path, run_dir, task = runner.call_args.args
    assert repo_path == tmp_path / "app"
    assert task["issue"]["key"] == "PROJ-7"
    assert set(task) == {"issue", "comments", "model", "effort", "max_turns", "max_budget_usd"}


def test_blocked_asks_for_clarification_without_pr(git, tmp_path: Path) -> None:
    client, github = _jira(), _github()
    runner = _runner({"status": "blocked", "summary": "Which date format should the CSV use?"})

    record = _run(client, github, runner, tmp_path)

    assert record["status"] == "blocked"
    git["discard_changes"].assert_called_once()
    github.create_pull.assert_not_called()
    text = _comment_text(client)
    assert "needs clarification" in text
    assert "Which date format" in text
    client.transition_by_name.assert_not_called()


def test_implemented_without_changes_opens_no_pr(git, tmp_path: Path) -> None:
    git["has_changes"].return_value = False
    client, github = _jira(), _github()
    runner = _runner({"status": "implemented", "title": "t", "summary": "Already done.", "tests": TESTS_PASSED})

    record = _run(client, github, runner, tmp_path)

    assert record["status"] == "no_changes"
    github.create_pull.assert_not_called()
    assert "without changing any files" in _comment_text(client)


def test_sandbox_failure_is_reported_not_raised(git, tmp_path: Path) -> None:
    client = _jira()
    runner = MagicMock(side_effect=SandboxError("docker is not installed"))

    record = _run(client, _github(), runner, tmp_path)

    assert record["status"] == "error"
    git["discard_changes"].assert_called_once()
    assert "docker is not installed" in _comment_text(client)


def test_publish_failure_is_reported(git, tmp_path: Path) -> None:
    git["push_branch"].side_effect = GitError("push rejected")
    client, github = _jira(), _github()
    runner = _runner({"status": "implemented", "title": "t", "summary": "s", "tests": TESTS_PASSED})

    record = _run(client, github, runner, tmp_path)

    assert record["status"] == "error"
    assert "push rejected" in record["error"]
    github.create_pull.assert_not_called()
    assert "push rejected" in _comment_text(client)


def test_rerun_updates_existing_pull(git, tmp_path: Path) -> None:
    github = _github()
    github.find_open_pull.return_value = {"number": 42}
    github.update_pull_body.return_value = github.create_pull.return_value
    runner = _runner({"status": "implemented", "title": "t", "summary": "s", "tests": TESTS_PASSED})

    record = _run(_jira(), github, runner, tmp_path)

    assert record["pr_url"].endswith("/42")
    github.create_pull.assert_not_called()
    github.update_pull_body.assert_called_once()


def test_empty_review_transition_skips_move(git, tmp_path: Path) -> None:
    client = _jira()
    runner = _runner({"status": "implemented", "title": "t", "summary": "s", "tests": TESTS_PASSED})
    _run(client, _github(), runner, tmp_path, review_transition="")
    client.transition_by_name.assert_not_called()


def test_missing_repo_label_fails_before_agent(git, tmp_path: Path) -> None:
    client = _jira()
    client.get_issue_summary.return_value["labels"] = ["bot"]
    runner = _runner({"status": "implemented"})

    with pytest.raises(JiraError, match="No repo:"):
        _run(client, _github(), runner, tmp_path)
    runner.assert_not_called()
    git["checkout_repo"].assert_not_called()


def test_pr_body_links_ticket_and_reports_tests() -> None:
    body = pr_body(
        "PROJ-7",
        "https://acme.atlassian.net/browse/PROJ-7",
        "Add CSV export",
        {"summary": "Adds /export.", "tests": TESTS_PASSED, "cost_usd": 1.25, "num_turns": 18},
    )
    assert "[PROJ-7](https://acme.atlassian.net/browse/PROJ-7)" in body
    assert "**Tests:** passed (`npm test`)" in body
    assert "12 passed" in body
    assert "$1.25 over 18 turns" in body
