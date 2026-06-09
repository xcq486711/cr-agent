"""Unit tests for LLM client — retry logic and structured output parsing."""

import json

import pytest

from cr_agent.llm.client import LLMClient
from cr_agent.llm.schema import ReviewFinding, ReviewOutput


class TestExtractAndParseJson:
    """Test JSON extraction from various LLM output formats."""

    def setup_method(self):
        self.client = LLMClient(api_key="test-key")

    def test_pure_json(self):
        raw = '{"findings": [], "summary": "No issues found"}'
        result = self.client._extract_and_parse_json(raw, ReviewOutput)
        assert isinstance(result, ReviewOutput)
        assert result.findings == []
        assert result.summary == "No issues found"

    def test_json_in_code_block(self):
        raw = '```json\n{"findings": [], "summary": "Clean"}\n```'
        result = self.client._extract_and_parse_json(raw, ReviewOutput)
        assert result.summary == "Clean"

    def test_json_with_prefix_text(self):
        raw = 'Here is my analysis:\n\n{"findings": [], "summary": "OK"}'
        result = self.client._extract_and_parse_json(raw, ReviewOutput)
        assert result.summary == "OK"

    def test_json_with_trailing_text(self):
        raw = '{"findings": [], "summary": "Done"}\n\nLet me know if you need more.'
        result = self.client._extract_and_parse_json(raw, ReviewOutput)
        assert result.summary == "Done"

    def test_finding_with_all_fields(self):
        finding_data = {
            "findings": [
                {
                    "file": "src/app.py",
                    "line_start": 42,
                    "line_end": 42,
                    "severity": "warning",
                    "category": "security",
                    "title": "SQL injection risk",
                    "description": "User input directly concatenated into SQL query",
                    "suggestion": "Use parameterized queries",
                    "confidence": 0.85,
                }
            ],
            "summary": "Found 1 security issue",
        }
        raw = json.dumps(finding_data)
        result = self.client._extract_and_parse_json(raw, ReviewOutput)
        assert len(result.findings) == 1
        assert result.findings[0].severity == "warning"
        assert result.findings[0].confidence == 0.85

    def test_invalid_json_raises(self):
        raw = "This is not JSON at all"
        with pytest.raises((json.JSONDecodeError, ValueError)):
            self.client._extract_and_parse_json(raw, ReviewOutput)

    def test_json_missing_required_field(self):
        # findings[0] missing 'file' field
        raw = json.dumps({
            "findings": [{"line_start": 1, "line_end": 1, "severity": "warning"}],
            "summary": "x",
        })
        with pytest.raises(Exception):  # Pydantic ValidationError
            self.client._extract_and_parse_json(raw, ReviewOutput)


class TestRetryDelay:
    """Test exponential backoff calculation."""

    def setup_method(self):
        self.client = LLMClient(api_key="test-key")

    def test_respects_retry_after(self):
        delay = self.client._get_retry_delay(attempt=1, retry_after=5.0)
        assert delay == 5.0

    def test_exponential_growth(self):
        # attempt 1: base = 0.5, attempt 2: base = 1.0, attempt 3: base = 2.0
        d1 = self.client._get_retry_delay(attempt=1)
        d2 = self.client._get_retry_delay(attempt=2)
        d3 = self.client._get_retry_delay(attempt=3)
        # With jitter, d2 should be roughly 2x d1 (not exact due to random)
        assert d1 < d2 < d3
        assert d1 < 1.0  # 0.5 + up to 0.125 jitter
        assert d3 < 3.0  # 2.0 + up to 0.5 jitter

    def test_caps_at_32_seconds(self):
        delay = self.client._get_retry_delay(attempt=100)
        assert delay <= 32.0 + 8.0  # 32 + max jitter (0.25 * 32)


class TestCostTracker:
    """Test cost tracking."""

    def test_records_usage(self):
        from cr_agent.llm.cost_tracker import CostTracker, TokenUsage

        tracker = CostTracker()
        usage = TokenUsage(input_tokens=1000, output_tokens=500)
        cost = tracker.record("deepseek-chat", "security", usage)

        assert cost > 0
        summary = tracker.summary()
        assert summary["total_input_tokens"] == 1000
        assert summary["total_output_tokens"] == 500
        assert summary["calls"] == 1
        assert "security" in summary["by_agent"]

    def test_budget_exceeded(self):
        from cr_agent.llm.cost_tracker import BudgetExceededError, CostTracker, TokenUsage

        tracker = CostTracker(budget_usd=0.001)  # Very low budget
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)

        with pytest.raises(BudgetExceededError):
            tracker.record("deepseek-chat", "security", usage)
