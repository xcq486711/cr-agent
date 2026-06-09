"""
Review orchestrator — the main pipeline that ties everything together.

Phase 1 flow (serial):
  diff text → parse → filter → build contexts → agent.review() → dedup → report
Phase 2+: parallel fan-out, verification phase, learned rules injection
"""

import asyncio
import time

import structlog

from cr_agent.agents.base import BaseReviewAgent
from cr_agent.llm import CostTracker, ReviewFinding, ReviewOutput

from .context_builder import build_contexts
from .diff_parser import parse_diff
from .file_filter import FilterConfig, filter_diffs
from .report_builder import ReviewReport, build_report

logger = structlog.get_logger()


class ReviewOrchestrator:
    """
    Main orchestrator for code review pipeline.

    Usage:
        orchestrator = ReviewOrchestrator(agents=[SecurityAgent(llm)])
        report = await orchestrator.run(diff_text)

    Phase 1: serial execution, simplified context, no verification phase.
    """

    def __init__(
        self,
        agents: list[BaseReviewAgent],
        filter_config: FilterConfig | None = None,
        cost_tracker: CostTracker | None = None,
        confidence_threshold: float = 0.7,
    ):
        self.agents = agents
        self.filter_config = filter_config or FilterConfig()
        self.cost_tracker = cost_tracker or CostTracker()
        self.confidence_threshold = confidence_threshold

    async def run(self, diff_text: str) -> ReviewReport:
        """
        Execute the full review pipeline on a diff.

        Returns a ReviewReport with findings, summaries, and stats.
        """
        start_time = time.monotonic()

        # Step 1: Parse
        logger.info("orchestrator.parse.start")
        all_diffs = parse_diff(diff_text)
        if not all_diffs:
            logger.info("orchestrator.parse.empty")
            return ReviewReport(
                status="completed",
                total_findings=0,
                findings=[],
                summaries={"all": "No files to review."},
            )

        # Step 2: Filter excluded files
        diffs = filter_diffs(all_diffs, self.filter_config)
        logger.info(
            "orchestrator.filter.done",
            total_files=len(all_diffs),
            kept_files=len(diffs),
        )

        if not diffs:
            return ReviewReport(
                status="completed",
                total_findings=0,
                findings=[],
                summaries={"all": "All changed files were excluded (lock files, generated code, etc.)."},
            )

        # Step 3: Build review contexts
        contexts = build_contexts(diffs)

        # Step 4: Run agents (serial in Phase 1, parallel in Phase 2+)
        logger.info("orchestrator.review.start", agents=len(self.agents), contexts=len(contexts))

        results: list[ReviewOutput] = []
        failed_agents: list[str] = []

        for agent in self.agents:
            try:
                agent_results = await self._run_agent_on_contexts(agent, contexts)
                results.append(agent_results)
                logger.info(
                    "orchestrator.review.agent_done",
                    agent=agent.metadata.name,
                    findings=len(agent_results.findings),
                )
            except Exception as e:
                logger.error(
                    "orchestrator.review.agent_failed",
                    agent=agent.metadata.name,
                    error=str(e),
                )
                failed_agents.append(agent.metadata.name)
                # Continue with other agents (don't fail the whole review)

        # Step 5: Merge, dedup, and filter
        all_findings = self._merge_and_dedup(results)
        all_findings = [
            f for f in all_findings if f.confidence >= self.confidence_threshold
        ]

        # Sort: critical first, then by confidence
        severity_order = {"critical": 0, "warning": 1, "suggestion": 2, "nitpick": 3}
        all_findings.sort(
            key=lambda f: (severity_order.get(f.severity, 99), -f.confidence)
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        # Collect cost from all agents' LLM clients
        cost_summary = self._collect_costs()

        logger.info(
            "orchestrator.done",
            total_findings=len(all_findings),
            failed_agents=failed_agents,
            elapsed_ms=elapsed_ms,
            cost_usd=cost_summary["total_cost_usd"],
        )

        return build_report(
            results=results,
            cost_summary=cost_summary,
            duration_ms=elapsed_ms,
            agents_failed=failed_agents,
        )

    def _collect_costs(self) -> dict:
        """Aggregate cost stats from all agents' LLM clients."""
        total_input = 0
        total_output = 0
        total_cost = 0.0
        for agent in self.agents:
            summary = agent.llm.cost_tracker.summary()
            total_input += summary["total_input_tokens"]
            total_output += summary["total_output_tokens"]
            total_cost += summary["total_cost_usd"]
        return {
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cost_usd": total_cost,
        }

    async def _run_agent_on_contexts(
        self,
        agent: BaseReviewAgent,
        contexts: list,
    ) -> ReviewOutput:
        """Run one agent across multiple file contexts, merging results."""
        all_findings: list[ReviewFinding] = []
        agent_summaries: list[str] = []

        for ctx in contexts:
            try:
                output = await asyncio.wait_for(
                    agent.review(ctx),
                    timeout=agent.metadata.timeout_seconds,
                )
                all_findings.extend(output.findings)
                if output.summary:
                    agent_summaries.append(f"[{ctx.file_path}] {output.summary}")
            except asyncio.TimeoutError:
                logger.warning(
                    "orchestrator.review.agent_timeout",
                    agent=agent.metadata.name,
                    file=ctx.file_path,
                )
                agent_summaries.append(f"[{ctx.file_path}] Review timed out")
            except Exception as e:
                logger.error(
                    "orchestrator.review.context_failed",
                    agent=agent.metadata.name,
                    file=ctx.file_path,
                    error=str(e),
                )
                agent_summaries.append(f"[{ctx.file_path}] Failed: {e}")

        return ReviewOutput(
            findings=all_findings,
            summary="\n".join(agent_summaries),
        )

    def _merge_and_dedup(self, results: list[ReviewOutput]) -> list[ReviewFinding]:
        """Phase 1 dedup: simple line-range overlap removal."""
        all_findings: list[ReviewFinding] = []
        for r in results:
            all_findings.extend(r.findings)

        if len(all_findings) <= 1:
            return all_findings

        # Sort by file, then line range
        all_findings.sort(key=lambda f: (f.file, f.line_start))

        # Remove exact duplicates (same file, same exact line range, same category)
        seen = set()
        unique: list[ReviewFinding] = []
        for f in all_findings:
            key = (f.file, f.line_start, f.line_end, f.category)
            if key not in seen:
                seen.add(key)
                unique.append(f)

        return unique
