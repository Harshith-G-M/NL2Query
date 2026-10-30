import os

from app.db import engine


def test_suite_runs_against_the_requested_database():
    expected = "postgresql" if os.environ.get("TEST_DATABASE_URL", "").startswith("postgres") else "sqlite"
    assert engine.dialect.name == expected
