import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


class LLMError(RuntimeError):
    """Provider unreachable, timed out, or returned something unusable."""


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    def complete(self, system: str, user: str, *, json_mode: bool = False) -> LLMResult: ...


_THINK = re.compile(r"<think>.*?</think>", re.S)


def strip_thinking(text: str) -> str:
    """Reasoning models (qwen3, deepseek-r1) wrap chain-of-thought in <think> tags."""
    return _THINK.sub("", text).strip()
