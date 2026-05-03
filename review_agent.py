"""
review_agent.py — The Core Claude PR Review Agent
===================================================
WHAT THIS FILE DOES:
    This is the heart of the project. It implements the "agentic loop" —
    the pattern where Claude repeatedly reasons, calls tools, reads results,
    and repeats until it has completed the task.

THE AGENTIC LOOP EXPLAINED:
    ┌─────────────────────────────────────────────────────────┐
    │                                                          │
    │   Messages:                                              │
    │   [system prompt] + [user: "Review PR #123"]            │
    │              │                                           │
    │              ▼                                           │
    │   Claude thinks → calls get_pr_details                  │
    │   stop_reason = "tool_use"                              │
    │              │                                           │
    │   We execute the tool, get the result                   │
    │              │                                           │
    │   Messages:  + [assistant: tool_use block]              │
    │              + [user: tool_result block]                 │
    │              │                                           │
    │              ▼                                           │
    │   Claude thinks → calls get_pr_files                    │
    │   stop_reason = "tool_use"                              │
    │              │                                           │
    │   ... (repeat for each tool call)                       │
    │              │                                           │
    │              ▼                                           │
    │   Claude calls submit_review                            │
    │   stop_reason = "tool_use"                              │
    │              │                                           │
    │   We execute submit_review (posts to GitHub)            │
    │              │                                           │
    │              ▼                                           │
    │   Claude sends final message                            │
    │   stop_reason = "end_turn" ← We exit the loop           │
    │                                                          │
    └─────────────────────────────────────────────────────────┘

PROMPT CACHING:
    The system prompt is large and the same for every review.
    By adding cache_control to it, the first request pays 1.25x,
    all subsequent requests pay 0.1x for those tokens.
    For a 2000-token system prompt at 100 reviews/day: saves ~90%.

ADAPTIVE THINKING:
    We enable thinking={"type": "adaptive"} which lets Claude decide
    when to "think step by step" internally before responding.
    For code review, this means Claude can reason through complex logic
    before deciding what comment to post.
"""

import anthropic
from typing import Optional
from config import config
from agent_tools import TOOLS, execute_tool

# The Anthropic client — this is how we talk to Claude.
# It reads ANTHROPIC_API_KEY from the environment automatically.
client = anthropic.Anthropic(api_key=config.anthropic_api_key)

# Trim the messages list when it exceeds this many characters (~100K tokens).
# Leaves headroom for the system prompt, tool schemas, output, and thinking blocks
# within Claude's 200K context window.
_CONTEXT_TRIM_CHARS = 400_000
_KEEP_RECENT_PAIRS  = 3   # Always keep the last N tool-call/result pairs intact


def _estimate_messages_chars(messages: list) -> int:
    """Rough character count across all messages — used to decide when to trim."""
    total = 0
    for msg in messages:
        content = msg["content"]
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(str(block.get("content", "")))
                    total += len(str(block.get("text", "")))
                else:
                    # SDK content block (ThinkingBlock, ToolUseBlock, TextBlock)
                    total += len(repr(block))
    return total


def _trim_context(messages: list) -> list:
    """
    Reduce context size by replacing old tool results with short placeholders.

    Keeps the message *structure* intact (Claude still sees that it called
    get_pr_details, etc.) but replaces the bulky result data for older turns.
    The last _KEEP_RECENT_PAIRS assistant+user pairs are always kept in full.
    messages[0] (the original task) is never touched.
    """
    # Walk the list and collect (assistant_idx, user_idx) pairs.
    # Structure: [user(task)] [assistant][user] [assistant][user] ...
    pair_indices = []
    i = 1
    while i < len(messages) - 1:
        if messages[i]["role"] == "assistant" and messages[i + 1]["role"] == "user":
            pair_indices.append((i, i + 1))
            i += 2
        else:
            i += 1

    if len(pair_indices) <= _KEEP_RECENT_PAIRS:
        return messages  # Nothing old enough to trim yet

    # Identify which user (tool_result) messages to slim down
    pairs_to_slim = pair_indices[:-_KEEP_RECENT_PAIRS]
    slim_indices = {user_idx for _, user_idx in pairs_to_slim}

    trimmed = []
    for i, msg in enumerate(messages):
        if i not in slim_indices:
            trimmed.append(msg)
            continue

        # Slim this tool_result message: keep the structure, gut the content
        content = msg["content"]
        if not isinstance(content, list):
            trimmed.append(msg)
            continue

        new_content = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                new_content.append({
                    "type": "tool_result",
                    "tool_use_id": block["tool_use_id"],
                    "content": (
                        "[Result evicted from context window to stay within size limits."
                        " This tool was already called and its data was processed.]"
                    ),
                })
            else:
                new_content.append(block)
        trimmed.append({**msg, "content": new_content})

    return trimmed

# ================================================================
# SYSTEM PROMPT
# ================================================================
# The system prompt defines Claude's role, capabilities, and behavior.
# Think of it as the "job description" for the AI agent.
#
# GOOD SYSTEM PROMPTS:
# - Define the agent's role clearly
# - Set expectations for output quality and format
# - List the available tools and when to use them
# - Provide review criteria (what to look for)
# - Set the tone (constructive, not critical)
#
# This is marked for PROMPT CACHING — it never changes per-request,
# so we pay full price once and then 0.1x for all subsequent requests.

