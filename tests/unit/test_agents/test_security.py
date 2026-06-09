"""Unit tests for Security Agent — prompt construction and metadata."""

from cr_agent.agents import ReviewContext, SecurityAgent
from cr_agent.llm import LLMClient


class TestSecurityAgentMetadata:
    """Test agent metadata declaration."""

    def test_metadata_values(self):
        agent = SecurityAgent(llm=LLMClient(api_key="fake"))
        assert agent.metadata.name == "security"
        assert agent.metadata.category == "security"
        assert agent.metadata.concurrency_safe is True
        assert agent.metadata.timeout_seconds == 90
        assert agent.metadata.priority == 1

    def test_system_prompt_contains_key_sections(self):
        agent = SecurityAgent(llm=LLMClient(api_key="fake"))
        prompt = agent.system_prompt()
        # Must contain security categories
        assert "SQL injection" in prompt
        assert "Command injection" in prompt
        assert "Hardcoded" in prompt
        # Must contain exclusion rules
        assert "Do NOT report" in prompt
        assert "test files" in prompt.lower()
        # Must contain severity guidelines
        assert "critical" in prompt
        assert "confidence" in prompt.lower()

    def test_user_prompt_includes_diff(self):
        agent = SecurityAgent(llm=LLMClient(api_key="fake"))
        context = ReviewContext(
            diff_content="+SECRET_KEY = 'hardcoded'",
            file_path="src/config.py",
            language="python",
        )
        prompt = agent.build_user_prompt(context)
        assert "src/config.py" in prompt
        assert "+SECRET_KEY = 'hardcoded'" in prompt
        assert "python" in prompt

    def test_user_prompt_includes_extra_context(self):
        agent = SecurityAgent(llm=LLMClient(api_key="fake"))
        context = ReviewContext(
            diff_content="+x = 1",
            file_path="foo.py",
            language="python",
            extra_context="This project uses Django ORM for all DB access.",
        )
        prompt = agent.build_user_prompt(context)
        assert "Django ORM" in prompt
        assert "Additional Context" in prompt

    def test_user_prompt_no_extra_context(self):
        agent = SecurityAgent(llm=LLMClient(api_key="fake"))
        context = ReviewContext(
            diff_content="+x = 1",
            file_path="foo.py",
            language="python",
        )
        prompt = agent.build_user_prompt(context)
        assert "Additional Context" not in prompt
