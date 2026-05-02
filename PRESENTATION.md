# Building an AI Agent from Scratch
### A Complete Guide Using the GitHub PR Review Agent as Reference

---

## SLIDE 1 — What Are We Building?

```
Developer opens a PR on GitHub
         │
         ▼
GitHub sends a webhook event  ──────────────────────────┐
         │                                               │
         ▼                                               │
Our webhook server receives it                          │
         │                                        Auto Mode
         ▼                                               │
AI Agent wakes up, reads the PR                         │
         │                              ┌───────────────┘
         ▼                              │
Claude analyzes code, calls tools  ◄────┘
         │
         ▼
Review posted on GitHub with inline
comments + verdict (APPROVE / REQUEST_CHANGES / COMMENT)
```

**Result**: Every PR automatically gets a senior engineer-level review in 1–3 minutes.

---

## SLIDE 2 — What is an AI Agent?

### A Simple LLM Call (NOT an agent)
```
You  ──► "Summarize this text"  ──► Claude  ──► "Here's a summary"  ──► Done
```
One request. One response. No tools. No decisions.

---

### An AI Agent
```
You  ──► "Review PR #123"  ──► Claude
                                  │
                           Claude thinks:
                           "I need to read the PR first"
                                  │
                           calls get_pr_details()
                                  │
                           reads result, thinks:
                           "Now I need the changed files"
                                  │
                           calls get_pr_files()
                                  │
                           reads diffs, thinks:
                           "Line 42 has a bug — I need context"
                                  │
                           calls get_file_content()
                                  │
                           thinks: "Now I have enough to review"
                                  │
                           calls submit_review()
                                  │
                           "Review complete" ──► Done
```

**An agent = LLM + Tools + a Loop that keeps going until the task is done.**

---

## SLIDE 3 — The 7 Components of This Agent

```
┌─────────────────────────────────────────────────────────────────┐
│                    PR REVIEW AGENT                              │
│                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────────┐  │
│  │  Config  │   │  GitHub  │   │  Agent   │   │  Webhook   │  │
│  │ (config  │   │  Client  │   │  Tools   │   │  Server    │  │
│  │  .py)    │   │(github_  │   │(agent_   │   │(webhook_   │  │
│  │          │   │client.py)│   │tools.py) │   │server.py)  │  │
│  └──────────┘   └──────────┘   └──────────┘   └────────────┘  │
│                                                                 │
│  ┌──────────────────────────┐   ┌──────────────────────────┐   │
│  │      Review Agent        │   │          CLI             │   │
│  │    (review_agent.py)     │   │        (main.py)         │   │
│  │   ← THE AGENTIC LOOP →  │   │  manual trigger mode     │   │
│  └──────────────────────────┘   └──────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                   Claude (claude-opus-4-7)               │  │
│  │          The brain — reasoning, decision-making          │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

| # | Component | File | Role |
|---|-----------|------|------|
| 1 | Configuration | `config.py` | Loads API keys, validates environment |
| 2 | GitHub Client | `github_client.py` | Talks to GitHub REST API |
| 3 | Agent Tools | `agent_tools.py` | Defines what Claude CAN do |
| 4 | Review Agent | `review_agent.py` | THE AGENTIC LOOP — orchestrates everything |
| 5 | Webhook Server | `webhook_server.py` | Auto-trigger when PR is created |
| 6 | CLI | `main.py` | Manual trigger by URL |
| 7 | Claude (LLM) | Anthropic API | The brain — reasoning and decisions |

---

## SLIDE 4 — Component 1: Configuration (`config.py`)

**What it does**: Loads secrets from environment variables and validates them at startup.

```python
# config.py — simplified
from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()  # Reads .env file into environment

@dataclass
class Config:
    anthropic_api_key: str   # Key to talk to Claude
    github_token: str        # Key to talk to GitHub API
    github_webhook_secret: str  # Shared secret to verify webhooks
    port: int                # Which port the server runs on
    model: str = "claude-opus-4-7"  # Which Claude model to use

def load_config() -> Config:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("Missing ANTHROPIC_API_KEY in .env")
    # ... validate all required keys
    return Config(anthropic_api_key=key, ...)

