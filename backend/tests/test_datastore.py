import pytest

from app.services import datastore as ds


def test_upload_sanitizes_names_and_parses_dates():
    df = ds.read_upload("My Sales.csv", b"Order ID,Order Date,Group,Order ID\n1,2024-01-05,a,9\n2,2024-02-06,b,8\n")
    assert list(df.columns) == ["order_id", "order_date", "group_col", "order_id_1"]
    assert str(df["order_date"].dtype).startswith("datetime64")


@pytest.mark.parametrize("name,content", [
    ("evil.exe", b"MZ"),
    ("empty.csv", b""),
    ("blank.csv", b"a,b\n,\n"),
    ("broken.xlsx", b"not an excel file"),
])
def test_bad_uploads_raise_data_error(name, content):
    with pytest.raises(ds.DataError):
        ds.read_upload(name, content)


def test_create_table_profile_and_query(tmp_path, monkeypatch):
    monkeypatch.setattr(ds.get_settings(), "data_dir", str(tmp_path))
    df = ds.read_upload("t.csv", b"region,amount\nN,10\nS,20\nN,30\n")
    rows, profile = ds.create_table(1, "t", df)
    assert rows == 3
    by_name = {c["name"]: c for c in profile}
    assert by_name["region"]["distinct"] == ["N", "S"]
    assert (by_name["amount"]["min"], by_name["amount"]["max"]) == (10, 30)

    res = ds.run_query(1, "SELECT region, SUM(amount) AS total FROM t GROUP BY 1 ORDER BY 1")
    assert res.columns == ["region", "total"] and res.rows == [["N", 40], ["S", 20]]


def test_row_cap_sets_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(ds.get_settings(), "data_dir", str(tmp_path))
    ds.create_table(2, "n", ds.read_upload("n.csv", ("x\n" + "\n".join(map(str, range(50)))).encode()))
    res = ds.run_query(2, "SELECT * FROM n", max_rows=10)
    assert len(res.rows) == 10 and res.truncated


def test_database_layer_blocks_writes_and_file_access_even_without_guard(tmp_path, monkeypatch):
    """Layer 2 of defence in depth: DuckDB itself refuses, independent of sql_guard."""
    monkeypatch.setattr(ds.get_settings(), "data_dir", str(tmp_path))
    ds.create_table(3, "t", ds.read_upload("t.csv", b"a\n1\n"))
    for sql in ["DROP TABLE t", "CREATE TABLE x(a INT)", "INSERT INTO t VALUES (2)",
                "SELECT * FROM read_csv('/etc/passwd')", "COPY t TO '/tmp/leak_nl2query.csv'"]:
        with pytest.raises(ds.QueryError):
            ds.run_query(3, sql)
    assert ds.run_query(3, "SELECT COUNT(*) FROM t").rows == [[1]]


def test_timeout_interrupts_long_query(tmp_path, monkeypatch):
    monkeypatch.setattr(ds.get_settings(), "data_dir", str(tmp_path))
    ds.create_table(4, "t", ds.read_upload("t.csv", b"a\n1\n"))
    with pytest.raises(ds.QueryTimeout):
        ds.run_query(4, "SELECT COUNT(*) FROM range(100000000000) a, range(1000) b", timeout_s=0.5)
