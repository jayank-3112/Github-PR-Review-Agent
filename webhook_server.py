"""
webhook_server.py — GitHub Webhook Server
==========================================
WHAT THIS FILE DOES:
    Runs a FastAPI HTTP server that GitHub sends events to when PRs are created.
    When a PR is opened/reopened/updated, this server triggers the review agent.

HOW GITHUB WEBHOOKS WORK:
    1. You tell GitHub: "Send a POST request to https://myserver.com/webhook
       whenever something happens in this repo"
    2. GitHub sends the POST request with:
       - A JSON body describing what happened (the "payload")
       - An HMAC-SHA256 signature in the X-Hub-Signature-256 header
    3. Your server verifies the signature, parses the payload, and acts on it

WHY SIGNATURE VERIFICATION MATTERS:
    Anyone who knows your webhook URL could send fake events.
    GitHub signs every payload with your webhook secret using HMAC-SHA256.
    By verifying the signature, we ensure only real GitHub events trigger reviews.

    HMAC-SHA256 works like this:
    - You and GitHub both know the secret
    - GitHub: signature = HMAC-SHA256(payload, secret)
    - You: expected = HMAC-SHA256(received_payload, secret)
    - If signature == expected → legitimate request from GitHub
    - CRITICAL: Compare signatures using hmac.compare_digest() not ==
      to prevent timing attacks (attacks that measure how long comparison takes)

ASYNC vs SYNC:
    FastAPI is async. When a webhook arrives, we want to:
    1. Quickly verify the signature and respond 200 OK to GitHub
    2. Run the (potentially slow) review in the background

    If we ran the review synchronously, GitHub's webhook might time out waiting.
    Using BackgroundTasks lets us acknowledge the webhook immediately and
    process it in the background.

ARCHITECTURE PATTERN: "Fire and forget with acknowledgment"
    Request arrives → Verify → Acknowledge (200 OK) → Process in background
    This is the standard pattern for webhooks that trigger slow operations.
"""

import hmac
import hashlib
import asyncio
import json
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from config import config
from review_agent import review_pr
from github_client import parse_pr_url


# FastAPI application instance.
# FastAPI automatically generates:
# - Interactive API docs at /docs (Swagger UI)
# - Alternative docs at /redoc
app = FastAPI(
    title="PR Review Agent Webhook Server",
    description="Receives GitHub webhooks and triggers AI-powered PR reviews",
    version="1.0.0",
)


def verify_github_signature(payload_body: bytes, signature_header: str) -> bool:
    """
    Verify that a webhook payload came from GitHub.

    GitHub signs every webhook payload using HMAC-SHA256 with your webhook secret.
    The signature is sent in the X-Hub-Signature-256 header as "sha256=<hex_digest>".

    Args:
        payload_body: The raw request body bytes (before any parsing)
        signature_header: Value of X-Hub-Signature-256 header

    Returns:
        True if signature is valid, False if invalid or missing

    SECURITY NOTE:
        - Always verify webhooks in production
        - Never compare signatures with == (use hmac.compare_digest)
        - Process the raw bytes BEFORE any parsing/decoding
    """
    if not signature_header:
        return False

    # The signature format is "sha256=<hex_digest>"
    if not signature_header.startswith("sha256="):
        return False

    # Extract just the hex digest from the header
    received_signature = signature_header[7:]  # Remove "sha256=" prefix

    # Compute what we expect the signature to be
    # HMAC-SHA256(secret=webhook_secret, message=payload_body)
    expected_signature = hmac.new(
        key=config.github_webhook_secret.encode("utf-8"),
        msg=payload_body,
        digestmod=hashlib.sha256
    ).hexdigest()

    # Compare signatures using constant-time comparison.
    # Regular == would return early on mismatch, revealing info to timing attacks.
    # hmac.compare_digest always takes the same time regardless of where they differ.
    return hmac.compare_digest(received_signature, expected_signature)


