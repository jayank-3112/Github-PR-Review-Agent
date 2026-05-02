"""
main.py — Entry Point
======================
WHAT THIS FILE DOES:
    The command-line interface (CLI) for the PR Review Agent.
    Two modes:
    1. `python main.py review <PR_URL>` — Review a specific PR manually
    2. `python main.py server` — Start the webhook server for auto-reviews

WHY TWO MODES:
    - Manual mode: Great for testing, one-off reviews, or CI/CD pipelines
    - Webhook mode: For fully automated reviews on every new PR

THE CLI PATTERN:
    We use argparse (Python's standard library CLI parser) to define
    commands and their arguments. This is a common pattern for Python tools.

ALTERNATIVE: You could also use `click` or `typer` for more feature-rich CLIs.
    We use argparse here to avoid extra dependencies and keep things educational.
"""

import argparse
import sys
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# Console object for beautiful terminal output with colors and formatting
console = Console()


def cmd_review(args):
    """
    Handle the 'review' command.

    Parses a GitHub PR URL and runs an immediate review.

    This is useful for:
    - Testing the agent with a specific PR
    - One-off reviews without setting up webhooks
    - CI/CD pipelines that review PRs on demand
    - Reviewing PRs in repos you don't control

    Args:
        args: Parsed CLI arguments with args.pr_url
    """
    from github_client import parse_pr_url
    from review_agent import review_pr

    pr_url = args.pr_url

    # Parse the GitHub PR URL into components
    console.print(f"\n[bold blue]🔍 PR Review Agent[/bold blue]")
    console.print(f"   Reviewing: [link={pr_url}]{pr_url}[/link]")

    try:
        owner, repo, pr_number = parse_pr_url(pr_url)
        console.print(f"   Parsed: [cyan]{owner}[/cyan]/[cyan]{repo}[/cyan]#[yellow]{pr_number}[/yellow]")
    except ValueError as e:
        console.print(f"\n[red]❌ Invalid PR URL:[/red] {e}")
        sys.exit(1)

    # Run the review
    console.print(f"\n[dim]Starting review... This may take 1-3 minutes depending on PR size.[/dim]\n")

    result = review_pr(owner=owner, repo=repo, pr_number=pr_number)

    # Display the result
    if result["success"]:
        console.print(Panel(
            Text.from_markup(
                f"[green]✅ Review completed successfully![/green]\n\n"
                f"Tool calls made: [cyan]{result['total_tool_calls']}[/cyan]\n\n"
                f"[dim]Check the PR on GitHub for the posted review.[/dim]"
            ),
            title="Review Complete",
            border_style="green",
        ))
    else:
        error_msg = result.get("error", "Unknown error")
        console.print(Panel(
            Text.from_markup(
                f"[red]❌ Review failed[/red]\n\n"
                f"Error: {error_msg}\n"
                f"Tool calls made: [cyan]{result['total_tool_calls']}[/cyan]"
            ),
            title="Review Failed",
            border_style="red",
        ))
        sys.exit(1)


def cmd_server(args):
    """
    Handle the 'server' command.

    Starts the FastAPI webhook server that receives GitHub PR events
    and automatically triggers reviews.

    Setup steps (shown to user):
    1. Start this server
    2. Expose it to the internet with ngrok
    3. Configure the webhook URL in GitHub repo settings
    4. Every new PR gets automatically reviewed

    Args:
        args: Parsed CLI arguments with args.port
    """
    from webhook_server import start_server

    console.print(Panel(
        Text.from_markup(
            "[bold blue]🤖 PR Review Agent — Webhook Server[/bold blue]\n\n"
            "[bold]Setup Steps:[/bold]\n\n"
            "1️⃣  Start this server (you're doing this now)\n\n"
            "2️⃣  Expose to internet:\n"
            "   [cyan]ngrok http 8000[/cyan]\n"
            "   → Note the https://xxx.ngrok.io URL\n\n"
            "3️⃣  Create GitHub Webhook:\n"
            "   • Go to: GitHub Repo → Settings → Webhooks → Add webhook\n"
            "   • Payload URL: [cyan]https://xxx.ngrok.io/webhook[/cyan]\n"
            "   • Content type: [cyan]application/json[/cyan]\n"
            "   • Secret: [cyan]your GITHUB_WEBHOOK_SECRET from .env[/cyan]\n"
            "   • Events: [cyan]Pull requests[/cyan] (check this checkbox)\n"
            "   • Active: ✓\n\n"
            "4️⃣  Create a PR in that repo → Watch the magic happen! 🎉"
        ),
        border_style="blue",
        padding=(1, 2),
    ))

    start_server(port=args.port)


