"""
Tool-using review agent — extends BaseReviewAgent with codebase exploration capability.

Instead of just analyzing the diff, the LLM can:
- Read full files to see context around changed lines
- Grep for callers/callees/definitions
- List directories to understand project structure
"""

import asyncio
import structlog

from cr_agent.llm import LLMClient, ReviewOutput, ToolRegistry

from .base import AgentMetadata, BaseReviewAgent, ReviewContext

logger = structlog.get_logger()

TOOL_AGENT_SYSTEM_SUFFIX = """

## 可用工具
你可以使用以下工具探索代码库。在分析 diff 之前或分析过程中，如果发现需要更多上下文才能判断，主动调用工具获取信息。每次工具调用都会返回结果，你可以基于结果决定是否需要进一步探索。

**使用原则：**
1. 先看 diff，判断是否需要更多信息，不要盲目调用工具
2. 如果 diff 中引用了某个函数/类但你看不到它的完整定义，用 read_file 查看
3. 如果需要了解某个函数被谁调用、影响范围有多大，用 grep 搜索引用
4. 工具调用会记录在对话中，用完工具后基于获取的信息继续分析
5. 最终必须输出审查报告，不要一直调用工具循环

工具使用示例：
- diff 显示调用了 `validateUser(input)`，但看不到这个函数的实现 → 调 read_file 查看
- 怀疑某段代码存在 SQL 注入，但不确定框架是否做了参数化 → 调用 grep 搜索 `PreparedStatement`
- 看到一个新增的 API 端点，想知道它的鉴权配置 → 调 read_file 读取 SecurityConfig
"""


class ToolUsingAgent(BaseReviewAgent):
    """
    A review agent that can use tools to explore the codebase.

    Wraps a base agent (e.g. SecurityAgent) and adds tool-using capability.
    Workspace must be set via ToolRegistry for file-based tools to work.
    """

    def __init__(self, base_agent: BaseReviewAgent, tool_registry: ToolRegistry):
        # Copy metadata from the base agent
        super().__init__(base_agent.llm)
        self.metadata = base_agent.metadata
        self._base = base_agent
        self.tool_registry = tool_registry
        self._tool_schemas = tool_registry.to_openai_schemas()

    def system_prompt(self) -> str:
        return self._base.system_prompt()

    def build_user_prompt(self, context: ReviewContext) -> str:
        return self._base.build_user_prompt(context)

    def _tool_augmented_prompt(self) -> str:
        """Augment system prompt with tool instructions."""
        return self.system_prompt() + TOOL_AGENT_SYSTEM_SUFFIX + "\n\n" + self.tool_registry.tool_descriptions()

    async def review(self, context: ReviewContext) -> ReviewOutput:
        """Execute tool-using review flow."""

        messages = [
            {"role": "system", "content": self._tool_augmented_prompt()},
            {"role": "user", "content": self.build_user_prompt(context)},
        ]

        # Phase 1: Exploration — LLM can use tools to understand context
        exploration_text = await self.llm.chat_with_tools(
            messages=messages,
            tools=self._tool_schemas,
            tool_registry=self.tool_registry,
            model=self.metadata.model_preference,
            temperature=self.metadata.temperature,
            agent_type=f"{self.metadata.name}:explore",
        )

        # Phase 2: Structured output — ask LLM to produce the final report
        messages.append({"role": "assistant", "content": exploration_text})
        messages.append({
            "role": "user",
            "content": "基于以上探索结果，请输出最终的审查报告。",
        })

        return await self.llm.chat_structured(
            messages,
            schema=ReviewOutput,
            model=self.metadata.model_preference,
            temperature=self.metadata.temperature,
            agent_type=f"{self.metadata.name}:report",
        )
