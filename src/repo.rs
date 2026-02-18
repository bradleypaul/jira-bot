use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{Context, Result};

/// Clone a repo (or pull if it already exists) and create a feature branch.
/// Returns the path to the local repo.
pub fn checkout_repo(
    repo_slug: &str,
    issue_key: &str,
    workspace_dir: &Path,
) -> Result<PathBuf> {
    let repo_name = repo_slug
        .split('/')
        .last()
        .unwrap_or(repo_slug);

    let repo_path = workspace_dir.join(repo_name);
    let clone_url = format!("https://github.com/{repo_slug}.git");

    if repo_path.exists() {
        eprintln!("  Repository already exists at {}, pulling latest...", repo_path.display());
        run_git(&repo_path, &["checkout", "main"])
            .or_else(|_| run_git(&repo_path, &["checkout", "master"]))?;
        run_git(&repo_path, &["pull"])?;
    } else {
        eprintln!("  Cloning {clone_url} into {}...", repo_path.display());
        std::fs::create_dir_all(workspace_dir)
            .context("Failed to create workspace directory")?;
        run_git(workspace_dir, &["clone", &clone_url])?;
    }

    // Create a feature branch named after the ticket
    let branch_name = format!("{}", issue_key.to_lowercase());
    eprintln!("  Creating branch {branch_name}...");

    // Check if branch already exists
    let branch_exists = Command::new("git")
        .args(["rev-parse", "--verify", &branch_name])
        .current_dir(&repo_path)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);

    if branch_exists {
        run_git(&repo_path, &["checkout", &branch_name])?;
        eprintln!("  Switched to existing branch {branch_name}");
    } else {
        run_git(&repo_path, &["checkout", "-b", &branch_name])?;
        eprintln!("  Created and switched to branch {branch_name}");
    }

    Ok(repo_path)
}

fn run_git(dir: &Path, args: &[&str]) -> Result<String> {
    let output = Command::new("git")
        .args(args)
        .current_dir(dir)
        .output()
        .with_context(|| format!("Failed to run git {}", args.join(" ")))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        anyhow::bail!("git {} failed: {}", args.join(" "), stderr.trim());
    }

    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}
