"""Health check endpoints."""

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cr_agent.llm import LLMClient
from cr_agent.storage.database import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness():
    """Kubernetes liveness probe — always OK if the process is alive."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness():
    """Kubernetes readiness probe — check DB and LLM reachability."""
    errors = []

    # Check DB
    try:
        async for session in get_db():
            await session.execute(text("SELECT 1"))
            break
    except Exception as e:
        errors.append(f"database: {e}")

    # Check LLM (lightweight — just verify connection config)
    try:
        client = LLMClient()
        if not client.api_key:
            errors.append("llm: no API key configured")
    except Exception as e:
        errors.append(f"llm: {e}")

    if errors:
        return {"status": "not ready", "errors": errors}

    return {"status": "ready"}