config = load_config()  # Module-level singleton
```

**Key concept**: Fail fast at startup. If an API key is missing, crash immediately with a helpful message — don't let the agent run halfway and fail mysteriously.

**.env file needed**:
```
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...
GITHUB_WEBHOOK_SECRET=your-random-secret
PORT=8000
```

---

## SLIDE 5 — Component 2: GitHub Client (`github_client.py`)

**What it does**: Wraps GitHub's REST API into clean Python methods.

```python
# github_client.py — simplified
import requests

class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(self):
        # All requests share these authentication headers
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {config.github_token}",
            "Accept": "application/vnd.github+json",
        })

    def get_pr_details(self, owner, repo, pr_number) -> dict:
        pr = self._get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
        return {
            "title": pr["title"],
            "description": pr.get("body"),
            "head_sha": pr["head"]["sha"],  # needed to anchor review comments
            "changed_files": pr["changed_files"],
            # ...
        }

    def get_pr_files(self, owner, repo, pr_number) -> list:
        # Returns list of files with their unified diffs
        return self._get(f"/repos/{owner}/{repo}/pulls/{pr_number}/files")

    def post_review(self, owner, repo, pr_number, commit_id, body, event, comments):
        # Posts the actual review to GitHub
        return self._post(f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews", {
            "commit_id": commit_id,
            "body": body,
            "event": event,         # "APPROVE" / "REQUEST_CHANGES" / "COMMENT"
            "comments": comments,   # inline line comments
        })

github = GitHubClient()  # Module-level singleton
```

**5 methods Claude can trigger**:
| Method | What it returns |
|--------|----------------|
| `get_pr_details()` | Title, author, branches, file counts |
| `get_pr_files()` | All changed files + their diffs |
| `get_file_content()` | Full content of any file (for context) |
| `get_pr_commits()` | List of commits with messages |
| `post_review()` | Posts review to GitHub (final action) |

---

## SLIDE 6 — Component 3: Agent Tools (`agent_tools.py`)

This is the most important concept for agents.

**Every tool has TWO parts:**

### Part 1 — JSON Schema (what Claude SEES)
```python
{
    "name": "get_pr_files",
    "description": "Get all files changed in a PR with their diffs. "
                   "Returns filename, status (added/modified/deleted), "
                   "and the unified diff showing exactly what changed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "Repository owner"},
            "repo":  {"type": "string", "description": "Repository name"},
            "pr_number": {"type": "integer", "description": "PR number"},
        },
        "required": ["owner", "repo", "pr_number"]
    }
}
```
Claude reads this description and decides WHEN and HOW to call the tool.

### Part 2 — Python Function (what actually EXECUTES)
```python
def execute_tool(tool_name: str, tool_input: dict) -> str:
    if tool_name == "get_pr_files":
        result = github.get_pr_files(
            owner=tool_input["owner"],
            repo=tool_input["repo"],
            pr_number=tool_input["pr_number"],
        )
        return _format_pr_files(result)  # Return as readable text
    # ...
```

**The 5 tools in this agent:**
| Tool | Purpose | When Claude uses it |
|------|---------|-------------------|
| `get_pr_details` | PR metadata | First — always |
| `get_pr_files` | Diffs of changed files | After getting details |
| `get_file_content` | Full file content | When needing more context |
| `get_pr_commits` | Commit history | Optional — for understanding intent |
| `submit_review` | Post review to GitHub | Last — always |

**Why tools matter**: Without tools, Claude can only generate text. Tools let Claude take REAL ACTIONS and read REAL DATA.

---

## SLIDE 7 — Component 4: The Agentic Loop (`review_agent.py`) ⭐

**This is the heart of every AI agent.** 

```python
def review_pr(owner, repo, pr_number):
    # Step 1: Start the conversation
    messages = [{
        "role": "user",
        "content": f"Please review PR #{pr_number} in {owner}/{repo}"
    }]

    # Step 2: THE AGENTIC LOOP
    while True:
        # Ask Claude what to do next
        response = client.messages.create(
            model="claude-opus-4-7",
            tools=TOOLS,          # Here are your available tools
            messages=messages,    # Here is the conversation so far
            # ...
        )

        # Claude is DONE — review complete
        if response.stop_reason == "end_turn":
            return {"success": True}

        # Claude wants to USE A TOOL
        if response.stop_reason == "tool_use":

            # 1. Append Claude's response to history (CRITICAL — includes tool call)
            messages.append({
                "role": "assistant",
                "content": response.content   # full content, not just text
            })

            # 2. Execute each tool Claude asked for
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,  # links result to the tool call
                        "content": result,
                    })

            # 3. Feed results back to Claude as a user message
            messages.append({
                "role": "user",
                "content": tool_results
            })

            # 4. Loop back → Claude reads results and decides what to do next
