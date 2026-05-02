"""
config.py — Configuration Management
======================================
WHAT THIS FILE DOES:
    Loads environment variables and validates that required ones are present.
    Centralizes all configuration so other files just import from here.

WHY THIS MATTERS:
    - Never hardcode secrets in source code. They end up in git history forever.
    - Centralizing config means you change one place, not 5 files.
    - Failing fast (raising an error at startup) is better than mysterious failures later.

PATTERN: The "Config object" pattern — load once, import everywhere.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

# Load .env file into environment variables.
# If a variable is already set in the environment (e.g., in production),
# load_dotenv won't overwrite it — which is the right behavior.
load_dotenv()


@dataclass
class Config:
    """
    All configuration for the PR Review Agent.

    Using a dataclass gives us:
    - Type hints (self-documenting)
    - Immutability (by convention)
    - Easy to print/inspect for debugging
    """
    anthropic_api_key: str
    github_token: str
    github_webhook_secret: str
    port: int

    # The Claude model to use for reviews.
    # claude-opus-4-7 is the most capable model — ideal for code review
    # which requires deep reasoning about code quality, bugs, and patterns.
    model: str = "claude-opus-4-7"


def load_config() -> Config:
    """
    Load and validate all required configuration.

    Raises ValueError if any required environment variable is missing.
    This ensures the app fails immediately at startup with a clear error
    message, rather than failing mysteriously later when a variable is used.
    """
    required_vars = {
        "ANTHROPIC_API_KEY": "Get from https://console.anthropic.com",
        "GITHUB_TOKEN": "GitHub Personal Access Token with repo scope",
        "GITHUB_WEBHOOK_SECRET": "Random secret for webhook signature verification",
    }

    missing = []
    for var, hint in required_vars.items():
        if not os.getenv(var):
            missing.append(f"  {var}  ({hint})")

    if missing:
        raise ValueError(
            "Missing required environment variables. "
            "Copy .env.example to .env and fill in:\n" + "\n".join(missing)
        )

    return Config(
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        github_token=os.environ["GITHUB_TOKEN"],
        github_webhook_secret=os.environ["GITHUB_WEBHOOK_SECRET"],
        port=int(os.getenv("PORT", "8000")),
    )


# Global config instance — loaded once at import time.
# Other modules do: from config import config
config = load_config()
