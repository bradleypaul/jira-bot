from __future__ import annotations

from unittest.mock import MagicMock

import anthropic
import httpx2
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


class _FakeStream:
    def __init__(self, message: object) -> None:
        self.message = message

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_final_message(self) -> object:
        return self.message


def _message(stop_reason: str = "end_turn", *texts: str) -> MagicMock:
    message = MagicMock()
    message.stop_reason = stop_reason
    message.content = [MagicMock(type="text", text=text) for text in texts]
    return message


def _client_returning(message: object) -> MagicMock:
    sdk = MagicMock()
    sdk.beta.messages.stream.return_value = _FakeStream(message)
    return sdk


def test_generate_from_ticket() -> None:
    sdk = _client_returning(_message("end_turn", "1. Add a handler", "2. Write tests"))
    client = ClaudeClient(model="claude-test", client=sdk)
    result = client.generate_from_ticket({"key": "PROJ-9", "summary": "Add export"}, [])

    assert result["issue_key"] == "PROJ-9"
    assert "Add a handler" in result["response"]
    assert "Write tests" in result["response"]
    kwargs = sdk.beta.messages.stream.call_args.kwargs
    assert kwargs["model"] == "claude-test"
    assert kwargs["messages"][0]["role"] == "user"
    assert kwargs["fallbacks"] == "default"


def test_model_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_BOT_MODEL", "claude-env")
    assert ClaudeClient(client=MagicMock()).model == "claude-env"


def test_refusal_raises() -> None:
    client = ClaudeClient(client=_client_returning(_message("refusal")))
    with pytest.raises(ClaudeError, match="declined"):
        client.generate_from_ticket({"key": "PROJ-1"}, [])


def test_claude_http_error() -> None:
    sdk = MagicMock()
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    sdk.beta.messages.stream.side_effect = anthropic.AuthenticationError(
        "unauthorized", response=httpx2.Response(401, request=request), body=None
    )
    with pytest.raises(ClaudeError, match="401"):
        ClaudeClient(client=sdk).generate_from_ticket({"key": "PROJ-1"}, [])


def test_missing_credentials() -> None:
    sdk = MagicMock()
    sdk.beta.messages.stream.side_effect = TypeError("Could not resolve authentication method")
    with pytest.raises(ClaudeError, match="No Anthropic credentials"):
        ClaudeClient(client=sdk).generate_from_ticket({"key": "PROJ-1"}, [])