```

**The loop visualized:**
```
┌─────────────────────────────────────────────────┐
│  messages = [user: "Review PR #123"]            │
│       │                                         │
│       ▼                                         │
│  Claude ──► stop_reason="tool_use"              │
│       │     tool: get_pr_details                │
│       │                                         │
│  messages += [assistant: tool_use block]        │
│  execute get_pr_details()                       │
│  messages += [user: tool_result]                │
│       │                                         │
│       ▼                                         │
│  Claude ──► stop_reason="tool_use"              │
│       │     tool: get_pr_files                  │
│       │                                         │
│  messages += [assistant: tool_use block]        │
│  execute get_pr_files()                         │
│  messages += [user: tool_result]                │
│       │                                         │
│       ▼                                         │
│  Claude ──► stop_reason="tool_use"              │
│       │     tool: submit_review                 │
│       │                                         │
│  execute submit_review() → posts to GitHub      │
│  messages += [user: tool_result]                │
│       │                                         │
│       ▼                                         │
│  Claude ──► stop_reason="end_turn" ← EXIT      │
└─────────────────────────────────────────────────┘
```

**Two critical rules:**
1. Always append the FULL `response.content` (not just the text) as the assistant turn
2. Always send tool results back as `tool_result` type blocks in a user message

---

## SLIDE 8 — Claude API Features Used

### Feature 1: Adaptive Thinking
```python
response = client.messages.create(
    model="claude-opus-4-7",
    thinking={"type": "adaptive"},  # ← THIS
    # ...
)
```
Claude decides when to "think step by step" internally before responding.

Without it: Claude gives a quick answer.
With it: Claude reasons through complex logic → better code review quality.

---

### Feature 2: Prompt Caching
```python
system=[{
    "type": "text",
    "text": SYSTEM_PROMPT,           # 500+ tokens of instructions
    "cache_control": {"type": "ephemeral"},  # ← THIS
}]
```
The system prompt is identical for every review.

| | Cost |
|--|------|
| First review | 1.25x (writes the cache) |
| All subsequent reviews | 0.1x (reads from cache) |
| **Savings at 100 reviews/day** | **~90% on system prompt tokens** |

---

### Feature 3: The System Prompt
```
You are an expert senior software engineer performing a thorough 
GitHub Pull Request review...

## Your Review Process
1. Call get_pr_details first
2. Call get_pr_files to see diffs
3. Call get_file_content when you need more context
4. Call submit_review as your final action

## What to Review
### Must Check (REQUEST_CHANGES if found)
- Bugs, security vulnerabilities, data integrity issues

### Should Check
- Error handling, performance, resource leaks

### Nice to Check
- Code clarity, duplication, test coverage

## Verdict Guidelines
- APPROVE: Ready to merge
- REQUEST_CHANGES: Bugs or serious issues
- COMMENT: Non-blocking suggestions
```

The system prompt is the "job description" for the AI.

---

## SLIDE 9 — Component 5: Webhook Server (`webhook_server.py`)

**The problem**: GitHub expects a response in 10 seconds. A code review takes 1–3 minutes.

**The solution**: Respond immediately, process in the background.

```python
from fastapi import FastAPI, BackgroundTasks

app = FastAPI()

@app.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):

    # 1. Read raw bytes BEFORE anything else (signature uses raw bytes)
    raw_body = await request.body()

    # 2. Verify this is really from GitHub (not a fake request)
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_github_signature(raw_body, signature):
        raise HTTPException(403, "Invalid signature")

    # 3. Parse the event
    payload = json.loads(raw_body)
    action = payload["action"]   # "opened", "synchronize", "closed"...

    # 4. Only review when relevant (new PR or new commits)
    if action not in {"opened", "reopened", "synchronize"}:
        return {"status": "ignored"}

    # 5. Schedule review to run IN THE BACKGROUND
    background_tasks.add_task(process_pr_review, owner, repo, pr_number)

    # 6. Respond to GitHub IMMEDIATELY (before review is done)
    return JSONResponse({"status": "accepted"}, status_code=202)
