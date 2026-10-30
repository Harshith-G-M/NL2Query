"""Turns dataset profiles into the schema text the LLM sees (with retrieval for wide schemas)."""
import re
from dataclasses import dataclass

from .retrieval import Index

MAX_TABLES_IN_PROMPT = 5
MAX_COLS_IN_PROMPT = 60


@dataclass
class TableInfo:
    name: str
    row_count: int
    columns: list[dict]  # entries from datastore._profile


def _clean(v, n: int = 40) -> str:
    """Sample values come from user files = untrusted. Flatten and truncate them."""
    return re.sub(r"\s+", " ", str(v))[:n]


def _render_column(c: dict) -> str:
    note = ""
    if c.get("distinct"):
        note = "values: " + ", ".join(f"'{_clean(v)}'" for v in c["distinct"])
    elif c.get("min") is not None:
        note = f"range: {_clean(c['min'])} .. {_clean(c['max'])}"
    elif c.get("samples"):
        note = "e.g. " + ", ".join(_clean(v) for v in c["samples"])
    return f'  "{c["name"]}" {c["type"]}' + (f"  -- {note}" if note else "")


def render_table(t: TableInfo, columns: list[dict] | None = None) -> str:
    cols = columns if columns is not None else t.columns
    return f'TABLE "{t.name}" ({t.row_count} rows)\n' + "\n".join(_render_column(c) for c in cols)


def _table_doc(t: TableInfo) -> str:
    parts = [t.name]
    for c in t.columns:
        parts.append(c["name"])
        parts.extend(str(v) for v in c.get("distinct", [])[:15])
    return " ".join(parts)


def build_schema_text(tables: list[TableInfo], question: str) -> tuple[str, list[str]]:
    """Return (schema_text, names_of_tables_included). Small schemas are included whole;
    wide ones are narrowed by retrieval so the prompt stays small and focused."""
    if len(tables) <= MAX_TABLES_IN_PROMPT:
        chosen = tables
    else:
        idx = Index([_table_doc(t) for t in tables])
        top = [i for i, _ in idx.top(question, MAX_TABLES_IN_PROMPT)] or list(range(MAX_TABLES_IN_PROMPT))
        # Pull in tables that share a column name with a chosen one: likely join partners.
        chosen_cols = {c["name"] for i in top for c in tables[i].columns}
        for i, t in enumerate(tables):
            if len(top) >= MAX_TABLES_IN_PROMPT:
                break
            if i not in top and chosen_cols & {c["name"] for c in t.columns}:
                top.append(i)
        chosen = [tables[i] for i in sorted(top)]

    blocks = []
    for t in chosen:
        cols = t.columns
        if len(cols) > MAX_COLS_IN_PROMPT:
            idx = Index([f'{c["name"]} {" ".join(map(str, c.get("distinct", [])))}' for c in cols])
            keep = sorted(i for i, _ in idx.top(question, MAX_COLS_IN_PROMPT)) or list(range(MAX_COLS_IN_PROMPT))
            cols = [cols[i] for i in keep]
        blocks.append(render_table(t, cols))
    return "\n\n".join(blocks), [t.name for t in chosen]
