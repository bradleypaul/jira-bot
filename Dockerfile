# Image for the coding agent that jira-bot runs once per ticket.
#
#   docker build -t jira-bot-agent .
#
# The host mounts the ticket's repo at /workspace and the run directory at
# /task, then runs `python -m jira_bot.agent /task/task.json /task/result.json`
# as the host user. Only ANTHROPIC_API_KEY enters the container; Jira and
# GitHub credentials stay on the host.
#
# node:22-bookworm already has git, curl, build tools, and Python 3, so the
# agent can install dependencies and run tests for Node and Python projects.
FROM node:22-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-venv ripgrep \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE /src/
COPY jira_bot /src/jira_bot
RUN python3 -m venv /opt/jira-bot \
 && /opt/jira-bot/bin/pip install --no-cache-dir "/src[agent]" \
 && rm -rf /src

# `python` resolves to the venv. HOME is set per run; create it writable for
# whatever uid the host passes with --user.
ENV PATH=/opt/jira-bot/bin:$PATH
RUN mkdir -m 1777 /tmp/home

WORKDIR /workspace
