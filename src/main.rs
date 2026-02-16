mod client;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};

use client::{JiraClient, JiraConfig};

#[derive(Parser)]
#[command(name = "jira-bot", about = "Read Jira ticket information from the command line")]
struct Cli {
    /// Output raw JSON instead of formatted text
    #[arg(long, global = true)]
    json: bool,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Get details of a single issue
    Issue {
        /// Issue key, e.g. PROJ-123
        issue_key: String,
    },
    /// List comments on an issue
    Comments {
        /// Issue key, e.g. PROJ-123
        issue_key: String,
    },
    /// Search issues with JQL
    Search {
        /// JQL query, e.g. "project = PROJ AND status = Open"
        jql: String,
        /// Maximum number of results
        #[arg(short = 'n', long, default_value_t = 20)]
        max_results: u32,
    },
    /// List visible projects
    Projects,
    /// Show authenticated user info
    Whoami,
}

fn build_client() -> Result<JiraClient> {
    dotenvy::dotenv().ok();

    let base_url =
        std::env::var("JIRA_BASE_URL").context("JIRA_BASE_URL not set in environment or .env")?;
    let email =
        std::env::var("JIRA_EMAIL").context("JIRA_EMAIL not set in environment or .env")?;
    let api_token =
        std::env::var("JIRA_API_TOKEN").context("JIRA_API_TOKEN not set in environment or .env")?;

    JiraClient::new(JiraConfig {
        base_url,
        email,
        api_token,
    })
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let client = build_client()?;

    match cli.command {
        Commands::Issue { issue_key } => {
            let summary = client.get_issue_summary(&issue_key)?;
            if cli.json {
                println!("{}", serde_json::to_string_pretty(&summary)?);
            } else {
                println!();
                println!("{}", "=".repeat(60));
                println!("  {}  --  {}", summary.key, summary.summary);
                println!("{}", "=".repeat(60));
                println!("  Type:     {}", summary.issue_type);
                println!("  Status:   {}", summary.status);
                println!("  Priority: {}", summary.priority);
                println!(
                    "  Assignee: {}",
                    summary.assignee.as_deref().unwrap_or("Unassigned")
                );
                println!(
                    "  Reporter: {}",
                    summary.reporter.as_deref().unwrap_or("Unknown")
                );
                println!("  Created:  {}", summary.created);
                println!("  Updated:  {}", summary.updated);
                if !summary.labels.is_empty() {
                    println!("  Labels:   {}", summary.labels.join(", "));
                }
                if !summary.description.is_empty() {
                    println!();
                    println!("  Description:");
                    println!("  {}", summary.description);
                }
                println!();
            }
        }

        Commands::Comments { issue_key } => {
            let comments = client.get_issue_comments(&issue_key)?;
            if cli.json {
                println!("{}", serde_json::to_string_pretty(&comments)?);
            } else if comments.is_empty() {
                println!("No comments on {issue_key}");
            } else {
                for c in &comments {
                    println!();
                    println!("--- {}  ({}) ---", c.author, c.created);
                    println!("{}", c.body);
                }
            }
        }

        Commands::Search { jql, max_results } => {
            let results = client.search_issues(&jql, max_results)?;
            if cli.json {
                println!("{}", serde_json::to_string_pretty(&results)?);
            } else {
                let total = results["total"].as_u64().unwrap_or(0);
                let issues = results["issues"].as_array();
                let count = issues.map(|a| a.len()).unwrap_or(0);
                println!();
                println!("{total} result(s) (showing {count})");
                println!();
                if let Some(issues) = issues {
                    for issue in issues {
                        let key = issue["key"].as_str().unwrap_or("");
                        let status = issue["fields"]["status"]["name"]
                            .as_str()
                            .unwrap_or("");
                        let summary = issue["fields"]["summary"].as_str().unwrap_or("");
                        println!("  {key:<12}  [{status:<12}]  {summary}");
                    }
                }
                println!();
            }
        }

        Commands::Projects => {
            let projects = client.get_projects()?;
            if cli.json {
                println!("{}", serde_json::to_string_pretty(&projects)?);
            } else {
                for p in &projects {
                    println!("  {:<10}  {}", p.key, p.name);
                }
            }
        }

        Commands::Whoami => {
            let me = client.get_myself()?;
            if cli.json {
                println!("{}", serde_json::to_string_pretty(&me)?);
            } else {
                println!(
                    "  Logged in as: {} ({})",
                    me.display_name,
                    me.email_address.as_deref().unwrap_or("no email")
                );
            }
        }
    }

    Ok(())
}
