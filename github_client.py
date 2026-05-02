"""
github_client.py — GitHub API Client
=======================================
WHAT THIS FILE DOES:
    Wraps the GitHub REST API into clean Python functions.
    These functions are called by agent_tools.py when Claude uses a tool.

WHY WE SEPARATE THIS:
    Separation of concerns. github_client.py knows HOW to talk to GitHub.
    agent_tools.py knows WHAT tools Claude can call.
    review_agent.py knows HOW to run the agent loop.
    None of them need to know about the others' internals.

GITHUB API BASICS:
    - Base URL: https://api.github.com
    - Auth: Bearer token in Authorization header
    - Rate limits: 5000 requests/hour with auth
    - Pagination: Results come in pages; we handle this transparently.

GITHUB PR REVIEW API:
    To post a review with line comments, GitHub requires you to:
    1. Create a review with comments array (post_review endpoint)
    2. Or create individual review comments (post_review_comment endpoint)
    We use approach 1 to post everything atomically in one API call.
"""

import requests
from typing import Optional
from config import config


class GitHubClient:
    """
    HTTP client for the GitHub REST API.

    All methods return Python dicts/lists (parsed JSON).
    On API errors, they raise GitHubAPIError with a helpful message.
    """

    BASE_URL = "https://api.github.com"

    def __init__(self):
        # Every request to GitHub needs these headers.
        # Accept header: use the latest API version (json+github+v3)
        # Authorization: Bearer token authenticates our requests
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {config.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _get(self, path: str, params: dict = None) -> dict | list:
        """
        Make an authenticated GET request to the GitHub API.

        Args:
            path: API path like "/repos/owner/repo/pulls/1"
            params: Query string parameters

        Returns:
            Parsed JSON response

        Raises:
            GitHubAPIError: If the API returns an error status
        """
        url = f"{self.BASE_URL}{path}"
        response = self.session.get(url, params=params)
        self._check_response(response)
        return response.json()

    def _post(self, path: str, data: dict) -> dict:
        """Make an authenticated POST request."""
        url = f"{self.BASE_URL}{path}"
        response = self.session.post(url, json=data)
        self._check_response(response)
        return response.json()

    def _check_response(self, response: requests.Response):
        """Raise a clear error if the GitHub API returns an error status."""
        if response.status_code >= 400:
            try:
                error_data = response.json()
                message = error_data.get("message", "Unknown error")
                errors = error_data.get("errors", [])
                detail = f"{message}"
                if errors:
                    detail += f" — {errors}"
            except Exception:
                detail = response.text
            raise GitHubAPIError(
                f"GitHub API error {response.status_code}: {detail}"
            )

    # ================================================================
    # PR Information Methods
    # ================================================================

    def get_pr_details(self, owner: str, repo: str, pr_number: int) -> dict:
        """
        Get comprehensive information about a pull request.

        Returns a dict with:
        - title, body (description)
        - author, state (open/closed/merged)
        - base branch (what we're merging INTO)
        - head branch (what we're merging FROM)
        - commit counts, file counts, additions, deletions
        - mergeable status
        - labels, reviewers
        """
        pr = self._get(f"/repos/{owner}/{repo}/pulls/{pr_number}")

        # Extract the most useful fields for code review
        return {
            "number": pr["number"],
            "title": pr["title"],
            "description": pr.get("body", "") or "(No description provided)",
            "author": pr["user"]["login"],
            "state": pr["state"],
            "created_at": pr["created_at"],
            "updated_at": pr["updated_at"],
            "base_branch": pr["base"]["ref"],
            "head_branch": pr["head"]["ref"],
            "base_sha": pr["base"]["sha"],
            "head_sha": pr["head"]["sha"],
            "commits": pr["commits"],
            "changed_files": pr["changed_files"],
            "additions": pr["additions"],
            "deletions": pr["deletions"],
            "labels": [label["name"] for label in pr.get("labels", [])],
            "requested_reviewers": [r["login"] for r in pr.get("requested_reviewers", [])],
            "draft": pr.get("draft", False),
            "mergeable": pr.get("mergeable"),
        }

    def get_pr_files(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """
        Get all changed files in a PR with their diffs.

        Each file entry contains:
        - filename: path to the file
        - status: added/removed/modified/renamed/copied
        - additions, deletions: line count changes
        - patch: the actual diff (unified diff format)

        HOW DIFFS WORK (unified diff format):
            --- a/file.py     ← old version
            +++ b/file.py     ← new version
            @@ -10,7 +10,9 @@ class Foo:   ← hunk header: old line 10, 7 lines | new line 10, 9 lines
             unchanged line   ← space = context (unchanged)
            -removed line     ← minus = deleted
            +added line       ← plus = added
        """
        # GitHub paginates this — we need to collect all pages
        files = []
        page = 1
        while True:
            page_data = self._get(
                f"/repos/{owner}/{repo}/pulls/{pr_number}/files",
                params={"per_page": 100, "page": page}
            )
            if not page_data:
                break
            for file in page_data:
                files.append({
                    "filename": file["filename"],
                    "status": file["status"],
                    "additions": file["additions"],
                    "deletions": file["deletions"],
                    "changes": file["changes"],
                    "patch": file.get("patch", "(Binary file or no diff available)"),
                    "previous_filename": file.get("previous_filename"),
                })
            if len(page_data) < 100:
                break
            page += 1

        return files

    def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str
    ) -> str:
        """
        Get the full content of a file at a specific git ref (branch/commit/tag).

        WHY THIS IS USEFUL:
            The diff only shows changed lines. For context (what does this function do?
            what are the surrounding imports? what class does this belong to?),
            the reviewer needs to see the full file.

        Args:
            ref: A git ref — branch name, commit SHA, or tag. Use the PR's head SHA
                 to get the file as it exists in the PR.
        """
        try:
            data = self._get(
                f"/repos/{owner}/{repo}/contents/{path}",
                params={"ref": ref}
            )
            # GitHub returns base64-encoded content
            import base64
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            return content
        except GitHubAPIError as e:
            return f"(Could not retrieve file content: {e})"

    def get_pr_commits(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """
        Get the list of commits in a PR.

        Useful for understanding the PR's history and intent.
        Commit messages often explain WHY changes were made.
        """
        commits = self._get(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/commits",
            params={"per_page": 100}
        )
        return [
            {
                "sha": c["sha"][:8],  # Short SHA for readability
                "message": c["commit"]["message"].split("\n")[0],  # First line only
                "author": c["commit"]["author"]["name"],
            }
            for c in commits
        ]

    # ================================================================
    # Review Posting Methods
    # ================================================================

    def post_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_id: str,
        body: str,
        event: str,
        comments: list[dict]
    ) -> dict:
        """
        Submit a PR review with line-level comments.

        GITHUB REVIEW API:
            POST /repos/{owner}/{repo}/pulls/{pr_number}/reviews

        Args:
            commit_id: The HEAD commit SHA of the PR. GitHub needs this to
                      anchor comments to a specific version of the code.
            body: The overall review comment (shown at the top of the review)
            event: One of:
                   - "COMMENT": Post without approving/requesting changes
                   - "APPROVE": Approve the PR
                   - "REQUEST_CHANGES": Request changes before merging
            comments: List of line-level comments. Each comment needs:
                      - path: file path (e.g., "src/auth.py")
                      - line: line number in the NEW version of the file
                      - body: the comment text

        IMPORTANT ABOUT LINE NUMBERS:
            GitHub uses two line numbering systems:
            1. "position" — position in the diff (complex, diff-relative)
            2. "line" — actual line number in the file (simple, but newer API)

            We use "line" + "side: RIGHT" which means line in the new file.
            This is simpler and what most review tools use.
        """
        # Format comments for GitHub's API
        github_comments = []
        for comment in comments:
            if not comment.get("path") or not comment.get("body"):
                continue  # Skip malformed comments

            github_comment = {
                "path": comment["path"],
                "body": comment["body"],
                "line": comment.get("line", 1),
                "side": "RIGHT",  # RIGHT = new version of file, LEFT = old version
            }

            # Multi-line comment (highlights a range of lines)
            if comment.get("start_line") and comment["start_line"] != comment.get("line"):
                github_comment["start_line"] = comment["start_line"]
                github_comment["start_side"] = "RIGHT"

            github_comments.append(github_comment)

        review_data = {
            "commit_id": commit_id,
            "body": body,
            "event": event,
            "comments": github_comments,
        }

        return self._post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            review_data
        )


