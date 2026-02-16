# jira-bot

A Rust CLI tool for reading Jira ticket information via the Jira REST API.

## Setup

### 1. Build

```bash
cargo build --release
```

The binary will be at `target/release/jira-bot`.

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
jira-bot whoami

# Get details of a ticket
jira-bot issue PROJ-123

# View comments on a ticket
jira-bot comments PROJ-123

# Search with JQL
jira-bot search "project = PROJ AND status = 'In Progress'"

# List all projects
jira-bot projects
```

Add `--json` to any command to get raw JSON output:

```bash
jira-bot --json issue PROJ-123
```

Or run directly with cargo:

```bash
cargo run -- issue PROJ-123
cargo run -- --json search "assignee = currentUser()"
```

## Using as a library

```rust
use jira_bot::client::{JiraClient, JiraConfig};

let client = JiraClient::new(JiraConfig {
    base_url: "https://your-domain.atlassian.net".into(),
    email: "you@example.com".into(),
    api_token: "your-token".into(),
})?;

// Get a formatted summary of an issue
let summary = client.get_issue_summary("PROJ-123")?;
println!("{:?}", summary);

// Search with JQL
let results = client.search_issues("assignee = currentUser()", 20)?;

// Get comments
let comments = client.get_issue_comments("PROJ-123")?;

// List available transitions
let transitions = client.get_issue_transitions("PROJ-123")?;
```
