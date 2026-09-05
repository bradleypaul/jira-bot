"""Optional CLI wrapper around the Jira REST workflows."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from jira_bot import __version__
from jira_bot.claude import ClaudeError
from jira_bot.client import JiraClient, JiraError
from jira_bot.repo import GitError
from jira_bot.workflow import (
    DEFAULT_WATCH_JQL,
    default_state_path,
    generate_plan,
    run_watch,
    start_ticket,
)


def _print_issue(summary: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {summary['key']}  —  {summary['summary']}")
    print(f"{'=' * 60}")
    print(f"  Type:     {summary['type']}")
    print(f"  Status:   {summary['status']}")
    print(f"  Priority: {summary['priority']}")
    print(f"  Assignee: {summary['assignee'] or 'Unassigned'}")
    print(f"  Reporter: {summary['reporter']}")
    print(f"  Created:  {summary['created']}")
    print(f"  Updated:  {summary['updated']}")
    if summary["labels"]:
        print(f"  Labels:   {', '.join(summary['labels'])}")
    if summary["description"]:
        print(f"\n  Description:\n  {summary['description']}")
    print()


def cmd_issue(args: argparse.Namespace, client: JiraClient) -> None:
    summary = client.get_issue_summary(args.issue_key)
    if args.json:
        print(json.dumps(summary, indent=2))
        return
    _print_issue(summary)


def cmd_comments(args: argparse.Namespace, client: JiraClient) -> None:
    comments = client.get_issue_comments(args.issue_key)
    if args.json:
        print(json.dumps(comments, indent=2))
        return
    if not comments:
        print(f"No comments on {args.issue_key}")
        return
    for comment in comments:
        print(f"\n--- {comment['author']}  ({comment['created']}) ---")
        print(comment["body"])


def cmd_search(args: argparse.Namespace, client: JiraClient) -> None:
    results = client.search_issues(args.jql, max_results=args.max_results)
    issues = results.get("issues", [])
    if args.json:
        print(json.dumps(results, indent=2))
        return
    print(f"\n{results.get('total', len(issues))} result(s) (showing {len(issues)})\n")
    for issue in issues:
        fields = issue.get("fields", {})
        status = (fields.get("status") or {}).get("name", "")
        print(f"  {issue['key']:12s}  [{status:12s}]  {fields.get('summary', '')}")
    print()


def cmd_projects(args: argparse.Namespace, client: JiraClient) -> None:
    projects = client.get_projects()
    if args.json:
        print(json.dumps(projects, indent=2))
        return
    for project in projects:
        print(f"  {project['key']:10s}  {project.get('name', '')}")


def cmd_whoami(args: argparse.Namespace, client: JiraClient) -> None:
    me = client.get_myself()
    if args.json:
        print(json.dumps(me, indent=2))
        return
    print(f"  Logged in as: {me.get('displayName')} ({me.get('emailAddress')})")


def cmd_transitions(args: argparse.Namespace, client: JiraClient) -> None:
    transitions = client.get_issue_transitions(args.issue_key)
    if args.json:
        print(json.dumps(transitions, indent=2))
        return
    if not transitions:
        print(f"No transitions available on {args.issue_key}")
        return
    for transition in transitions:
        print(f"  {transition['id']:8s}  {transition['name']}")


def cmd_generate(args: argparse.Namespace, client: JiraClient) -> None:
    print(f"Fetching ticket {args.issue_key} from Jira...", file=sys.stderr)
    print("Sending to Claude for analysis...", file=sys.stderr)
    result = generate_plan(
        client, args.issue_key, extra_context=args.context, model=args.model
    )
    if args.json:
        print(json.dumps(result, indent=2))
        return
    print()
    print("=" * 60)
    print(f"  Generated plan for {result['issue_key']}")
    print("=" * 60)
    print()
    print(result["response"])


def cmd_start(args: argparse.Namespace, client: JiraClient) -> None:
    workspace = Path(args.workspace) if args.workspace else None
    start_ticket(client, args.issue_key, workspace=workspace, log=print)


def cmd_watch(args: argparse.Namespace, client: JiraClient) -> None:
    _setup_service_logging()
    workspace = args.workspace or os.getenv("JIRA_WORKSPACE")
    jql = args.jql or os.getenv("JIRA_WATCH_JQL") or DEFAULT_WATCH_JQL
    interval = args.interval
    if interval is None:
        interval = _env_int("JIRA_WATCH_INTERVAL", 30)
    state = args.state_file or os.getenv("JIRA_STATE_FILE")
    state_file = Path(state) if state else default_state_path()
    run_watch(
        client,
        jql=jql,
        interval=interval,
        workspace=Path(workspace) if workspace else None,
        state_file=state_file,
        once=args.once,
    )


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _setup_service_logging() -> None:
    level_name = os.getenv("JIRA_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jira-bot",
        description=(
            "Call the Jira Cloud REST API from your machine. "
            "Does not use the Atlassian CLI."
        ),
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_issue = subparsers.add_parser("issue", help="Get details of a single issue")
    p_issue.add_argument("issue_key", help="Issue key, e.g. PROJ-123")
    p_issue.set_defaults(func=cmd_issue)

    p_comments = subparsers.add_parser("comments", help="List comments on an issue")
    p_comments.add_argument("issue_key")
    p_comments.set_defaults(func=cmd_comments)

    p_search = subparsers.add_parser("search", help="Search issues with JQL")
    p_search.add_argument("jql")
    p_search.add_argument("-n", "--max-results", type=int, default=20)
    p_search.set_defaults(func=cmd_search)

    p_projects = subparsers.add_parser("projects", help="List visible projects")
    p_projects.set_defaults(func=cmd_projects)

    p_whoami = subparsers.add_parser("whoami", help="Show authenticated user")
    p_whoami.set_defaults(func=cmd_whoami)

    p_transitions = subparsers.add_parser(
        "transitions", help="List available status transitions"
    )
    p_transitions.add_argument("issue_key")
    p_transitions.set_defaults(func=cmd_transitions)

    p_generate = subparsers.add_parser(
        "generate", help="Ask Claude for an implementation plan"
    )
    p_generate.add_argument("issue_key")
    p_generate.add_argument("-c", "--context", help="Extra context for the model")
    p_generate.add_argument("-m", "--model", help="Claude model id")
    p_generate.set_defaults(func=cmd_generate)

    p_start = subparsers.add_parser(
        "start", help="Move a ticket to In Progress and check out its repo"
    )
    p_start.add_argument("issue_key")
    p_start.add_argument("-w", "--workspace", help="Directory to clone into")
    p_start.set_defaults(func=cmd_start)

    p_watch = subparsers.add_parser(
        "watch",
        help="Poll Jira and start work when assigned tickets move to In Progress",
    )
    p_watch.add_argument("-j", "--jql", help="JQL filter")
    p_watch.add_argument(
        "-i",
        "--interval",
        type=int,
        default=None,
        help="Seconds between polls (default: 30 or JIRA_WATCH_INTERVAL)",
    )
    p_watch.add_argument("-w", "--workspace", help="Directory to clone into")
    p_watch.add_argument(
        "--state-file",
        help="JSON file of already-handled ticket keys",
    )
    p_watch.add_argument(
        "--once",
        action="store_true",
        help="Poll once and exit (for cron)",
    )
    p_watch.set_defaults(func=cmd_watch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        client = JiraClient()
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    try:
        args.func(args, client)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 0
    except (JiraError, ClaudeError, GitError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
