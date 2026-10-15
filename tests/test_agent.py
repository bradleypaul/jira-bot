from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from jira_bot.agent import build_options, build_prompt, guard_bash, to_result

TASK = {
    "issue": {"key": "PROJ-7", "summary": "Add CSV export", "description": "Export reports"},
    "comments": [],
    "model": "claude-opus-5-5",
    "effort": "high",
    "max_turns": 100,
    "max_budget_usd": 10.0,
}


def _decision(command: str) -> dict:
    return asyncio.run(guard_bash({"tool_input": {"command": command}}, None, None))


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git diff --stat && git log -5",
        "npm test",
        "pytest -q",
        "echo legit commit",
        "git --no-pager show HEAD",
    ],
)
def test_guard_allows_reads_and_non_git(command: str) -> None:
    assert _decision(command) == {}


@pytest.mark.parametrize(
    ("command", "subcommand"),
    [
        ("git commit -m wip", "commit"),
        ("git push origin main", "push"),
        ("git -C /workspace checkout main", "checkout"),
        ("npm test && git reset --hard HEAD~1", "reset"),
        ("/usr/bin/git push --force", "push"),
        ("git status; git stash", "stash"),
        ("bash -c 'git rebase main'", "rebase"),
    ],
)
def test_guard_denies_history_changes(command: str, subcommand: str) -> None:
    output = _decision(command)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert f"git {subcommand}" in output["permissionDecisionReason"]


def test_options_lock_down_the_session() -> None:
    options = build_options(TASK, "/workspace")
    assert options.permission_mode == "dontAsk"
    assert "WebFetch" in options.disallowed_tools
    assert "WebSearch" in options.disallowed_tools
    assert "WebFetch" not in options.allowed_tools
    assert options.setting_sources == ["project"]
    assert options.max_budget_usd == 10.0
    assert options.output_format["type"] == "json_schema"


def test_prompt_contains_ticket() -> None:
    prompt = build_prompt(TASK)
    assert "PROJ-7" in prompt
    assert "Export reports" in prompt


def _result_message(**overrides) -> MagicMock:
    message = MagicMock(
        total_cost_usd=1.5,
        num_turns=20,
        duration_ms=90_000,
        session_id="s1",
        is_error=False,
        subtype="success",
        errors=None,
        structured_output={
            "outcome": "implemented",
            "title": "Add CSV export",
            "summary": "Adds /export.",
            "tests": {"command": "npm test", "status": "passed", "details": ""},
        },
    )
    for key, value in overrides.items():
        setattr(message, key, value)
    return message


def test_to_result_success() -> None:
    result = to_result(_result_message())
    assert result["status"] == "implemented"
    assert result["title"] == "Add CSV export"
    assert result["tests"]["status"] == "passed"
    assert result["cost_usd"] == 1.5


def test_to_result_error_keeps_cost() -> None:
    result = to_result(
        _result_message(is_error=True, subtype="error_max_turns", structured_output=None)
    )
    assert result["status"] == "error"
    assert result["error"] == "error_max_turns"
    assert result["cost_usd"] == 1.5


def test_to_result_without_message() -> None:
    assert to_result(None, error="boom") == {"status": "error", "error": "boom"}
