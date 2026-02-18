# jira-bot

A Python CLI tool for reading Jira ticket information via the Jira REST API.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env` with your Jira details:

| Variable         | Description                                                                 |
| ---------------- | --------------------------------------------------------------------------- |
| `JIRA_BASE_URL`  | Your Jira instance URL, e.g. `https://your-domain.atlassian.net`            |
| `JIRA_EMAIL`     | Email address of the Jira account                                           |
| `JIRA_API_TOKEN` | API token — generate one at https://id.atlassian.com/manage-profile/security/api-tokens |

## Usage

```bash
# Test your credentials
python -m jira_bot.cli whoami

# Get details of a ticket
python -m jira_bot.cli issue PROJ-123

# View comments on a ticket
python -m jira_bot.cli comments PROJ-123

# Search with JQL
python -m jira_bot.cli search "project = PROJ AND status = 'In Progress'"

# List all projects
python -m jira_bot.cli projects
```

Add `--json` to any command to get raw JSON output:

```bash
python -m jira_bot.cli --json issue PROJ-123
```

## Using as a library

```python
from jira_bot.client import JiraClient

client = JiraClient()

# Get a formatted summary of an issue
summary = client.get_issue_summary("PROJ-123")
print(summary)

# Search with JQL
results = client.search_issues("assignee = currentUser() ORDER BY updated DESC")

# Get comments
comments = client.get_issue_comments("PROJ-123")

# List available transitions
transitions = client.get_issue_transitions("PROJ-123")
```
