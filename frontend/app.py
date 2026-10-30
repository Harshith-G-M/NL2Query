"""Streamlit UI for nl2query. Talks to the FastAPI backend only over HTTP."""
import os

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

API = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
st.set_page_config(page_title="nl2query", page_icon="🔎", layout="wide")


# ----------------------------------------------------------------- API helpers
def call(method: str, path: str, **kw):
    headers = {"Authorization": f"Bearer {st.session_state.token}"} if st.session_state.get("token") else {}
    try:
        r = requests.request(method, f"{API}{path}", headers=headers, timeout=180, **kw)
    except requests.RequestException as e:
        st.error(f"Cannot reach the API at {API}: {e}")
        st.stop()
    if r.status_code == 401 and st.session_state.get("token"):
        st.session_state.pop("token", None)
        st.warning("Session expired. Please log in again.")
        st.rerun()
    return r


def detail(r) -> str:
    try:
        d = r.json().get("detail", r.text)
        return d if isinstance(d, str) else str(d)
    except ValueError:
        return r.text


def draw_chart(chart: dict | None, columns: list[str], rows: list[list]):
    if not chart or not rows:
        return
    df = pd.DataFrame(rows, columns=columns)
    kind, x, y = chart["type"], chart["x"], chart["y"]
    if kind == "pie":
        fig = px.pie(df, names=x, values=y)
    elif kind == "line":
        fig = px.line(df, x=x, y=chart.get("series", [y]), markers=True)
    elif kind == "scatter":
        fig = px.scatter(df, x=x, y=y)
    else:
        fig = px.bar(df, x=x, y=chart.get("series", [y]), barmode="group")
    st.plotly_chart(fig, width="stretch")


# ----------------------------------------------------------------- auth screen
def auth_screen():
    st.title("🔎 nl2query")
    st.caption("Upload a CSV or Excel file and ask questions in plain English. SQL is generated, validated and run read-only.")
    login, register = st.tabs(["Log in", "Create account"])
    with login:
        with st.form("login"):
            email = st.text_input("Email")
            pw = st.text_input("Password", type="password")
            if st.form_submit_button("Log in"):
                r = call("POST", "/auth/login", data={"username": email, "password": pw})
                if r.ok:
                    st.session_state.token = r.json()["access_token"]
                    st.rerun()
                st.error(detail(r))
    with register:
        with st.form("register"):
            email = st.text_input("Email", key="reg_email")
            pw = st.text_input("Password (min 8 characters)", type="password", key="reg_pw")
            if st.form_submit_button("Create account"):
                r = call("POST", "/auth/register", json={"email": email, "password": pw})
                if r.ok:
                    r2 = call("POST", "/auth/login", data={"username": email, "password": pw})
                    st.session_state.token = r2.json()["access_token"]
                    st.rerun()
                st.error(detail(r))


# ----------------------------------------------------------------- tabs
def tab_ask(datasets: list[dict]):
    if not datasets:
        st.info("Upload a dataset in the **Datasets** tab to get started.")
        return
    for d in datasets:
        with st.expander(f"📄 {d['table_name']} ({d['row_count']:,} rows)"):
            st.write(", ".join(f"`{c['name']}`" for c in d["columns"]))

    if "last" in st.session_state:
        render_result(st.session_state.last)

    follow = st.session_state.get("last", {}).get("id") if st.session_state.get("follow_up") else None
    st.checkbox("Treat my next question as a follow-up to the last one", key="follow_up",
                disabled="last" not in st.session_state)
    q = st.chat_input("Ask a question about your data, e.g. 'revenue by region as a pie chart'")
    if q:
        with st.spinner("Thinking..."):
            r = call("POST", "/query", json={"question": q, "parent_query_id": follow})
        if r.ok:
            st.session_state.last = r.json()
            st.rerun()
        st.error(detail(r))


