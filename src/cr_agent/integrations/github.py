"""
GitHub API client — pull PR data, post review comments.

Uses GitHub App installation tokens (not personal access tokens).
"""

import httpx
import structlog

from .github_auth import get_cached_installation_token

logger = structlog.get_logger()

GITHUB_API = "https://api.github.com"
ACCEPT_HEADER = "application/vnd.github+json"
API_VERSION = "2022-11-28"


class GitHubClient:
    """GitHub API client authenticated as a GitHub App installation."""

    def __init__(self, installation_id: int):
        self.installation_id = installation_id
        self._token: str | None = None

    async def _headers(self) -> dict:
        if not self._token:
            self._token = await get_cached_installation_token(self.installation_id)
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": ACCEPT_HEADER,
            "X-GitHub-Api-Version": API_VERSION,
        }

    async def _get(self, path: str) -> dict | str:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GITHUB_API}{path}", headers=await self._headers()
            )
            response.raise_for_status()

            # Some endpoints return raw diff text, others JSON
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                return response.json()
            return response.text

    async def _post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GITHUB_API}{path}",
                headers=await self._headers(),
                json=body,
            )
            response.raise_for_status()
            return response.json()

    # ── PR operations ──

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Pull the raw diff for a PR."""
        path = f"/repos/{owner}/{repo}/pulls/{pr_number}"
        response = await self._get(path)
        if isinstance(response, dict):
            diff_url = response.get("diff_url", "")
            return await self._get_raw(diff_url)
        return ""

    async def get_pr_info(self, owner: str, repo: str, pr_number: int) -> dict:
        """Get PR metadata: title, head.sha, base.sha, etc."""
        path = f"/repos/{owner}/{repo}/pulls/{pr_number}"
        response = await self._get(path)
        if isinstance(response, dict):
            return response
        return {}

    async def _get_raw(self, url: str) -> str:
        """Fetch raw content from a URL (e.g. diff_url)."""
        async with httpx.AsyncClient() as client:
            headers = await self._headers()
            # GitHub diff URLs require the diff media type
            headers["Accept"] = "application/vnd.github.v3.diff"
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text

    # ── Review operations ──

    async def create_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_id: str,
        body: str,
        comments: list[dict],
        event: str = "COMMENT",
    ) -> dict:
        """
        Create a PR review with inline comments.

        comments: list of {path, line, body, side: "RIGHT"}
        event: "COMMENT" | "APPROVE" | "REQUEST_CHANGES"
        """
        return await self._post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            {
                "commit_id": commit_id,
                "body": body,
                "event": event,
                "comments": comments,
            },
        )

    async def create_check_run(
        self,
        owner: str,
        repo: str,
        head_sha: str,
        name: str,
        conclusion: str,
        summary: str,
    ) -> dict:
        """Create a GitHub Check Run with the review summary."""
        return await self._post(
            f"/repos/{owner}/{repo}/check-runs",
            {
                "name": name,
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": conclusion,
                "output": {
                    "title": name,
                    "summary": summary,
                },
            },
        )

    @staticmethod
    def findings_to_comments(
        findings: list[dict], max_comments: int = 15
    ) -> list[dict]:
        """Convert review findings to GitHub PR review comments."""
        comments = []
        for f in findings[:max_comments]:
            severity = f.get("severity", "suggestion")
            category = f.get("category", "")
            comments.append({
                "path": f["file"],
                "line": f.get("line_end", f.get("line_start", 1)),
                "side": "RIGHT",
                "body": (
                    f"**{severity.upper()}** [{category}] {f['title']}\n\n"
                    f"{f['description']}\n\n"
                    + (f"**Fix:** {f['suggestion']}" if f.get("suggestion") else "")
                ),
            })
        return comments
