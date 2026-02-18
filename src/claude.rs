use anyhow::{Context, Result};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::client::IssueSummary;

const API_URL: &str = "https://api.anthropic.com/v1/messages";
const DEFAULT_MODEL: &str = "claude-sonnet-4-5-20250929";

pub struct ClaudeClient {
    client: Client,
    api_key: String,
    model: String,
}

#[derive(Debug, Deserialize)]
struct MessagesResponse {
    content: Vec<ContentBlock>,
}

#[derive(Debug, Deserialize)]
struct ContentBlock {
    text: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct GenerateResult {
    pub issue_key: String,
    pub response: String,
}

impl ClaudeClient {
    pub fn new(api_key: String, model: Option<String>) -> Self {
        Self {
            client: Client::new(),
            api_key,
            model: model.unwrap_or_else(|| DEFAULT_MODEL.to_string()),
        }
    }

    /// Send a prompt to Claude and return the text response.
    fn send_message(&self, system: &str, user_message: &str) -> Result<String> {
        let body = json!({
            "model": self.model,
            "max_tokens": 4096,
            "system": system,
            "messages": [
                { "role": "user", "content": user_message }
            ]
        });

        let resp = self
            .client
            .post(API_URL)
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .header("content-type", "application/json")
            .json(&body)
            .send()
            .context("Failed to connect to Claude API")?;

        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().unwrap_or_default();
            anyhow::bail!("Claude API returned {status}: {body}");
        }

        let parsed: MessagesResponse =
            resp.json().context("Failed to parse Claude API response")?;

        let text = parsed
            .content
            .into_iter()
            .filter_map(|block| block.text)
            .collect::<Vec<_>>()
            .join("\n");

        Ok(text)
    }

    /// Given a Jira issue summary (and optional comments), ask Claude to
    /// generate the code changes needed to implement / fix the ticket.
    pub fn generate_from_ticket(
        &self,
        issue: &IssueSummary,
        comments: &[crate::client::Comment],
        extra_context: Option<&str>,
    ) -> Result<GenerateResult> {
        let system = "You are a senior software engineer. Given a Jira ticket, analyze the \
            requirements and produce a clear, actionable implementation plan with code changes. \
            Include file paths, function signatures, and concrete code snippets where possible. \
            If the ticket is a bug, explain the likely root cause and the fix. \
            If it's a feature, break it down into steps.";

        let mut prompt = format!(
            "## Jira Ticket: {key}\n\
             **Summary:** {summary}\n\
             **Type:** {issue_type}\n\
             **Status:** {status}\n\
             **Priority:** {priority}\n",
            key = issue.key,
            summary = issue.summary,
            issue_type = issue.issue_type,
            status = issue.status,
            priority = issue.priority,
        );

        if !issue.description.is_empty() {
            prompt.push_str(&format!("\n**Description:**\n{}\n", issue.description));
        }

        if !issue.labels.is_empty() {
            prompt.push_str(&format!("**Labels:** {}\n", issue.labels.join(", ")));
        }

        if !comments.is_empty() {
            prompt.push_str("\n**Comments:**\n");
            for c in comments {
                prompt.push_str(&format!("- {} ({}): {}\n", c.author, c.created, c.body));
            }
        }

        if let Some(ctx) = extra_context {
            prompt.push_str(&format!("\n**Additional context:**\n{ctx}\n"));
        }

        prompt.push_str(
            "\nPlease analyze this ticket and provide the implementation plan with code changes.",
        );

        let response = self.send_message(system, &prompt)?;

        Ok(GenerateResult {
            issue_key: issue.key.clone(),
            response,
        })
    }
}
