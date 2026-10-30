import httpx

from .base import LLMError, LLMProvider, LLMResult, strip_thinking


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, url: str, model: str, timeout: float = 90):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> LLMResult:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0},
        }
        if json_mode:
            payload["format"] = "json"
        try:
            r = httpx.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise LLMError(f"Ollama request failed: {type(e).__name__}: {str(e)[:150]}") from e
        text = strip_thinking(data.get("message", {}).get("content", ""))
        return LLMResult(text, data.get("prompt_eval_count", 0), data.get("eval_count", 0))
