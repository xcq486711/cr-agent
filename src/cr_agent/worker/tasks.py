"""arq worker task — executes review asynchronously."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cr_agent.agents import LogicAgent, PerformanceAgent, SecurityAgent, StyleAgent
from cr_agent.core import ReviewOrchestrator
from cr_agent.llm import LLMClient
from cr_agent.storage.database import async_session_factory
from cr_agent.storage.models import Finding, Review

import structlog

logger = structlog.get_logger()


async def run_review(ctx, review_id: str) -> None:
    """
    Execute a review task and persist results.

    Called by arq worker when a job is dequeued.
    """
    review_uid = UUID(review_id)

    async with async_session_factory() as db:
        # Load the review
        review = await db.get(Review, review_uid)
        if not review:
            logger.error("review_not_found", review_id=review_id)
            return

        # Mark as running
        review.status = "running"
        review.started_at = datetime.now(timezone.utc)
        await db.commit()

        if not review.config or "diff_content" not in review.config:
            review.status = "failed"
            review.error = "No diff_content in review config"
            await db.commit()
            return

        diff_text = review.config["diff_content"]

        try:
            # Wire up agents and run
            agents = [
                SecurityAgent(llm=LLMClient()),
                LogicAgent(llm=LLMClient()),
                PerformanceAgent(llm=LLMClient()),
                StyleAgent(llm=LLMClient()),
            ]
            orchestrator = ReviewOrchestrator(agents=agents)
            report = await orchestrator.run(diff_text)

            # Persist findings
            for agent_finding in report.findings:
                db_finding = Finding(
                    review_id=review_uid,
                    file_path=agent_finding.file,
                    line_start=agent_finding.line_start,
                    line_end=agent_finding.line_end,
                    severity=agent_finding.severity,
                    category=agent_finding.category,
                    title=agent_finding.title,
                    description=agent_finding.description,
                    suggestion=agent_finding.suggestion,
                    confidence=agent_finding.confidence,
                )
                db.add(db_finding)

            # Update review
            review.status = "completed"
            review.tokens_in = report.tokens_in
            review.tokens_out = report.tokens_out
            review.cost_usd = report.cost_usd
            review.completed_at = datetime.now(timezone.utc)
            await db.commit()

            logger.info("review_completed", review_id=review_id, findings=report.total_findings)

        except Exception as e:
            logger.error("review_failed", review_id=review_id, error=str(e))
            review.status = "failed"
            review.error = str(e)[:2000]
            review.completed_at = datetime.now(timezone.utc)
            await db.commit()
