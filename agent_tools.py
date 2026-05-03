from github_client import github


# ================================================================
# TOOL DEFINITIONS (JSON Schema — what Claude sees)
# ================================================================

TOOLS = [
    {
        "name": "get_pr_details",
        "description": (
            "Get comprehensive information about a pull request: title, description, "
            "author, base/head branches, file counts, line additions/deletions, and labels. "
            "Call this FIRST before any other tool to understand what the PR is about."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "GitHub repository owner (username or org name). E.g., 'anthropics'"
                },
                "repo": {
                    "type": "string",
                    "description": "GitHub repository name (without owner). E.g., 'anthropic-sdk-python'"
                },
                "pr_number": {
                    "type": "integer",
                    "description": "The pull request number (the integer in the URL, e.g., 123)"
                },
            },
            "required": ["owner", "repo", "pr_number"]
        }
    },
    {
        "name": "get_pr_files",
        "description": (
            "Get all files changed in a pull request with their diffs. "
            "Returns filename, change status (added/modified/deleted), line counts, "
            "and the unified diff showing exactly what changed. "
            "Use this to see what code was actually modified. "
            "For large PRs, focus on the most important files first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pr_number": {"type": "integer", "description": "PR number"},
            },
            "required": ["owner", "repo", "pr_number"]
        }
    },
    {
        "name": "get_file_content",
        "description": (
            "Get the complete content of a specific file in the PR's version of the code. "
            "Use this when you need more context around a change — e.g., to see what class "
            "a modified method belongs to, what imports are at the top of the file, or how "
            "the rest of a module is structured. "
            "The diff shows WHAT changed; this shows the FULL PICTURE."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "path": {
                    "type": "string",
                    "description": "File path relative to repo root. E.g., 'src/auth/login.py'"
                },
                "ref": {
                    "type": "string",
                    "description": (
                        "Git ref (branch, commit SHA, or tag) to get the file from. "
                        "Use the PR's head_sha (from get_pr_details) to see the file "
                        "as it exists in the PR."
                    )
                },
            },
            "required": ["owner", "repo", "path", "ref"]
        }
    },
    {
        "name": "get_pr_commits",
        "description": (
            "Get the list of commits in the PR with their messages and authors. "
            "Useful for understanding the history and intent of the changes. "
            "Good commit messages explain WHY the code changed, not just WHAT changed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pr_number": {"type": "integer", "description": "PR number"},
            },
            "required": ["owner", "repo", "pr_number"]
        }
    },
    {
        "name": "submit_review",
        "description": (
            "Submit the complete PR review to GitHub. "
            "This posts your overall assessment and all line-level comments atomically. "
            "Call this as your FINAL action after analyzing all relevant files. "
            "\n\nFor the 'event' parameter:"
            "\n- COMMENT: Post observations without approving/blocking (use for informational reviews)"
            "\n- APPROVE: Approve the PR (use when code is ready to merge)"
            "\n- REQUEST_CHANGES: Block merging until issues are fixed (use for bugs or serious concerns)"
            "\n\nFor comments, provide specific file paths and line numbers from the PR. "
            "If you're unsure of exact line numbers, use COMMENT event and describe locations in the body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pr_number": {"type": "integer", "description": "PR number"},
                "commit_id": {
                    "type": "string",
                    "description": "The HEAD commit SHA of the PR (from get_pr_details as head_sha). Required by GitHub to anchor comments."
                },
                "body": {
                    "type": "string",
                    "description": (
                        "The overall review summary shown at the top. Should include: "
                        "1) Brief summary of what the PR does, "
                        "2) Your overall assessment, "
                        "3) Summary of key findings (bugs, improvements, good practices). "
                        "Write in markdown. Be constructive and specific."
                    )
                },
                "event": {
                    "type": "string",
                    "enum": ["COMMENT", "APPROVE", "REQUEST_CHANGES"],
                    "description": "Review verdict: COMMENT (no vote), APPROVE, or REQUEST_CHANGES"
                },
                "comments": {
                    "type": "array",
                    "description": "Array of line-level review comments",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path, e.g., 'src/auth.py'"
                            },
                            "line": {
                                "type": "integer",
                                "description": "Line number in the NEW version of the file (after the PR changes)"
                            },
                            "start_line": {
                                "type": "integer",
                                "description": "Optional: Start line for a multi-line comment. Must be <= line."
                            },
                            "body": {
                                "type": "string",
                                "description": (
                                    "The comment text. Include: "
                                    "1) What the issue is, "
                                    "2) Why it matters (bug? security? performance? style?), "
                                    "3) How to fix it (concrete suggestion or code snippet). "
                                    "Write in markdown."
                                )
                            },
                        },
                        "required": ["path", "line", "body"]
                    }
                },
            },
            "required": ["owner", "repo", "pr_number", "commit_id", "body", "event", "comments"]
        }
    },
]