def cmd_test(args):
    """
    Handle the 'test' command.

    Tests the configuration without making any API calls.
    Useful to verify setup before running a real review.
    """
    console.print("\n[bold blue]🧪 Testing Configuration[/bold blue]\n")

    # Test 1: Config loading
    try:
        from config import config
        console.print("[green]✅[/green] Config loaded successfully")
        console.print(f"   Model: [cyan]{config.model}[/cyan]")
        console.print(f"   GitHub token: [cyan]{'*' * 8 + config.github_token[-4:]}[/cyan]")
        console.print(f"   Anthropic key: [cyan]{'*' * 8 + config.anthropic_api_key[-4:]}[/cyan]")
    except Exception as e:
        console.print(f"[red]❌ Config error:[/red] {e}")
        sys.exit(1)

    # Test 2: GitHub API access
    console.print("\n[dim]Testing GitHub API...[/dim]")
    try:
        from github_client import github
        # Try to get rate limit info — this is a lightweight authenticated request
        rate_limit = github._get("/rate_limit")
        core = rate_limit["rate"]
        console.print(f"[green]✅[/green] GitHub API accessible")
        console.print(f"   Rate limit: {core['remaining']}/{core['limit']} requests remaining")
    except Exception as e:
        console.print(f"[red]❌ GitHub API error:[/red] {e}")
        sys.exit(1)

    # Test 3: Anthropic API access
    console.print("\n[dim]Testing Anthropic API...[/dim]")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        # Make a tiny test call
        response = client.messages.create(
            model=config.model,
            max_tokens=10,
            messages=[{"role": "user", "content": "Say 'OK'"}]
        )
        console.print(f"[green]✅[/green] Anthropic API accessible")
        console.print(f"   Model: [cyan]{response.model}[/cyan]")
    except Exception as e:
        console.print(f"[red]❌ Anthropic API error:[/red] {e}")
        sys.exit(1)

    console.print("\n[bold green]All tests passed! You're ready to run reviews.[/bold green]")


def main():
    """
    Main entry point for the CLI.

    Parses command-line arguments and routes to the appropriate command handler.
    """
    # ── Argument Parser ─────────────────────────────────────────
    # argparse creates the CLI interface and help text automatically
    parser = argparse.ArgumentParser(
        prog="pr-review-agent",
        description="AI-powered GitHub PR Review Agent using Claude",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Review a specific PR:
  python main.py review https://github.com/owner/repo/pull/123

  # Start webhook server (auto-review new PRs):
  python main.py server

  # Start webhook server on custom port:
  python main.py server --port 3000

  # Test your configuration:
  python main.py test
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    subparsers.required = True

    # ── 'review' subcommand ──────────────────────────────────────
    review_parser = subparsers.add_parser(
        "review",
        help="Review a specific PR by URL",
        description="Immediately review a GitHub pull request and post review comments.",
    )
    review_parser.add_argument(
        "pr_url",
        metavar="PR_URL",
        help="Full GitHub PR URL, e.g., https://github.com/owner/repo/pull/123",
    )
    review_parser.set_defaults(func=cmd_review)

    # ── 'server' subcommand ──────────────────────────────────────
    server_parser = subparsers.add_parser(
        "server",
        help="Start webhook server for automatic reviews",
        description="Start a FastAPI server that receives GitHub webhooks and auto-reviews PRs.",
    )
    server_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to run the server on (default: from .env or 8000)",
    )
    server_parser.set_defaults(func=cmd_server)

    # ── 'test' subcommand ────────────────────────────────────────
    test_parser = subparsers.add_parser(
        "test",
        help="Test configuration and API access",
        description="Verify that your API keys and configuration are working correctly.",
    )
    test_parser.set_defaults(func=cmd_test)

    # ── Parse and dispatch ───────────────────────────────────────
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
