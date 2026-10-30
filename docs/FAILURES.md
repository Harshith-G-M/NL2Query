# What broke and how it was fixed

A running log. Each entry: symptom → root cause → fix → how it is prevented now. Entries 1-4 happened while building
this project; **add yours as you run the stack and the model evaluations**. This is the document that shows you debug real systems.

## 1. Uploaded date columns were silently stored as text
- **Symptom:** a test asserting a parsed date column failed; `order_date` was a plain string.
- **Root cause:** pandas 3 stores strings in a dedicated `str` dtype, not `object`; my date-coercion loop only inspected
  `object` columns, so it skipped every text column. Queries using `strftime`/`EXTRACT` would have failed on real uploads.
- **Fix:** detect string columns with `pd.api.types.is_string_dtype`.
- **Prevented by:** `test_upload_sanitizes_names_and_parses_dates`; versions are pinned in `requirements.txt`.

## 2. Schema retrieval dropped the table needed for a join
- **Symptom:** with a wide schema, a question about orders returned only the `orders` table, with its join partner
  `customers` missing from the prompt, so the model could not write the join.
- **Root cause:** retrieval kept only tables with a non-zero lexical score; join partners often share only a key column name.
- **Fix:** after retrieval, add tables sharing a column name with a selected table (up to the prompt limit).
- **Prevented by:** `test_retrieval_pulls_in_join_partners_sharing_a_column`.

## 3. The "PostgreSQL" test run was secretly SQLite
- **Symptom:** I ran the API tests with `DATABASE_URL` pointing at Postgres and they passed, but `conftest.py` had hard-coded SQLite and overwrote the variable, so nothing had run on Postgres.
- **Fix:** `TEST_DATABASE_URL` override, plus `test_dialect.py` which fails if the active dialect isn't the requested one. The full suite was then run on PostgreSQL 16.
- **Lesson:** a green result only means something if you can prove what it ran against.

## 4. Streamlit API deprecation
- **Symptom:** warnings on every render: `use_container_width` is deprecated.
- **Fix:** switched to `width="stretch"`.

## Template for your own entries

```
## N. <short title>
- Symptom:
- Root cause:
- Fix:
- Prevented by (test / eval question / monitoring):
```

Likely candidates once you run real models: hallucinated column names, wrong date-function dialect, `LIMIT` missing on
"top N", over-aggressive guard rejections, slow first request on a sleeping free host, Groq rate limits (429).
