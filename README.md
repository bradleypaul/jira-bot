# jira-bot

Python client for the **Jira Cloud REST API v3**.

It does **not** use the Atlassian CLI (`acli`) or the Jira CLI marketplace app. Those are often turned off in site admin settings. Every Jira read and transition is a direct HTTPS call to `/rest/api/3` with an email + API token.

The `jira-bot` command is an optional wrapper around the same functions. Prefer importing the library from a script, notebook, or job if installing a CLI binary is blocked.

`generate` also calls the Anthropic Messages API.

## Setup

### 1. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Or install the runtime dependencies only:

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

| Variable            | Description                                                                 |
| ------------------- | --------------------------------------------------------------------------- |
| `JIRA_BASE_URL`     | Your Jira Cloud URL, e.g. `https://your-domain.atlassian.net`               |
| `JIRA_EMAIL`        | Email address of the Jira account                                           |
| `JIRA_API_TOKEN`    | API token from https://id.atlassian.com/manage-profile/security/api-tokens  |
| `ANTHROPIC_API_KEY` | Claude API key — required only for `generate_plan`                          |

Create the token in your Atlassian account. No Jira marketplace app or admin-approved CLI plugin is required.

## Run on a Linux machine

Leave `watch` running on a box that can reach Jira. When you move a ticket assigned to you to **In Progress**, the service polls `POST /rest/api/3/search/jql`, clones the repo from the `repo:owner/name` label, and checks out a branch named after the ticket.

Default JQL:

```
assignee = currentUser() AND status = 'In Progress'
```

The first start records tickets that are already In Progress and leaves them alone. After that, new keys are started even if the machine was down when you flipped the status. Handled keys are stored in `~/.local/state/jira-bot/seen.json`.

### systemd (stays up across reboot)

```bash
# On the Linux host
git clone https://github.com/bradleypaul/jira-bot.git ~/jira-bot
cd ~/jira-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

mkdir -p ~/.config/jira-bot ~/jira-repos
cp deploy/env.example ~/.config/jira-bot/env
# edit ~/.config/jira-bot/env — no quotes around values
# set JIRA_WORKSPACE=/home/YOU/jira-repos

mkdir -p ~/.config/systemd/user
cp deploy/jira-bot.service ~/.config/systemd/user/jira-bot.service
# edit ExecStart if the clone is not at ~/jira-bot

# Keep the user service running after logout
sudo loginctl enable-linger "$USER"

systemctl --user daemon-reload
systemctl --user enable --now jira-bot
systemctl --user status jira-bot
journalctl --user -u jira-bot -f
```

Confirm Jira auth before enabling the service:

```bash
~/jira-bot/.venv/bin/jira-bot whoami
```

GitHub clones use HTTPS. For private repos, either configure a credential helper for that user or set `JIRA_GIT_SSH=1` in the env file and install an SSH deploy key.

To start an already-In-Progress ticket by hand:

```bash
jira-bot start PROJ-123
```

### cron (one poll per minute)

```
* * * * * /home/YOU/jira-bot/.venv/bin/jira-bot watch --once
```

Load the same env vars in the crontab (`EnvironmentFile` is systemd-only), or keep a `.env` in the project directory.

## Library (recommended)

```python
from jira_bot import JiraClient, generate_plan, repo_from_labels, start_ticket

client = JiraClient()  # reads JIRA_* from the environment or .env

me = client.get_myself()
summary = client.get_issue_summary("PROJ-123")
comments = client.get_issue_comments("PROJ-123")
results = client.search_issues("assignee = currentUser() ORDER BY updated DESC")
transitions = client.get_issue_transitions("PROJ-123")

# Move the ticket to In Progress over the API, then clone the linked repo
start_ticket(client, "PROJ-123")

# Implementation plan (needs ANTHROPIC_API_KEY)
plan = generate_plan(client, "PROJ-123", extra_context="Python backend")

repo = repo_from_labels(summary["labels"])
```

Jira endpoints used:

| Action              | Request                                      |
| ------------------- | -------------------------------------------- |
| Current user        | `GET /rest/api/3/myself`                     |
| Issue               | `GET /rest/api/3/issue/{key}`                |
| Comments            | `GET /rest/api/3/issue/{key}/comment`        |
| JQL search          | `POST /rest/api/3/search/jql`                |
| Projects            | `GET /rest/api/3/project`                    |
| Transitions         | `GET /rest/api/3/issue/{key}/transitions`    |
| Apply transition    | `POST /rest/api/3/issue/{key}/transitions`   |

Search uses the current `search/jql` POST API. The older `GET /search` route is deprecated.

### Linking a repo to a ticket

Add a Jira label `repo:owner/repo-name`, for example `repo:bradleypaul/jira-bot`.

`start_ticket(client, "PROJ-123")` then:

1. Reads the `repo:` label from the ticket
2. Transitions the ticket to In Progress (matches a transition name containing "in progress" or "start")
3. Clones the GitHub repo — or pulls if it already exists — into `~/jira-repos/`
4. Creates or checks out a branch named after the ticket key, lowercased (`proj-123`)

## Optional CLI

If you can install a console script:

```bash
jira-bot whoami
jira-bot issue PROJ-123
jira-bot comments PROJ-123
jira-bot search "project = PROJ AND status = 'In Progress'"
jira-bot projects
jira-bot transitions PROJ-123
jira-bot generate PROJ-123 --context "Python backend, PostgreSQL"
jira-bot start PROJ-123
jira-bot start PROJ-123 --workspace ~/projects
jira-bot watch
jira-bot watch --interval 60 --once
jira-bot watch --jql "project = PROJ AND assignee = currentUser() AND status = 'In Progress'"
```

Or run the module without the `jira-bot` entry point:

```bash
python -m jira_bot issue PROJ-123
python -m jira_bot --json issue PROJ-123
```

`watch` is the long-running service. It polls `POST /rest/api/3/search/jql` and runs `start_ticket` for assigned In Progress keys that are not in the state file.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
