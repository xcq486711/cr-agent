"""Review API — submit and query reviews."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cr_agent.core import ReviewOrchestrator
from cr_agent.llm import LLMClient
from cr_agent.agents import LogicAgent, PerformanceAgent, SecurityAgent, StyleAgent
from cr_agent.storage.database import get_db
from cr_agent.storage.models import Finding, Review
from cr_agent.api.deps import get_current_tenant
from cr_agent.storage.models import Tenant

import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])


class ReviewRequest(BaseModel):
    repo_url: str | None = None
    pr_number: int | None = None
    diff_content: str = Field(..., min_length=1, max_length=5_000_000)
    config_override: dict | None = None


class ReviewResponse(BaseModel):
    review_id: str
    status_url: str


class ReviewStatus(BaseModel):
    review_id: str
    status: str
    progress: dict | None = None
    error: str | None = None
    cost: dict | None = None
    findings: list[dict] | None = None


async def _execute_review(review_id: uuid.UUID, diff_content: str):
    """Run review in background and persist results."""
    from cr_agent.storage.database import async_session_factory

    async with async_session_factory() as db:
        review = await db.get(Review, review_id)
        if not review:
            return

        review.status = "running"
        review.started_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            agents = [
                SecurityAgent(llm=LLMClient()),
                LogicAgent(llm=LLMClient()),
                PerformanceAgent(llm=LLMClient()),
                StyleAgent(llm=LLMClient()),
            ]
            orchestrator = ReviewOrchestrator(agents=agents)
            report = await orchestrator.run(diff_content)

            for f in report.findings:
                db_finding = Finding(
                    review_id=review_id,
                    file_path=f.file, line_start=f.line_start, line_end=f.line_end,
                    severity=f.severity, category=f.category,
                    title=f.title, description=f.description,
                    suggestion=f.suggestion, confidence=f.confidence,
                )
                db.add(db_finding)

            review.status = "completed"
            review.tokens_in = report.tokens_in
            review.tokens_out = report.tokens_out
            review.cost_usd = report.cost_usd
            review.completed_at = datetime.now(timezone.utc)
            await db.commit()

            logger.info("review_completed", review_id=str(review_id), findings=report.total_findings)

        except Exception as e:
            logger.error("review_failed", review_id=str(review_id), error=str(e))
            review.status = "failed"
            review.error = str(e)[:2000]
            review.completed_at = datetime.now(timezone.utc)
            await db.commit()


@router.post("", status_code=202, response_model=ReviewResponse)
async def submit_review(
    body: ReviewRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant | None = Depends(get_current_tenant),
):
    """Submit a diff for review. Returns immediately with a review ID."""
    review_id = uuid.uuid4()

    review = Review(
        id=review_id,
        tenant_id=tenant.id if tenant else None,
        repo_url=body.repo_url,
        pr_number=body.pr_number,
        status="queued",
        config={"diff_content": body.diff_content, **(body.config_override or {})},
    )
    db.add(review)
    await db.commit()

    # Run review in background
    background_tasks.add_task(_execute_review, review_id, body.diff_content)

    return ReviewResponse(
        review_id=str(review_id),
        status_url=f"/api/v1/reviews/{review_id}",
    )


@router.get("/{review_id}", response_model=ReviewStatus)
async def get_review_status(
    review_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the status and results of a review."""
    try:
        uid = uuid.UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid review ID")

    result = await db.execute(
        select(Review).where(Review.id == uid).options(selectinload(Review.findings))
    )
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    findings_list = None
    if review.status == "completed":
        findings_list = [
            {
                "file": f.file_path, "line_start": f.line_start, "line_end": f.line_end,
                "severity": f.severity, "category": f.category,
                "title": f.title, "description": f.description,
                "suggestion": f.suggestion, "confidence": f.confidence,
            }
            for f in review.findings
        ]

    return ReviewStatus(
        review_id=str(review_id),
        status=review.status,
        error=review.error,
        cost={
            "tokens_in": review.tokens_in,
            "tokens_out": review.tokens_out,
            "cost_usd": review.cost_usd,
        },
        findings=findings_list,
    )
