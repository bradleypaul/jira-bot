"""Run the coding agent in a throwaway Docker container."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Only these host variables enter the container. Jira and GitHub credentials
# stay on the host.
PASSTHROUGH_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")


class SandboxError(RuntimeError):
    """The container could not be started or did not produce a result."""


@dataclass(frozen=True)
class AgentConfig:
    model: str = "claude-opus-5-5"
    effort: str = "high"
    max_turns: int = 100
    max_budget_usd: float = 10.0
    image: str = "jira-bot-agent:latest"
    timeout_minutes: int = 45
    memory: str = "4g"
    cpus: str = "2"

    @classmethod
    def from_env(cls) -> AgentConfig:
        defaults = cls()
        return cls(
            model=os.getenv("JIRA_BOT_MODEL") or defaults.model,
            effort=os.getenv("JIRA_BOT_EFFORT") or defaults.effort,
            max_turns=int(os.getenv("JIRA_BOT_MAX_TURNS") or defaults.max_turns),
            max_budget_usd=float(
                os.getenv("JIRA_BOT_MAX_BUDGET_USD") or defaults.max_budget_usd
            ),
            image=os.getenv("JIRA_BOT_IMAGE") or defaults.image,
            timeout_minutes=int(
                os.getenv("JIRA_BOT_TIMEOUT_MINUTES") or defaults.timeout_minutes
            ),
            memory=os.getenv("JIRA_BOT_MEMORY") or defaults.memory,
            cpus=os.getenv("JIRA_BOT_CPUS") or defaults.cpus,
        )


def docker_command(
    config: AgentConfig, repo_path: Path, run_dir: Path, name: str
) -> list[str]:
    command = [
        "docker", "run", "--rm",
        "--name", name,
        "--memory", config.memory,
        "--cpus", config.cpus,
        "--pids-limit", "1024",
        "--volume", f"{repo_path.resolve()}:/workspace",
        "--volume", f"{run_dir.resolve()}:/task",
        "--workdir", "/workspace",
        # Write files as the host user so the host can commit them.
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--env", "HOME=/tmp/home",
        # The mount's owner differs from the container user on some hosts;
        # let read-only git commands work anyway.
        "--env", "GIT_CONFIG_COUNT=1",
        "--env", "GIT_CONFIG_KEY_0=safe.directory",
        "--env", "GIT_CONFIG_VALUE_0=/workspace",
    ]
    # `--env NAME` copies the value from this process, so secrets never
    # appear in the command line.
    for var in PASSTHROUGH_ENV:
        if os.getenv(var):
            command += ["--env", var]
    command += [config.image, "python", "-m", "jira_bot.agent", "/task/task.json", "/task/result.json"]
    return command


def run_agent_container(
    config: AgentConfig,
    repo_path: Path,
    run_dir: Path,
    task: dict[str, Any],
) -> dict[str, Any]:
    """Run one agent session and return its result.json contents.

    The full transcript of tool calls goes to run_dir/agent.log.
    """
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("ANTHROPIC_BASE_URL"):
        raise SandboxError("ANTHROPIC_API_KEY must be set for the agent container")

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "task.json").write_text(json.dumps(task, indent=2) + "\n")
    result_path = run_dir / "result.json"
    result_path.unlink(missing_ok=True)

    name = f"jira-bot-{run_dir.name}".lower()
    command = docker_command(config, repo_path, run_dir, name)
    with open(run_dir / "agent.log", "w") as log:
        try:
            completed = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=config.timeout_minutes * 60,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SandboxError("docker is not installed or not on PATH") from exc
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True, check=False)
            return {
                "status": "error",
                "error": f"Agent timed out after {config.timeout_minutes} minutes",
            }

    if not result_path.exists():
        tail = _tail(run_dir / "agent.log")
        raise SandboxError(
            f"Agent container exited with code {completed.returncode} and no result. "
            f"Last output:\n{tail}"
        )
    return json.loads(result_path.read_text())


def _tail(path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log)"