# ================================================================
# TOOL EXECUTOR
# ================================================================

_STATUS_EMOJI = {
    "added": "✅ ADDED",
    "removed": "❌ DELETED",
    "modified": "✏️ MODIFIED",
    "renamed": "🔀 RENAMED",
    "copied": "📋 COPIED",
}


def _pr_kwargs(tool_input: dict) -> dict:
    return {
        "owner": tool_input["owner"],
        "repo": tool_input["repo"],
        "pr_number": tool_input["pr_number"],
    }


def _handle_get_pr_details(tool_input: dict) -> str:
    result = github.get_pr_details(**_pr_kwargs(tool_input))
    return _format_pr_details(result)


def _handle_get_pr_files(tool_input: dict) -> str:
    result = github.get_pr_files(**_pr_kwargs(tool_input))
    return _format_pr_files(result)


def _handle_get_file_content(tool_input: dict) -> str:
    content = github.get_file_content(
        owner=tool_input["owner"],
        repo=tool_input["repo"],
        path=tool_input["path"],
        ref=tool_input["ref"],
    )
    numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(content.split("\n")))
    return f"File: {tool_input['path']} (at ref {tool_input['ref']})\n\n{numbered}"


def _handle_get_pr_commits(tool_input: dict) -> str:
    result = github.get_pr_commits(**_pr_kwargs(tool_input))
    return _format_commits(result)


def _handle_submit_review(tool_input: dict) -> str:
    result = github.post_review(
        owner=tool_input["owner"],
        repo=tool_input["repo"],
        pr_number=tool_input["pr_number"],
        commit_id=tool_input["commit_id"],
        body=tool_input["body"],
        event=tool_input["event"],
        comments=tool_input.get("comments", []),
    )
    n_comments = len(tool_input.get("comments", []))
    return (
        f"Review submitted successfully!\n"
        f"Review ID: {result.get('id')}\n"
        f"Event: {tool_input['event']}\n"
        f"Comments posted: {n_comments}\n"
        f"URL: {result.get('html_url', 'N/A')}"
    )


_TOOL_DISPATCH = {
    "get_pr_details": _handle_get_pr_details,
    "get_pr_files": _handle_get_pr_files,
    "get_file_content": _handle_get_file_content,
    "get_pr_commits": _handle_get_pr_commits,
    "submit_review": _handle_submit_review,
}


def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Execute a tool by name. Returns a string result or an error message."""
    handler = _TOOL_DISPATCH.get(tool_name)
    if handler is None:
        return f"ERROR: Unknown tool '{tool_name}'."
    try:
        return handler(tool_input)
    except Exception as e:
        return f"ERROR executing {tool_name}: {type(e).__name__}: {e}"


# ================================================================
# FORMATTING HELPERS
# ================================================================

def _format_pr_details(pr: dict) -> str:
    lines = [
        f"PR #{pr['number']}: {pr['title']}",
        f"Author: {pr['author']}",
        f"State: {pr['state']}{'  [DRAFT]' if pr.get('draft') else ''}",
        f"",
        f"Base branch: {pr['base_branch']}  ←  Head branch: {pr['head_branch']}",
        f"Head commit SHA: {pr['head_sha']}",
        f"",
        f"Changes: {pr['changed_files']} files, +{pr['additions']} -{pr['deletions']} lines",
        f"Commits: {pr['commits']}",
        f"",
        f"Description:\n{pr['description']}",
    ]
    if pr.get("labels"):
        lines.append(f"\nLabels: {', '.join(pr['labels'])}")
    return "\n".join(lines)


def _format_pr_files(files: list) -> str:
    if not files:
        return "No files changed in this PR."

    parts = []
    for f in files:
        status = _STATUS_EMOJI.get(f["status"], f["status"].upper())
        header = (
            f"{'=' * 60}\n"
            f"FILE: {f['filename']}\n"
            f"Status: {status}\n"
            f"Changes: +{f['additions']} -{f['deletions']}\n"
        )
        if f.get("previous_filename"):
            header += f"Renamed from: {f['previous_filename']}\n"
        patch = f.get("patch", "(No diff available)")
        parts.append(f"{header}\nDiff:\n{patch}")

    return "\n\n".join(parts)


def _format_commits(commits: list) -> str:
    if not commits:
        return "No commits found."
    lines = ["Commits in this PR:"]
    for c in commits:
        lines.append(f"  {c['sha']}  {c['author']}: {c['message']}")
    return "\n".join(lines)
