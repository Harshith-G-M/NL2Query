"""Deterministic offline provider so the whole stack can be demoed and tested with no LLM."""
import re

from .base import LLMProvider, LLMResult


class MockProvider(LLMProvider):
    name = "mock"
    model = "rule-based"

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> LLMResult:
        if "Write ONE read-only DuckDB SQL" in system:
            m = re.search(r'TABLE "(\w+)"', user)
            table = m.group(1) if m else "data"
            q = user.rsplit("Question:", 1)[-1].lower()
            sql = f'SELECT COUNT(*) AS total_rows FROM "{table}"' if "how many" in q else \
                f'SELECT * FROM "{table}" LIMIT 10'
            return LLMResult(sql, 50, 15)
        if json_mode:
            return LLMResult("[]", 20, 2)
        return LLMResult("Here is what I found in your data.", 40, 10)
