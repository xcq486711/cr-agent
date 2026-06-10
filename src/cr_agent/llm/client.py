"""
Unified LLM client with retry, fallback, and structured output.

Design references:
- Claude Code withRetry.ts: exponential backoff + jitter, consecutive 529 → fallback
- Claude Code cost-tracker.ts: immediate per-call cost recording
"""

import asyncio
import json
import random
import re
import time

import httpx
import structlog
from pydantic import BaseModel

from cr_agent.config import settings

from .cost_tracker import BudgetExceededError, CostTracker, TokenUsage

logger = structlog.get_logger()


class LLMError(Exception):
    """Base class for LLM errors."""

    pass


class RateLimitError(LLMError):
    """429 — rate limited, may include retry_after."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class OverloadedError(LLMError):
    """529 — server overloaded."""

    pass


class StructuredOutputError(LLMError):
    """Failed to parse LLM output into the expected schema after retries."""

    pass


class FallbackTriggeredError(LLMError):
    """Consecutive failures triggered model fallback."""

    def __init__(self, original_model: str, fallback_model: str | None):
        super().__init__(f"Fallback triggered: {original_model} → {fallback_model}")
        self.original_model = original_model
        self.fallback_model = fallback_model


class LLMClient:
    """
    Unified LLM client.

    Features:
    - Exponential backoff + jitter retry (from Claude Code withRetry.ts)
    - Consecutive 529 → FallbackTriggeredError
    - Structured output with Pydantic validation + auto-retry on parse failure
    - Per-call cost tracking
    """

    BASE_DELAY_S = 0.5
    MAX_CONSECUTIVE_529 = 3
    STRUCTURED_OUTPUT_MAX_RETRIES = 3

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        cost_tracker: CostTracker | None = None,
    ):
        self.api_key = api_key or settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.model = model or settings.model
        self.cost_tracker = cost_tracker or CostTracker()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(settings.timeout, connect=10.0),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float | None = None,
        agent_type: str = "unknown",
    ) -> str:
        """Send chat request, return raw text response."""
        model = model or self.model
        temperature = temperature if temperature is not None else settings.temperature

        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        response_data = await self._request_with_retry(body)

        # Track cost
        usage = response_data.get("usage", {})
        token_usage = TokenUsage(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )
        self.cost_tracker.record(model, agent_type, token_usage)

        # Extract text
        choices = response_data.get("choices", [])
        if not choices:
            raise LLMError("No choices in response")
        return choices[0]["message"]["content"]

    async def chat_structured(
        self,
        messages: list[dict],
        schema: type[BaseModel],
        model: str | None = None,
        temperature: float | None = None,
        agent_type: str = "unknown",
    ) -> BaseModel:
        """
        Chat with structured output — forces LLM to return valid JSON matching schema.

        Strategy:
        1. Append JSON Schema + example to system prompt
        2. Parse LLM output (handle ```json wrapping)
        3. Validate with Pydantic
        4. On failure: retry with error message (up to 3 times)
        """
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        schema_instruction = (
            "\n\n## Output Format\n"
            "You MUST respond with valid JSON matching this schema. "
            "Do NOT wrap in markdown code blocks. Output ONLY the JSON object.\n\n"
            f"```json\n{schema_json}\n```"
        )

        # Inject schema into system message
        augmented_messages = self._inject_schema(messages, schema_instruction)

        last_error: Exception | None = None
        for attempt in range(1, self.STRUCTURED_OUTPUT_MAX_RETRIES + 1):
            raw_text = await self.chat(
                augmented_messages, model=model, temperature=temperature, agent_type=agent_type
            )

            try:
                parsed = self._extract_and_parse_json(raw_text, schema)
                return parsed
            except (json.JSONDecodeError, ValueError) as e:
                last_error = e
                logger.warning(
                    "structured_output_parse_failed",
                    attempt=attempt,
                    error=str(e),
                    raw_preview=raw_text[:200],
                )
                # Add error feedback for next retry
                augmented_messages = [
                    *augmented_messages,
                    {"role": "assistant", "content": raw_text},
                    {
                        "role": "user",
                        "content": (
                            f"Your response was not valid JSON. Error: {e}\n"
                            "Please try again. Output ONLY valid JSON, no markdown, no explanation."
                        ),
                    },
                ]

        raise StructuredOutputError(
            f"Failed to parse structured output after {self.STRUCTURED_OUTPUT_MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        )

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_registry,  # ToolRegistry
        model: str | None = None,
        temperature: float | None = None,
        agent_type: str = "unknown",
        max_tool_rounds: int = 5,
    ) -> str:
        """
        ReAct loop: send messages + tools, execute tool calls, repeat.

        Returns the final text response after all tool calls are resolved.
        Max `max_tool_rounds` rounds to prevent infinite loops.
        """
        model = model or self.model
        temperature = temperature if temperature is not None else settings.temperature

        for round_num in range(max_tool_rounds):
            body = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "tools": tools,
                "tool_choice": "auto",
            }

            response_data = await self._request_with_retry(body)

            # Track cost
            usage = response_data.get("usage", {})
            token_usage = TokenUsage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            )
            self.cost_tracker.record(model, agent_type, token_usage)

            choice = response_data.get("choices", [{}])[0]
            message = choice.get("message", {})

            # If finish_reason is stop and no tool calls → done
            tool_calls = message.get("tool_calls", [])
            if not tool_calls or choice.get("finish_reason") == "stop":
                return message.get("content", "")

            # Append assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }
                    for tc in tool_calls
                ],
            })

            # Execute each tool call and append results
            for tc in tool_calls:
                func_name = tc["function"]["name"]
                try:
                    func_args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    func_args = {}

                tool = tool_registry.get(func_name)
                if tool is None:
                    result = ToolResult(success=False, content="", error=f"Unknown tool: {func_name}")
                    logger.warning("tool_call_unknown", tool=func_name)
                else:
                    logger.info("tool_call", tool=func_name, args=str(func_args)[:100])
                    try:
                        result = await tool.handler(func_args)
                        logger.info("tool_result", tool=func_name, success=result.success,
                                    length=len(result.content[:200]))
                    except Exception as e:
                        logger.error("tool_error", tool=func_name, error=str(e))
                        result = ToolResult(success=False, content="", error=str(e))

                result_content = result.content if result.success else f"Error: {result.error}"
                if result.truncated:
                    result_content += "\n(content truncated)"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_content,
                })

        # Exhausted rounds — return what we have
        logger.warning("chat_with_tools_max_rounds", max_rounds=max_tool_rounds)
        # Ask LLM for final answer based on all accumulated context
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        response_data = await self._request_with_retry(body)
        choice = response_data.get("choices", [{}])[0]
        return choice.get("message", {}).get("content", "")

    async def _request_with_retry(self, body: dict) -> dict:
        """Core retry loop — exponential backoff + jitter, 529 counting."""
        max_retries = settings.max_retries
        consecutive_529 = 0

        for attempt in range(1, max_retries + 1):
            try:
                client = await self._get_client()
                response = await client.post("/v1/chat/completions", json=body)

                if response.status_code == 200:
                    return response.json()

                # Handle errors by status code
                if response.status_code == 429:
                    retry_after = self._parse_retry_after(response)
                    raise RateLimitError(
                        f"Rate limited (429): {response.text[:200]}", retry_after=retry_after
                    )
                elif response.status_code == 529 or response.status_code >= 500:
                    raise OverloadedError(f"Server error ({response.status_code}): {response.text[:200]}")
                else:
                    # 400, 401, 403 — not retryable
                    raise LLMError(
                        f"API error ({response.status_code}): {response.text[:200]}"
                    )

            except RateLimitError as e:
                delay = self._get_retry_delay(attempt, e.retry_after)
                logger.warning("llm_rate_limited", attempt=attempt, delay_s=f"{delay:.2f}")
                await asyncio.sleep(delay)

            except OverloadedError:
                consecutive_529 += 1
                if consecutive_529 >= self.MAX_CONSECUTIVE_529:
                    raise FallbackTriggeredError(self.model, None)
                delay = self._get_retry_delay(attempt)
                logger.warning(
                    "llm_overloaded",
                    attempt=attempt,
                    consecutive_529=consecutive_529,
                    delay_s=f"{delay:.2f}",
                )
                await asyncio.sleep(delay)

            except httpx.TimeoutException:
                delay = self._get_retry_delay(attempt)
                logger.warning("llm_timeout", attempt=attempt, delay_s=f"{delay:.2f}")
                await asyncio.sleep(delay)

            except httpx.ConnectError:
                # Stale connection — recreate client
                await self.close()
                delay = self._get_retry_delay(attempt)
                logger.warning("llm_connection_error", attempt=attempt)
                await asyncio.sleep(delay)

            except (LLMError, BudgetExceededError):
                raise  # Not retryable

        raise LLMError(f"Max retries ({max_retries}) exhausted")

    def _get_retry_delay(self, attempt: int, retry_after: float | None = None) -> float:
        """Exponential backoff + jitter (same formula as Claude Code getRetryDelay)."""
        if retry_after:
            return retry_after
        base = min(self.BASE_DELAY_S * (2 ** (attempt - 1)), 32.0)
        jitter = random.random() * 0.25 * base
        return base + jitter

    def _parse_retry_after(self, response: httpx.Response) -> float | None:
        """Extract retry-after header value in seconds."""
        header = response.headers.get("retry-after")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        return None

    def _inject_schema(self, messages: list[dict], schema_instruction: str) -> list[dict]:
        """Append schema instruction to the system message."""
        messages = [dict(m) for m in messages]  # shallow copy
        if messages and messages[0].get("role") == "system":
            messages[0] = {**messages[0], "content": messages[0]["content"] + schema_instruction}
        else:
            messages.insert(0, {"role": "system", "content": schema_instruction})
        return messages

    def _extract_and_parse_json(self, raw_text: str, schema: type[BaseModel]) -> BaseModel:
        """Extract JSON from LLM output with multi-level fallback."""
        text = raw_text.strip()

        # Step 1: Extract from markdown code block
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if json_match:
            text = json_match.group(1).strip()

        # Step 2: Find JSON boundaries
        if not text.startswith("{"):
            start = text.find("{")
            if start != -1:
                text = text[start:]

        if not text.endswith("}"):
            end = text.rfind("}")
            if end != -1:
                text = text[: end + 1]

        # Step 3: Try direct parsing
        try:
            data = json.loads(text)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            pass

        # Step 4: Try sanitizing — fix common LLM JSON mistakes
        sanitized = self._sanitize_json(text)
        try:
            data = json.loads(sanitized)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            pass

        # Step 5: Regex extraction — pull out key fields and build model manually
        return self._regex_extract_findings(text, schema)

    def _sanitize_json(self, text: str) -> str:
        """Fix common JSON issues from LLM output."""
        # Remove BOM
        if text.startswith("﻿"):
            text = text[1:]

        # Fix unescaped newlines within string values
        # (JSON strings cannot contain literal newlines)
        result = []
        in_string = False
        escape_next = False
        for ch in text:
            if escape_next:
                escape_next = False
                result.append(ch)
                continue
            if ch == "\\":
                escape_next = True
                result.append(ch)
                continue
            if ch == '"':
                in_string = not in_string
                result.append(ch)
                continue
            if in_string and ch in ("\n", "\r", "\t"):
                result.append({"\\n": "\\\\n", "\\r": "\\\\r", "\\t": "\\\\t"}.get(repr(ch)[1:-1], " "))
                continue
            result.append(ch)
        return "".join(result)

    def _regex_extract_findings(self, text: str, schema: type[BaseModel]) -> BaseModel:
        """Last resort: extract findings fields via regex."""
        # Look for "findings" array
        findings_match = re.search(r'"findings"\s*:\s*\[(.*)\]', text, re.DOTALL)
        if not findings_match:
            # Return empty result rather than failing
            return schema(findings=[], summary="Parse failed — returning empty result")

        findings_text = findings_match.group(1)
        # Extract individual finding objects via brace matching
        findings = []
        depth = 0
        start = -1
        for i, ch in enumerate(findings_text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start != -1:
                    obj_text = findings_text[start : i + 1]
                    finding = self._extract_single_finding(obj_text)
                    if finding:
                        findings.append(finding)
                    start = -1

        # Extract summary
        summary_match = re.search(r'"summary"\s*:\s*"([^"]*)"', text)
        summary = summary_match.group(1) if summary_match else ""

        return schema(findings=findings, summary=summary)

    def _extract_single_finding(self, obj_text: str) -> dict | None:
        """Extract a single finding dict from JSON-like text using regex."""
        try:
            return json.loads(obj_text)
        except json.JSONDecodeError:
            pass

        # Manual field extraction
        result = {}
        field_patterns = {
            "file": r'"file"\s*:\s*"([^"]*)"',
            "severity": r'"severity"\s*:\s*"([^"]*)"',
            "category": r'"category"\s*:\s*"([^"]*)"',
            "title": r'"title"\s*:\s*"([^"]*)"',
            "description": r'"description"\s*:\s*"([^"]*)"',
            "suggestion": r'"suggestion"\s*:\s*"([^"]*)"',
            "line_start": r'"line_start"\s*:\s*(\d+)',
            "line_end": r'"line_end"\s*:\s*(\d+)',
            "confidence": r'"confidence"\s*:\s*([\d.]+)',
        }
        for key, pattern in field_patterns.items():
            match = re.search(pattern, obj_text, re.DOTALL)
            if match:
                val = match.group(1)
                if key in ("line_start", "line_end"):
                    result[key] = int(val)
                elif key == "confidence":
                    result[key] = float(val)
                else:
                    result[key] = val

        return result if "file" in result and "title" in result else None
