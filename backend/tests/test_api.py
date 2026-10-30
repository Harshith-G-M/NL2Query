from app.config import get_settings

from .conftest import register_and_login, upload_sales


# ------------------------------------------------------------- auth
def test_register_login_me(client):
    h = register_and_login(client, "Me@Example.com")
    r = client.get("/auth/me", headers=h)
    assert r.status_code == 200 and r.json()["email"] == "me@example.com" and r.json()["is_admin"] is False


def test_duplicate_wrong_password_and_validation(client):
    register_and_login(client, "x@example.com")
    assert client.post("/auth/register", json={"email": "x@example.com", "password": "password123"}).status_code == 409
    assert client.post("/auth/login", data={"username": "x@example.com", "password": "wrong-pass"}).status_code == 401
    assert client.post("/auth/register", json={"email": "not-an-email", "password": "password123"}).status_code == 422
    assert client.post("/auth/register", json={"email": "y@example.com", "password": "short"}).status_code == 422


def test_protected_routes_need_a_token(client):
    for method, path in [("get", "/datasets"), ("post", "/query"), ("get", "/query/history"), ("get", "/metrics/me")]:
        assert getattr(client, method)(path).status_code == 401
    assert client.get("/datasets", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


# ------------------------------------------------------------- datasets
def test_upload_list_preview_delete(client, auth):
    r = upload_sales(client, auth)
    assert r.status_code == 201
    ds = r.json()
    assert ds["table_name"] == "sales" and ds["row_count"] == 5
    assert [c["name"] for c in ds["columns"]] == ["order_id", "order_date", "region", "product_category", "revenue"]

    assert upload_sales(client, auth).json()["table_name"] == "sales_2"  # same filename -> unique table
    assert len(client.get("/datasets", headers=auth).json()) == 2

    pv = client.get(f"/datasets/{ds['id']}/preview", headers=auth).json()
    assert len(pv["rows"]) == 5 and pv["columns"][0] == "order_id"

    assert client.delete(f"/datasets/{ds['id']}", headers=auth).status_code == 204
    assert len(client.get("/datasets", headers=auth).json()) == 1


def test_upload_validation(client, auth):
    assert client.post("/datasets", headers=auth, files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert client.post("/datasets", headers=auth, files={"file": ("e.csv", b"", "text/csv")}).status_code == 400
    s = get_settings()
    old = s.max_upload_mb
    s.max_upload_mb = 0
    try:
        assert upload_sales(client, auth).status_code == 413
    finally:
        s.max_upload_mb = old


def test_dataset_limit(client, auth):
    s = get_settings()
    old = s.max_datasets_per_user
    s.max_datasets_per_user = 1
    try:
        assert upload_sales(client, auth).status_code == 201
        assert upload_sales(client, auth).status_code == 400
    finally:
        s.max_datasets_per_user = old


def test_users_cannot_see_or_touch_each_others_data(client, auth, sales, provider):
    other = register_and_login(client, "b@example.com")
    assert client.get("/datasets", headers=other).json() == []
    assert client.get(f"/datasets/{sales['id']}/preview", headers=other).status_code == 404
    assert client.delete(f"/datasets/{sales['id']}", headers=other).status_code == 404

    # B has no data at all -> cannot query
    assert client.post("/query", headers=other, json={"question": "show everything"}).status_code == 400

    # B uploads their own data, then tries to read A's table by name -> not available to B
    upload_sales(client, other, name="mine.csv")
    provider.sql_responses = ['SELECT * FROM "sales"'] * 3
    r = client.post("/query", headers=other, json={"question": "show A's sales"}).json()
    assert r["status"] == "error" and "Unknown table" in r["attempt_log"][0]["error"]


# ------------------------------------------------------------- query
def test_query_end_to_end(client, auth, sales, provider):
    provider.sql_responses = ["SELECT region, SUM(revenue) AS total FROM sales GROUP BY 1 ORDER BY 2 DESC"]
    r = client.post("/query", headers=auth, json={"question": "Total revenue by region?"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["route"] == "data" and body["attempts"] == 1
    assert body["columns"] == ["region", "total"]
    assert body["rows"][0] == ["South", 510] and len(body["rows"]) == 3
    assert body["chart"]["type"] == "bar"
    assert body["prompt_tokens"] == 200 and body["completion_tokens"] == 40 and body["llm_calls"] == 2
    assert body["provider"] == "scripted" and body["latency_ms"] >= 0

    hist = client.get("/query/history", headers=auth).json()
    assert len(hist) == 1 and hist[0]["sql"].startswith("SELECT region") and hist[0]["rows"][0][0] == "South"


def test_query_self_corrects_via_api(client, auth, sales, provider):
    provider.sql_responses = ["SELECT SUM(revenu) FROM sales", "SELECT SUM(revenue) AS total FROM sales"]
    body = client.post("/query", headers=auth, json={"question": "total revenue"}).json()
    assert body["status"] == "ok" and body["attempts"] == 2 and body["rows"] == [[735.75]]
    assert body["attempt_log"][0]["error"] and body["attempt_log"][1]["error"] is None


def test_query_failure_is_reported_not_raised(client, auth, sales, provider):
    provider.sql_responses = ["SELECT nope FROM sales"] * 5
    body = client.post("/query", headers=auth, json={"question": "something impossible"}).json()
    assert body["status"] == "error" and body["attempts"] == 3 and body["error"]


def test_llm_outage_returns_graceful_error(client, auth, sales, provider):
    provider.fail = True
    body = client.post("/query", headers=auth, json={"question": "total revenue"}).json()
    assert body["status"] == "error" and body["attempts"] == 1


def test_query_without_dataset_and_bad_input(client, auth):
    assert client.post("/query", headers=auth, json={"question": "anything at all"}).status_code == 400
    assert client.post("/query", headers=auth, json={"question": "x"}).status_code == 422
    assert client.post("/query", headers=auth, json={"question": "x" * 600}).status_code == 422


def test_follow_up_receives_parent_sql(client, auth, sales, provider):
    provider.sql_responses = ["SELECT region, SUM(revenue) AS t FROM sales GROUP BY 1",
                              "SELECT region, SUM(revenue) AS t FROM sales GROUP BY 1 ORDER BY 2 DESC LIMIT 1"]
    first = client.post("/query", headers=auth, json={"question": "revenue by region"}).json()
    client.post("/query", headers=auth, json={"question": "only the top one", "parent_query_id": first["id"]})
    assert "Previous turn" in provider.sql_prompts()[1] and "GROUP BY 1" in provider.sql_prompts()[1]
    assert "Previous turn" not in provider.sql_prompts()[0]


def test_thumbs_up_becomes_few_shot_example(client, auth, sales, provider):
    provider.sql_responses = ["SELECT COUNT(*) AS n FROM sales", "SELECT COUNT(*) AS n FROM sales"]
    q1 = client.post("/query", headers=auth, json={"question": "how many orders are there"}).json()
    assert client.post(f"/query/{q1['id']}/feedback", headers=auth, json={"value": 1}).status_code == 204
    client.post("/query", headers=auth, json={"question": "how many orders do we have"})
    assert "answered correctly before" in provider.sql_prompts()[1]
    assert "answered correctly before" not in provider.sql_prompts()[0]


def test_feedback_is_owner_only_and_validated(client, auth, sales, provider):
    provider.sql_responses = ["SELECT 1 AS one"]
    q = client.post("/query", headers=auth, json={"question": "just one"}).json()
    other = register_and_login(client, "b@example.com")
    assert client.post(f"/query/{q['id']}/feedback", headers=other, json={"value": 1}).status_code == 404
    assert client.post(f"/query/{q['id']}/feedback", headers=auth, json={"value": 5}).status_code == 422


def test_rate_limit(client, auth, sales, provider):
    s = get_settings()
    old = s.rate_limit_per_min
    s.rate_limit_per_min = 2
    try:
        provider.sql_responses = ["SELECT 1 AS a"] * 5
        codes = [client.post("/query", headers=auth, json={"question": f"question {i}"}).status_code for i in range(4)]
        assert codes == [200, 200, 429, 429]
    finally:
        s.rate_limit_per_min = old


def test_table_is_rebuilt_from_stored_upload_when_disk_is_wiped(client, auth, sales, provider):
    import shutil
    shutil.rmtree(get_settings().data_dir)  # simulate ephemeral container disk
    provider.sql_responses = ["SELECT COUNT(*) AS n FROM sales"]
    body = client.post("/query", headers=auth, json={"question": "how many orders"}).json()
    assert body["status"] == "ok" and body["rows"] == [[5]]


def test_insights_route_via_api(client, auth, sales, provider):
    provider.json_response = '{"queries":[{"title":"Revenue by region","sql":"SELECT region, SUM(revenue) AS t FROM sales GROUP BY 1"}]}'
    body = client.post("/query", headers=auth, json={"question": "give me insights on this data"}).json()
    assert body["route"] == "insights" and body["status"] == "ok" and len(body["insights"]) == 1


# ------------------------------------------------------------- metrics
def test_metrics_summary_and_admin_access(client, auth, sales, provider):
    provider.sql_responses = ["SELECT COUNT(*) AS n FROM sales", "SELECT bad FROM sales", "SELECT bad FROM sales",
                              "SELECT bad FROM sales"]
    client.post("/query", headers=auth, json={"question": "how many orders"})
    client.post("/query", headers=auth, json={"question": "an impossible one"})
    m = client.get("/metrics/me", headers=auth).json()
    assert m["total_queries"] == 2 and m["success_rate"] == 0.5
    assert m["latency_ms"]["p95"] >= m["latency_ms"]["p50"]
    assert m["retry_rate"] == 0.5 and m["prompt_tokens"] > 0

    assert client.get("/metrics/admin", headers=auth).status_code == 403
    s = get_settings()
    s.admin_emails = "boss@example.com"
    try:
        admin = register_and_login(client, "boss@example.com")
        assert client.get("/metrics/admin", headers=admin).json()["active_users"] == 1
    finally:
        s.admin_emails = ""
