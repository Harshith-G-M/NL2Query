import pytest

from app.services.sql_guard import GuardError, validate

ALLOWED = {"sales", "customers"}


@pytest.mark.parametrize("sql", [
    "SELECT * FROM sales",
    "select region, sum(revenue) from sales group by 1 order by 2 desc limit 5",
    'SELECT "order_id" FROM "sales" WHERE revenue > 10;',
    "WITH t AS (SELECT region, SUM(revenue) r FROM sales GROUP BY 1) SELECT * FROM t",
    "SELECT s.region, c.name FROM sales s JOIN customers c ON s.customer_id = c.customer_id",
    "SELECT region, revenue, LAG(revenue) OVER (PARTITION BY region ORDER BY order_date) FROM sales",
    "SELECT 1 UNION SELECT 2",
])
def test_allows_read_only_selects(sql):
    assert validate(sql, ALLOWED)


@pytest.mark.parametrize("sql", [
    "DROP TABLE sales",
    "DELETE FROM sales",
    "UPDATE sales SET revenue = 0",
    "INSERT INTO sales VALUES (1)",
    "CREATE TABLE x AS SELECT * FROM sales",
    "ALTER TABLE sales ADD COLUMN z INT",
    "COPY sales TO '/tmp/leak.csv'",
    "ATTACH '/tmp/other.db' AS o",
    "PRAGMA database_list",
    "SELECT * FROM sales; DROP TABLE sales",
    "SELECT * INTO leaked FROM sales",
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_parquet('x.parquet')",
    "SELECT * FROM '/etc/passwd'",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM main.sales",
    "SELECT * FROM duckdb_tables()",
    "SELECT * FROM generate_series(1, 10)",
    "SELECT query('SELECT 1')",
    "SELECT * FROM somebody_elses_table",
    "",
    "   ",
    "this is not sql at all",
])
def test_blocks_everything_else(sql):
    with pytest.raises(GuardError):
        validate(sql, ALLOWED)


def test_error_message_lists_available_tables():
    with pytest.raises(GuardError, match="customers, sales"):
        validate("SELECT * FROM nope", ALLOWED)


def test_cte_names_are_not_treated_as_unknown_tables():
    assert validate("WITH x AS (SELECT 1 AS a) SELECT a FROM x", set())
