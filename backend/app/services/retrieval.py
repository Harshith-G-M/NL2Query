"""Lightweight retrieval (TF-IDF + cosine, pure Python) used for schema RAG and few-shot examples.

Why not embeddings? For schemas of a few dozen tables, lexical retrieval over table/column
names and categorical values is fast, free and deterministic. The `Index` interface is small
on purpose so it can be swapped for pgvector / Ollama embeddings later.
"""
import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    toks = _TOKEN.findall(text.lower().replace("_", " "))
    # crude plural stemming so "orders" matches "order"
    return [t[:-1] if len(t) > 3 and t.endswith("s") else t for t in toks]


class Index:
    def __init__(self, docs: list[str]):
        self.docs = [Counter(tokenize(d)) for d in docs]
        n = max(len(docs), 1)
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.vecs = [self._vec(d) for d in self.docs]

    def _vec(self, counts: Counter) -> dict[str, float]:
        v = {t: c * self.idf.get(t, 1.0) for t, c in counts.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def scores(self, query: str) -> list[float]:
        q = self._vec(Counter(tokenize(query)))
        return [sum(w * dv.get(t, 0.0) for t, w in q.items()) for dv in self.vecs]

    def top(self, query: str, k: int, min_score: float = 0.0) -> list[tuple[int, float]]:
        ranked = sorted(enumerate(self.scores(query)), key=lambda p: p[1], reverse=True)
        return [(i, s) for i, s in ranked[:k] if s > min_score]
