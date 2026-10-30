# nl2query

Ask questions about your own data in plain English. Upload a CSV/Excel file, ask *"revenue by region as a pie chart"*,
and get the SQL, the result table, a chart and a short explanation. Every query is validated and run **read-only**
with row, time and memory limits; if the generated SQL fails, the agent reads the error and fixes it itself.

Built as a deployable, multi-user system rather than a notebook: FastAPI + PostgreSQL + DuckDB + LangGraph, Docker,
CI, an evaluation harness with a measured accuracy number, and a metrics dashboard.

## Features

| Area | What it does |
|---|---|
| **Auth & multi-tenancy** | JWT login, bcrypt passwords, per-user isolated DuckDB store, ownership checks (404 not 403), rate limiting |
| **Data ingestion** | CSV/XLSX upload, column-name sanitising, automatic date parsing, per-column profiling (types, ranges, categorical values) |
| **Agent (LangGraph)** | Router (data question / insights / chat) → SQL generation → guard + execute → **self-correction loop** → explanation + chart |
| **Schema RAG** | TF-IDF retrieval narrows wide schemas to relevant tables/columns and pulls in join partners; 👍 answers become few-shot examples |
| **Safety** | 4-layer SQL defence (below), prompt-injection hardening of schema/sample values, upload size/count limits |
| **Observability** | Per-request latency, tokens, cost, attempts, success/retry rate, p50/p95, user feedback → `/metrics/me`, `/metrics/admin`, UI tab |
| **Evaluation** | 31-question execution-accuracy suite over two joinable tables, per-difficulty breakdown, failure report |
| **Providers** | Ollama (local, private) or any OpenAI-compatible API (Groq, Gemini, OpenRouter…) behind one interface |
| **Ops** | Docker + compose, GitHub Actions (lint, tests on SQLite *and* PostgreSQL, eval self-check, compose smoke test), Render blueprint |

## Architecture

```mermaid
flowchart LR
  UI[Streamlit UI] -->|JWT / REST| API[FastAPI]
  API --> PG[(PostgreSQL<br/>users, datasets, query logs, uploads)]
  API --> AG[LangGraph agent]
  AG -->|prompt| LLM{{LLM provider<br/>Ollama or OpenAI-compatible}}
  AG --> G[SQL guard<br/>sqlglot]
  G --> DK[(DuckDB file per user<br/>read-only connection)]
  AG --> CH[Chart suggester]
  API --> M[Metrics]
  PG --- M
```

Details, data model, agent graph and trade-offs: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

### Option A: Docker (recommended)

```bash
cp .env.example .env
# edit .env: set SECRET_KEY (python -c "import secrets;print(secrets.token_hex(32))") and choose an LLM provider
docker compose up --build
```
UI → http://localhost:8501  ·  API docs → http://localhost:8000/docs

For local Ollama, run `ollama serve` and `ollama pull qwen2.5-coder:7b` on your machine; the API container reaches it
through `host.docker.internal`. No LLM yet? Set `LLM_PROVIDER=mock` to try the whole flow with trivial SQL.

### Option B: without Docker (Windows PowerShell shown)

```powershell
cd backend
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy ..\.env.example .env        # edit it
uvicorn app.main:app --reload    # uses SQLite + ./data when DATABASE_URL is not set

# second terminal
cd frontend
pip install -r requirements.txt
$env:API_URL = "http://localhost:8000"
streamlit run app.py
```

## Configuration

All settings are environment variables (see `.env.example`). The important ones:

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./nl2query.db` | Use PostgreSQL in production (`postgresql://…`; `postgres://` also accepted) |
| `SECRET_KEY` | placeholder | **Required** on non-SQLite databases (the app refuses to start otherwise) |
| `LLM_PROVIDER` | `ollama` | `ollama`, `openai` (any OpenAI-compatible endpoint) or `mock` |
| `PRICE_IN_PER_MTOK` / `PRICE_OUT_PER_MTOK` | `0` | Your provider's real prices, to get cost tracking |
| `MAX_ROWS`, `QUERY_TIMEOUT_S`, `MAX_UPLOAD_MB`, `RATE_LIMIT_PER_MIN` | 500, 15, 10, 30 | Limits |

## API

| Method & path | Purpose |
|---|---|
| `POST /auth/register`, `POST /auth/login`, `GET /auth/me` | Accounts and JWT |
| `POST /datasets` · `GET /datasets` · `GET /datasets/{id}/preview` · `DELETE /datasets/{id}` | Manage uploaded data |
| `POST /query` | Ask a question (`parent_query_id` makes it a follow-up) |
| `GET /query/history` · `POST /query/{id}/feedback` | History and 👍/👎 |
| `GET /metrics/me` · `GET /metrics/admin` | Operational metrics (admin = emails in `ADMIN_EMAILS`) |
| `GET /health` | Liveness + DB check |

Interactive docs at `/docs`.

