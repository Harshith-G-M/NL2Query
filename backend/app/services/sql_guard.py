"""SQL guardrail: the only SQL that ever reaches the database passed through validate().

Defence in depth (each layer assumes the previous one failed):
  1. This validator   - parses with sqlglot, allows exactly one read-only SELECT over the
                        user's own tables, blocks file/system functions and table functions.
  2. DuckDB config    - read-only connection, external access disabled, memory-limited.
  3. Execution limits - row cap and wall-clock timeout (see datastore.run_query).
  4. Isolation        - every user has a separate DuckDB file, so other users' tables
                        simply do not exist in the connection.
"""
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError


class GuardError(ValueError):
    """Raised when SQL is rejected. The message is safe to show to the user / feed back to the LLM."""


_FORBIDDEN_NAMES = [
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Copy", "Merge",
    "Set", "Use", "Transaction", "Commit", "Rollback", "Pragma", "Attach", "Detach",
    "Export", "Install", "Load", "TruncateTable", "AlterTable", "Grant", "Revoke",
]
FORBIDDEN_NODES = tuple(getattr(exp, n) for n in _FORBIDDEN_NAMES if hasattr(exp, n))

# DuckDB functions that read files, run nested SQL, or expose system state.
BLOCKED_FUNC_PREFIXES = ("read_", "pragma_", "duckdb_", "parquet_", "sniff_", "glob",
                         "iceberg_", "delta_", "json_read", "csv_", "write_", "getenv",
                         "current_setting", "query", "load_", "install_", "http", "s3")


def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    try:
        return node.sql_name().lower()
    except Exception:  # pragma: no cover - defensive
        return node.key.lower()


def validate(sql: str, allowed_tables: set[str]) -> str:
    """Return the cleaned SQL or raise GuardError."""
    if not sql or not sql.strip():
        raise GuardError("Empty query.")
    cleaned = sql.strip().rstrip(";").strip()
    allowed = {t.lower() for t in allowed_tables}

    try:
        statements = [s for s in sqlglot.parse(cleaned, read="duckdb") if s is not None]
    except SqlglotError as e:
        raise GuardError(f"SQL syntax error: {str(e).splitlines()[0][:200]}") from e

    if len(statements) != 1:
        raise GuardError("Exactly one SQL statement is allowed.")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise GuardError("Only read-only SELECT queries are allowed.")
    if isinstance(tree, exp.Select) and tree.args.get("into"):
        raise GuardError("SELECT ... INTO is not allowed.")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise GuardError(f"Statement type '{node.key}' is not allowed.")

        if isinstance(node, exp.Table):
            if not isinstance(node.this, exp.Identifier):
                raise GuardError("Table functions and file paths are not allowed in FROM.")
            if node.args.get("db") or node.args.get("catalog"):
                raise GuardError("Schema-qualified table names are not allowed.")
            name = node.name.lower()
            if name not in allowed and name not in cte_names:
                raise GuardError(f"Unknown table '{node.name}'. Available tables: "
                                 f"{', '.join(sorted(allowed)) or '(none)'}.")

        if isinstance(node, exp.Func):
            fname = _func_name(node)
            if fname.startswith(BLOCKED_FUNC_PREFIXES) or fname.startswith("read"):
                raise GuardError(f"Function '{fname}' is not allowed.")

    return cleaned
