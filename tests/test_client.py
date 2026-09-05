from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from jira_bot.client import JiraClient, JiraError, extract_text, repo_from_labels


def _client() -> JiraClient:
    return JiraClient(
        base_url="https://example.atlassian.net",
        email="dev@example.com",
        api_token="token",
    )


def _response(status_code: int = 200, payload: Any = None, text: str = "") -> MagicMock:
    response = MagicMock()
    response.ok = 200 <= status_code < 300
    response.status_code = status_code
    response.text = text or ("" if payload is None else "error")
    response.reason = "Error"
    if payload is None:
        response.content = b""
        response.json.side_effect = ValueError("no json")
    else:
        response.content = b"{}"
        response.json.return_value = payload
    return response


def test_client_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="JIRA_BASE_URL"):
        JiraClient()


def test_extract_text_from_adf_paragraphs() -> None:
    node = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Hello"}],
            },
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "World"}],
            },
        ],
    }
    assert extract_text(node) == "Hello\nWorld\n"


def test_extract_text_plain_string_and_none() -> None:
    assert extract_text(None) == ""
    assert extract_text("already text") == "already text"


def test_repo_from_labels() -> None:
    assert repo_from_labels(["bug", "repo:owner/name", "backend"]) == "owner/name"
    assert repo_from_labels(["repo:  spaced/repo  "]) == "spaced/repo"
    assert repo_from_labels(["frontend"]) is None


def test_search_issues_posts_jql(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, params=None, json=None):
        captured.update(method=method, url=url, json=json)
        return _response(payload={"issues": [], "total": 0})

    monkeypatch.setattr(client.session, "request", fake_request)
    client.search_issues("assignee = currentUser()", max_results=10, fields=["summary"])

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/rest/api/3/search/jql")
    assert captured["json"] == {
        "jql": "assignee = currentUser()",
        "maxResults": 10,
        "fields": ["summary"],
    }


def test_get_issue_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(
        client.session,
        "request",
        lambda *args, **kwargs: _response(
            payload={
                "key": "PROJ-1",
                "fields": {
                    "summary": "Fix login",
                    "status": {"name": "To Do"},
                    "priority": {"name": "High"},
                    "issuetype": {"name": "Bug"},
                    "assignee": None,
                    "reporter": {"displayName": "Ada"},
                    "created": "2026-01-01",
                    "updated": "2026-01-02",
                    "description": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Broken auth"}],
                            }
                        ],
                    },
                    "labels": ["repo:acme/app"],
                },
            }
        ),
    )

    summary = client.get_issue_summary("PROJ-1")
    assert summary["key"] == "PROJ-1"
    assert summary["assignee"] is None
    assert summary["description"] == "Broken auth\n"
    assert summary["labels"] == ["repo:acme/app"]


def test_error_uses_jira_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(
        client.session,
        "request",
        lambda *args, **kwargs: _response(
            status_code=404,
            payload={"errorMessages": ["Issue does not exist"]},
        ),
    )
    with pytest.raises(JiraError, match="Issue does not exist") as exc:
        client.get_issue("MISSING-1")
    assert exc.value.status_code == 404


def test_transition_to_in_progress_skips_when_already_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    monkeypatch.setattr(
        client,
        "get_issue_summary",
        lambda key: {"status": "In Progress"},
    )
    assert client.transition_to_in_progress("PROJ-1") is False


def test_transition_to_in_progress_matches_start_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    monkeypatch.setattr(
        client,
        "get_issue_summary",
        lambda key: {"status": "To Do"},
    )
    monkeypatch.setattr(
        client,
        "get_issue_transitions",
        lambda key: [{"id": "21", "name": "Start Progress"}],
    )
    called: list[str] = []
    monkeypatch.setattr(
        client,
        "transition_issue",
        lambda key, tid: called.append(tid),
    )
    assert client.transition_to_in_progress("PROJ-1") is True
    assert called == ["21"]


def test_transition_to_in_progress_errors_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    monkeypatch.setattr(
        client,
        "get_issue_summary",
        lambda key: {"status": "To Do"},
    )
    monkeypatch.setattr(
        client,
        "get_issue_transitions",
        lambda key: [{"id": "31", "name": "Done"}],
    )
    with pytest.raises(JiraError, match="No In Progress transition"):
        client.transition_to_in_progress("PROJ-1")
