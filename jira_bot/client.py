import os
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

load_dotenv()


class JiraClient:
    """Client for interacting with the Jira REST API."""

    def __init__(self, base_url=None, email=None, api_token=None):
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
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _url(self, path):
        return f"{self.base_url}/rest/api/3/{path.lstrip('/')}"

    def _get(self, path, params=None):
        response = self.session.get(self._url(path), params=params)
        response.raise_for_status()
        return response.json()

    # ── Read operations ───────────────────────────────────────────────

    def get_issue(self, issue_key):
        """Fetch a single issue by key (e.g. 'PROJ-123')."""
        return self._get(f"issue/{issue_key}")

    def get_issue_summary(self, issue_key):
        """Return a concise dict with the most useful fields for an issue."""
        data = self.get_issue(issue_key)
        fields = data.get("fields", {})

        assignee = fields.get("assignee") or {}
        reporter = fields.get("reporter") or {}
        status = fields.get("status") or {}
        priority = fields.get("priority") or {}
        issuetype = fields.get("issuetype") or {}

        # Description can be Atlassian Document Format (ADF); extract plain text.
        description = _extract_text(fields.get("description"))

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
            "description": description,
            "labels": fields.get("labels", []),
        }

    def search_issues(self, jql, max_results=50, fields=None):
        """Run a JQL query and return the matching issues."""
        params = {
            "jql": jql,
            "maxResults": max_results,
        }
        if fields:
            params["fields"] = ",".join(fields)
        return self._get("search", params=params)

    def get_issue_comments(self, issue_key):
        """Return all comments on an issue."""
        data = self._get(f"issue/{issue_key}/comment")
        comments = []
        for c in data.get("comments", []):
            author = (c.get("author") or {}).get("displayName", "Unknown")
            comments.append({
                "author": author,
                "created": c.get("created"),
                "body": _extract_text(c.get("body")),
            })
        return comments

    def get_issue_transitions(self, issue_key):
        """Return available status transitions for an issue."""
        data = self._get(f"issue/{issue_key}/transitions")
        return [
            {"id": t["id"], "name": t["name"]}
            for t in data.get("transitions", [])
        ]

    def get_projects(self):
        """List all projects visible to the authenticated user."""
        return self._get("project")

    def get_myself(self):
        """Return the profile of the authenticated user (good for testing auth)."""
        return self._get("myself")


def _extract_text(adf_node):
    """Recursively extract plain text from an Atlassian Document Format node."""
    if adf_node is None:
        return ""
    if isinstance(adf_node, str):
        return adf_node
    if isinstance(adf_node, dict):
        if adf_node.get("type") == "text":
            return adf_node.get("text", "")
        parts = [_extract_text(child) for child in adf_node.get("content", [])]
        return "".join(parts)
    if isinstance(adf_node, list):
        return "".join(_extract_text(item) for item in adf_node)
    return ""
