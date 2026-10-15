"""Coding agent that runs inside the sandbox container.

The host mounts the ticket's repo at /workspace, writes /task/task.json, and
runs `python -m jira_bot.agent /task/task.json /task/result.json`. This
process never sees Jira or GitHub credentials: it edits files and runs tests,
and the host commits, pushes, and reports back.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from jira_bot import __version__
from jira_bot.ticket import format_ticket

TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "TodoWrite"]

# The host owns commits, branches, and pushes, so the agent only gets git
# subcommands that read. This keeps the branch in the state the host expects;
# the container, which has no GitHub credentials, is the security boundary.
READ_ONLY_GIT = {
    "blame",
    "describe",
    "diff",
    "grep",
    "log",
    "ls-files",
    "rev-parse",
    "shortlog",
    "show",
    "status",
}
GIT_SUBCOMMAND = re.compile(
    r"(?:^|[\s;&|(`'\"])(?:[\w./-]*/)?git(?:\s+-[Cc]\s+\S+|\s+--[\w-]+(?:=\S+)?)*\s+([a-z][\w-]*)"
)

INSTRUCTIONS = """\
You are running unattended inside a container to implement a Jira ticket. The \
repository is checked out in the working directory on a branch created for \
this ticket. Nobody can answer questions while you work.

- Learn the project's conventions from CLAUDE.md, the README, and nearby code \
before changing anything.
- Make the smallest change that fully addresses the ticket, and add or update \
tests for it.
- Run the project's tests, and its linters if it has them, and fix failures \
your change causes. Install dependencies if you need to.
- Leave your changes uncommitted in the working tree. The harness commits, \
pushes, and opens a draft pull request for human review, so don't run git \
commands that commit, switch branches, or push.
- If the ticket is too ambiguous to implement safely, or needs access you \
don't have, make no changes and report the outcome as "blocked", saying what \
a human needs to clarify.
"""

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": ["implemented", "blocked"]},
        "title": {
            "type": "string",
            "description": "Imperative pull request title under 70 characters, without the ticket key.",
        },
        "summary": {
            "type": "string",
            "description": "What changed and why, for reviewers. Markdown. If blocked, what a human needs to clarify.",
        },
        "tests": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "status": {"type": "string", "enum": ["passed", "failed", "not_run"]},
                "details": {"type": "string"},
            },
            "required": ["command", "status", "details"],
            "additionalProperties": False,
        },
    },
    "required": ["outcome", "title", "summary", "tests"],
    "additionalProperties": False,
}


async def guard_bash(
    input_data: dict[str, Any], tool_use_id: str | None, context: Any
) -> dict[str, Any]:
    """PreToolUse hook: deny git subcommands that change history or remotes."""
    command = (input_data.get("tool_input") or {}).get("command", "")
    blocked = sorted(
        {sub for sub in GIT_SUBCOMMAND.findall(command) if sub not in READ_ONLY_GIT}
    )
    if not blocked:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"git {', '.join(blocked)} is not allowed here. The harness commits "
                "and pushes after you finish; leave changes in the working tree."
            ),
        }
    }


def build_options(task: dict[str, Any], cwd: str) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=cwd,
        model=task["model"],
        effort=task["effort"],
        max_turns=task["max_turns"],
        max_budget_usd=task["max_budget_usd"],
        system_prompt={"type": "preset", "preset": "claude_code", "append": INSTRUCTIONS},
        allowed_tools=TOOLS,
        # Ticket text and repo files are the only inputs; keep web pages out
        # of a session that can run shell commands.
        disallowed_tools=["WebFetch", "WebSearch"],
        permission_mode="dontAsk",
        # Load the repo's CLAUDE.md and .claude/settings.json, nothing else.
        setting_sources=["project"],
        hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[guard_bash])]},
        output_format={"type": "json_schema", "schema": RESULT_SCHEMA},
        env={
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_AGENT_SDK_CLIENT_APP": f"jira-bot/{__version__}",
        },
    )


def build_prompt(task: dict[str, Any]) -> str:
    return (
        "Implement this Jira ticket.\n\n"
        f"{format_ticket(task['issue'], task.get('comments') or [])}\n"
    )


def to_result(message: ResultMessage | None, error: str | None = None) -> dict[str, Any]:
    """Flatten the SDK's final message into the JSON the host reads."""
    if message is None:
        return {"status": "error", "error": error or "Agent exited without a result"}

    result: dict[str, Any] = {
        "cost_usd": message.total_cost_usd,
        "num_turns": message.num_turns,
        "duration_ms": message.duration_ms,
        "session_id": message.session_id,
    }
    output = message.structured_output
    if message.is_error or message.subtype != "success" or not isinstance(output, dict):
        details = "; ".join(message.errors or []) or error or message.subtype
        return {**result, "status": "error", "error": details}

    return {
        **result,
        "status": output["outcome"],
        "title": output["title"],
        "summary": output["summary"],
        "tests": output["tests"],
    }


async def run_agent(task: dict[str, Any], cwd: str) -> dict[str, Any]:
    final: ResultMessage | None = None
    try:
        async for message in query(prompt=build_prompt(task), options=build_options(task, cwd)):
            if isinstance(message, AssistantMessage):
                _log_assistant(message)
            elif isinstance(message, ResultMessage):
                final = message
    except ClaudeSDKError as exc:
        # query() raises after yielding an error result (e.g. max turns),
        # so keep the result message if one arrived.
        return to_result(final, error=str(exc))
    return to_result(final)


def _log_assistant(message: AssistantMessage) -> None:
    for block in message.content:
        if isinstance(block, TextBlock) and block.text.strip():
            print(block.text.strip(), flush=True)
        elif isinstance(block, ToolUseBlock):
            target = block.input.get("command") or block.input.get("file_path") or ""
            print(f"→ {block.name} {target}".rstrip(), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print("usage: python -m jira_bot.agent TASK_JSON RESULT_JSON", file=sys.stderr)
        return 2
    task_path, result_path = Path(args[0]), Path(args[1])
    task = json.loads(task_path.read_text())
    try:
        result = asyncio.run(run_agent(task, os.getcwd()))
    except Exception as exc:  # report anything to the host instead of crashing silently
        result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
