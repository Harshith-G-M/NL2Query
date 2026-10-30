from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import get_current_user, query_rate_limit
from ..llm import LLMProvider, get_provider
from ..models import QueryLog, User
from ..schemas import FeedbackIn, HistoryItem, QueryIn, QueryOut
from ..services import query_service

router = APIRouter(prefix="/query", tags=["query"])


@router.post("", response_model=QueryOut)
def ask(body: QueryIn, db: Session = Depends(get_db), user: User = Depends(query_rate_limit),
        provider: LLMProvider = Depends(get_provider), settings: Settings = Depends(get_settings)):
    try:
        entry, extra = query_service.ask(db, user, body.question.strip(), body.parent_query_id, provider, settings)
    except query_service.NoDatasets as e:
        raise HTTPException(400, str(e)) from e
    res = extra["result"]
    return QueryOut(
        id=entry.id, question=entry.question, route=entry.route, status=entry.status, answer=entry.answer,
        sql=entry.sql, columns=res["columns"], rows=res["rows"], truncated=res["truncated"], chart=entry.chart,
        insights=extra["insights"], attempts=entry.attempts, attempt_log=extra["attempt_log"], error=entry.error,
        latency_ms=entry.latency_ms, llm_calls=entry.llm_calls, prompt_tokens=entry.prompt_tokens,
        completion_tokens=entry.completion_tokens, cost_usd=entry.cost_usd, provider=entry.provider,
        model=entry.model,
    )


@router.get("/history", response_model=list[HistoryItem])
def history(limit: int = 30, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.scalars(select(QueryLog).where(QueryLog.user_id == user.id)
                      .order_by(QueryLog.id.desc()).limit(min(max(limit, 1), 100)))
    out = []
    for r in rows:
        prev = r.result_preview or {}
        out.append(HistoryItem(id=r.id, question=r.question, sql=r.sql, answer=r.answer, status=r.status,
                               route=r.route, latency_ms=r.latency_ms, feedback=r.feedback,
                               created_at=r.created_at, columns=prev.get("columns", []),
                               rows=prev.get("rows", []), chart=r.chart))
    return out


@router.post("/{query_id}/feedback", status_code=204)
def feedback(query_id: int, body: FeedbackIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    entry = db.get(QueryLog, query_id)
    if not entry or entry.user_id != user.id:
        raise HTTPException(404, "Query not found")
    entry.feedback = body.value
    db.commit()
