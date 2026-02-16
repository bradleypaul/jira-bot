"""CLI entrypoint for jira-bot."""

import argparse
import json
import sys

from jira_bot.client import JiraClient


def _print_issue(summary):
    """Pretty-print an issue summary dict."""
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


def cmd_issue(args, client):
    summary = client.get_issue_summary(args.issue_key)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_issue(summary)


def cmd_comments(args, client):
    comments = client.get_issue_comments(args.issue_key)
    if args.json:
        print(json.dumps(comments, indent=2))
        return
    if not comments:
        print(f"No comments on {args.issue_key}")
        return
    for c in comments:
        print(f"\n--- {c['author']}  ({c['created']}) ---")
        print(c["body"])


def cmd_search(args, client):
    results = client.search_issues(args.jql, max_results=args.max_results)
    issues = results.get("issues", [])
    if args.json:
        print(json.dumps(results, indent=2))
        return
    print(f"\n{results.get('total', 0)} result(s) (showing {len(issues)})\n")
    for issue in issues:
        fields = issue.get("fields", {})
        status = (fields.get("status") or {}).get("name", "")
        print(f"  {issue['key']:12s}  [{status:12s}]  {fields.get('summary', '')}")
    print()


def cmd_projects(args, client):
    projects = client.get_projects()
    if args.json:
        print(json.dumps(projects, indent=2))
        return
    for p in projects:
        print(f"  {p['key']:10s}  {p.get('name', '')}")


def cmd_whoami(args, client):
    me = client.get_myself()
    if args.json:
        print(json.dumps(me, indent=2))
    else:
        print(f"  Logged in as: {me.get('displayName')} ({me.get('emailAddress')})")


def main():
    parser = argparse.ArgumentParser(
        prog="jira-bot",
        description="Read Jira ticket information from the command line.",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # issue
    p_issue = subparsers.add_parser("issue", help="Get details of a single issue")
    p_issue.add_argument("issue_key", help="Issue key, e.g. PROJ-123")
    p_issue.set_defaults(func=cmd_issue)

    # comments
    p_comments = subparsers.add_parser("comments", help="List comments on an issue")
    p_comments.add_argument("issue_key", help="Issue key, e.g. PROJ-123")
    p_comments.set_defaults(func=cmd_comments)

    # search
    p_search = subparsers.add_parser("search", help="Search issues with JQL")
    p_search.add_argument("jql", help='JQL query, e.g. "project = PROJ AND status = Open"')
    p_search.add_argument("-n", "--max-results", type=int, default=20, help="Max results")
    p_search.set_defaults(func=cmd_search)

    # projects
    p_projects = subparsers.add_parser("projects", help="List visible projects")
    p_projects.set_defaults(func=cmd_projects)

    # whoami
    p_whoami = subparsers.add_parser("whoami", help="Show authenticated user info")
    p_whoami.set_defaults(func=cmd_whoami)

    args = parser.parse_args()

    try:
        client = JiraClient()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        args.func(args, client)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
