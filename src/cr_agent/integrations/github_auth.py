"""
GitHub App authentication — JWT generation + installation token exchange.

Flow:
  1. Generate JWT signed with the App's private key
  2. Use JWT to get an installation access token (valid 1 hour)
  3. Use installation token for all GitHub API calls
"""

import time

import httpx
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from cr_agent.config import settings


def _make_jwt(app_id: str, private_key_pem: str) -> str:
    """Generate a JWT for GitHub App authentication."""
    import jwt  # python-jose

    now = int(time.time())
    payload = {
        "iat": now - 60,       # issued at (60s clock drift tolerance)
        "exp": now + 600,      # expires in 10 minutes (GitHub max)
        "iss": app_id,
    }
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode(), password=None, backend=default_backend()
    )
    return jwt.encode(payload, private_key, algorithm="RS256")


async def get_installation_token(installation_id: int) -> str:
    """
    Exchange JWT for an installation access token.

    Returns a token valid for 1 hour. Cache this and reuse until near expiry.
    """
    jwt_token = _make_jwt(settings.github_app_id, settings.github_app_private_key)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["token"]


# In-memory cache: installation_id → (token, expires_at)
_token_cache: dict[int, tuple[str, float]] = {}


async def get_cached_installation_token(installation_id: int) -> str:
    """Get installation token, using cache if still valid (5 min buffer)."""
    if installation_id in _token_cache:
        token, expires_at = _token_cache[installation_id]
        if time.time() + 300 < expires_at:  # 5 min buffer
            return token

    token = await get_installation_token(installation_id)
    # GitHub installation tokens expire in 1 hour
    _token_cache[installation_id] = (token, time.time() + 3600)
    return token
