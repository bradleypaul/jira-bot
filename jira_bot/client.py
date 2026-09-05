"""Jira REST API v3 client."""

from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


class JiraError(RuntimeError):
    """HTTP or API failure from Jira."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Jira API {status_code}: {message}")


class JiraClient:
    """Client for the Jira Cloud REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("JIRA_BASE_URL", "")).rstrip("/")
        self.email = email or os.getenv("JIRA_EMAIL", "")
        self.api_token = api_token or os.getenv("JIRA_API_TOKEN", "")

        if not all([self.base_url, self.email, self.api_token]):
            raise ValueError(
                "JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN must be set. "
                "Provide them as arguments or set them in a .env file."
            )

        self.session = requests.Session()
        self.session.auth = (self.email, self.api_token)
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}/rest/api/3/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        response = self.session.request(
            method, self._url(path), params=params, json=json
        )
        if not response.ok:
            raise JiraError(response.status_code, _error_message(response))
        if not response.content:
            return {}
        return response.json()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, json=payload)

    def get_issue(self, issue_key: str) -> dict[str, Any]:
        """Fetch a single issue by key (e.g. 'PROJ-123')."""
        return self._get(f"issue/{issue_key}")

    def get_issue_summary(self, issue_key: str) -> dict[str, Any]:
        """Return a concise dict with the most useful fields for an issue."""
        data = self.get_issue(issue_key)
        fields = data.get("fields", {})

        assignee = fields.get("assignee") or {}
        reporter = fields.get("reporter") or {}
        status = fields.get("status") or {}
        priority = fields.get("priority") or {}
        issuetype = fields.get("issuetype") or {}

        return {
            "key": data.get("key"),
            "summary": fields.get("summary"),
            "status": status.get("name"),
            "priority": priority.get("name"),
            "type": issuetype.get("name"),
            "assignee": assignee.get("displayName"),
            "reporter": reporter.get("displayName"),
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "description": extract_text(fields.get("description")),
            "labels": fields.get("labels", []),
        }

    def search_issues(
        self,
        jql: str,
        max_results: int = 50,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run a JQL query and return the matching issues."""
        payload: dict[str, Any] = {"jql": jql, "maxResults": max_results}
        if fields:
            payload["fields"] = fields
        return self._post("search/jql", payload)

    def get_issue_comments(self, issue_key: str) -> list[dict[str, Any]]:
        """Return all comments on an issue."""
        data = self._get(f"issue/{issue_key}/comment")
        comments = []
        for comment in data.get("comments", []):
            author = (comment.get("author") or {}).get("displayName", "Unknown")
            comments.append(
                {
                    "author": author,
                    "created": comment.get("created"),
                    "body": extract_text(comment.get("body")),
                }
            )
        return comments

    def get_issue_transitions(self, issue_key: str) -> list[dict[str, str]]:
        """Return available status transitions for an issue."""
        data = self._get(f"issue/{issue_key}/transitions")
        return [
            {"id": t["id"], "name": t["name"]}
            for t in data.get("transitions", [])
        ]

    def transition_issue(self, issue_key: str, transition_id: str) -> None:
        """Apply a workflow transition by id."""
        self._post(
            f"issue/{issue_key}/transitions",
            {"transition": {"id": transition_id}},
        )

    def transition_to_in_progress(self, issue_key: str) -> bool:
        """Move a ticket to In Progress. Returns False if already there."""
        summary = self.get_issue_summary(issue_key)
        status = (summary.get("status") or "").lower()
        if status == "in progress":
            return False

        transitions = self.get_issue_transitions(issue_key)
        match = next(
            (
                t
                for t in transitions
                if "in progress" in t["name"].lower() or "start" in t["name"].lower()
            ),
            None,
        )
        if match is None:
            names = ", ".join(t["name"] for t in transitions) or "(none)"
            raise JiraError(
                400,
                f"No In Progress transition for {issue_key}. Available: {names}",
            )
        self.transition_issue(issue_key, match["id"])
        return True

    def get_projects(self) -> list[dict[str, Any]]:
        """List all projects visible to the authenticated user."""
        return self._get("project")

    def get_myself(self) -> dict[str, Any]:
        """Return the profile of the authenticated user."""
        return self._get("myself")


def repo_from_labels(labels: list[str]) -> str | None:
    """Return the first `repo:owner/name` label value, if any."""
    for label in labels:
        if label.startswith("repo:"):
            return label.removeprefix("repo:").strip()
    return None


def extract_text(adf_node: Any) -> str:
    """Recursively extract plain text from an Atlassian Document Format node."""
    if adf_node is None:
        return ""
    if isinstance(adf_node, str):
        return adf_node
    if isinstance(adf_node, list):
        return "".join(extract_text(item) for item in adf_node)
    if isinstance(adf_node, dict):
        if adf_node.get("type") == "text":
            return adf_node.get("text", "")
        parts = [extract_text(child) for child in adf_node.get("content", [])]
        text = "".join(parts)
        if adf_node.get("type") in {"paragraph", "heading", "blockquote"}:
            return text + "\n"
        return text
    return ""


def _error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or response.reason

    messages = payload.get("errorMessages")
    if isinstance(messages, list) and messages:
        return "; ".join(str(m) for m in messages)
    if payload.get("message"):
        return str(payload["message"])
    return response.text or response.reason
