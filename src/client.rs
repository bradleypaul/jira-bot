use anyhow::{Context, Result};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Configuration for connecting to a Jira instance.
pub struct JiraConfig {
    pub base_url: String,
    pub email: String,
    pub api_token: String,
}

/// Client for interacting with the Jira REST API v3.
pub struct JiraClient {
    client: Client,
    base_url: String,
    email: String,
    api_token: String,
}

#[derive(Debug, Serialize)]
pub struct IssueSummary {
    pub key: String,
    pub summary: String,
    pub status: String,
    pub priority: String,
    pub issue_type: String,
    pub assignee: Option<String>,
    pub reporter: Option<String>,
    pub created: String,
    pub updated: String,
    pub description: String,
    pub labels: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct Comment {
    pub author: String,
    pub created: String,
    pub body: String,
}

#[derive(Debug, Serialize)]
pub struct Transition {
    pub id: String,
    pub name: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct Project {
    pub key: String,
    pub name: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct UserInfo {
    #[serde(rename = "displayName")]
    pub display_name: String,
    #[serde(rename = "emailAddress")]
    pub email_address: Option<String>,
}

impl JiraClient {
    pub fn new(config: JiraConfig) -> Result<Self> {
        let client = Client::new();
        Ok(Self {
            client,
            base_url: config.base_url.trim_end_matches('/').to_string(),
            email: config.email,
            api_token: config.api_token,
        })
    }

    /// Build a full API URL from a relative path.
    fn url(&self, path: &str) -> String {
        format!("{}/rest/api/3/{}", self.base_url, path.trim_start_matches('/'))
    }

    /// Perform an authenticated GET request and return the JSON body.
    fn get(&self, path: &str, query: &[(&str, &str)]) -> Result<Value> {
        let resp = self
            .client
            .get(self.url(path))
            .basic_auth(&self.email, Some(&self.api_token))
            .header("Accept", "application/json")
            .query(query)
            .send()
            .context("Failed to connect to Jira")?;

        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().unwrap_or_default();
            anyhow::bail!("Jira API returned {status}: {body}");
        }

        resp.json::<Value>().context("Failed to parse Jira response")
    }

    /// Perform an authenticated POST request with a JSON body.
    fn post(&self, path: &str, body: &Value) -> Result<()> {
        let resp = self
            .client
            .post(self.url(path))
            .basic_auth(&self.email, Some(&self.api_token))
            .header("Accept", "application/json")
            .header("Content-Type", "application/json")
            .json(body)
            .send()
            .context("Failed to connect to Jira")?;

        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().unwrap_or_default();
            anyhow::bail!("Jira API returned {status}: {body}");
        }

        Ok(())
    }

    // ── Read operations ──────────────────────────────────────────────

    /// Fetch the raw JSON for a single issue.
    pub fn get_issue(&self, issue_key: &str) -> Result<Value> {
        self.get(&format!("issue/{issue_key}"), &[])
    }

    /// Fetch an issue and return a concise summary.
    pub fn get_issue_summary(&self, issue_key: &str) -> Result<IssueSummary> {
        let data = self.get_issue(issue_key)?;
        let fields = &data["fields"];

        Ok(IssueSummary {
            key: str_field(&data, "key"),
            summary: str_field(fields, "summary"),
            status: str_field(&fields["status"], "name"),
            priority: str_field(&fields["priority"], "name"),
            issue_type: str_field(&fields["issuetype"], "name"),
            assignee: optional_str_field(&fields["assignee"], "displayName"),
            reporter: optional_str_field(&fields["reporter"], "displayName"),
            created: str_field(fields, "created"),
            updated: str_field(fields, "updated"),
            description: extract_text(&fields["description"]),
            labels: fields["labels"]
                .as_array()
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default(),
        })
    }

    /// Run a JQL search query.
    pub fn search_issues(&self, jql: &str, max_results: u32) -> Result<Value> {
        let max_str = max_results.to_string();
        self.get("search", &[("jql", jql), ("maxResults", &max_str)])
    }

