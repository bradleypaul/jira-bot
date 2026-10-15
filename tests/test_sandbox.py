from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jira_bot.sandbox import AgentConfig, SandboxError, docker_command, run_agent_container


def test_docker_command_passes_key_by_name_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-secret")
    monkeypatch.setenv("JIRA_API_TOKEN", "jira-secret")

    command = docker_command(AgentConfig(memory="2g", cpus="1"), tmp_path, tmp_path / "run", "jb-1")
    joined = " ".join(command)

    assert "sk-secret" not in joined
    assert "ghp-secret" not in joined and "GITHUB_TOKEN" not in joined
    assert "jira-secret" not in joined and "JIRA_API_TOKEN" not in joined
    key = command.index("ANTHROPIC_API_KEY")
    assert command[key - 1] == "--env"
    assert command[command.index("--memory") + 1] == "2g"
    assert command[-6:] == [
        "jira-bot-agent:latest", "python", "-m", "jira_bot.agent", "/task/task.json", "/task/result.json",
    ]


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_BOT_MAX_BUDGET_USD", "2.5")
    monkeypatch.setenv("JIRA_BOT_MODEL", "claude-sonnet-5-5")
    config = AgentConfig.from_env()
    assert config.max_budget_usd == 2.5
    assert config.model == "claude-sonnet-5-5"
    assert config.max_turns == AgentConfig().max_turns


def test_requires_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    with pytest.raises(SandboxError, match="ANTHROPIC_API_KEY"):
        run_agent_container(AgentConfig(), tmp_path, tmp_path / "run", {})


def test_returns_result_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    run_dir = tmp_path / "run"

    def fake_run(command, **kwargs):
        assert json.loads((run_dir / "task.json").read_text()) == {"issue": {"key": "PROJ-1"}}
        (run_dir / "result.json").write_text('{"status": "blocked"}')
        return MagicMock(returncode=0)

    monkeypatch.setattr("jira_bot.sandbox.subprocess.run", fake_run)
    result = run_agent_container(AgentConfig(), tmp_path, run_dir, {"issue": {"key": "PROJ-1"}})
    assert result == {"status": "blocked"}


def test_missing_result_raises_with_log_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def fake_run(command, stdout, **kwargs):
        stdout.write("Traceback: ImportError\n")
        return MagicMock(returncode=1)

    monkeypatch.setattr("jira_bot.sandbox.subprocess.run", fake_run)
    with pytest.raises(SandboxError, match="ImportError"):
        run_agent_container(AgentConfig(), tmp_path, tmp_path / "run", {})


def test_timeout_kills_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(command, 1)
        return MagicMock(returncode=0)

    monkeypatch.setattr("jira_bot.sandbox.subprocess.run", fake_run)
    result = run_agent_container(AgentConfig(timeout_minutes=1), tmp_path, tmp_path / "run", {})
    assert result["status"] == "error"
    assert "timed out" in result["error"]
    assert calls[-1][:2] == ["docker", "kill"]
