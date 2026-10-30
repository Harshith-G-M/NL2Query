"""The evaluation harness must itself be trustworthy: it must score a perfect answer 100%
and a wrong answer 0%, and every expected SQL must pass the production guard."""
import json
import tempfile

import pytest

from app.config import get_settings
from app.llm.base import LLMProvider, LLMResult
from app.services import sql_guard
from evals import run_eval as ev


@pytest.fixture(scope="module")
def spec():
    return json.loads((ev.HERE / "eval_set.json").read_text())


@pytest.fixture(scope="module")
def tables():
    get_settings().data_dir = tempfile.mkdtemp(prefix="nl2query-evaltest-")
    return ev.load_datasets(get_settings())


def test_eval_set_has_enough_varied_questions(spec):
    qs = spec["questions"]
    assert len(qs) >= 30
    assert {q["difficulty"] for q in qs} == {"easy", "medium", "hard"}
    assert len({q["question"] for q in qs}) == len(qs)


def test_every_expected_sql_passes_the_production_guard(spec, tables):
    allowed = {t.name for t in tables}
    for q in spec["questions"]:
        sql_guard.validate(q["expected_sql"], allowed)


def test_oracle_scores_100_percent(spec, tables):
    rep = ev.evaluate(ev.OracleProvider(spec["questions"]), spec["questions"], tables, verbose=False)
    assert rep["execution_accuracy"] == 1.0 and rep["first_try_valid_rate"] == 1.0


class Wrong(LLMProvider):
    name, model = "wrong", "constant"

    def complete(self, system, user, *, json_mode=False):
        return LLMResult("SELECT 12345 AS nonsense", 0, 0)


def test_wrong_answers_score_zero(spec, tables):
    rep = ev.evaluate(Wrong(), spec["questions"][:10], tables, verbose=False)
    assert rep["execution_accuracy"] == 0.0


def test_comparison_rules():
    assert ev.results_match([[2, "b"], [1, "a"]], "SELECT x, y FROM t", [[1, "a"], [2, "b"]])           # order ignored
    assert not ev.results_match([[2, "b"], [1, "a"]], "SELECT x, y FROM t ORDER BY 1", [[1, "a"], [2, "b"]])  # order matters
    assert ev.results_match([[1.001]], "SELECT 1", [[1.0]])                                              # 2 dp rounding
    assert ev.results_match([[3]], "SELECT 1", [[3.0]])                                                  # int vs float
    assert not ev.results_match([[1]], "SELECT 1", [[1], [1]])                                           # duplicates matter
