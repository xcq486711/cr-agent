"""Cost tracking — records token usage per LLM call, per model × agent."""

import structlog

logger = structlog.get_logger()


class TokenUsage:
    """Token counts from a single LLM call."""

    def __init__(self, input_tokens: int = 0, output_tokens: int = 0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class CostTracker:
    """
    Tracks token usage and estimated cost for a single review session.

    Records immediately after each LLM call (not post-hoc).
    Raises BudgetExceededError if total cost exceeds budget.
    """

    # DeepSeek pricing (USD per million tokens)
    PRICING = {
        "deepseek-chat": {"input": 0.14, "output": 0.28},
        "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    }

    def __init__(self, budget_usd: float | None = None):
        self.budget_usd = budget_usd
        self._records: list[dict] = []
        self._total_cost_usd: float = 0.0
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0

    def record(self, model: str, agent_type: str, usage: TokenUsage) -> float:
        """Record a single LLM call's usage. Returns cost in USD."""
        cost = self._calculate_cost(model, usage)
        self._records.append({
            "model": model,
            "agent_type": agent_type,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": cost,
        })
        self._total_cost_usd += cost
        self._total_input_tokens += usage.input_tokens
        self._total_output_tokens += usage.output_tokens

        logger.debug(
            "llm_call_recorded",
            model=model,
            agent_type=agent_type,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=f"{cost:.6f}",
            total_cost_usd=f"{self._total_cost_usd:.6f}",
        )

        if self.budget_usd and self._total_cost_usd >= self.budget_usd:
            raise BudgetExceededError(
                f"Review cost ${self._total_cost_usd:.4f} exceeded budget ${self.budget_usd}"
            )

        return cost

    def summary(self) -> dict:
        """Return cost summary for this review session."""
        return {
            "total_cost_usd": round(self._total_cost_usd, 6),
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "calls": len(self._records),
            "by_agent": self._group_by_agent(),
        }

    def _calculate_cost(self, model: str, usage: TokenUsage) -> float:
        pricing = self.PRICING.get(model, {"input": 0.14, "output": 0.28})
        input_cost = usage.input_tokens * pricing["input"] / 1_000_000
        output_cost = usage.output_tokens * pricing["output"] / 1_000_000
        return input_cost + output_cost

    def _group_by_agent(self) -> dict:
        groups: dict[str, dict] = {}
        for r in self._records:
            key = r["agent_type"]
            if key not in groups:
                groups[key] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}
            groups[key]["input_tokens"] += r["input_tokens"]
            groups[key]["output_tokens"] += r["output_tokens"]
            groups[key]["cost_usd"] += r["cost_usd"]
            groups[key]["calls"] += 1
        return groups


class BudgetExceededError(Exception):
    """Raised when review cost exceeds the configured budget."""

    pass
