"""Application settings loaded from environment variables (12-factor)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "SmartLog Analyzer"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # Database. The same URL works for the async engine and for Alembic (psycopg 3 supports both).
    database_url: str = "postgresql+psycopg://smartlog:smartlog@localhost:5432/smartlog"
    db_pool_size: int = 5

    # LLM
    llm_provider: Literal["mock", "anthropic", "openai", "google", "ollama"] = "mock"
    llm_model: str = "claude-haiku-5-5"
    llm_api_key: SecretStr | None = None
    # Optional API endpoint: an OpenAI-compatible server, a proxy, or a remote Ollama.
    llm_base_url: str | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_tokens: int = Field(default=2000, gt=0)
    # How many times Pydantic AI sends validation errors back to the model before giving up.
    llm_output_retries: int = Field(default=1, ge=0)
    mock_llm_latency_ms: int = Field(default=300, ge=0)

    # Analysis limits: protect the LLM context window and the cost per request.
    analysis_max_logs: int = Field(default=500, gt=0)
    analysis_max_patterns: int = Field(default=50, gt=0)

    # Ingestion: store the document as received in event.original when the sender did not
    # include one. Keeps a raw copy for reprocessing and forensics, at about twice the storage.
    ingest_keep_original: bool = False

    # Pagination
    default_page_size: int = 50
    max_page_size: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()