def render_result(res: dict):
    st.markdown(f"**You asked:** {res['question']}")
    if res["status"] == "error":
        st.error(res["answer"] or res["error"])
    else:
        st.success(res["answer"])
    if res.get("insights"):
        for ins in res["insights"]:
            st.subheader(ins["title"])
            draw_chart(ins.get("chart"), ins["columns"], ins["rows"])
            st.dataframe(pd.DataFrame(ins["rows"], columns=ins["columns"]), width="stretch")
    elif res["rows"]:
        draw_chart(res["chart"], res["columns"], res["rows"])
        st.dataframe(pd.DataFrame(res["rows"], columns=res["columns"]), width="stretch")
        if res["truncated"]:
            st.caption("Result truncated to the row limit.")
    if res.get("sql"):
        with st.expander("SQL"):
            st.code(res["sql"], language="sql")
    if len(res.get("attempt_log", [])) > 1:
        with st.expander(f"Self-correction: {res['attempts']} attempts"):
            for a in res["attempt_log"]:
                st.code(a["sql"], language="sql")
                st.caption(f"❌ {a['error']}" if a["error"] else "✅ succeeded")
    st.caption(f"{res['latency_ms']} ms · {res['prompt_tokens'] + res['completion_tokens']} tokens · "
               f"${res['cost_usd']:.5f} · {res['provider']}/{res['model']}")
    c1, c2, _ = st.columns([1, 1, 8])
    for col, label, val in ((c1, "👍", 1), (c2, "👎", -1)):
        if col.button(label, key=f"fb{val}_{res['id']}"):
            call("POST", f"/query/{res['id']}/feedback", json={"value": val})
            st.toast("Thanks! Good answers are reused as examples for future questions." if val == 1 else "Thanks, noted.")


def tab_datasets(datasets: list[dict]):
    up = st.file_uploader("Upload a CSV or Excel (.xlsx) file", type=["csv", "xlsx"])
    if up and st.button("Upload"):
        r = call("POST", "/datasets", files={"file": (up.name, up.getvalue())})
        st.success(f"Loaded {r.json()['row_count']:,} rows as table `{r.json()['table_name']}`") if r.ok else st.error(detail(r))
        if r.ok:
            st.rerun()
    for d in datasets:
        c1, c2 = st.columns([8, 1])
        with c1.expander(f"{d['table_name']}  ·  {d['row_count']:,} rows  ·  {d['filename']}"):
            pv = call("GET", f"/datasets/{d['id']}/preview")
            if pv.ok:
                st.dataframe(pd.DataFrame(pv.json()["rows"], columns=pv.json()["columns"]), width="stretch")
            st.json(d["columns"], expanded=False)
        if c2.button("🗑", key=f"del{d['id']}"):
            call("DELETE", f"/datasets/{d['id']}")
            st.rerun()


def tab_history():
    r = call("GET", "/query/history", params={"limit": 50})
    if not r.ok:
        return st.error(detail(r))
    for h in r.json():
        icon = "✅" if h["status"] == "ok" else "❌"
        fb = {1: " 👍", -1: " 👎"}.get(h["feedback"], "")
        with st.expander(f"{icon} {h['question']}{fb}"):
            st.write(h["answer"])
            if h["sql"]:
                st.code(h["sql"], language="sql")
            if h["rows"]:
                st.dataframe(pd.DataFrame(h["rows"], columns=h["columns"]), width="stretch")


def tab_metrics():
    r = call("GET", "/metrics/me")
    m = r.json() if r.ok else {}
    if not m.get("total_queries"):
        return st.info("No queries yet.")
    a, b, c, d = st.columns(4)
    a.metric("Queries", m["total_queries"])
    b.metric("Success rate", f"{m['success_rate']:.0%}")
    c.metric("p95 latency", f"{m['latency_ms']['p95']} ms")
    d.metric("Total cost", f"${m['total_cost_usd']:.4f}")
    e, f, g, h = st.columns(4)
    e.metric("Retry rate", f"{m['retry_rate']:.0%}")
    f.metric("Avg attempts", m["avg_attempts"])
    g.metric("Tokens", f"{m['prompt_tokens'] + m['completion_tokens']:,}")
    h.metric("👍 / 👎", f"{m['feedback']['up']} / {m['feedback']['down']}")
    if m["queries_per_day"]:
        st.plotly_chart(px.bar(pd.DataFrame(m["queries_per_day"], columns=["day", "queries"]), x="day", y="queries"),
                        width="stretch")
    if m["top_errors"]:
        st.subheader("Top error types")
        st.table(pd.DataFrame(m["top_errors"], columns=["error", "count"]))


# ----------------------------------------------------------------- main
if not st.session_state.get("token"):
    auth_screen()
else:
    me = call("GET", "/auth/me").json()
    with st.sidebar:
        st.write(f"Signed in as **{me['email']}**")
        if st.button("Log out"):
            st.session_state.clear()
            st.rerun()
    datasets = call("GET", "/datasets").json()
    t1, t2, t3, t4 = st.tabs(["💬 Ask", "📁 Datasets", "🕘 History", "📊 Metrics"])
    with t1:
        tab_ask(datasets)
    with t2:
        tab_datasets(datasets)
    with t3:
        tab_history()
    with t4:
        tab_metrics()
