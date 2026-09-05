"""Jira Cloud REST API client and workflows.

Talks to `/rest/api/3` with an email + API token. Does not use the Atlassian
CLI (`acli`) or the Jira CLI marketplace app, which site admins often disable.
"""

from jira_bot.client import JiraClient, JiraError, repo_from_labels
from jira_bot.workflow import generate_plan, run_watch, start_ticket

__version__ = "0.2.0"

__all__ = [
    "JiraClient",
    "JiraError",
    "generate_plan",
    "repo_from_labels",
    "run_watch",
    "start_ticket",
    "__version__",
]
