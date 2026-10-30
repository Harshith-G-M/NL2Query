from functools import lru_cache

from ..config import get_settings
from .base import LLMError, LLMProvider
from .mock import MockProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider


@lru_cache
def _build(provider: str, url: str, model: str, base: str, key: str, omodel: str, timeout: float) -> LLMProvider:
    if provider == "ollama":
        return OllamaProvider(url, model, timeout)
    if provider == "openai":
        return OpenAICompatProvider(base, key, omodel, timeout)
    if provider == "mock":
        return MockProvider()
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}' (use ollama | openai | mock).")


def get_provider() -> LLMProvider:
    """FastAPI dependency; tests override it with a scripted provider."""
    s = get_settings()
    return _build(s.llm_provider, s.ollama_url, s.ollama_model, s.openai_base_url,
                  s.openai_api_key, s.openai_model, s.llm_timeout_s)
