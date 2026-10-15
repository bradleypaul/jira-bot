"""Jira REST workflows used by scripts, jobs, and the optional CLI."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from jira_bot.claude import ClaudeClient
from jira_bot.client import JiraClient, JiraError, repo_from_labels
from jira_bot.github import GitHubClient, GitHubError
from jira_bot.pipeline import implement_ticket
from jira_bot.repo import GitError, checkout_repo, default_workspace

LogFn = Callable[[str], None]
DEFAULT_BOT_LABEL = "bot"


def watch_jql(label: str = DEFAULT_BOT_LABEL) -> str:
    """Assigned In Progress tickets that opted in with the bot label."""
    return f'assignee = currentUser() AND status = "In Progress" AND labels = "{label}"'


DEFAULT_WATCH_JQL = watch_jql()
logger = logging.getLogger(__name__)


def generate_plan(
    client: JiraClient,
    issue_key: str,
    extra_context: str | None = None,
    model: str | None = None,
    claude: ClaudeClient | None = None,
) -> dict[str, str]:
    """Fetch a ticket over the Jira API and ask Claude for an implementation plan."""
    summary = client.get_issue_summary(issue_key)
    comments = client.get_issue_comments(issue_key)
    claude_client = claude or ClaudeClient(model=model)
    return claude_client.generate_from_ticket(summary, comments, extra_context)


def start_ticket(
    client: JiraClient,
    issue_key: str,
    workspace: Path | str | None = None,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Transition a ticket via the Jira API and check out the linked GitHub repo."""
    emit = log or logger.info
    workspace_dir = Path(workspace) if workspace else default_workspace()

    emit(f"Fetching ticket {issue_key}...")
    summary = client.get_issue_summary(issue_key)
    repo_slug = repo_from_labels(summary.get("labels") or [])
    if not repo_slug:
        raise JiraError(
            400,
            f"No repo: label found on {issue_key}. "
            "Add a label like repo:owner/repo-name",
        )

    emit(f"  Ticket:  {summary['key']} — {summary['summary']}")
    emit(f"  Repo:    {repo_slug}")
    emit("Transitioning to In Progress...")
    transitioned = client.transition_to_in_progress(issue_key)
    if transitioned:
        emit("  Done — ticket is now In Progress")
    else:
        emit("  Ticket is already In Progress")

    emit("Setting up repository...")
    repo_path = checkout_repo(repo_slug, issue_key, workspace_dir)
    emit("Ready to work!")
    emit(f"  cd {repo_path}")
    return {
        "summary": summary,
        "repo": repo_slug,
        "repo_path": repo_path,
        "transitioned": transitioned,
    }


def search_issue_keys(client: JiraClient, jql: str) -> list[dict[str, Any]]:
    """Return issues matching JQL via POST /rest/api/3/search/jql."""
    results = client.search_issues(jql, max_results=50)
    return list(results.get("issues") or [])


def seed_seen_keys(client: JiraClient, jql: str) -> set[str]:
    """Snapshot keys that already match the watch query."""
    seen: set[str] = set()
    for issue in search_issue_keys(client, jql):
        key = issue.get("key")
        if key:
            seen.add(key)
    return seen


def unmatched_issues(
    client: JiraClient, jql: str, seen: set[str]
) -> list[dict[str, Any]]:
    """Return matching issues whose keys are not in the seen set."""
    found: list[dict[str, Any]] = []
    for issue in search_issue_keys(client, jql):
        key = issue.get("key")
        if key and key not in seen:
            found.append(issue)
    return found


def default_state_path() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME")
    root = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return root / "jira-bot" / "seen.json"


def load_seen(path: Path) -> set[str] | None:
    """Load persisted keys, or None if this machine has never watched."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise JiraError(500, f"Could not read state file {path}: {exc}") from exc
    keys = payload.get("keys", [])
    if not isinstance(keys, list):
        raise JiraError(500, f"State file {path} is missing a keys list")
    return {str(key) for key in keys}


def save_seen(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"keys": sorted(seen)}, indent=2) + "\n")


def prepare_seen(client: JiraClient, jql: str, state_file: Path) -> set[str]:
    """Load seen keys, or record current In Progress tickets on first run."""
    loaded = load_seen(state_file)
    if loaded is not None:
        return loaded
    seen = seed_seen_keys(client, jql)
    save_seen(state_file, seen)
    return seen


def handle_issue(
    client: JiraClient,
    issue: dict[str, Any],
    seen: set[str],
    state_file: Path,
    workspace: Path | str | None,
    github: GitHubClient | None = None,
) -> bool:
    """Run the pipeline on one ticket. Persist the key once it's handled.

    Failures before the agent starts are retried on the next poll. After the
    agent runs, the outcome is reported on the ticket and the key is kept, so
    a paid run isn't repeated; `jira-bot run KEY` re-runs it by hand.
    """
    key = str(issue.get("key") or "")
    summary = (issue.get("fields") or {}).get("summary", "")
    logger.info("New ticket for jira-bot: %s — %s", key, summary)
    try:
        record = implement_ticket(
            client,
            key,
            workspace=Path(workspace) if workspace else default_workspace(),
            run_root=state_file.parent / "runs",
            github=github,
        )
    except JiraError as exc:
        logger.error("Error handling %s: %s", key, exc)
        if "No repo:" not in str(exc):
            return False
    except (GitError, GitHubError) as exc:
        logger.error("Error handling %s: %s", key, exc)
        return False
    else:
        logger.info("%s finished: %s %s", key, record["status"], record.get("pr_url", ""))

    seen.add(key)
    save_seen(state_file, seen)
    return True


def run_watch(
    client: JiraClient,
    jql: str = DEFAULT_WATCH_JQL,
    interval: int = 30,
    workspace: Path | str | None = None,
    state_file: Path | None = None,
    once: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    github: GitHubClient | None = None,
) -> None:
    """Poll Jira for opted-in tickets and run the pipeline on new ones."""
    path = state_file or default_state_path()
    workspace_dir = Path(workspace) if workspace else default_workspace()
    # Fail at startup, not on the first ticket, if GitHub isn't configured.
    github = github or GitHubClient()
    seen = prepare_seen(client, jql, path)

    logger.info("Watching for opted-in In Progress tickets")
    logger.info("  JQL:       %s", jql)
    logger.info("  Interval:  %ss", interval)
    logger.info("  Workspace: %s", workspace_dir)
    logger.info("  State:     %s", path)
    if seen:
        logger.info("  Already handled: %s", ", ".join(sorted(seen)))

    while True:
        try:
            issues = unmatched_issues(client, jql, seen)
        except JiraError as exc:
            logger.error("Poll error: %s", exc)
        else:
            for issue in issues:
                handle_issue(client, issue, seen, path, workspace_dir, github)

        if once:
            return
        sleep(interval)
