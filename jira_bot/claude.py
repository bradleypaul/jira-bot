"""Claude Messages API helper for implementation plans."""

from __future__ import annotations

import os
from typing import Any

import anthropic
from dotenv import load_dotenv

from jira_bot.ticket import format_ticket

load_dotenv()

DEFAULT_MODEL = "claude-opus-5-5"
SYSTEM_PROMPT = (
    "You are a senior software engineer. Given a Jira ticket, analyze the "
    "requirements and produce a clear, actionable implementation plan with "
    "code changes. Include file paths, function signatures, and concrete "
    "code snippets where possible. If the ticket is a bug, explain the "
    "likely root cause and the fix. If it is a feature, break it down into steps."
)


class ClaudeError(RuntimeError):
    """Failure talking to the Anthropic API."""


class ClaudeClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        # With no key, the SDK resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
        # or an `ant auth login` profile.
        self.client = client or anthropic.Anthropic(api_key=api_key)
        self.model = model or os.getenv("JIRA_BOT_MODEL") or DEFAULT_MODEL

    def generate_from_ticket(
        self,
        issue: dict[str, Any],
        comments: list[dict[str, Any]],
        extra_context: str | None = None,
    ) -> dict[str, str]:
        prompt = _build_prompt(issue, comments, extra_context)
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=32000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "high"},
                # If a safety classifier declines, retry server-side on the
                # model Anthropic recommends for that refusal category.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                message = stream.get_final_message()
        except anthropic.APIStatusError as exc:
            raise ClaudeError(f"Claude API {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ClaudeError(f"Could not reach the Claude API: {exc}") from exc
        except TypeError as exc:
            # The SDK raises TypeError when it finds no credentials at all.
            raise ClaudeError(
                f"No Anthropic credentials. Set ANTHROPIC_API_KEY. ({exc})"
            ) from exc

        if message.stop_reason == "refusal":
            raise ClaudeError(f"Claude declined to plan {issue.get('key')}")
        response = "\n".join(
            block.text for block in message.content if block.type == "text"
        )
        return {"issue_key": str(issue.get("key") or ""), "response": response}


def _build_prompt(
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    extra_context: str | None,
) -> str:
    lines = [format_ticket(issue, comments)]
    if extra_context:
        lines.extend(["", f"**Additional context:**\n{extra_context}"])
    lines.append(
        "\nPlease analyze this ticket and provide the implementation plan "
        "with code changes."
    )
    return "\n".join(lines)