async def process_pr_review(owner: str, repo: str, pr_number: int, action: str):
    """
    Run the PR review in the background.

    This is called via FastAPI's BackgroundTasks mechanism, which runs it
    after the HTTP response has been sent to GitHub.

    Running in background means:
    - GitHub gets an immediate 200 response (won't retry thinking we failed)
    - The review can take as long as needed (no HTTP timeout)
    - Multiple webhooks can be processed concurrently
    """
    print(f"\n🔔 Background review started: {owner}/{repo}#{pr_number} ({action})")
    try:
        result = review_pr(owner=owner, repo=repo, pr_number=pr_number)
        if result["success"]:
            print(f"✅ Review complete: {result['total_tool_calls']} tool calls")
        else:
            print(f"❌ Review failed: {result.get('error', 'Unknown error')}")
    except Exception as e:
        # Log the error but don't let it crash the server
        print(f"❌ Unexpected error during review: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


@app.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Handle incoming GitHub webhook events.

    This endpoint is registered in your GitHub repo settings.
    GitHub sends POST requests here for configured events (pull_request).

    FLOW:
    1. Read raw body (before parsing — needed for signature verification)
    2. Verify HMAC-SHA256 signature
    3. Parse JSON payload
    4. Check if it's a PR event we care about (opened/reopened/synchronize)
    5. Enqueue background review task
    6. Return 200 immediately

    EVENTS WE CARE ABOUT:
    - opened: New PR created
    - reopened: Closed PR reopened
    - synchronize: New commits pushed to an existing PR
    """
    # ── 1. Read raw body ─────────────────────────────────────────
    # IMPORTANT: Read the body as raw bytes BEFORE anything else.
    # JSON parsing can change the byte representation, breaking signature verification.
    raw_body = await request.body()

    # ── 2. Verify signature ───────────────────────────────────────
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_github_signature(raw_body, signature):
        # Return 403 Forbidden for invalid signatures.
        # Don't reveal WHY it failed (timing attack prevention).
        print(f"⚠️  Webhook signature verification failed. Headers: {dict(request.headers)}")
        raise HTTPException(
            status_code=403,
            detail="Invalid signature"
        )

    # ── 3. Parse the event ────────────────────────────────────────
    # GitHub-Event header tells us what type of event this is
    event_type = request.headers.get("X-GitHub-Event", "unknown")
    delivery_id = request.headers.get("X-GitHub-Delivery", "unknown")

    print(f"\n📨 Webhook received: {event_type} (delivery: {delivery_id})")

    # ── 4. Handle the event ───────────────────────────────────────
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as e:
        print(f"⚠️  Failed to parse webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # We only care about pull_request events
    if event_type != "pull_request":
        print(f"ℹ️  Ignoring event type: {event_type}")
        return JSONResponse({"status": "ignored", "reason": f"Not a pull_request event"})

    # Extract PR info from the payload
    action = payload.get("action", "unknown")
    pr = payload.get("pull_request", {})
    pr_number = pr.get("number")
    repo_info = payload.get("repository", {})
    owner = repo_info.get("owner", {}).get("login")
    repo = repo_info.get("name")

    # ── 5. Filter to relevant actions ────────────────────────────
    # These are the actions that warrant a new review:
    # - opened: First time this PR is created
    # - reopened: PR was closed then reopened
    # - synchronize: New commits pushed to the PR branch
    # We skip: labeled, unlabeled, assigned, review_requested, etc.
    review_actions = {"opened", "reopened", "synchronize"}

    if action not in review_actions:
        print(f"ℹ️  Ignoring PR action: {action}")
        return JSONResponse({"status": "ignored", "reason": f"Action '{action}' doesn't trigger review"})

    # Validate we have all required info
    if not all([pr_number, owner, repo]):
        print(f"⚠️  Missing required PR info in payload")
        raise HTTPException(status_code=422, detail="Missing PR info in payload")

    # Skip draft PRs (optional — remove this block if you want to review drafts)
    if pr.get("draft"):
        print(f"ℹ️  Skipping draft PR #{pr_number}")
        return JSONResponse({"status": "ignored", "reason": "Draft PR"})

    pr_title = pr.get("title", "Unknown")
    print(f"✅ Triggering review for: {owner}/{repo}#{pr_number} '{pr_title}' (action: {action})")

    # ── 6. Enqueue background review ─────────────────────────────
    # background_tasks.add_task() schedules the function to run AFTER
    # this endpoint returns. GitHub gets our 200 OK immediately.
    background_tasks.add_task(
        process_pr_review,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        action=action,
    )

    # ── 7. Respond immediately ────────────────────────────────────
    # GitHub expects a 2xx response within 10 seconds.
    # If it doesn't get one, it marks the delivery as failed and retries.
    # Since review takes much longer, we always respond 202 Accepted immediately.
    return JSONResponse({
        "status": "accepted",
        "message": f"Review started for {owner}/{repo}#{pr_number}",
    }, status_code=202)


@app.get("/health")
async def health_check():
    """
    Health check endpoint. Useful for monitoring and deployment validation.

    Returns 200 if the server is running and all required config is present.
    """
    return {
        "status": "healthy",
        "model": config.model,
        "github_token_configured": bool(config.github_token),
        "anthropic_key_configured": bool(config.anthropic_api_key),
    }


@app.get("/")
async def root():
    """Root endpoint with basic info about the service."""
    return {
        "service": "PR Review Agent",
        "webhook_endpoint": "/webhook",
        "health_endpoint": "/health",
        "docs": "/docs",
    }


def start_server(port: int = None):
    """Start the webhook server."""
    import uvicorn
    server_port = port or config.port
    print(f"\n🚀 Starting PR Review Agent webhook server on port {server_port}")
    print(f"   Webhook URL: http://localhost:{server_port}/webhook")
    print(f"   Health check: http://localhost:{server_port}/health")
    print(f"   API docs: http://localhost:{server_port}/docs")
    print(f"\n   For local testing, use ngrok: ngrok http {server_port}")
    print(f"   Then set webhook URL in GitHub: https://<ngrok-url>/webhook\n")
    uvicorn.run(app, host="0.0.0.0", port=server_port, log_level="info")
