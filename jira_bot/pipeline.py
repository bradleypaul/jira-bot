"""Take a Jira ticket through implementation and tests to a draft pull request."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jira_bot import adf
from jira_bot.client import JiraClient, JiraError, repo_from_labels
from jira_bot.github import GitHubClient, GitHubError
from jira_bot.repo import (
    GitError,
    checkout_repo,
    commit_all,
    default_branch,
    discard_changes,
    has_changes,
    push_branch,
)
from jira_bot.sandbox import AgentConfig, SandboxError, run_agent_container

logger = logging.getLogger(__name__)

Runner = Callable[[AgentConfig, Path, Path, dict[str, Any]], dict[str, Any]]


def implement_ticket(
    client: JiraClient,
    issue_key: str,
    workspace: Path,
    run_root: Path,
    config: AgentConfig | None = None,
    github: GitHubClient | None = None,
    runner: Runner = run_agent_container,
    review_transition: str | None = None,
) -> dict[str, Any]:
    """Run the agent on one ticket and report the outcome on the ticket.

    Errors before the agent starts (Jira, clone) propagate so the watcher can
    retry. Once the agent has run, failures are reported on the ticket and
    returned, so a paid run isn't repeated automatically.

    `review_transition` names the transition to apply once a pull request is
    open (default JIRA_BOT_REVIEW_TRANSITION or "review"); "" skips it.
    """
    config = config or AgentConfig.from_env()
    if review_transition is None:
        review_transition = os.getenv("JIRA_BOT_REVIEW_TRANSITION", "review")
    summary = client.get_issue_summary(issue_key)
    repo_slug = repo_from_labels(summary.get("labels") or [])
    if not repo_slug:
        raise JiraError(
            400,
            f"No repo: label found on {issue_key}. Add a label like repo:owner/repo-name",
        )
    comments = client.get_issue_comments(issue_key)
    github = github or GitHubClient()

    branch = issue_key.lower()
    repo_path = checkout_repo(repo_slug, issue_key, workspace)
    base = default_branch(repo_path)
    run_dir = run_root / f"{issue_key}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"

    client.add_comment(
        issue_key,
        adf.doc(adf.paragraph(f"jira-bot started on this ticket in {repo_slug} (branch {branch}).")),
    )
    task = {
        "issue": summary,
        "comments": comments,
        "model": config.model,
        "effort": config.effort,
        "max_turns": config.max_turns,
        "max_budget_usd": config.max_budget_usd,
    }
    logger.info("Running agent for %s in %s", issue_key, repo_path)
    try:
        result = runner(config, repo_path, run_dir, task)
    except SandboxError as exc:
        result = {"status": "error", "error": str(exc)}
    record = {
        "key": issue_key,
        "status": result.get("status"),
        "cost_usd": result.get("cost_usd"),
        "run_dir": str(run_dir),
    }

    try:
        if result.get("status") != "implemented":
            discard_changes(repo_path)
            _report_no_pr(client, issue_key, result, run_dir)
            return record
        if not has_changes(repo_path):
            record["status"] = "no_changes"
            _report_no_pr(client, issue_key, {**result, "status": "no_changes"}, run_dir)
            return record

        pr = _publish(client, github, summary, result, repo_path, repo_slug, branch, base)
        record["pr_url"] = pr["html_url"]
        _report_pr(client, issue_key, pr, result)
        if review_transition:
            moved = client.transition_by_name(issue_key, review_transition)
            if not moved:
                logger.info("No transition matching %r on %s", review_transition, issue_key)
    except (GitError, GitHubError, JiraError) as exc:
        logger.error("Publishing %s failed: %s", issue_key, exc)
        record["status"] = "error"
        record["error"] = str(exc)
        _report_no_pr(client, issue_key, {"status": "error", "error": str(exc)}, run_dir)
    return record


def _publish(
    client: JiraClient,
    github: GitHubClient,
    summary: dict[str, Any],
    result: dict[str, Any],
    repo_path: Path,
    repo_slug: str,
    branch: str,
    base: str,
) -> dict[str, Any]:
    key = summary["key"]
    ticket_url = f"{client.base_url}/browse/{key}"
    commit_all(
        repo_path,
        f"{key}: {result['title']}\n\n{result['summary']}\n\nJira: {ticket_url}\n",
    )
    push_branch(repo_path, branch)

    body = pr_body(key, ticket_url, summary.get("summary") or "", result)
    existing = github.find_open_pull(repo_slug, branch)
    if existing:
        return github.update_pull_body(repo_slug, existing["number"], body)
    return github.create_pull(
        repo_slug, branch=branch, base=base, title=f"{key}: {result['title']}", body=body
    )


def pr_body(key: str, ticket_url: str, ticket_summary: str, result: dict[str, Any]) -> str:
    tests = result.get("tests") or {}
    lines = [
        f"Implements [{key}]({ticket_url}): {ticket_summary}",
        "",
        result.get("summary", ""),
        "",
        f"**Tests:** {tests.get('status', 'not_run')}"
        + (f" (`{tests['command']}`)" if tests.get("command") else ""),
    ]
    if tests.get("details"):
        lines += ["", tests["details"]]
    lines += ["", "---", f"Opened by jira-bot as a draft for review. {_usage(result)}"]
    return "\n".join(lines)


def _report_pr(
    client: JiraClient, issue_key: str, pr: dict[str, Any], result: dict[str, Any]
) -> None:
    url = pr["html_url"]
    tests = result.get("tests") or {}
    client.add_comment(
        issue_key,
        adf.doc(
            adf.paragraph("Draft pull request ready for review: ", adf.text(url, href=url)),
            adf.paragraph(result.get("summary", "")),
            adf.paragraph(
                adf.text("Tests: ", bold=True),
                f"{tests.get('status', 'not_run')} {tests.get('command', '')}".strip(),
            ),
            adf.paragraph(_usage(result)),
        ),
    )
    client.add_remote_link(issue_key, url, f"PR #{pr['number']}: {pr['title']}")


def _report_no_pr(
    client: JiraClient, issue_key: str, result: dict[str, Any], run_dir: Path
) -> None:
    status = result.get("status")
    if status == "blocked":
        headline = "jira-bot needs clarification before it can implement this ticket:"
        detail = result.get("summary", "")
    elif status == "no_changes":
        headline = "jira-bot finished without changing any files:"
        detail = result.get("summary", "")
    else:
        headline = "jira-bot could not complete this ticket:"
        detail = result.get("error", "unknown error")
    blocks = [adf.paragraph(headline), adf.paragraph(detail)]
    if usage := _usage(result):
        blocks.append(adf.paragraph(usage))
    blocks.append(adf.paragraph(f"Logs on the bot host: {run_dir}"))
    try:
        client.add_comment(issue_key, adf.doc(*blocks))
    except JiraError as exc:
        logger.error("Could not comment on %s: %s", issue_key, exc)


def _usage(result: dict[str, Any]) -> str:
    cost = result.get("cost_usd")
    if cost is None:
        return ""
    return f"Agent cost ${cost:.2f} over {result.get('num_turns', '?')} turns."
