"""Execution-accuracy evaluation for the NL-to-SQL agent.

For every question the generated SQL and the hand-written expected SQL are both executed and the
result sets compared (column names ignored; row order ignored unless the expected SQL has ORDER BY;
numbers rounded to 2 dp). This is the standard "execution accuracy" metric for text-to-SQL.

    python -m evals.run_eval                       # uses LLM_PROVIDER from .env / environment
    python -m evals.run_eval --provider oracle     # harness self-check: returns expected SQL (must be 100%)
    python -m evals.run_eval --limit 5 --tag qwen2.5-coder-7b

Results are written to evals/results/<tag>.json and .md (commit them: they are your evidence).
"""
import argparse
import json
import re
import statistics
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import sqlglot

from app.config import get_settings
from app.llm.base import LLMProvider, LLMResult
from app.services import datastore
from app.services.agent import AgentContext, run_agent
from app.services.schema_context import TableInfo, build_schema_text

HERE = Path(__file__).parent
SYSTEM_UID = 0


class OracleProvider(LLMProvider):
    """Returns the expected SQL. Used to prove the harness + dataset + expected SQL are all sound."""

    name, model = "oracle", "expected-sql"

    def __init__(self, questions: list[dict]):
        self.by_q = {q["question"]: q["expected_sql"] for q in questions}

    def complete(self, system, user, *, json_mode=False):
        q = user.rsplit("Question:", 1)[-1].strip()
        return LLMResult(self.by_q.get(q, "SELECT 1"), 0, 0)