class GitHubAPIError(Exception):
    """Raised when GitHub API returns an error response."""
    pass


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """
    Parse a GitHub PR URL into (owner, repo, pr_number).

    Handles formats:
    - https://github.com/owner/repo/pull/123
    - https://github.com/owner/repo/pull/123/files
    - github.com/owner/repo/pull/123  (without https://)

    Returns:
        tuple of (owner, repo, pr_number)

    Raises:
        ValueError: If URL doesn't match expected GitHub PR format
    """
    # Normalize: remove protocol and www prefix
    url = url.strip()
    url = url.replace("https://", "").replace("http://", "").replace("www.", "")

    # Expected format: github.com/owner/repo/pull/number
    parts = url.split("/")

    # Find "pull" in the parts
    try:
        pull_idx = parts.index("pull")
    except ValueError:
        raise ValueError(
            f"Not a valid GitHub PR URL: {url!r}\n"
            "Expected format: https://github.com/owner/repo/pull/123"
        )

    if pull_idx < 3 or pull_idx + 1 >= len(parts):
        raise ValueError(f"Malformed GitHub PR URL: {url!r}")

    try:
        owner = parts[pull_idx - 2]
        repo = parts[pull_idx - 1]
        pr_number = int(parts[pull_idx + 1])
    except (IndexError, ValueError) as e:
        raise ValueError(f"Could not parse PR URL {url!r}: {e}")

    return owner, repo, pr_number


# Module-level client instance — created once, reused everywhere
github = GitHubClient()
