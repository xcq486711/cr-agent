"""
GitHub webhook handler — receives PR events, triggers reviews, posts results.

Verifies HMAC-SHA256 signature before processing.
"""

import hashlib
import hmac
import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from cr_agent.config import settings
from cr_agent.integrations.github import GitHubClient
from cr_agent.integrations.github_auth import get_cached_installation_token

import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1/webhook", tags=["webhook"])


def verify_signature(payload_body: bytes, signature_header: str) -> bool:
    """Verify GitHub HMAC-SHA256 webhook signature."""
    if not settings.github_webhook_secret:
        logger.warning("webhook_no_secret_configured")
        return True  # Allow in dev mode without secret

    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        payload_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def parse_repo_from_body(body: dict) -> tuple[str, str] | None:
    """Extract owner/repo from webhook payload."""
    repo = body.get("repository", {})
    full_name = repo.get("full_name", "")
    if "/" in full_name:
        owner, repo_name = full_name.split("/", 1)
        return owner, repo_name
    return None


async def _handle_pr_event(body: dict):
    """Process a pull_request webhook event."""
    action = body.get("action", "")
    pr = body.get("pull_request", {})
    installation = body.get("installation", {})

    if action not in ("opened", "synchronize"):
        logger.info("webhook_skipped_action", action=action)
        return

    pr_number = pr.get("number")
    head_sha = pr.get("head", {}).get("sha", "")
    repo_info = parse_repo_from_body(body)
    installation_id = installation.get("id")

    if not all([pr_number, head_sha, repo_info, installation_id]):
        logger.error("webhook_missing_fields")
        return

    owner, repo = repo_info
    logger.info("webhook_pr_event", action=action, repo=f"{owner}/{repo}", pr=pr_number)

    try:
        gh = GitHubClient(installation_id=installation_id)

        # Pull the diff
        diff_text = await gh.get_pr_diff(owner, repo, pr_number)
        if not diff_text.strip():
            logger.info("webhook_empty_diff", pr=pr_number)
            return

        # Run review (same pipeline as CLI)
        from cr_agent.agents import LogicAgent, PerformanceAgent, SecurityAgent, StyleAgent
        from cr_agent.core import ReviewOrchestrator
        from cr_agent.llm import LLMClient

        agents = [
            SecurityAgent(llm=LLMClient()),
            LogicAgent(llm=LLMClient()),
            PerformanceAgent(llm=LLMClient()),
            StyleAgent(llm=LLMClient()),
        ]
        orchestrator = ReviewOrchestrator(agents=agents)
        report = await orchestrator.run(diff_text)

        if not report.findings:
            logger.info("webhook_no_findings", pr=pr_number)
            return

        # Convert findings to inline comments
        findings_dicts = [
            {
                "file": f.file, "line_start": f.line_start, "line_end": f.line_end,
                "severity": f.severity, "category": f.category,
                "title": f.title, "description": f.description,
                "suggestion": f.suggestion, "confidence": f.confidence,
            }
            for f in report.findings
        ]
        comments = GitHubClient.findings_to_comments(findings_dicts)

        # Post review with inline comments
        review_body = (
            f"## CR-Agent Review\n\n"
            f"**{report.total_findings} 发现问题** | "
            f"耗时 {report.duration_ms}ms | "
            f"成本 ${report.cost_usd:.4f}\n\n"
        )
        for severity, count in report.findings_by_severity.items():
            review_body += f"- {severity}: {count}\n"

        await gh.create_review(
            owner, repo, pr_number, head_sha, review_body, comments, event="COMMENT"
        )

        logger.info("webhook_review_posted", pr=pr_number, findings=report.total_findings)

    except Exception as e:
        logger.error("webhook_review_failed", pr=pr_number, error=str(e))


@router.post("/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive GitHub webhook events."""
    # Read raw payload for signature verification
    payload_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_signature(payload_body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event_type = request.headers.get("X-GitHub-Event", "")
    body = await request.json()

    logger.info("webhook_received", event=event_type, delivery=request.headers.get("X-GitHub-Delivery"))

    if event_type == "pull_request":
        background_tasks.add_task(_handle_pr_event, body)
    elif event_type == "ping":
        logger.info("webhook_ping", zen=body.get("zen", ""))
    else:
        logger.info("webhook_ignored_event", event=event_type)

    return {"status": "ok"}