    /// Fetch all comments on an issue.
    pub fn get_issue_comments(&self, issue_key: &str) -> Result<Vec<Comment>> {
        let data = self.get(&format!("issue/{issue_key}/comment"), &[])?;
        let comments = data["comments"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .map(|c| Comment {
                        author: optional_str_field(&c["author"], "displayName")
                            .unwrap_or_else(|| "Unknown".into()),
                        created: str_field(c, "created"),
                        body: extract_text(&c["body"]),
                    })
                    .collect()
            })
            .unwrap_or_default();
        Ok(comments)
    }

    /// Fetch available status transitions for an issue.
    pub fn get_issue_transitions(&self, issue_key: &str) -> Result<Vec<Transition>> {
        let data = self.get(&format!("issue/{issue_key}/transitions"), &[])?;
        let transitions = data["transitions"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .map(|t| Transition {
                        id: str_field(t, "id"),
                        name: str_field(t, "name"),
                    })
                    .collect()
            })
            .unwrap_or_default();
        Ok(transitions)
    }

    /// List all projects visible to the authenticated user.
    pub fn get_projects(&self) -> Result<Vec<Project>> {
        let data = self.get("project", &[])?;
        let projects: Vec<Project> = serde_json::from_value(data)?;
        Ok(projects)
    }

    /// Transition an issue to a new status by transition ID.
    pub fn transition_issue(&self, issue_key: &str, transition_id: &str) -> Result<()> {
        let body = serde_json::json!({
            "transition": { "id": transition_id }
        });
        self.post(&format!("issue/{issue_key}/transitions"), &body)
    }

    /// Find the "In Progress" transition for an issue and execute it.
    /// Returns Ok(true) if transitioned, Ok(false) if already in progress or no transition found.
    pub fn transition_to_in_progress(&self, issue_key: &str) -> Result<bool> {
        let summary = self.get_issue_summary(issue_key)?;
        if summary.status.eq_ignore_ascii_case("In Progress") {
            return Ok(false);
        }

        let transitions = self.get_issue_transitions(issue_key)?;
        let in_progress = transitions.iter().find(|t| {
            let name = t.name.to_lowercase();
            name.contains("in progress") || name.contains("start")
        });

        match in_progress {
            Some(t) => {
                self.transition_issue(issue_key, &t.id)?;
                Ok(true)
            }
            None => {
                let names: Vec<_> = transitions.iter().map(|t| t.name.as_str()).collect();
                anyhow::bail!(
                    "No 'In Progress' transition found for {issue_key}. \
                     Available transitions: {}",
                    names.join(", ")
                );
            }
        }
    }

    /// Return the authenticated user's profile (useful for testing auth).
    pub fn get_myself(&self) -> Result<UserInfo> {
        let data = self.get("myself", &[])?;
        let user: UserInfo = serde_json::from_value(data)?;
        Ok(user)
    }
}

/// Extract a repository identifier from labels matching the `repo:owner/name` pattern.
/// Returns the first match, e.g. `"bradleypaul/my-app"`.
pub fn repo_from_labels(labels: &[String]) -> Option<String> {
    labels
        .iter()
        .find(|l| l.starts_with("repo:"))
        .map(|l| l.trim_start_matches("repo:").to_string())
}

// ── Helpers ──────────────────────────────────────────────────────────

fn str_field(value: &Value, key: &str) -> String {
    value[key].as_str().unwrap_or("").to_string()
}

fn optional_str_field(value: &Value, key: &str) -> Option<String> {
    if value.is_null() {
        return None;
    }
    value[key].as_str().map(String::from)
}

/// Recursively extract plain text from an Atlassian Document Format node.
fn extract_text(node: &Value) -> String {
    match node {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Object(map) => {
            if map.get("type").and_then(|v| v.as_str()) == Some("text") {
                return map
                    .get("text")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
            }
            if let Some(content) = map.get("content").and_then(|v| v.as_array()) {
                return content.iter().map(extract_text).collect::<String>();
            }
            String::new()
        }
        Value::Array(arr) => arr.iter().map(extract_text).collect::<String>(),
        _ => String::new(),
    }
}
