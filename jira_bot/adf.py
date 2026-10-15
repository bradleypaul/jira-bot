"""Build Atlassian Document Format bodies for Jira comments.

REST API v3 rejects plain-text comment bodies, so every comment goes through
these helpers.
"""

from __future__ import annotations

from typing import Any

Node = dict[str, Any]


def text(value: str, href: str | None = None, bold: bool = False) -> Node:
    marks: list[Node] = []
    if bold:
        marks.append({"type": "strong"})
    if href:
        marks.append({"type": "link", "attrs": {"href": href}})
    node: Node = {"type": "text", "text": value}
    if marks:
        node["marks"] = marks
    return node


def paragraph(*parts: str | Node) -> Node:
    content = [text(part) if isinstance(part, str) else part for part in parts]
    return {"type": "paragraph", "content": [n for n in content if n.get("text")]}


def bullet_list(items: list[str]) -> Node:
    return {
        "type": "bulletList",
        "content": [{"type": "listItem", "content": [paragraph(item)]} for item in items],
    }


def code_block(value: str) -> Node:
    return {"type": "codeBlock", "content": [text(value)] if value else []}


def doc(*blocks: Node) -> Node:
    return {"type": "doc", "version": 1, "content": list(blocks)}
