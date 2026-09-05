from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from jira_bot.cli import build_parser, cmd_generate, cmd_start, main


def test_parser_requires_command() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_exposes_work_commands() -> None:
    parser = build_parser()
    generate = parser.parse_args(["generate", "PROJ-1", "-c", "Python"])
    assert generate.issue_key == "PROJ-1"
    assert generate.context == "Python"

    start = parser.parse_args(["start", "PROJ-2", "-w", "/tmp/work"])
    assert start.workspace == "/tmp/work"

    watch = parser.parse_args(["watch", "-i", "45", "--once", "--state-file", "/tmp/seen.json"])
    assert watch.interval == 45
    assert watch.once is True
    assert watch.state_file == "/tmp/seen.json"


def test_main_reports_missing_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    assert main(["whoami"]) == 1
    assert "Configuration error" in capsys.readouterr().err


def test_cmd_start_uses_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    called: dict[str, object] = {}

    def fake_start(jira_client, issue_key, workspace=None, log=None):
        called["key"] = issue_key
        called["client"] = jira_client
        if log:
            log("ok")
        return {"repo": "acme/app"}

    monkeypatch.setattr("jira_bot.cli.start_ticket", fake_start)
    cmd_start(Namespace(issue_key="PROJ-1", workspace=None), client)
    assert called["key"] == "PROJ-1"
    assert called["client"] is client


def test_cmd_generate_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = MagicMock()
    monkeypatch.setattr(
        "jira_bot.cli.generate_plan",
        lambda *args, **kwargs: {
            "issue_key": "PROJ-1",
            "response": "Do the work",
        },
    )
    cmd_generate(
        Namespace(issue_key="PROJ-1", context=None, model=None, json=True),
        client,
    )
    out = capsys.readouterr().out
    assert "PROJ-1" in out
    assert "Do the work" in out
