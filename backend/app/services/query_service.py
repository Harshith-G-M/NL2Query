"""Glue between the HTTP layer, the agent, the data store and the metrics log."""
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..llm.base import LLMProvider
from ..models import Dataset, QueryLog, User
from . import datastore
from .agent import AgentContext, run_agent
from .retrieval import Index
from .schema_context import TableInfo, build_schema_text

log = logging.getLogger("nl2query.query")
MAX_EXAMPLES = 2
EXAMPLE_MIN_SCORE = 0.25


class NoDatasets(ValueError):
    pass


def ensure_tables(db: Session, user: User, datasets: list[Dataset]) -> None:
    """Rebuild DuckDB tables from the stored upload if the container disk was wiped
    (free hosting tiers have ephemeral disks)."""
    present = datastore.existing_tables(user.id)
    for ds in datasets:
        if ds.table_name.lower() in present:
            continue
        blob = db.execute(select(Dataset.file_blob).where(Dataset.id == ds.id)).scalar_one_or_none()
        if not blob:
            log.warning("dataset %s missing on disk and no blob stored", ds.id)
            continue
        df = datastore.read_upload(ds.filename, blob)
        datastore.create_table(user.id, ds.table_name, df)
        log.info("rebuilt table %s for user %s from stored upload", ds.table_name, user.id)


def _few_shot(db: Session, user: User, question: str) -> list[tuple[str, str]]:
    """Thumbs-up answers from this user become few-shot examples (the feedback loop)."""
    rows = db.execute(
        select(QueryLog.question, QueryLog.sql)
        .where(QueryLog.user_id == user.id, QueryLog.feedback == 1, QueryLog.status == "ok",
               QueryLog.sql.is_not(None))
        .order_by(QueryLog.created_at.desc()).limit(50)
    ).all()
    if not rows:
        return []
    idx = Index([r[0] for r in rows])
    return [(rows[i][0], rows[i][1]) for i, _ in idx.top(question, MAX_EXAMPLES, EXAMPLE_MIN_SCORE)]


def ask(db: Session, user: User, question: str, parent_id: int | None,
        provider: LLMProvider, settings: Settings) -> tuple[QueryLog, dict]:
    datasets = list(db.scalars(select(Dataset).where(Dataset.user_id == user.id).order_by(Dataset.id)))
    if not datasets:
        raise NoDatasets("Upload a dataset first.")
    ensure_tables(db, user, datasets)

    tables = [TableInfo(d.table_name, d.row_count, d.profile) for d in datasets]
    schema_text, included = build_schema_text(tables, question)

    previous = None
    if parent_id:
        parent = db.get(QueryLog, parent_id)
        if parent and parent.user_id == user.id and parent.sql:
            previous = (parent.question, parent.sql)

    ctx = AgentContext(
        provider=provider,
        schema_text=schema_text,
        allowed_tables=set(t.name for t in tables),
        executor=lambda sql: datastore.run_query(user.id, sql, settings.max_rows, settings.query_timeout_s),
        max_retries=settings.max_sql_retries,
        explain=settings.explain_answers,
        examples=_few_shot(db, user, question),
        previous=previous,
    )

    t0 = time.perf_counter()
    state = run_agent(ctx, question)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    result = state.get("result") or {"columns": [], "rows": [], "truncated": False}
    failed = bool(state.get("error")) and not state.get("result")
    u = ctx.usage
    cost = (u.prompt_tokens * settings.price_in_per_mtok + u.completion_tokens * settings.price_out_per_mtok) / 1e6

    entry = QueryLog(
        user_id=user.id, parent_id=parent_id, question=question, route=state.get("route", "data"),
        sql=state.get("sql"), answer=state.get("answer"), status="error" if failed else "ok",
        error=state.get("error") if failed else None, attempts=state.get("attempts", 0),
        row_count=len(result["rows"]),
        result_preview={"columns": result["columns"], "rows": result["rows"][:50]},
        chart=state.get("chart"), latency_ms=latency_ms, llm_calls=u.llm_calls,
        prompt_tokens=u.prompt_tokens, completion_tokens=u.completion_tokens, cost_usd=cost,
        provider=provider.name, model=provider.model,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    log.info("query id=%s user=%s route=%s status=%s attempts=%s latency_ms=%s tokens=%s/%s",
             entry.id, user.id, entry.route, entry.status, entry.attempts, latency_ms,
             u.prompt_tokens, u.completion_tokens)
    extras = {"result": result, "attempt_log": state.get("attempt_log", []),
              "insights": state.get("insights", [])}
    return entry, extras
