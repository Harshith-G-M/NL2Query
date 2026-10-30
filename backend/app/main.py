import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .config import get_settings
from .db import Base, engine
from .llm.base import LLMError
from .routers import auth, datasets, metrics, query

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("nl2query")
settings = get_settings()



PLACEHOLDER_SECRETS = {"dev-secret-change-me", "change-me-to-a-long-random-string"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.secret_key in PLACEHOLDER_SECRETS:
        if engine.dialect.name != "sqlite":
            raise RuntimeError("SECRET_KEY is a placeholder. Set a long random value: "
                               "python -c \"import secrets;print(secrets.token_hex(32))\"")
        log.warning("Using the placeholder SECRET_KEY (fine for local dev only).")
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="nl2query", version="1.0.0", lifespan=lifespan,
              description="Ask questions about your data in plain English. Read-only, guarded SQL.")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","),
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def access_log(request: Request, call_next):
    rid = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = int((time.perf_counter() - t0) * 1000)
    response.headers["X-Request-ID"] = rid
    log.info("req=%s %s %s -> %s %dms", rid, request.method, request.url.path, response.status_code, ms)
    return response


@app.exception_handler(LLMError)
async def llm_error_handler(_: Request, exc: LLMError):
    return JSONResponse(status_code=503, content={"detail": f"LLM provider unavailable: {exc}"})


@app.get("/health", tags=["ops"])
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "down"})
    return {"status": "ok", "db": "up", "llm_provider": settings.llm_provider}


app.include_router(auth.router)
app.include_router(datasets.router)
app.include_router(query.router)
app.include_router(metrics.router)
