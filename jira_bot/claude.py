"""Claude Messages API helper for implementation plans."""

from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
SYSTEM_PROMPT = (
    "You are a senior software engineer. Given a Jira ticket, analyze the "
    "requirements and produce a clear, actionable implementation plan with "
    "code changes. Include file paths, function signatures, and concrete "
    "code snippets where possible. If the ticket is a bug, explain the "
    "likely root cause and the fix. If it is a feature, break it down into steps."
)


class ClaudeError(RuntimeError):
    """Failure talking to the Anthropic API."""


class ClaudeClient:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY must be set for the generate command."
            )
        self.model = model or DEFAULT_MODEL

    def generate_from_ticket(
        self,
        issue: dict[str, Any],
        comments: list[dict[str, Any]],
        extra_context: str | None = None,
    ) -> dict[str, str]:
        prompt = _build_prompt(issue, comments, extra_context)
        response = self._send_message(SYSTEM_PROMPT, prompt)
        return {"issue_key": str(issue.get("key") or ""), "response": response}

    def _send_message(self, system: str, user_message: str) -> str:
        response = requests.post(
            API_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4096,
                "system": system,
                "messages": [{"role": "user", "content": user_message}],
            },
            timeout=120,
        )
        if not response.ok:
            raise ClaudeError(
                f"Claude API {response.status_code}: {response.text}"
            )
        blocks = response.json().get("content", [])
        return "\n".join(
            block.get("text", "") for block in blocks if block.get("text")
        )


def _build_prompt(
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    extra_context: str | None,
) -> str:
    lines = [
        f"## Jira Ticket: {issue.get('key')}",
        f"**Summary:** {issue.get('summary')}",
        f"**Type:** {issue.get('type')}",
        f"**Status:** {issue.get('status')}",
        f"**Priority:** {issue.get('priority')}",
    ]
    if issue.get("description"):
        lines.extend(["", f"**Description:**\n{issue['description']}"])
    if issue.get("labels"):
        lines.append(f"**Labels:** {', '.join(issue['labels'])}")
    if comments:
        lines.append("\n**Comments:**")
        for comment in comments:
            lines.append(
                f"- {comment.get('author')} ({comment.get('created')}): "
                f"{comment.get('body')}"
            )
    if extra_context:
        lines.extend(["", f"**Additional context:**\n{extra_context}"])
    lines.append(
        "\nPlease analyze this ticket and provide the implementation plan "
        "with code changes."
    )
    return "\n".join(lines)