## SQL safety model

The LLM is untrusted. Generated SQL passes four independent layers:

1. **Validator** (`services/sql_guard.py`): parsed with sqlglot; exactly one statement; must be a `SELECT`/`WITH`; only the user's own tables; no table functions, file paths, schema-qualified names, `INTO`, or file/system functions (`read_csv`, `duckdb_*`, `query`, …).
2. **Database configuration**: DuckDB opened `read_only=True` with `enable_external_access=false` and a memory limit. Tests prove writes and file reads fail *even if the validator is bypassed*.
3. **Execution limits**: row cap and wall-clock timeout with query interruption.
4. **Isolation**: one DuckDB file per user, so other users' tables do not exist on the connection.

Schema text sent to the model (column names, sample values from the user's file) is treated as untrusted: values are flattened to one line and truncated, and the system prompt tells the model not to follow instructions found in it.

## Evaluation

```bash
cd backend
python -m evals.run_eval --provider oracle      # harness self-check, must be 100%
python -m evals.run_eval --tag qwen2.5-coder-7b # real run using LLM_PROVIDER from your .env
python -m evals.run_eval --tag llama-3.3-70b    # after switching to Groq
```

Writes `evals/results/<tag>.md` and `.json`: execution accuracy overall and by difficulty, first-attempt-valid rate,
average attempts, latency, tokens, and a list of every failure with generated vs expected SQL.
**Run it, commit the results, and put the real number here:**

| Model | Execution accuracy | First-try valid | Avg attempts | p95 latency |
|---|---|---|---|---|
| _your run_ | | | | |

The workflow that makes this valuable: read the failures → change the prompt / retrieval → re-run → record the delta in
[docs/FAILURES.md](docs/FAILURES.md). Grow `evals/eval_set.json` toward 100 questions over more datasets as you go.

## Tests

```bash
cd backend
pytest -q                                                          # SQLite
TEST_DATABASE_URL=postgresql://user:pass@localhost/db pytest -q    # PostgreSQL
```
85 tests: SQL-guard attack cases, datastore limits/timeouts/read-only enforcement, agent self-correction and retry
exhaustion, LLM outage handling, tenant isolation, auth, rate limiting, feedback loop, table rebuild after disk wipe,
metrics, and the evaluation harness itself (a perfect answer must score 100%, a wrong one 0%).

## Deployment (Render + Neon + Groq, all free tiers)

1. **Database**: create a free Postgres at neon.tech; copy the connection string.
2. **LLM**: create a Groq API key (or use any OpenAI-compatible provider).
3. **Push this repo to GitHub**, then in Render: *New → Blueprint →* select the repo (`render.yaml`).
4. Fill the prompted variables on `nl2query-api`: `DATABASE_URL`, `OPENAI_API_KEY`, `ADMIN_EMAILS`, and after the UI
   service exists, `CORS_ORIGINS=https://<your-ui>.onrender.com`.
5. On `nl2query-ui` set `API_URL=https://<your-api>.onrender.com`.
6. Every push to `main` redeploys after CI passes. Put the live URL and a 2-minute demo video at the top of this README.

**Deployment notes**
- Free web services sleep when idle (first request is slow) and have **ephemeral disks**. nl2query keeps each upload
  in PostgreSQL and rebuilds the user's DuckDB tables on demand, so data survives restarts. That is why uploads are capped at 10 MB.
- The in-memory rate limiter and per-user DuckDB lock are per-process; run a single API instance, or move them to Redis
  and a shared store before scaling out.
- Ollama cannot run on free hosts; production uses the hosted provider, local development uses Ollama. Same code, one env var.

## Known limitations (be upfront about these in interviews)

- Schema changes use `create_all`; add **Alembic** migrations before a real production rollout.
- Retrieval is lexical (TF-IDF). Embeddings (e.g. `nomic-embed-text` via Ollama, or pgvector) are a drop-in upgrade behind `services/retrieval.Index`.
- Queries for one user are serialised by a lock (DuckDB cannot mix read-only and read-write connections to one file in a process).
- No email verification, password reset or token refresh.
- Execution accuracy is measured on synthetic sales/customer data; real-world messy schemas will score lower.
- The "why did X change?" multi-step investigation mode from the prototype is not ported; the insights route covers overview-style requests.

## Verification status (what was and wasn't actually run)

Run and passing when this was built: all 85 tests on SQLite and on PostgreSQL 16; a live `uvicorn` + HTTP smoke test
(register → upload → query → 401 on missing token); the Streamlit UI driven end-to-end against that live API;
the evaluation harness self-check (oracle = 100%, wrong answers = 0%).

**Not run (no Docker or LLM in the build environment):** `docker compose up`, the Docker image builds, the GitHub Actions
workflow, the Render deploy, and any real-model accuracy number. Expect to fix small things on first run of those;
`docs/FAILURES.md` is where you record them.
