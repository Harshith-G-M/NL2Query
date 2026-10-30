from app.services.charts import suggest_chart


def test_category_and_number_gives_bar():
    c = suggest_chart(["region", "total"], [["N", 1], ["S", 2], ["E", 3]], "revenue by region")
    assert c["type"] == "bar" and c["x"] == "region" and c["y"] == "total"


def test_time_axis_gives_line():
    c = suggest_chart(["month", "rev"], [["2024-01", 1], ["2024-02", 2]], "monthly revenue")
    assert c["type"] == "line"


def test_pie_hint_respected_for_small_categorical():
    c = suggest_chart(["region", "total"], [["N", 1], ["S", 2]], "show that as a pie chart")
    assert c["type"] == "pie"


def test_no_chart_for_scalars_or_opt_out():
    assert suggest_chart(["total"], [[5]], "total") is None
    assert suggest_chart(["a", "b"], [["x", 1], ["y", 2]], "revenue by region, no chart") is None


def test_two_numeric_columns_give_scatter():
    assert suggest_chart(["a", "b"], [[1, 2], [3, 4]], "relation")["type"] == "scatter"
