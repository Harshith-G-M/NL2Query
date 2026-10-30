"""Central configuration. Every value can be overridden with an environment variable."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "nl2query"
    database_url: str = "sqlite:///./nl2query.db"
    data_dir: str = "./data"
    secret_key: str = "dev-secret-change-me"
    access_token_minutes: int = 720
    admin_emails: str = ""
    cors_origins: str = "*"

    # LLM: "ollama" (local), "openai" (any OpenAI-compatible API: Groq, Gemini, OpenRouter...), "mock"
    llm_provider: str = "ollama"
    llm_timeout_s: float = 90
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    openai_base_url: str = "https://api.groq.com/openai/v1"
    openai_api_key: str = ""
    openai_model: str = "llama-3.3-70b-versatile"
    # Cost tracking: set these to your provider's real prices (USD per million tokens). 0 = free/local.
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0

    # Query execution limits
    max_rows: int = 500
    query_timeout_s: float = 15
    max_sql_retries: int = 2
    explain_answers: bool = True

    # Upload / abuse limits
    max_upload_mb: int = 10
    max_datasets_per_user: int = 10
    rate_limit_per_min: int = 30

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        # Render / Heroku / Neon style URLs
        if url.startswith("postgres://"):
            url = "postgresql+psycopg2://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
