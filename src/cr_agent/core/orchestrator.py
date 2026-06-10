"""
Review orchestrator — the main pipeline that ties everything together.

Flow:
  diff text → parse → filter → build contexts → [parallel agents × file contexts] → dedup → report
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

    All agents run in parallel, each processing file contexts concurrently.
    A global semaphore limits total LLM calls to avoid rate limits.
    """

    def __init__(
        self,
        agents: list[BaseReviewAgent],
        filter_config: FilterConfig | None = None,
        cost_tracker: CostTracker | None = None,
        confidence_threshold: float = 0.7,
        max_concurrent: int = 10,
    ):
        self.agents = agents
        self.filter_config = filter_config or FilterConfig()
        self.cost_tracker = cost_tracker or CostTracker()
        self.confidence_threshold = confidence_threshold
        self.max_concurrent = max_concurrent

    async def run(self, diff_text: str) -> ReviewReport:
        """Execute the full review pipeline."""
        start_time = time.monotonic()

        # Step 1: Parse
        logger.info("orchestrator.parse.start")
        all_diffs = parse_diff(diff_text)
        if not all_diffs:
            return ReviewReport(status="completed", total_findings=0,
                                findings=[], summaries={"all": "No files to review."})

        # Step 2: Filter
        diffs = filter_diffs(all_diffs, self.filter_config)
        logger.info("orchestrator.filter.done", total=len(all_diffs), kept=len(diffs))
        if not diffs:
            return ReviewReport(status="completed", total_findings=0,
                                findings=[], summaries={"all": "All files excluded."})

        # Step 3: Build contexts
        contexts = build_contexts(diffs)
        total_tasks = len(self.agents) * len(contexts)
        logger.info("orchestrator.review.start", agents=len(self.agents),
                    contexts=len(contexts), total_tasks=total_tasks)

        # Step 4: Parallel fan-out — all agents × all contexts concurrently
        semaphore = asyncio.Semaphore(self.max_concurrent)
        failed_agents: set[str] = set()

        async def run_one(agent: BaseReviewAgent, ctx):
            async with semaphore:
                try:
                    output = await asyncio.wait_for(
                        agent.review(ctx), timeout=agent.metadata.timeout_seconds)
                    return (agent.metadata.name, ctx.file_path, output, None)
                except asyncio.TimeoutError:
                    logger.warning("agent_timeout", agent=agent.metadata.name, file=ctx.file_path)
                    failed_agents.add(agent.metadata.name)
                    return (agent.metadata.name, ctx.file_path, None, "timeout")
                except Exception as e:
                    logger.error("agent_error", agent=agent.metadata.name, file=ctx.file_path, error=str(e))
                    failed_agents.add(agent.metadata.name)
                    return (agent.metadata.name, ctx.file_path, None, str(e))

        tasks = [run_one(agent, ctx) for agent in self.agents for ctx in contexts]
        raw_results = await asyncio.gather(*tasks)

        # Step 5: Group results by agent
        agent_results: dict[str, ReviewOutput] = {}
        for agent_name, file_path, output, error in raw_results:
            if agent_name not in agent_results:
                agent_results[agent_name] = ReviewOutput(findings=[], summary="")
            agg = agent_results[agent_name]
            if output is not None:
                agg.findings.extend(output.findings)
                if output.summary:
                    agg.summary += f"[{file_path}] {output.summary}\n"
            elif error:
                agg.summary += f"[{file_path}] {error}\n"

        results = list(agent_results.values())
        failed_list = sorted(failed_agents)

        # Step 6: Merge, dedup, filter, sort
        all_findings = self._merge_and_dedup(results)
        all_findings = [f for f in all_findings if f.confidence >= self.confidence_threshold]
        severity_order = {"critical": 0, "warning": 1, "suggestion": 2, "nitpick": 3}
        all_findings.sort(key=lambda f: (severity_order.get(f.severity, 99), -f.confidence))

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        cost_summary = self._collect_costs()

        logger.info("orchestrator.done", total_findings=len(all_findings),
                    failed_agents=failed_list, elapsed_ms=elapsed_ms,
                    cost_usd=cost_summary["total_cost_usd"])

        return build_report(results=results, cost_summary=cost_summary,
                           duration_ms=elapsed_ms, agents_failed=failed_list)

    def _collect_costs(self) -> dict:
        total_input = sum(a.llm.cost_tracker.summary()["total_input_tokens"] for a in self.agents)
        total_output = sum(a.llm.cost_tracker.summary()["total_output_tokens"] for a in self.agents)
        total_cost = sum(a.llm.cost_tracker.summary()["total_cost_usd"] for a in self.agents)
        return {"total_input_tokens": total_input, "total_output_tokens": total_output, "total_cost_usd": total_cost}

    def _merge_and_dedup(self, results: list[ReviewOutput]) -> list[ReviewFinding]:
        all_findings: list[ReviewFinding] = []
        for r in results:
            all_findings.extend(r.findings)
        if len(all_findings) <= 1:
            return all_findings
        all_findings.sort(key=lambda f: (f.file, f.line_start))
        seen = set()
        unique = []
        for f in all_findings:
            key = (f.file, f.line_start, f.line_end, f.category)
            if key not in seen:
                seen.add(key)
                unique.append(f)
        return unique
