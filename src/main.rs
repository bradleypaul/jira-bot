mod claude;
mod client;
mod repo;

use std::collections::HashSet;
use std::path::PathBuf;
use std::thread;
use std::time::Duration;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};

use claude::ClaudeClient;
use client::{repo_from_labels, JiraClient, JiraConfig};

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
    /// Fetch a ticket and send it to Claude to generate an implementation plan
    Generate {
        /// Issue key, e.g. PROJ-123
        issue_key: String,
        /// Additional context to include in the prompt (e.g. tech stack, repo info)
        #[arg(short, long)]
        context: Option<String>,
        /// Claude model to use
        #[arg(short, long)]
        model: Option<String>,
    },
    /// Transition a ticket to In Progress and check out the linked repository
    Start {
        /// Issue key, e.g. MP-1
        issue_key: String,
        /// Directory to clone repos into (default: ~/jira-repos)
        #[arg(short, long)]
        workspace: Option<PathBuf>,
    },
    /// Poll Jira for tickets moving to In Progress and auto-checkout repos
    Watch {
        /// JQL filter for which tickets to watch (default: your assigned tickets)
        #[arg(short, long)]
        jql: Option<String>,
        /// Polling interval in seconds
        #[arg(short, long, default_value_t = 30)]
        interval: u64,
        /// Directory to clone repos into (default: ~/jira-repos)
        #[arg(short, long)]
        workspace: Option<PathBuf>,
    },
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

fn build_claude_client(model: Option<String>) -> Result<ClaudeClient> {
    let api_key = std::env::var("ANTHROPIC_API_KEY")
        .context("ANTHROPIC_API_KEY not set in environment or .env")?;
    Ok(ClaudeClient::new(api_key, model))
}

fn default_workspace() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("jira-repos")
}

fn handle_start(
    client: &JiraClient,
    issue_key: &str,
    workspace: &PathBuf,
) -> Result<()> {
    eprintln!("Fetching ticket {issue_key}...");
    let summary = client.get_issue_summary(issue_key)?;

    let repo_slug = repo_from_labels(&summary.labels).ok_or_else(|| {
        anyhow::anyhow!(
            "No repo: label found on {issue_key}. Add a label like 'repo:owner/repo-name'"
        )
    })?;

    eprintln!(
        "  Ticket:  {} — {}",
        summary.key, summary.summary
    );
    eprintln!("  Repo:    {repo_slug}");

    // Transition to In Progress
    eprintln!("Transitioning to In Progress...");
    match client.transition_to_in_progress(issue_key)? {
        true => eprintln!("  Done — ticket is now In Progress"),
        false => eprintln!("  Ticket is already In Progress"),
    }

    // Clone/pull and create branch
    eprintln!("Setting up repository...");
    let repo_path = repo::checkout_repo(&repo_slug, issue_key, workspace)?;

    eprintln!();
    eprintln!("Ready to work!");
    eprintln!("  cd {}", repo_path.display());
    Ok(())
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

        Commands::Generate {
            issue_key,
            context,
            model,
        } => {
            let claude = build_claude_client(model)?;

            eprintln!("Fetching ticket {issue_key} from Jira...");
            let summary = client.get_issue_summary(&issue_key)?;
            let comments = client.get_issue_comments(&issue_key)?;

            eprintln!("Sending to Claude for analysis...");
            let result =
                claude.generate_from_ticket(&summary, &comments, context.as_deref())?;

            if cli.json {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!();
                println!("{}", "=".repeat(60));
                println!("  Generated plan for {}", result.issue_key);
                println!("{}", "=".repeat(60));
                println!();
                println!("{}", result.response);
            }
        }

        Commands::Start {
            issue_key,
            workspace,
        } => {
            let workspace = workspace.unwrap_or_else(default_workspace);
            handle_start(&client, &issue_key, &workspace)?;
        }

        Commands::Watch {
            jql,
            interval,
            workspace,
        } => {
            let workspace = workspace.unwrap_or_else(default_workspace);
            let jql = jql.unwrap_or_else(|| {
                "assignee = currentUser() AND status = 'In Progress'".to_string()
            });

            eprintln!("Watching for In Progress tickets...");
            eprintln!("  JQL:      {jql}");
            eprintln!("  Interval: {interval}s");
            eprintln!("  Workspace: {}", workspace.display());
            eprintln!();

            let mut seen: HashSet<String> = HashSet::new();

            // Seed with already in-progress tickets so we don't re-trigger them
            if let Ok(results) = client.search_issues(&jql, 50) {
                if let Some(issues) = results["issues"].as_array() {
                    for issue in issues {
                        if let Some(key) = issue["key"].as_str() {
                            seen.insert(key.to_string());
                            eprintln!("  (already in progress: {key})");
                        }
                    }
                }
            }

            loop {
                thread::sleep(Duration::from_secs(interval));

                let results = match client.search_issues(&jql, 50) {
                    Ok(r) => r,
                    Err(e) => {
                        eprintln!("  Poll error: {e}");
                        continue;
                    }
                };

                let issues = match results["issues"].as_array() {
                    Some(arr) => arr,
                    None => continue,
                };

                for issue in issues {
                    let key = match issue["key"].as_str() {
                        Some(k) => k.to_string(),
                        None => continue,
                    };

                    if seen.contains(&key) {
                        continue;
                    }

                    seen.insert(key.clone());

                    let summary = issue["fields"]["summary"]
                        .as_str()
                        .unwrap_or("(no summary)");
                    eprintln!();
                    eprintln!("New In Progress ticket: {key} — {summary}");

                    match handle_start(&client, &key, &workspace) {
                        Ok(()) => {}
                        Err(e) => eprintln!("  Error handling {key}: {e}"),
                    }
                }
            }
        }
    }

    Ok(())
}
