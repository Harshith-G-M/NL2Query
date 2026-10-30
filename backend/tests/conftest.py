import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="nl2query-test-")
# Set TEST_DATABASE_URL=postgresql://... to run the whole suite against PostgreSQL (CI does this)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_TMP}/test.db")
os.environ["DATA_DIR"] = f"{_TMP}/data"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["RATE_LIMIT_PER_MIN"] = "1000"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.deps import rate_limiter  # noqa: E402
from app.llm import LLMProvider, LLMResult, get_provider  # noqa: E402
from app.main import app  # noqa: E402

SALES_CSV = (
    "Order ID,Order Date,Region,Product Category,Revenue\n"
    "1,2024-01-05,North,Tech,100.5\n"
    "2,2024-01-17,South,Tech,200\n"
    "3,2024-02-02,North,Home,50\n"
    "4,2024-02-20,East,Home,75.25\n"
    "5,2024-03-11,South,Tech,310\n"
)


class ScriptedProvider(LLMProvider):
    """Returns pre-programmed SQL so tests are deterministic and need no LLM."""

    name = "scripted"
    model = "test"

    def __init__(self):
        self.sql_responses: list[str] = []
        self.json_response = '{"queries": []}'
        self.calls: list[tuple[str, str]] = []
        self.fail = False

    def complete(self, system, user, *, json_mode=False):
        from app.llm import LLMError
        self.calls.append((system, user))
        if self.fail:
            raise LLMError("scripted outage")
        if "Write ONE read-only DuckDB SQL" in system:
            text = self.sql_responses.pop(0) if self.sql_responses else "SELECT 1"
        elif json_mode:
            text = self.json_response
        elif "careful data analyst" in system:
            text = "Scripted explanation."
        else:
            text = "Scripted reply."
        return LLMResult(text, 100, 20)

    def sql_prompts(self):
        return [u for s, u in self.calls if "Write ONE read-only DuckDB SQL" in s]


@pytest.fixture
def provider():
    return ScriptedProvider()


@pytest.fixture
def client(provider):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    shutil.rmtree(get_settings().data_dir, ignore_errors=True)
    rate_limiter.reset()
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def register_and_login(client, email="a@example.com", password="password123"):
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    r = client.post("/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def upload_sales(client, headers, name="sales.csv", content=SALES_CSV):
    return client.post("/datasets", headers=headers, files={"file": (name, content.encode(), "text/csv")})


@pytest.fixture
def auth(client):
    return register_and_login(client)


@pytest.fixture
def sales(client, auth):
    r = upload_sales(client, auth)
    assert r.status_code == 201, r.text
    return r.json()
