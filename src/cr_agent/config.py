"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """CR-Agent configuration. All values can be overridden via environment variables."""

    # DeepSeek API
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    # Model
    model: str = "deepseek-chat"
    temperature: float = 0.1
    max_retries: int = 10
    timeout: int = 60  # seconds per LLM call

    # Review
    confidence_threshold: float = 0.7
    max_concurrent_agents: int = 10
    context_token_budget: int = 58_000  # 64K - 4K output reserve - 2K buffer

    model_config = {
        "env_prefix": "CR_AGENT_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# Singleton
settings = Settings()
