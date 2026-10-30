"""Per-user analytical store (DuckDB) + file ingestion + column profiling.

Each user gets their own DuckDB file. Writes (uploads) use a read-write connection;
queries use a READ-ONLY connection with external file access disabled. A per-user lock
serialises access because DuckDB does not allow mixed-mode connections to one file
inside a single process.
"""
import datetime as dt
import io
import re
import threading
import warnings
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd

from ..config import get_settings

_locks: dict[int, threading.RLock] = defaultdict(threading.RLock)

RESERVED = {
    "select", "from", "where", "group", "order", "by", "table", "user", "limit", "index", "key",
    "primary", "union", "all", "distinct", "as", "on", "join", "case", "when", "then", "end",
    "and", "or", "not", "null", "true", "false", "in", "is", "like", "having", "create", "drop",
    "insert", "update", "delete", "check", "default", "references", "desc", "asc", "offset",
}
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}


class DataError(ValueError):
    """User-facing problem with an upload."""


class QueryError(RuntimeError):
    """The database rejected or failed the query (message is fed back to the LLM)."""


class QueryTimeout(QueryError):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool


# ---------------------------------------------------------------- helpers
def to_jsonable(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return None if v != v or v in (float("inf"), float("-inf")) else v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, bytes):
        return "<binary>"
    if isinstance(v, (list, tuple)):
        return [to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): to_jsonable(x) for k, x in v.items()}
    return str(v)


def sanitize_identifier(name, fallback: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", str(name).strip()).strip("_").lower()
    if not s:
        s = fallback
    if s[0].isdigit():
        s = "c_" + s
    if s in RESERVED:
        s += "_col"
    return s[:60]


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}_{seen[n]}")
        else:
            seen[n] = 1
            out.append(n)
    return out


def user_db_path(user_id: int) -> Path:
    return Path(get_settings().data_dir) / f"user_{user_id}.duckdb"


def _connect(user_id: int, read_only: bool) -> duckdb.DuckDBPyConnection:
    path = user_db_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = {"enable_external_access": "false", "memory_limit": "512MB", "threads": "2"}
    if read_only and not path.exists():
        duckdb.connect(str(path), config=cfg).close()
    return duckdb.connect(str(path), read_only=read_only, config=cfg)


# ---------------------------------------------------------------- ingestion
_DATE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})")


def read_upload(filename: str, content: bytes) -> pd.DataFrame:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise DataError("Only .csv and .xlsx files are supported.")
    try:
        if ext == ".csv":
            try:
                df = pd.read_csv(io.BytesIO(content))
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(content), encoding="latin-1")
        else:
            df = pd.read_excel(io.BytesIO(content), engine="openpyxl")
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError, OSError) as e:
        raise DataError(f"Could not read the file: {str(e)[:150]}") from e
    except Exception as e:  # corrupt xlsx raises zipfile/openpyxl errors
        raise DataError(f"Could not read the file: {type(e).__name__}") from e

    df = df.dropna(how="all").dropna(axis=1, how="all")
    if df.empty or len(df.columns) == 0:
        raise DataError("The file contains no data.")
    if len(df.columns) > 200:
        raise DataError("Too many columns (limit 200).")

    df.columns = _dedupe([sanitize_identifier(c, f"col_{i + 1}") for i, c in enumerate(df.columns)])
    return _coerce_dates(df)


def _coerce_dates(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        # pandas >= 3 uses a dedicated string dtype instead of `object`
        if not (df[col].dtype == object or pd.api.types.is_string_dtype(df[col])):
            continue
        sample = df[col].dropna().astype(str).head(20)
        if sample.empty or not sample.map(lambda s: bool(_DATE_RE.match(s))).all():
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = pd.to_datetime(df[col], errors="coerce")
        if parsed.notna().sum() >= 0.9 * df[col].notna().sum():
            df[col] = parsed
    return df


def _profile(con: duckdb.DuckDBPyConnection, table: str) -> list[dict]:
    cols = con.execute(f'DESCRIBE "{table}"').fetchall()
    profile = []
    for name, ctype, *_ in cols:
        entry: dict = {"name": name, "type": str(ctype)}
        q = f'"{name}"'
        samples = con.execute(f'SELECT DISTINCT {q} FROM "{table}" WHERE {q} IS NOT NULL LIMIT 3').fetchall()
        entry["samples"] = [to_jsonable(r[0]) for r in samples]
        t = str(ctype).upper()
        if t.startswith("VARCHAR"):
            n = con.execute(f'SELECT COUNT(DISTINCT {q}) FROM "{table}"').fetchone()[0]
            if n <= 15:
                vals = con.execute(f'SELECT DISTINCT {q} FROM "{table}" WHERE {q} IS NOT NULL ORDER BY 1').fetchall()
                entry["distinct"] = [to_jsonable(r[0]) for r in vals]
        elif any(k in t for k in ("INT", "DOUBLE", "DECIMAL", "FLOAT", "DATE", "TIMESTAMP")):
            lo, hi = con.execute(f'SELECT MIN({q}), MAX({q}) FROM "{table}"').fetchone()
            entry["min"], entry["max"] = to_jsonable(lo), to_jsonable(hi)
        profile.append(entry)
    return profile


def create_table(user_id: int, table: str, df: pd.DataFrame) -> tuple[int, list[dict]]:
    """(Re)create `table` from a DataFrame. Returns (row_count, column_profile)."""
    with _locks[user_id]:
        con = _connect(user_id, read_only=False)
        try:
            con.register("_upload_df", df)
            con.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM _upload_df')
            con.unregister("_upload_df")
            rows = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            return rows, _profile(con, table)
        finally:
            con.close()


def drop_table(user_id: int, table: str) -> None:
    with _locks[user_id]:
        con = _connect(user_id, read_only=False)
        try:
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
        finally:
            con.close()


def existing_tables(user_id: int) -> set[str]:
    with _locks[user_id]:
        con = _connect(user_id, read_only=True)
        try:
            return {r[0].lower() for r in con.execute("SHOW TABLES").fetchall()}
        finally:
            con.close()


# ---------------------------------------------------------------- execution
def run_query(user_id: int, sql: str, max_rows: int | None = None,
              timeout_s: float | None = None) -> QueryResult:
    s = get_settings()
    max_rows = max_rows or s.max_rows
    timeout_s = timeout_s or s.query_timeout_s
    with _locks[user_id]:
        con = _connect(user_id, read_only=True)
        timer = threading.Timer(timeout_s, con.interrupt)
        timer.start()
        try:
            cur = con.execute(sql)
            columns = [d[0] for d in cur.description]
            fetched = cur.fetchmany(max_rows + 1)
        except duckdb.InterruptException as e:
            raise QueryTimeout(f"Query exceeded the {timeout_s:.0f}s time limit.") from e
        except duckdb.Error as e:
            raise QueryError(str(e).splitlines()[0][:300]) from e
        finally:
            timer.cancel()
            con.close()
    truncated = len(fetched) > max_rows
    rows = [[to_jsonable(v) for v in r] for r in fetched[:max_rows]]
    return QueryResult(columns=columns, rows=rows, truncated=truncated)
