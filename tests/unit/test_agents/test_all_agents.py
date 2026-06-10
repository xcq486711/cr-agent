"""Smoke tests for all review agents."""

from cr_agent.agents import LogicAgent, PerformanceAgent, SecurityAgent, StyleAgent
from cr_agent.llm import LLMClient


class TestAllAgents:
    """Ensure all agents can be instantiated and produce valid prompts."""

    def test_logic_agent_metadata(self):
        agent = LogicAgent(llm=LLMClient(api_key="fake"))
        assert agent.metadata.name == "logic"
        assert agent.metadata.category == "logic"
        assert len(agent.system_prompt()) > 100
        prompt = agent.build_user_prompt(
            type("Ctx", (), {"diff_content": "+x=1", "file_path": "a.py", "language": "python", "extra_context": ""})()
        )
        assert "a.py" in prompt
        assert "python" in prompt

    def test_performance_agent_metadata(self):
        agent = PerformanceAgent(llm=LLMClient(api_key="fake"))
        assert agent.metadata.name == "performance"
        assert "N+1" in agent.system_prompt()

    def test_style_agent_metadata(self):
        agent = StyleAgent(llm=LLMClient(api_key="fake"))
        assert agent.metadata.name == "style"
        prompt = agent.system_prompt()
        assert "nitpick" in prompt  # Style agent has nitpick severity

    def test_all_agents_concurrency_safe(self):
        """All current agents are read-only and concurrency safe."""
        for agent_cls in [SecurityAgent, LogicAgent, PerformanceAgent, StyleAgent]:
            agent = agent_cls(llm=LLMClient(api_key="fake"))
            assert agent.metadata.concurrency_safe, f"{agent.metadata.name} should be concurrency safe"
            assert agent.metadata.is_read_only, f"{agent.metadata.name} should be read-only"

    def test_all_agents_different_priorities(self):
        """Each agent should have a distinct priority for scheduling."""
        agents = [
            SecurityAgent(llm=LLMClient(api_key="fake")),
            LogicAgent(llm=LLMClient(api_key="fake")),
            PerformanceAgent(llm=LLMClient(api_key="fake")),
            StyleAgent(llm=LLMClient(api_key="fake")),
        ]
        priorities = {a.metadata.priority for a in agents}
        assert len(priorities) == 4, "All agents should have distinct priorities"
