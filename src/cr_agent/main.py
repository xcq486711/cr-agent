"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cr_agent.api.routes import health, review, webhook
from cr_agent.storage.database import engine
from cr_agent.storage.models import Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables on startup, clean up on shutdown."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
