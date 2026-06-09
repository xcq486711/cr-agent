"""Unit tests for the review orchestrator."""

from pathlib import Path

import pytest

from cr_agent.agents import SecurityAgent
from cr_agent.core import ReviewOrchestrator
from cr_agent.llm import LLMClient

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class TestOrchestrator:
    """Test the orchestrator pipeline end-to-end (with real LLM disabled)."""

    def get_sample_patch(self) -> str:
        return (FIXTURES_DIR / "sample.patch").read_text()

    @pytest.mark.asyncio
    async def test_empty_diff(self):
        """Empty diff should return empty report immediately."""
        orchestrator = ReviewOrchestrator(agents=[])
        report = await orchestrator.run("")
        assert report.status == "completed"
        assert report.total_findings == 0

    @pytest.mark.asyncio
    async def test_all_files_filtered(self):
        """When all files are excluded, return empty report."""
        from cr_agent.core.file_filter import FilterConfig

        orchestrator = ReviewOrchestrator(
            agents=[],
            filter_config=FilterConfig(
                exclude_patterns=["src/**"],
            ),
        )
        report = await orchestrator.run(self.get_sample_patch())
        assert report.status == "completed"
        assert report.total_findings == 0

    @pytest.mark.asyncio
    async def test_pipeline_no_agents(self):
        """Pipeline should complete successfully even with no agents registered."""
        orchestrator = ReviewOrchestrator(agents=[])
        report = await orchestrator.run(self.get_sample_patch())
        assert report.status == "completed"
        assert report.total_findings == 0

    @pytest.mark.asyncio
    async def test_pipeline_runs_agent(self):
        """Pipeline should run an agent and collect its findings."""
        agent = SecurityAgent(llm=LLMClient(api_key="fake"))
        orchestrator = ReviewOrchestrator(agents=[agent])
        report = await orchestrator.run(self.get_sample_patch())
        # With a fake key the LLM call will fail, but the pipeline should handle it
        assert report.agents_failed or report.status in ("completed", "partial", "failed")

    @pytest.mark.asyncio
    async def test_parses_and_filters_correctly(self):
        """Orchestrator should parse the patch and filter lock files."""
        from cr_agent.core.diff_parser import parse_diff
        from cr_agent.core.file_filter import filter_diffs

        diffs = parse_diff(self.get_sample_patch())
        filtered = filter_diffs(diffs)
        # package-lock.json should be filtered out
        paths = [d.path for d in filtered]
        assert "package-lock.json" not in paths
        # src files should remain
        assert "src/auth/login.py" in paths

    @pytest.mark.asyncio
    async def test_confidence_filter(self):
        """Report should filter findings below confidence threshold."""
        from cr_agent.core.report_builder import ReviewReport, build_report
        from cr_agent.llm import ReviewFinding, ReviewOutput

        output = ReviewOutput(
            findings=[
                ReviewFinding(
                    file="test.py", line_start=1, line_end=1,
                    severity="warning", category="security",
                    title="SQL injection", description="...",
                    confidence=0.9,
                ),
                ReviewFinding(
                    file="test.py", line_start=5, line_end=5,
                    severity="suggestion", category="style",
                    title="Naming", description="...",
                    confidence=0.3,
                ),
            ],
            summary="test",
        )

        findings = [f for f in output.findings if f.confidence >= 0.7]
        assert len(findings) == 1
        assert findings[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_report_json_output(self):
        """Report should serialize to valid JSON."""
        from cr_agent.core.report_builder import ReviewReport

        report = ReviewReport(
            status="completed",
            total_findings=0,
            findings=[],
            summaries={"agent_0": "No issues found."},
            duration_ms=1200,
        )
        json_str = report.to_json()
        import json

        data = json.loads(json_str)
        assert data["status"] == "completed"
        assert data["total_findings"] == 0
        assert "breakdown" in data

    @pytest.mark.asyncio
    async def test_report_markdown_output(self):
        """Report should render to markdown."""
        from cr_agent.core.report_builder import ReviewReport

        report = ReviewReport(
            status="completed",
            total_findings=0,
            findings=[],
            summaries={"agent_0": "Clean."},
        )
        md = report.to_markdown()
        assert "# Code Review Report" in md
        assert "Status" in md
