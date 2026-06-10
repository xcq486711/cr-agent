"""Evaluation metrics — precision, recall, F1, by category and severity."""


def compute_metrics(results: list[dict]) -> dict:
    """Compute evaluation metrics from individual sample results."""
    total_findings = sum(r["num_findings"] for r in results)
    total_truth = sum(r["num_truth"] for r in results)
    total_matched = sum(r["num_matched"] for r in results)
    total_fp = sum(r["num_false_positive"] for r in results)
    total_missed = sum(r["num_missed"] for r in results)

    precision = total_matched / total_findings if total_findings > 0 else 0.0
    recall = total_matched / total_truth if total_truth > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "total_findings": total_findings,
        "total_truth": total_truth,
        "total_matched": total_matched,
        "total_false_positive": total_fp,
        "total_missed": total_missed,
        "false_positive_rate": round(total_fp / total_findings, 3) if total_findings > 0 else 0.0,
    }


def print_report(metrics: dict):
    """Print a human-readable evaluation report."""
    print(f"  Precision:          {metrics['precision']:.1%}  (正确发现 / Agent 总输出)")
    print(f"  Recall:             {metrics['recall']:.1%}  (正确发现 / 人工标注总数)")
    print(f"  F1 Score:           {metrics['f1']:.3f}")
    print(f"  False Positive Rate:{metrics['false_positive_rate']:.1%}")
    print(f"  ─────────────────────────────────────")
    print(f"  Agent 总输出:  {metrics['total_findings']}")
    print(f"  人工标注总数:  {metrics['total_truth']}")
    print(f"  正确匹配:      {metrics['total_matched']}")
    print(f"  误报:          {metrics['total_false_positive']}")
    print(f"  漏报:          {metrics['total_missed']}")
    if metrics.get("total_samples"):
        print(f"  样本数:        {metrics['total_samples']}")
        print(f"  总成本:        ${metrics.get('total_cost_usd', 0):.4f}")
        print(f"  总耗时:        {metrics.get('total_duration_ms', 0)}ms")
