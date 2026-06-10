"""FastAPI dependencies — DB session, auth."""

import hashlib
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cr_agent.storage.database import get_db
from cr_agent.storage.models import Tenant


async def get_api_key(request: Request) -> str:
    """Extract API key from X-API-Key header, or use dev key."""
    api_key = request.headers.get("X-API-Key", "")
    if not api_key:
        # In dev mode without auth, return a default key
        api_key = "dev-key"
    return api_key


async def get_current_tenant(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Tenant | None:
    """Resolve tenant from API key. Returns None in dev mode."""
    api_key = await get_api_key(request)
    if api_key == "dev-key":
        return None

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    result = await db.execute(select(Tenant).where(Tenant.api_key_hash == key_hash))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return tenant