SYSTEM_PROMPT = """You are an expert senior software engineer performing a thorough GitHub Pull Request review. Your goal is to provide the most helpful, constructive, and technically accurate review possible.

## Your Review Process

Follow these steps for every PR review:

1. **Understand the PR**: Call `get_pr_details` first to understand the title, description, base/head branches, and scope of changes.

2. **Read Commits (optional)**: Call `get_pr_commits` if commit messages would help understand the intent behind changes.

3. **Analyze Changed Files**: Call `get_pr_files` to see all modified files with their diffs. Read each diff carefully.

4. **Get Context When Needed**: Call `get_file_content` for files where you need to see the full context — e.g., to understand what a class does, what other methods exist, or what imports are present.

5. **Prepare Your Review**: Based on your analysis, prepare:
   - An overall summary and verdict
   - Specific line-level comments for issues found

6. **Submit the Review**: Call `submit_review` with your verdict and all comments.

## What to Review

### Must Check (file these as `REQUEST_CHANGES` if found)
- **Bugs**: Logic errors, off-by-one errors, null pointer issues, race conditions
- **Security vulnerabilities**: SQL injection, XSS, hardcoded secrets, insecure authentication
- **Data integrity issues**: Missing validation, incorrect data transformations, lost data
- **Breaking changes**: API changes, database migrations without backward compatibility

### Should Check (file as comments, may warrant `REQUEST_CHANGES`)
- **Error handling**: Missing try/catch, unhandled promise rejections, swallowed exceptions
- **Performance**: N+1 queries, unnecessary loops, missing indexes, large data in memory
- **Resource leaks**: Unclosed file handles, database connections, network connections
- **Concurrency**: Thread safety, missing locks, shared mutable state

### Nice to Check (file as `COMMENT` suggestions)
- **Code clarity**: Confusing variable names, missing comments on complex logic
- **Duplication**: Code that could be extracted into a reusable function
- **Test coverage**: Missing tests for new functionality or edge cases
- **Documentation**: Missing docstrings, outdated comments, unclear APIs

## Review Comment Format

For each issue found, your comment should explain:
1. **What**: Clearly state the problem
2. **Why**: Explain why it matters (bug? security? performance? maintainability?)
3. **How to fix**: Provide a concrete suggestion or code snippet

Example good comment:
```
This function doesn't handle the case where `user_id` is None, which can happen
when the session expires. This will cause an `AttributeError` on line 45.

**Fix:**
```python
if user_id is None:
    raise ValueError("user_id is required")
```
Or add a guard earlier: `user = get_user(user_id) if user_id else None`
```

## Verdict Guidelines

- **APPROVE**: Code is correct, safe, and ready to merge. Minor suggestions OK.
- **REQUEST_CHANGES**: Found bugs, security issues, or significant correctness problems.
- **COMMENT**: Good code overall but has non-blocking suggestions, questions, or observations.

## Tone

Be constructive and respectful. The goal is to help the author write better code, not to criticize them. Acknowledge good patterns when you see them. Ask questions rather than making assumptions about intent. Suggest improvements rather than just pointing out problems."""


# ================================================================
# THE REVIEW AGENT
# ================================================================