```

**Timeline:**
```
GitHub sends webhook
      │
      ▼
Server responds 202 Accepted  ← happens in < 1 second
      │
      ▼  (background)
Review runs for 1–3 minutes
      │
      ▼
Review posted on GitHub
```

---

### Security: Webhook Signature Verification

**Why**: Anyone who knows your webhook URL can send fake PR events.

**How GitHub signs payloads**:
```
signature = HMAC-SHA256(payload_bytes, your_webhook_secret)
```
GitHub sends this in the `X-Hub-Signature-256` header.

**How we verify**:
```python
def verify_github_signature(payload_body: bytes, signature_header: str) -> bool:
    # Compute what the signature SHOULD be
    expected = hmac.new(
        key=config.github_webhook_secret.encode(),
        msg=payload_body,
        digestmod=hashlib.sha256
    ).hexdigest()

    received = signature_header.replace("sha256=", "")

    # CRITICAL: Use compare_digest, NOT ==
    # Regular == leaks timing info (returns early on mismatch)
    # compare_digest always takes the same time → timing-attack safe
    return hmac.compare_digest(received, expected)
```

---

## SLIDE 10 — Component 6: CLI (`main.py`)

**Two ways to trigger a review:**

### Manual Mode — review by URL
```bash
python main.py review https://github.com/facebook/react/pull/31234
```
```python
def cmd_review(args):
    owner, repo, pr_number = parse_pr_url(args.pr_url)
    result = review_pr(owner=owner, repo=repo, pr_number=pr_number)
    print("Review posted!" if result["success"] else result["error"])
```

### Server Mode — auto-review new PRs
```bash
python main.py server --port 8000
# Then: ngrok http 8000 → set URL in GitHub webhook settings
```

### Test Mode — verify setup
```bash
python main.py test
```
Checks config, GitHub API access, and Anthropic API access without doing a real review.

---

## SLIDE 11 — How It All Fits Together

```
                        ┌──────────────┐
                        │    GitHub    │
                        │  Repository  │
                        └──────┬───────┘
                               │ Developer opens PR
                               │
               ┌───────────────┼───────────────┐
               │ Auto mode     │         Manual │ mode
               ▼               │               ▼
    ┌──────────────────┐       │    ┌──────────────────┐
    │  webhook_server  │       │    │     main.py      │
    │  (FastAPI)       │       │    │  review <URL>    │
    │  /webhook POST   │       │    └────────┬─────────┘
    └────────┬─────────┘       │             │
             │                 │             │
             └─────────────────┘             │
                               │             │
                               ▼             ▼
                    ┌──────────────────────────┐
                    │      review_agent.py      │
                    │                          │
                    │   while not done:        │
                    │     ask Claude           │
                    │     if tool_use:         │
                    │       execute tool       │
                    │       feed result back   │
                    │     if end_turn: break   │
                    └──────────┬───────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │ agent_tools  │  │   Claude     │  │github_client │
    │              │  │  (Anthropic  │  │              │
    │ Tool schemas │  │    API)      │  │ GitHub REST  │
    │ execute_tool │  │ The brain    │  │     API      │
    └──────────────┘  └──────────────┘  └──────────────┘
```

---

## SLIDE 12 — The Review Output

What gets posted on GitHub:

### Overall Review Comment (top of review)
```markdown
## PR Review: Add user authentication middleware

This PR adds JWT-based authentication middleware to the Express app.

**Verdict**: REQUEST_CHANGES

