"""
Evaluation runner — runs the orchestrator on labeled PRs and computes metrics.

Usage:
  python -m tests.eval.run_eval              # Run all
  python -m tests.eval.run_eval --sample 5   # Run first 5
  python -m tests.eval.run_eval --agent security  # Single agent mode
"""

import argparse
import json
import time
from pathlib import Path

from cr_agent.agents import LogicAgent, PerformanceAgent, SecurityAgent, StyleAgent
from cr_agent.core import ReviewOrchestrator
from cr_agent.llm import LLMClient

from .metrics import compute_metrics, print_report

DATASET_DIR = Path(__file__).parent / "dataset"


def load_datasets() -> list[dict]:
    """Load all dataset JSON files."""
    samples = []
    for fpath in sorted(DATASET_DIR.glob("*.json")):
        with open(fpath, encoding="utf-8") as f:
            sample = json.load(f)
            sample["_id"] = fpath.stem
            samples.append(sample)
    return samples


def fuzzy_match_finding(finding, truth) -> bool:
    """
    Check if a finding matches ground truth.

    Requirements for a match:
    - Same file (exact match or contains)
    - Line within ±5 of expected
    - Same category

    Returns True if this is a correct finding (matches some ground truth item).
    """
    f_file = finding.file.replace("\\", "/")
    t_file = truth["file"].replace("\\", "/")

    # File match
    if f_file != t_file and f_file not in t_file and t_file not in f_file:
        return False

    # Line match (within 5 lines) — LLM line numbers can be imprecise
    f_line = finding.line_start
    t_line = truth.get("line_start", truth.get("line", 1))
    if abs(f_line - t_line) > 5:
        return False

    # Category match
    if finding.category != truth.get("category", ""):
        return False

    return True


async def run_eval(samples: list[dict], single_agent: str | None = None) -> dict:
    """Run evaluation on all samples."""
    all_results = []

    for sample in samples:
        print(f"\n  Evaluating {sample['_id']}: {sample.get('title', '')[:60]}...")

        # Run review
        if single_agent:
            agent_map = {
                "security": SecurityAgent,
                "logic": LogicAgent,
                "performance": PerformanceAgent,
                "style": StyleAgent,
            }
            agent_cls = agent_map.get(single_agent)
            if not agent_cls:
                print(f"    Unknown agent: {single_agent}")
                continue
            agents = [agent_cls(llm=LLMClient())]
        else:
            agents = [
                SecurityAgent(llm=LLMClient()),
                LogicAgent(llm=LLMClient()),
                PerformanceAgent(llm=LLMClient()),
                StyleAgent(llm=LLMClient()),
            ]

        start = time.monotonic()
        orchestrator = ReviewOrchestrator(agents=agents)
        report = await orchestrator.run(sample["diff"])
        elapsed = int((time.monotonic() - start) * 1000)

        # Match findings against ground truth
        ground_truth = sample.get("ground_truth", [])
        false_positive_patterns = sample.get("false_positive_patterns", [])

        matched_truth = set()
        for finding in report.findings:
            for i, truth in enumerate(ground_truth):
                if i in matched_truth:
                    continue
                if fuzzy_match_finding(finding, truth):
                    finding._matched = True
                    matched_truth.add(i)
                    break
            else:
                finding._matched = False

        findings_data = []
        for f in report.findings:
            findings_data.append({
                "file": f.file,
                "line": f.line_start,
                "severity": f.severity,
                "category": f.category,
                "title": f.title,
                "confidence": f.confidence,
                "matched": getattr(f, "_matched", False),
            })

        result = {
            "pr_id": sample["_id"],
            "title": sample.get("title", ""),
            "language": sample.get("language", "unknown"),
            "num_findings": len(report.findings),
            "num_truth": len(ground_truth),
            "num_matched": len(matched_truth),
            "num_false_positive": len(report.findings) - len(matched_truth),
            "num_missed": len(ground_truth) - len(matched_truth),
            "cost_usd": report.cost_usd,
            "duration_ms": elapsed,
            "tokens_in": report.tokens_in,
            "tokens_out": report.tokens_out,
            "findings": findings_data,
        }
        all_results.append(result)

    # Compute aggregate metrics
    metrics = compute_metrics(all_results)
    metrics["total_samples"] = len(all_results)
    metrics["total_cost_usd"] = sum(r["cost_usd"] for r in all_results)
    metrics["total_duration_ms"] = sum(r["duration_ms"] for r in all_results)

    return {
        "metrics": metrics,
        "results": all_results,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=None, help="Run only first N samples")
    parser.add_argument("--agent", type=str, default=None, help="Single agent: security/logic/performance/style")
    parser.add_argument("--output", type=str, default=None, help="Save results to JSON file")
    args = parser.parse_args()

    samples = load_datasets()
    if not samples:
        print("No evaluation datasets found in", DATASET_DIR)
        return

    if args.sample:
        samples = samples[:args.sample]

    print(f"Running evaluation on {len(samples)} samples...")
    if args.agent:
        print(f"  (single agent: {args.agent})")

    result = await run_eval(samples, single_agent=args.agent)

    print("\n" + "=" * 50)
    print_report(result["metrics"])

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull results saved to {args.output}")

    return result


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
