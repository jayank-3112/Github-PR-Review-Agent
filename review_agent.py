import anthropic
from config import config
from agent_tools import TOOLS, execute_tool

client = anthropic.Anthropic(api_key=config.anthropic_api_key)

MAX_ITERATIONS = 20

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


def _log_usage(usage) -> None:
    parts = [f"📊 Tokens: {usage.input_tokens} input, {usage.output_tokens} output"]
    if cache_read := getattr(usage, "cache_read_input_tokens", 0):
        parts.append(f"{cache_read} cache_read")
    if cache_write := getattr(usage, "cache_creation_input_tokens", 0):
        parts.append(f"{cache_write} cache_write")
    print(", ".join(parts))


def _extract_final_text(content) -> str:
    for block in content:
        if hasattr(block, "text"):
            return block.text
    return ""


def _collect_tool_results(content) -> list:
    results = []
    for block in content:
        if block.type == "text" and block.text.strip():
            preview = block.text[:200] + ("..." if len(block.text) > 200 else "")
            print(f"\n💬 Claude: {preview}")
        elif block.type == "tool_use":
            print(f"\n🔧 Tool call: {block.name}")
            for key in ("owner", "repo", "pr_number", "path", "event"):
                if key in block.input:
                    print(f"   {key}: {block.input[key]}")

            result = execute_tool(block.name, block.input)
            preview = result[:200].replace("\n", " ") + ("..." if len(result) > 200 else "")
            print(f"   Result: {preview}")

            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })
    return results


def review_pr(owner: str, repo: str, pr_number: int) -> dict:
    """Run the complete PR review agent for a given pull request."""
    print(f"\n🤖 Starting PR review for {owner}/{repo}#{pr_number}")
    print("=" * 60)

    messages = [{
        "role": "user",
        "content": (
            f"Please review this pull request:\n\n"
            f"Repository: {owner}/{repo}\n"
            f"PR Number: {pr_number}\n\n"
            f"Provide a thorough code review. Start by getting the PR details "
            f"and changed files, then post your review with specific line comments "
            f"for any issues you find."
        ),
    }]

    total_tool_calls = 0

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"\n⚡ Agent iteration {iteration}/{MAX_ITERATIONS}")

        response = client.messages.create(
            model=config.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            tools=TOOLS,
            messages=messages,
        )

        _log_usage(response.usage)
        print(f"🛑 Stop reason: {response.stop_reason}")

        if response.stop_reason == "end_turn":
            final_text = _extract_final_text(response.content)
            print(f"\n✅ Review complete! Used {total_tool_calls} tool calls.")
            if final_text:
                print(f"\nClaude's final message:\n{final_text}")
            return {
                "success": True,
                "total_tool_calls": total_tool_calls,
                "final_message": final_text,
            }

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = _collect_tool_results(response.content)
            total_tool_calls += len(tool_results)
            messages.append({"role": "user", "content": tool_results})
            continue

        print(f"⚠️  Unexpected stop_reason: {response.stop_reason}")
        break

    return {
        "success": False,
        "total_tool_calls": total_tool_calls,
        "final_message": "",
        "error": f"Hit maximum iteration limit ({MAX_ITERATIONS}). Review may be incomplete.",
    }