def _norm_value(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    return str(v)


def normalize(rows: list[list], ordered: bool):
    out = [tuple(_norm_value(v) for v in r) for r in rows]
    return out if ordered else sorted(out, key=repr)


def has_order_by(sql: str) -> bool:
    """True if the outermost query has an ORDER BY (so row order is part of the answer)."""
    try:
        return bool(sqlglot.parse_one(sql, read="duckdb").args.get("order"))
    except Exception:
        return bool(re.search(r"\border\s+by\b", sql, re.I))


def results_match(generated: list[list], expected_sql: str, expected: list[list]) -> bool:
    ordered = has_order_by(expected_sql)
    return normalize(generated, ordered) == normalize(expected, ordered)


def load_datasets(settings) -> list[TableInfo]:
    tables = []
    spec = json.loads((HERE / "eval_set.json").read_text())
    for fname in spec["datasets"]:
        content = (HERE / "datasets" / fname).read_bytes()
        name = datastore.sanitize_identifier(Path(fname).stem, "data")
        rows, profile = datastore.create_table(SYSTEM_UID, name, datastore.read_upload(fname, content))
        tables.append(TableInfo(name, rows, profile))
    return tables


def evaluate(provider: LLMProvider, questions: list[dict], tables: list[TableInfo], max_retries: int = 2,
             verbose: bool = True) -> dict:
    allowed = {t.name for t in tables}
    records = []
    for q in questions:
        schema_text, _ = build_schema_text(tables, q["question"])
        ctx = AgentContext(provider=provider, schema_text=schema_text, allowed_tables=allowed,
                           executor=lambda sql: datastore.run_query(SYSTEM_UID, sql, max_rows=10_000),
                           max_retries=max_retries, explain=False)
        t0 = time.perf_counter()
        state = run_agent(ctx, q["question"])
        latency = int((time.perf_counter() - t0) * 1000)
        expected = datastore.run_query(SYSTEM_UID, q["expected_sql"], max_rows=10_000)
        got = state.get("result")
        correct = bool(got) and results_match(got["rows"], q["expected_sql"], expected.rows)
        first_try = bool(state.get("attempt_log")) and state["attempt_log"][0]["error"] is None
        records.append({"id": q["id"], "difficulty": q["difficulty"], "question": q["question"],
                        "correct": correct, "attempts": state.get("attempts", 0), "first_try_valid": first_try,
                        "latency_ms": latency, "prompt_tokens": ctx.usage.prompt_tokens,
                        "completion_tokens": ctx.usage.completion_tokens, "generated_sql": state.get("sql"),
                        "expected_sql": q["expected_sql"], "error": state.get("error")})
        if verbose:
            print(f"[{'PASS' if correct else 'FAIL'}] #{q['id']:>2} ({q['difficulty']:<6}) {latency:>6} ms  {q['question']}")

    n = len(records)
    by_diff: dict[str, list] = defaultdict(list)
    for r in records:
        by_diff[r["difficulty"]].append(r["correct"])
    lat = sorted(r["latency_ms"] for r in records)
    return {
        "provider": provider.name, "model": provider.model, "questions": n,
        "execution_accuracy": round(sum(r["correct"] for r in records) / n, 4) if n else 0,
        "accuracy_by_difficulty": {d: round(sum(v) / len(v), 4) for d, v in by_diff.items()},
        "first_try_valid_rate": round(sum(r["first_try_valid"] for r in records) / n, 4) if n else 0,
        "avg_attempts": round(statistics.mean(r["attempts"] for r in records), 2) if n else 0,
        "latency_ms": {"avg": int(statistics.mean(lat)) if lat else 0, "p95": lat[int(0.95 * (len(lat) - 1))] if lat else 0},
        "total_prompt_tokens": sum(r["prompt_tokens"] for r in records),
        "total_completion_tokens": sum(r["completion_tokens"] for r in records),
        "records": records,
    }


def to_markdown(rep: dict, tag: str) -> str:
    lines = [f"# Eval: {tag}", "",
             f"- Provider / model: `{rep['provider']}` / `{rep['model']}`",
             f"- Questions: {rep['questions']}",
             f"- **Execution accuracy: {rep['execution_accuracy']:.1%}**",
             f"- First-attempt valid SQL: {rep['first_try_valid_rate']:.1%} (avg attempts {rep['avg_attempts']})",
             f"- Latency: avg {rep['latency_ms']['avg']} ms, p95 {rep['latency_ms']['p95']} ms",
             f"- Tokens: {rep['total_prompt_tokens']} prompt / {rep['total_completion_tokens']} completion", "",
             "| Difficulty | Accuracy |", "|---|---|"]
    lines += [f"| {d} | {a:.1%} |" for d, a in rep["accuracy_by_difficulty"].items()]
    fails = [r for r in rep["records"] if not r["correct"]]
    if fails:
        lines += ["", "## Failures (read these: they drive your next prompt/RAG improvement)", ""]
        for r in fails:
            lines += [f"**#{r['id']} {r['question']}**", f"- generated: `{r['generated_sql']}`",
                      f"- expected:  `{r['expected_sql']}`", f"- error: {r['error']}", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="configured", choices=["configured", "oracle"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--max-retries", type=int, default=None)
    args = ap.parse_args()

    settings = get_settings()
    settings.data_dir = tempfile.mkdtemp(prefix="nl2query-eval-")
    spec = json.loads((HERE / "eval_set.json").read_text())
    questions = spec["questions"][: args.limit or None]

    if args.provider == "oracle":
        provider: LLMProvider = OracleProvider(spec["questions"])
    else:
        from app.llm import get_provider
        provider = get_provider()
    tag = args.tag or f"{provider.name}-{provider.model}".replace("/", "_").replace(":", "-")

    tables = load_datasets(settings)
    rep = evaluate(provider, questions, tables, args.max_retries if args.max_retries is not None else settings.max_sql_retries)
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / f"{tag}.json").write_text(json.dumps(rep, indent=2))
    (out / f"{tag}.md").write_text(to_markdown(rep, tag))
    print(f"\nExecution accuracy: {rep['execution_accuracy']:.1%}  ({rep['questions']} questions)  -> evals/results/{tag}.md")


if __name__ == "__main__":
    main()
