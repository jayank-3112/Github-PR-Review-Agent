# GitHub PR Review Agent — Built from Scratch

A complete, educational guide to building an AI-powered GitHub PR Review Agent using Claude.

---

## What This Teaches You

This project is intentionally over-documented so anyone can understand:
1. **What an AI Agent is** — and how it's different from a single API call
2. **The Agentic Loop** — how Claude reasons, uses tools, and decides what to do next
3. **Tool Use** — defining functions Claude can call to interact with the real world
4. **Prompt Caching** — how to make repeated API calls cheap
5. **Webhooks** — how to trigger automation when GitHub events happen
6. **Production patterns** — signature verification, error handling, async processing

---

## Architecture Overview

```
GitHub PR Created
       │
       ▼
[GitHub Webhook] ──POST──► [FastAPI Webhook Server]
                                     │
                           Verifies signature
                           Parses PR event
                                     │
                                     ▼
                          [Review Agent (Claude)]
                          ┌─────────────────────┐
                          │  System Prompt       │
                          │  (cached)            │
                          │                      │
                          │  Agent Loop:         │
                          │  1. Analyze PR info  │
                          │  2. Read file diffs  │
                          │  3. Read file ctx    │
                          │  4. Post comments    │
                          │  5. Submit review    │
                          └─────────────────────┘
                                     │
                          Calls tools ◄──► GitHub API
                                     │
                                     ▼
                          PR gets review comments
```

---

## Key Concepts

### What is an "Agent"?
A regular LLM call = you ask, Claude answers, done.
An Agent = Claude can take actions, see results, and decide what to do next — in a loop.

The loop looks like:
```
User prompt → Claude thinks → Claude calls a tool → You run the tool →
Tool result back to Claude → Claude thinks again → ... → Claude says "done"
```

### What are "Tools"?
Tools are functions you define that Claude can call. You describe them in JSON Schema format.
Claude sees the description and decides when/how to use them. You execute them and send results back.

Example: You define `get_pr_files(repo, pr_number)`. Claude decides to call it when it needs
to see what files changed. You make the GitHub API call and return the data. Claude reads it and continues.

### Prompt Caching
Claude charges per token read. If you always include the same large system prompt,
you pay full price every time. With caching, the first call pays 1.25x, all subsequent
calls pay 0.1x (90% savings). We cache our system prompt since it never changes.

---

## Project Structure

```
pr-review-agent/
├── .env.example          ← Environment variables (never commit real values!)
├── requirements.txt      ← Python dependencies
├── config.py             ← Load and validate configuration
├── github_client.py      ← All GitHub API interactions
├── agent_tools.py        ← Tool DEFINITIONS (JSON schemas Claude sees)
├── review_agent.py       ← The core Claude agent + agentic loop
├── webhook_server.py     ← FastAPI server receiving GitHub webhooks
└── main.py               ← Entry point: CLI review or start server
```

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your keys
```

### 3. Get your API keys

**Anthropic API Key:**
- Go to https://console.anthropic.com
- Settings → API Keys → Create Key

**GitHub Personal Access Token:**
- Go to GitHub Settings → Developer settings → Personal access tokens
- Tokens (classic) → Generate new token
- Required scopes: `repo` (full control of repos) or at minimum:
  - `pull_requests:write` (to post reviews)
  - `contents:read` (to read file content)

**GitHub Webhook Secret:**
- A random string you choose. GitHub signs webhook payloads with it.
- Generate one: `python -c "import secrets; print(secrets.token_hex(32))"`

---

## Running the Agent

### Option A: Review a specific PR manually

```bash
python main.py review https://github.com/owner/repo/pull/123
```

### Option B: Auto-review on every new PR (webhook server)

1. Start the server:
   ```bash
   python main.py server --port 8000
   ```

2. Expose it to the internet (for local dev):
   ```bash
   # Install ngrok: https://ngrok.com
   ngrok http 8000
   ```

3. Set up GitHub webhook:
   - Go to your GitHub repo → Settings → Webhooks → Add webhook
   - Payload URL: `https://your-ngrok-url.ngrok.io/webhook`
   - Content type: `application/json`
   - Secret: your WEBHOOK_SECRET from .env
   - Events: Select "Pull requests"
   - Active: ✓

Now every PR creates an automatic review!

---

## How the Agent Works (Step by Step)

1. **PR arrives** — Either via webhook or CLI command
2. **Agent starts** — Claude is given a system prompt describing its role
3. **Claude decides** — It calls `get_pr_details` to understand the PR
4. **Claude reads diffs** — It calls `get_pr_files` to see what changed
5. **Claude analyzes** — It may call `get_file_content` for more context
6. **Claude comments** — It calls `add_review_comment` for specific issues
7. **Claude submits** — It calls `submit_review` with an overall verdict
8. **Loop ends** — Claude returns "end_turn" when done

---

## Extending This Agent

Want to build a different kind of agent? This same pattern applies to:
- **Security audit agent** — Review code for vulnerabilities
- **Documentation agent** — Check if PRs have adequate documentation  
- **Test coverage agent** — Verify tests exist for new code
- **Changelog agent** — Auto-generate changelogs from PRs
- **Issue triage agent** — Categorize and assign GitHub issues

The pattern is always:
1. Define tools that interact with your API/system
2. Write a system prompt describing the agent's purpose
3. Implement the agentic loop
4. Add a trigger mechanism (webhook, scheduler, CLI)
