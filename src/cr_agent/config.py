"""Application configuration via environment variables."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """CR-Agent configuration. All values can be overridden via environment variables."""

    # DeepSeek API
    deepseek_api_key: str = Field(
        default="",
        validation_alias="DEEPSEEK_API_KEY",
    )
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias="DEEPSEEK_BASE_URL",
    )

    # Model
    model: str = Field(default="deepseek-chat", validation_alias="CR_AGENT_MODEL")
    temperature: float = Field(default=0.1, validation_alias="CR_AGENT_TEMPERATURE")
    max_retries: int = Field(default=10, validation_alias="CR_AGENT_MAX_RETRIES")
    timeout: int = Field(default=60, validation_alias="CR_AGENT_TIMEOUT")

    # Review
    confidence_threshold: float = Field(
        default=0.7, validation_alias="CR_AGENT_CONFIDENCE_THRESHOLD"
    )
    max_concurrent_agents: int = Field(
        default=10, validation_alias="CR_AGENT_MAX_CONCURRENT_AGENTS"
    )
    context_token_budget: int = Field(
        default=58_000,
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": False,
    }


# Singleton
settings = Settings()
