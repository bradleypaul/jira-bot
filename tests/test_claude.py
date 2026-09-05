from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jira_bot.claude import ClaudeClient, ClaudeError, _build_prompt


def test_build_prompt_includes_ticket_and_comments() -> None:
    prompt = _build_prompt(
        {
            "key": "PROJ-9",
            "summary": "Add export",
            "type": "Story",
            "status": "To Do",
            "priority": "Medium",
            "description": "CSV export of reports",
            "labels": ["repo:acme/app"],
        },
        [{"author": "Ada", "created": "2026-01-01", "body": "Use streaming"}],
        "Python backend",
    )
    assert "PROJ-9" in prompt
    assert "CSV export of reports" in prompt
    assert "Use streaming" in prompt
    assert "Python backend" in prompt
    assert "repo:acme/app" in prompt


def test_claude_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        ClaudeClient()


def test_generate_from_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ClaudeClient(api_key="sk-test", model="claude-test")
    response = MagicMock()
    response.ok = True
    response.json.return_value = {
        "content": [{"text": "1. Add a handler"}, {"text": "2. Write tests"}]
    }

    def fake_post(url, headers, json, timeout):
        assert url.endswith("/v1/messages")
        assert headers["x-api-key"] == "sk-test"
        assert json["model"] == "claude-test"
        assert json["messages"][0]["role"] == "user"
        return response

    monkeypatch.setattr("jira_bot.claude.requests.post", fake_post)
    result = client.generate_from_ticket(
        {"key": "PROJ-9", "summary": "Add export"},
        [],
    )
    assert result["issue_key"] == "PROJ-9"
    assert "Add a handler" in result["response"]


def test_claude_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ClaudeClient(api_key="sk-test")
    response = MagicMock()
    response.ok = False
    response.status_code = 401
    response.text = "unauthorized"
    monkeypatch.setattr(
        "jira_bot.claude.requests.post",
        lambda *args, **kwargs: response,
    )
    with pytest.raises(ClaudeError, match="401"):
        client.generate_from_ticket({"key": "PROJ-1"}, [])
