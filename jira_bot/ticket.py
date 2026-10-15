"""Render a Jira ticket as Markdown for a model prompt."""

from __future__ import annotations

from typing import Any


def format_ticket(issue: dict[str, Any], comments: list[dict[str, Any]]) -> str:
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
    return "\n".join(lines)
