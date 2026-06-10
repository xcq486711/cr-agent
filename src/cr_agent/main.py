"""FastAPI application entry point."""

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cr_agent.api.routes import health, review, webhook
from cr_agent.storage.database import async_session_factory, engine
from cr_agent.storage.models import Base, Finding, Review


async def _seed_demo_data():
    """Insert demo reviews if database is empty (for dashboard testing)."""
    async with async_session_factory() as db:
        from sqlalchemy import select, func
        count = await db.scalar(select(func.count()).select_from(Review))
        if count and count > 0:
            return

        demo_reviews = [
            Review(id=uuid.uuid4(), repo_url="elderly-care-backend", status="completed",
                   tokens_in=2856, tokens_out=910, cost_usd=0.000655),
            Review(id=uuid.uuid4(), repo_url="elderly-care-backend", status="completed",
                   tokens_in=4120, tokens_out=1203, cost_usd=0.001200),
            Review(id=uuid.uuid4(), repo_url="cr-agent", status="completed",
                   tokens_in=1830, tokens_out=520, cost_usd=0.000340),
            Review(id=uuid.uuid4(), repo_url="elderly-care-backend", status="completed",
                   tokens_in=6200, tokens_out=1805, cost_usd=0.001580),
            Review(id=uuid.uuid4(), repo_url="cr-agent", status="failed",
                   tokens_in=0, tokens_out=0, cost_usd=0),
        ]
        for r in demo_reviews:
            db.add(r)
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables + seed demo data on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _seed_demo_data()
    yield
    await engine.dispose()


app = FastAPI(
    title="CR-Agent",
    description="Multi-agent code review system",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for web dashboard (Phase 4)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(health.router)
app.include_router(review.router)
app.include_router(webhook.router)
