"""Base class for all review agents — metadata-driven orchestration."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from cr_agent.llm import LLMClient, ReviewOutput


@dataclass
class AgentMetadata:
    """Declarative metadata — orchestrator uses this for scheduling decisions."""

    name: str
    category: str  # security / logic / performance / style
    concurrency_safe: bool = True
    is_read_only: bool = True
    max_context_tokens: int = 12_000
    timeout_seconds: int = 60
    priority: int = 1  # lower = higher priority
    model_preference: str = "deepseek-chat"
    temperature: float = 0.1


@dataclass
class ReviewContext:
    """Context passed to each agent for review."""

    diff_content: str  # Formatted diff text for LLM consumption
    file_path: str  # Primary file being reviewed
    language: str = "unknown"  # Detected language
    extra_context: str = ""  # Additional context (types, rules, etc.)


class BaseReviewAgent(ABC):
    """
    Abstract base for all review agents.

    Subclasses declare their metadata and implement prompt construction.
    The review() method is the unified entry point called by the orchestrator.
    """

    metadata: AgentMetadata

    def __init__(self, llm: LLMClient):
        self.llm = llm

    @abstractmethod
    def system_prompt(self) -> str:
        """Return the system prompt for this agent."""
        ...

    @abstractmethod
    def build_user_prompt(self, context: ReviewContext) -> str:
        """Build the user prompt from review context."""
        ...

    async def review(self, context: ReviewContext) -> ReviewOutput:
        """Execute review — unified flow with cost tracking."""
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": self.build_user_prompt(context)},
        ]
        return await self.llm.chat_structured(
            messages,
            schema=ReviewOutput,
            model=self.metadata.model_preference,
            temperature=self.metadata.temperature,
            agent_type=self.metadata.name,
        )
