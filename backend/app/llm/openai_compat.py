"""Any OpenAI-compatible chat API: Groq, Gemini (OpenAI endpoint), OpenRouter, vLLM, OpenAI..."""
import time

import httpx

from .base import LLMError, LLMProvider, LLMResult, strip_thinking


class OpenAICompatProvider(LLMProvider):
    name = "openai"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 90):
        if not api_key:
            raise LLMError("OPENAI_API_KEY is not set (needed for LLM_PROVIDER=openai).")
        self.base_url, self.api_key, self.model, self.timeout = base_url.rstrip("/"), api_key, model, timeout

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> LLMResult:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last: Exception | None = None
        for attempt in range(3):  # free tiers rate-limit aggressively: honour Retry-After briefly
            try:
                r = httpx.post(f"{self.base_url}/chat/completions", json=payload, headers=headers,
                               timeout=self.timeout)
                if r.status_code == 429 and attempt < 2:
                    time.sleep(min(float(r.headers.get("retry-after", 2)), 5))
                    continue
                r.raise_for_status()
                data = r.json()
                text = strip_thinking(data["choices"][0]["message"]["content"] or "")
                usage = data.get("usage", {})
                return LLMResult(text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                last = e
                break
        raise LLMError(f"LLM request failed: {type(last).__name__}: {str(last)[:150]}")
