from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jira_bot.client import JiraError
from jira_bot.repo import GitError
from jira_bot.workflow import (
    generate_plan,
    handle_issue,
    prepare_seen,
    run_watch,
    seed_seen_keys,
    start_ticket,
    unmatched_issues,
)


def test_start_ticket_requires_repo_label() -> None:
    client = MagicMock()
    client.get_issue_summary.return_value = {
        "key": "PROJ-1",
        "summary": "No repo",
        "labels": ["bug"],
    }
    with pytest.raises(JiraError, match="No repo:"):
        start_ticket(client, "PROJ-1")


def test_start_ticket_transitions_and_checks_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = MagicMock()
    client.get_issue_summary.return_value = {
        "key": "PROJ-1",
        "summary": "Fix login",
        "labels": ["repo:acme/app"],
    }
    client.transition_to_in_progress.return_value = True
    monkeypatch.setattr(
        "jira_bot.workflow.checkout_repo",
        lambda slug, key, workspace: workspace / "app",
    )

    result = start_ticket(client, "PROJ-1", workspace=tmp_path)
    assert result["repo"] == "acme/app"
    assert result["transitioned"] is True
    assert result["repo_path"] == tmp_path / "app"
    client.transition_to_in_progress.assert_called_once_with("PROJ-1")


def test_generate_plan_uses_jira_then_claude() -> None:
    client = MagicMock()
    client.get_issue_summary.return_value = {"key": "PROJ-1", "summary": "Fix"}
    client.get_issue_comments.return_value = [{"author": "Ada", "body": "Ship it"}]
    claude = MagicMock()
    claude.generate_from_ticket.return_value = {
        "issue_key": "PROJ-1",
        "response": "Do the work",
    }

    result = generate_plan(
        client, "PROJ-1", extra_context="Python", claude=claude
    )
    assert result["response"] == "Do the work"
    claude.generate_from_ticket.assert_called_once()
    args = claude.generate_from_ticket.call_args[0]
    assert args[0]["key"] == "PROJ-1"
    assert args[1][0]["author"] == "Ada"
    assert args[2] == "Python"


def test_watch_helpers_track_new_keys() -> None:
    client = MagicMock()
    client.search_issues.return_value = {
        "issues": [
            {"key": "PROJ-1", "fields": {"summary": "Old"}},
            {"key": "PROJ-2", "fields": {"summary": "New"}},
        ]
    }
    seen = seed_seen_keys(client, "assignee = currentUser()")
    assert seen == {"PROJ-1", "PROJ-2"}

    client.search_issues.return_value = {
        "issues": [
            {"key": "PROJ-2", "fields": {"summary": "New"}},
            {"key": "PROJ-3", "fields": {"summary": "Newer"}},
        ]
    }
    found = unmatched_issues(client, "assignee = currentUser()", seen)
    assert [issue["key"] for issue in found] == ["PROJ-3"]
    assert "PROJ-3" not in seen


def test_first_run_seeds_state_without_starting(tmp_path: Path) -> None:
    client = MagicMock()
    client.search_issues.return_value = {
        "issues": [{"key": "PROJ-1", "fields": {"summary": "Already going"}}]
    }
    state = tmp_path / "seen.json"
    seen = prepare_seen(client, "assignee = currentUser()", state)
    assert seen == {"PROJ-1"}
    assert json.loads(state.read_text())["keys"] == ["PROJ-1"]


def test_restart_keeps_persisted_keys(tmp_path: Path) -> None:
    state = tmp_path / "seen.json"
    state.write_text('{"keys": ["PROJ-1"]}\n')
    client = MagicMock()
    seen = prepare_seen(client, "assignee = currentUser()", state)
    assert seen == {"PROJ-1"}
    client.search_issues.assert_not_called()


def test_handle_issue_retries_git_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = MagicMock()
    monkeypatch.setattr(
        "jira_bot.workflow.start_ticket",
        MagicMock(side_effect=GitError("clone failed")),
    )
    seen: set[str] = set()
    state = tmp_path / "seen.json"
    ok = handle_issue(
        client,
        {"key": "PROJ-9", "fields": {"summary": "Fix"}},
        seen,
        state,
        tmp_path,
    )
    assert ok is False
    assert seen == set()
    assert not state.exists()


def test_handle_issue_records_missing_repo_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = MagicMock()
    monkeypatch.setattr(
        "jira_bot.workflow.start_ticket",
        MagicMock(side_effect=JiraError(400, "No repo: label found on PROJ-9")),
    )
    seen: set[str] = set()
    state = tmp_path / "seen.json"
    ok = handle_issue(
        client,
        {"key": "PROJ-9", "fields": {"summary": "Fix"}},
        seen,
        state,
        tmp_path,
    )
    assert ok is True
    assert seen == {"PROJ-9"}


def test_run_watch_once_starts_unseen_ticket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = MagicMock()
    client.search_issues.return_value = {
        "issues": [{"key": "PROJ-4", "fields": {"summary": "New work"}}]
    }
    started: list[str] = []
    monkeypatch.setattr(
        "jira_bot.workflow.start_ticket",
        lambda client, key, workspace=None, log=None: started.append(key),
    )
    state = tmp_path / "seen.json"
    state.write_text('{"keys": []}\n')
    run_watch(
        client,
        jql="assignee = currentUser() AND status = 'In Progress'",
        interval=30,
        workspace=tmp_path,
        state_file=state,
        once=True,
        sleep=lambda _seconds: None,
    )
    assert started == ["PROJ-4"]
    assert json.loads(state.read_text())["keys"] == ["PROJ-4"]
