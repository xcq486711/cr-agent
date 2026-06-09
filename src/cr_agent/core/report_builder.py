"""
Report builder — aggregates findings into a final review report.
"""

import json
from dataclasses import dataclass, field

from cr_agent.llm import ReviewFinding, ReviewOutput


@dataclass
class ReviewReport:
    """Final review report — the orchestrator's output."""

    status: str  # "completed" | "partial" | "failed"
    total_findings: int
    findings: list[ReviewFinding] = field(default_factory=list)
    summaries: dict[str, str] = field(default_factory=dict)  # agent_name → summary

    # Stats
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    agents_completed: int = 0
    agents_failed: list[str] = field(default_factory=list)

    @property
    def findings_by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts

    @property
    def findings_by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.category] = counts.get(f.category, 0) + 1
        return counts

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON."""
        return json.dumps({
            "status": self.status,
            "total_findings": self.total_findings,
            "findings": [f.model_dump() for f in self.findings],
            "summaries": self.summaries,
            "stats": {
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "cost_usd": self.cost_usd,
                "duration_ms": self.duration_ms,
                "agents_completed": self.agents_completed,
                "agents_failed": self.agents_failed,
            },
            "breakdown": {
                "by_severity": self.findings_by_severity,
                "by_category": self.findings_by_category,
            },
        }, indent=indent, ensure_ascii=False)

    def to_markdown(self) -> str:
        """Render as markdown for GitHub comment / human reading."""
        lines = [
            "# Code Review Report",
            "",
            f"**Status**: {self.status}",
            f"**Findings**: {self.total_findings}",
            f"**Duration**: {self.duration_ms}ms | **Cost**: ${self.cost_usd:.4f}",
            f"**Agents**: {self.agents_completed} completed",
        ]
        if self.agents_failed:
            lines.append(f"**Failed agents**: {', '.join(self.agents_failed)}")
        lines.append("")

        # Summary
        lines.append("## Summary")
        for agent_name, summary in self.summaries.items():
            lines.append(f"### {agent_name.title()}")
            lines.append(summary)
            lines.append("")

        # Findings table
        lines.append("## Findings")
        if not self.findings:
            lines.append("_No issues found._")
        else:
            lines.append("| Severity | Category | File | Line | Issue |")
            lines.append("|---|---|---|---|---|")
            for f in self.findings:
                severity_tag = {
                    "critical": "[!]", "warning": "[~]",
                    "suggestion": "[*]", "nitpick": "[ ]",
                }.get(f.severity, "[ ]")
                lines.append(
                    f"| {severity_tag} {f.severity} | {f.category} | "
                    f"`{f.file}` | {f.line_start}-{f.line_end} | {f.title} |"
                )

            lines.append("")
            lines.append("## Details")
            for i, f in enumerate(self.findings, 1):
                lines.append(f"### {i}. [{f.severity.upper()}] {f.title}")
                lines.append(f"**File**: `{f.file}:{f.line_start}-{f.line_end}`")
                lines.append(f"**Category**: {f.category} | **Confidence**: {f.confidence:.0%}")
                lines.append("")
                lines.append(f.description)
                if f.suggestion:
                    lines.append("")
                    lines.append("**Fix suggestion:**")
                    lines.append(f"```{_guess_lang(f.file)}")
                    lines.append(f.suggestion)
                    lines.append("```")
                lines.append("")

        return "\n".join(lines)


def _guess_lang(path: str) -> str:
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    return ext


def build_report(
    results: list[ReviewOutput],
    cost_summary: dict | None = None,
    duration_ms: int = 0,
    agents_failed: list[str] | None = None,
) -> ReviewReport:
    """Build a ReviewReport from agent results."""
    all_findings: list[ReviewFinding] = []
    summaries: dict[str, str] = {}

    for i, output in enumerate(results):
        all_findings.extend(output.findings)
        # Use a generic agent label since we don't track names in ReviewOutput yet
        agent_key = f"agent_{i}"
        summaries[agent_key] = output.summary

    report = ReviewReport(
        status="completed" if not agents_failed else "partial",
        total_findings=len(all_findings),
        findings=all_findings,
        summaries=summaries,
        duration_ms=duration_ms,
        agents_completed=len(results),
        agents_failed=agents_failed or [],
    )

    if cost_summary:
        report.tokens_in = cost_summary.get("total_input_tokens", 0)
        report.tokens_out = cost_summary.get("total_output_tokens", 0)
        report.cost_usd = cost_summary.get("total_cost_usd", 0)

    return report