def review_pr(owner: str, repo: str, pr_number: int) -> dict:
    """
    Run the complete PR review agent for a given pull request.

    This is the main entry point. It:
    1. Sets up the initial messages with the PR to review
    2. Runs the agentic loop until Claude is done
    3. Returns a summary of what happened

    Args:
        owner: GitHub repository owner (username or org)
        repo: GitHub repository name
        pr_number: The PR number to review

    Returns:
        dict with keys:
        - success: bool
        - total_tool_calls: int
        - final_message: str
        - error: str (only if success=False)
    """
    print(f"\n🤖 Starting PR review for {owner}/{repo}#{pr_number}")
    print("=" * 60)

    # ── Step 1: Build initial messages ──────────────────────────
    # The messages list is the "conversation history" we maintain.
    # We start with one user message asking for the review.
    # As Claude calls tools and we return results, we append to this list.
    messages = [
        {
            "role": "user",
            "content": (
                f"Please review this pull request:\n\n"
                f"Repository: {owner}/{repo}\n"
                f"PR Number: {pr_number}\n\n"
                f"Provide a thorough code review. Start by getting the PR details "
                f"and changed files, then post your review with specific line comments "
                f"for any issues you find."
            )
        }
    ]

    total_tool_calls = 0

    # ── Step 2: The Agentic Loop ─────────────────────────────────
    # We keep calling Claude until it signals it's done (end_turn).
    # Each iteration either:
    # a) Claude calls a tool → we run it, append result, loop again
    # b) Claude says "end_turn" → review is complete, we exit

    max_iterations = 20  # Safety limit to prevent infinite loops
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        print(f"\n⚡ Agent iteration {iteration}/{max_iterations}")

        # ── Trim context if it's growing too large ───────────────
        # Evicts old tool-result bodies while keeping the message structure,
        # so Claude still knows what it called but isn't re-reading old data.
        context_chars = _estimate_messages_chars(messages)
        if context_chars > _CONTEXT_TRIM_CHARS:
            messages = _trim_context(messages)
            new_chars = _estimate_messages_chars(messages)
            print(f"✂️  Context trimmed: {context_chars:,} → {new_chars:,} chars")

        # ── Call Claude ──────────────────────────────────────────
        # Key parameters:
        # - model: claude-opus-4-7 — most capable, best for code review
        # - max_tokens: 16000 — allows for detailed reviews with many comments
        # - thinking: adaptive — Claude decides when to reason step by step
        # - tools: our GitHub tools — what Claude can do
        # - system + cache_control: the system prompt is cached

        response = client.messages.create(
            model=config.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},  # Claude reasons before acting
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                    # ↑ PROMPT CACHING: This large system prompt is marked as
                    # cacheable. First call: 1.25x cost to write cache.
                    # All subsequent calls: 0.1x cost to read from cache.
                    # With 100 reviews/day on a 2000-token prompt,
                    # this saves ~$0.18/day at Opus 4.7 pricing.
                }
            ],
            tools=TOOLS,
            messages=messages,
        )

        # ── Log token usage ──────────────────────────────────────
        usage = response.usage
        cache_read = getattr(usage, 'cache_read_input_tokens', 0)
        cache_write = getattr(usage, 'cache_creation_input_tokens', 0)
        print(
            f"📊 Tokens: {usage.input_tokens} input, {usage.output_tokens} output"
            + (f", {cache_read} cache_read" if cache_read else "")
            + (f", {cache_write} cache_write" if cache_write else "")
        )
        print(f"🛑 Stop reason: {response.stop_reason}")

        # ── Handle end_turn: Claude is done ──────────────────────
        if response.stop_reason == "end_turn":
            # Extract the final text message from Claude
            final_text = ""
            for block in response.content:
                if hasattr(block, 'text'):
                    final_text = block.text
                    break

            print(f"\n✅ Review complete! Used {total_tool_calls} tool calls.")
            if final_text:
                print(f"\nClaude's final message:\n{final_text}")

            return {
                "success": True,
                "total_tool_calls": total_tool_calls,
                "final_message": final_text,
            }

        # ── Handle tool_use: Claude wants to call a tool ─────────
        if response.stop_reason == "tool_use":
            # The response content can have multiple blocks:
            # - ThinkingBlock: Claude's internal reasoning (if thinking is on)
            # - TextBlock: Claude explaining what it's about to do
            # - ToolUseBlock: The actual tool call(s)

            # Step 2a: Append Claude's response to message history.
            # CRITICAL: We must include the FULL response.content, not just text.
            # The ToolUseBlock must be preserved so Claude knows what it called.
            messages.append({
                "role": "assistant",
                "content": response.content  # Full content including tool_use blocks
            })

            # Step 2b: Process each tool call in the response.
            # Claude can request multiple tool calls in one response.
            tool_results = []

            for block in response.content:
                # Check if this is a tool use block
                if block.type != "tool_use":
                    # Show text blocks (Claude's narration) but skip thinking blocks
                    if block.type == "text" and block.text.strip():
                        print(f"\n💬 Claude: {block.text[:200]}{'...' if len(block.text) > 200 else ''}")
                    continue

                # This is a tool call!
                tool_name = block.name
                tool_input = block.input
                tool_use_id = block.id  # Must echo this back in tool_result

                total_tool_calls += 1
                print(f"\n🔧 Tool call: {tool_name}")

                # Show the key parameters for logging
                for key in ["owner", "repo", "pr_number", "path", "event"]:
                    if key in tool_input:
                        print(f"   {key}: {tool_input[key]}")

                # Step 2c: Execute the tool and get the result
                result = execute_tool(tool_name, tool_input)

                # Preview the result for logging
                result_preview = result[:200].replace('\n', ' ')
                print(f"   Result: {result_preview}{'...' if len(result) > 200 else ''}")

                # Step 2d: Build the tool_result block.
                # This is sent back to Claude so it knows what the tool returned.
                # The tool_use_id must match the id from the tool_use block.
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,  # Links result to the tool call
                    "content": result,            # The tool's output as a string
                })

            # Step 2e: Append all tool results as a user message.
            # All results from this iteration go in ONE user message.
            # This is the pattern the Anthropic API expects.
            messages.append({
                "role": "user",
                "content": tool_results
            })

            # Loop back to call Claude again with the tool results in context
            continue

        # ── Unexpected stop reason ────────────────────────────────
        # This shouldn't happen, but be defensive
        print(f"⚠️  Unexpected stop_reason: {response.stop_reason}")
        break

    # If we hit max iterations, something went wrong
    return {
        "success": False,
        "total_tool_calls": total_tool_calls,
        "final_message": "",
        "error": f"Hit maximum iteration limit ({max_iterations}). Review may be incomplete.",
    }