**Summary of findings**:
- 🔴 Token expiry is not validated (security bug)
- 🟡 No rate limiting on the login endpoint
- 🟢 Good error handling patterns used throughout
```

### Inline Line Comments (on specific lines)
```
auth/middleware.js line 42:
┌─────────────────────────────────────────────────────┐
│ The JWT token is decoded but the `exp` claim is     │
│ never checked. An expired token will be accepted.   │
│                                                     │
│ **Fix:**                                            │
│ ```javascript                                       │
│ if (decoded.exp < Date.now() / 1000) {              │
│   return res.status(401).json({error: 'Token       │
│   expired'});                                       │
│ }                                                   │
│ ```                                                 │
└─────────────────────────────────────────────────────┘
```

---

## SLIDE 13 — Key Concepts Summary

| Concept | What It Is | Why It Matters |
|---------|-----------|---------------|
| **Agentic Loop** | A while loop that calls Claude, executes tools, feeds results back | Lets Claude handle multi-step tasks autonomously |
| **Tool Use** | JSON Schema (what Claude sees) + Python function (what executes) | Gives Claude the ability to take real actions |
| **stop_reason** | `"tool_use"` = keep going, `"end_turn"` = done | Controls the loop |
| **tool_result** | How you send tool outputs back to Claude | Claude reads this to decide next step |
| **System Prompt** | Claude's "job description" — what to do and how | Defines agent behavior |
| **Prompt Caching** | `cache_control: {"type": "ephemeral"}` | 90% cost reduction on repeated calls |
| **Adaptive Thinking** | `thinking: {"type": "adaptive"}` | Better reasoning quality for complex tasks |
| **Webhook** | GitHub calls your server when events happen | Auto-trigger without polling |
| **HMAC Verification** | Cryptographic signature check | Ensures webhook is really from GitHub |
| **BackgroundTasks** | FastAPI runs slow work after responding | Lets us respond in < 1 sec, review in 3 min |

---

## SLIDE 14 — How to Build Your Own Agent

This pattern works for ANY agent — not just PR review.

### Step 1: Define what the agent needs to READ
```python
# What information does the agent need to gather?
# → For PR review: PR details, files, diffs, file content
# → For data analysis agent: database tables, query results
# → For web research agent: web pages, search results
```

### Step 2: Define what the agent can DO
```python
# What actions can the agent take?
# → For PR review: submit_review
# → For data analysis: run_query, generate_chart
# → For web research: search_web, save_report
```

### Step 3: Write the JSON schema for each tool
```python
TOOLS = [
    {"name": "my_tool", "description": "...", "input_schema": {...}},
]
```

### Step 4: Write the Python function for each tool
```python
def execute_tool(tool_name, tool_input) -> str:
    if tool_name == "my_tool":
        return my_tool_function(**tool_input)
```

### Step 5: Write the agentic loop
```python
while True:
    response = client.messages.create(model=..., tools=TOOLS, messages=messages)
    if response.stop_reason == "end_turn": break
    if response.stop_reason == "tool_use":
        # execute tools, append results, loop again
```

### Step 6: Write a good system prompt
```
You are an expert at [domain].
Your process: 1) gather info, 2) analyze, 3) take action.
Use [tool_a] when..., use [tool_b] when...
```

**That's it. Every agent ever built follows this same pattern.**

---

## SLIDE 15 — What You Can Build Next

Using the same pattern from this project:

| Agent | Tools It Would Have |
|-------|-------------------|
| **Code Documentation Agent** | read_file, write_file, list_files, git_diff |
| **Database Query Agent** | run_sql, describe_table, list_tables, export_csv |
| **Customer Support Agent** | search_knowledge_base, lookup_ticket, reply_to_customer |
| **Data Analysis Agent** | query_database, run_python, plot_chart, send_report |
| **DevOps Agent** | check_logs, restart_service, scale_deployment, alert_team |
| **Research Agent** | search_web, fetch_page, save_note, write_report |
| **Test Writing Agent** | read_file, run_tests, write_file, check_coverage |

The GitHub PR Review Agent in this project is the template.
Swap in different tools and a different system prompt → you have a completely different agent.

---

## Project Structure Reference

```
pr-review-agent/
├── config.py          # Component 1: Configuration & secrets
├── github_client.py   # Component 2: GitHub API wrapper
├── agent_tools.py     # Component 3: Tool definitions + executor
├── review_agent.py    # Component 4: THE AGENTIC LOOP ⭐
├── webhook_server.py  # Component 5: Auto-trigger server
├── main.py            # Component 6: CLI (manual trigger)
├── .env               # Your API keys (never commit this)
├── .env.example       # Template for others to copy
├── requirements.txt   # Python dependencies
└── README.md          # Full setup guide
```

**Run it:**
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python main.py test    # verify setup
python main.py review https://github.com/owner/repo/pull/123
```
