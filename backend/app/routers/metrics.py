"""Operational metrics: latency, success rate, retries, tokens, cost, user feedback."""
from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user, require_admin
from ..models import QueryLog, User

router = APIRouter(prefix="/metrics", tags=["metrics"])
WINDOW = 5000


def _pct(sorted_vals: list[int], p: float) -> int:
    if not sorted_vals:
        return 0
    return sorted_vals[min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1))))]


def summarize(rows: list[QueryLog]) -> dict:
    n = len(rows)
    if n == 0:
        return {"total_queries": 0}
    ok = [r for r in rows if r.status == "ok"]
    lat = sorted(r.latency_ms for r in rows)
    data_rows = [r for r in rows if r.route == "data"]
    by_day: Counter = Counter()
    errors: Counter = Counter()
    for r in rows:
        by_day[r.created_at.date().isoformat()] += 1
        if r.status == "error" and r.error:
            errors[r.error.split(":")[0][:60]] += 1
    rated = [r for r in rows if r.feedback]
    return {
        "total_queries": n,
        "success_rate": round(len(ok) / n, 4),
        "retry_rate": round(sum(1 for r in data_rows if r.attempts > 1) / len(data_rows), 4) if data_rows else 0,
        "avg_attempts": round(sum(r.attempts for r in data_rows) / len(data_rows), 2) if data_rows else 0,
        "latency_ms": {"avg": int(sum(lat) / n), "p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95), "max": lat[-1]},
        "prompt_tokens": sum(r.prompt_tokens for r in rows),
        "completion_tokens": sum(r.completion_tokens for r in rows),
        "total_cost_usd": round(sum(r.cost_usd for r in rows), 6),
        "avg_cost_usd": round(sum(r.cost_usd for r in rows) / n, 6),
        "feedback": {"up": sum(1 for r in rated if r.feedback == 1), "down": sum(1 for r in rated if r.feedback == -1)},
        "top_errors": errors.most_common(5),
        "queries_per_day": sorted(by_day.items()),
        "by_route": dict(Counter(r.route for r in rows)),
    }


@router.get("/me")
def my_metrics(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = list(db.scalars(select(QueryLog).where(QueryLog.user_id == user.id)
                           .order_by(QueryLog.id.desc()).limit(WINDOW)))
    return summarize(rows)


@router.get("/admin")
def admin_metrics(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = list(db.scalars(select(QueryLog).order_by(QueryLog.id.desc()).limit(WINDOW)))
    out = summarize(rows)
    out["active_users"] = len({r.user_id for r in rows})
    return out
