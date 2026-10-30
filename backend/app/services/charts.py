"""Deterministic chart suggestion from a result set (no LLM call = no extra cost or latency)."""
import re

_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?")
HINTS = {"pie": "pie", "donut": "pie", "line": "line", "trend": "line", "scatter": "scatter",
         "bar": "bar", "column chart": "bar"}
NO_CHART = ("no chart", "without chart", "table only", "just the table", "don't chart", "do not chart")
MAX_POINTS = 200


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _col_kind(values: list) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "empty"
    if all(_is_num(v) for v in vals):
        return "num"
    if all(isinstance(v, str) and _DATE.match(v) for v in vals):
        return "time"
    return "cat"


def suggest_chart(columns: list[str], rows: list[list], question: str = "") -> dict | None:
    q = question.lower()
    if any(p in q for p in NO_CHART):
        return None
    if len(rows) < 2 or len(columns) < 2 or len(rows) > MAX_POINTS:
        return None

    kinds = [_col_kind([r[i] for r in rows]) for i in range(len(columns))]
    x_idx = next((i for i, k in enumerate(kinds) if k == "time"), None)
    if x_idx is None:
        x_idx = next((i for i, k in enumerate(kinds) if k == "cat"), None)
    nums = [i for i, k in enumerate(kinds) if k == "num" and i != x_idx]

    hint = next((v for k, v in HINTS.items() if k in q), None)

    if x_idx is None:
        if len(nums) >= 2:
            return {"type": "scatter", "x": columns[nums[0]], "y": columns[nums[1]]}
        return None
    if not nums:
        return None

    y = columns[nums[0]]
    x = columns[x_idx]
    ctype = "line" if kinds[x_idx] == "time" else "bar"
    if hint == "pie" and kinds[x_idx] == "cat" and len(rows) <= 12:
        ctype = "pie"
    elif hint in ("bar", "line") and hint != ctype and not (hint == "line" and kinds[x_idx] == "cat"):
        ctype = hint
    elif hint == "scatter" and len(nums) >= 1 and kinds[x_idx] != "cat":
        ctype = "scatter"
    series = [columns[i] for i in nums[:3]] if ctype in ("line", "bar") and len(nums) > 1 else [y]
    return {"type": ctype, "x": x, "y": y, "series": series}
