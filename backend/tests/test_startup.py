import pytest
from fastapi import FastAPI

from app import main


@pytest.mark.anyio
async def test_placeholder_secret_refused_on_non_sqlite(monkeypatch):
    monkeypatch.setattr(main.settings, "secret_key", "dev-secret-change-me")
    monkeypatch.setattr(main.engine.dialect, "name", "postgresql")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        async with main.lifespan(FastAPI()):
            pass


@pytest.mark.anyio
async def test_placeholder_secret_allowed_on_sqlite(monkeypatch):
    monkeypatch.setattr(main.settings, "secret_key", "dev-secret-change-me")
    monkeypatch.setattr(main.engine.dialect, "name", "sqlite")
    async with main.lifespan(FastAPI()):
        pass
