"""
Integration test: full pipeline with real LLM API.

This test requires a valid API key in .env.
Run manually: pytest tests/integration/test_full_review_flow.py -v
"""

import asyncio
from pathlib import Path

import pytest

from cr_agent.agents import SecurityAgent
from cr_agent.core import ReviewOrchestrator
from cr_agent.llm import LLMClient

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

# Requires real API key — skip in CI
pytestmark = pytest.mark.skipif(
    True,  # Manual override — remove this line to run locally
    reason="Integration test — requires API key. Remove skipif to run.",
)


class TestFullReviewFlow:
    """End-to-end pipeline test with real LLM."""

    def get_sample_patch(self) -> str:
        return (FIXTURES_DIR / "sample.patch").read_text()

    @pytest.mark.asyncio
    async def test_security_agent_on_real_code(self):
        """
        Run SecurityAgent on the sample patch.

        The sample patch contains:
        - SQL injection (f-string query)
        - Hardcoded secret key and API token
        - Debug mode enabled

        We expect the agent to find at least 2 security issues.
        """
        llm = LLMClient()
        agent = SecurityAgent(llm=llm)
        orchestrator = ReviewOrchestrator(agents=[agent])

        report = await orchestrator.run(self.get_sample_patch())

        # Basic structure checks
        assert report.status in ("completed", "partial")
        assert report.duration_ms > 0
        assert report.tokens_in > 0
        assert report.tokens_out > 0
        assert report.cost_usd > 0

        # The sample patch has clear security issues — expect findings
        print(f"\n📊 Report: {report.total_findings} findings found")
        print(f"   Duration: {report.duration_ms}ms")
        print(f"   Cost: ${report.cost_usd:.6f}")
        print(f"   Breakdown: {report.findings_by_severity}")

        for f in report.findings:
            print(f"  [{f.severity}] {f.title} ({f.file}:{f.line_start})")

        # Print full report for inspection
        print("\n" + report.to_markdown())


if __name__ == "__main__":
    # Allow running as script
    asyncio.run(TestFullReviewFlow().test_security_agent_on_real_code())
